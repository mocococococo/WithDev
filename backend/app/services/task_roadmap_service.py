import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

from app.core.config import get_settings


MIN_ROADMAP_STEPS = 1
MAX_ROADMAP_STEPS = 8
MAX_RELATED_MINUTES = 20
MAX_RELATED_MINUTES_CHARS = 20_000
GEMINI_REQUEST_TIMEOUT_SECONDS = 30
TASK_ROADMAP_GENERATION_POLICY_VERSION = "no-fallback-v1"
ROADMAP_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "overview": {
            "type": "string",
            "description": "タスクを完了するための方針を説明する1〜2文の概要",
        },
        "steps": {
            "type": "array",
            "min_items": MIN_ROADMAP_STEPS,
            "max_items": MAX_ROADMAP_STEPS,
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "実際に着手できる具体的な作業名",
                    },
                    "description": {
                        "type": "string",
                        "description": "ステップの完了を判断できる具体的な条件",
                    },
                },
                "required": ["title", "description"],
            },
        },
    },
    "required": ["overview", "steps"],
}
logger = logging.getLogger(__name__)


class TaskRoadmapGenerationError(Exception):
    def __init__(self, message: str, *, reason: str = "invalid_response") -> None:
        super().__init__(message)
        self.reason = reason


def generate_task_roadmap(
    *,
    task: dict[str, Any],
    related_minutes: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        raise TaskRoadmapGenerationError(
            "Gemini settings are missing",
            reason="configuration_error",
        )

    prompt = (
        _load_prompt()
        .replace("{task}", json.dumps(task, ensure_ascii=False, default=str))
        .replace(
            "{related_minutes}",
            json.dumps(
                _compact_related_minutes(related_minutes),
                ensure_ascii=False,
                default=str,
            ),
        )
    )

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                candidate_count=1,
                temperature=0.2,
                max_output_tokens=2048,
                response_mime_type="application/json",
                response_schema=ROADMAP_RESPONSE_SCHEMA,
            ),
            request_options=genai.types.RequestOptions(
                retry=None,
                timeout=GEMINI_REQUEST_TIMEOUT_SECONDS,
            ),
        )
    except Exception as exc:
        reason = "quota_exceeded" if _is_gemini_quota_error(exc) else "request_failed"
        logger.warning(
            "Task roadmap Gemini request failed task_id=%s reason=%s: %s",
            task.get("task_id"),
            reason,
            exc,
        )
        raise TaskRoadmapGenerationError(
            "Gemini request failed",
            reason=reason,
        ) from exc

    if not getattr(response, "parts", None):
        logger.warning(
            "Task roadmap Gemini response was empty task_id=%s",
            task.get("task_id"),
        )
        raise TaskRoadmapGenerationError(
            "Gemini returned an empty response",
            reason="empty_response",
        )

    try:
        return parse_task_roadmap(response.text)
    except TaskRoadmapGenerationError as exc:
        logger.warning(
            "Task roadmap Gemini response was invalid task_id=%s reason=%s: %s",
            task.get("task_id"),
            exc.reason,
            exc,
        )
        raise


def parse_task_roadmap(value: str) -> dict[str, Any]:
    text = _strip_code_fence(value)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise TaskRoadmapGenerationError("Gemini response does not contain a JSON object")

    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise TaskRoadmapGenerationError("Gemini response JSON is invalid") from exc

    if not isinstance(payload, dict):
        raise TaskRoadmapGenerationError("Gemini response must be a JSON object")

    overview = _clean_text(payload.get("overview"), max_length=2000)
    raw_steps = payload.get("steps")
    if not overview or not isinstance(raw_steps, list):
        raise TaskRoadmapGenerationError("Gemini response is missing roadmap fields")

    steps: list[dict[str, Any]] = []
    for raw_step in raw_steps[:MAX_ROADMAP_STEPS]:
        if not isinstance(raw_step, dict):
            continue
        title = _clean_text(raw_step.get("title"), max_length=255)
        description = _clean_text(raw_step.get("description"), max_length=5000)
        if not title or not description:
            continue

        steps.append(
            {
                "existing_step_id": None,
                "title": title,
                "description": description,
            }
        )

    if len(steps) < MIN_ROADMAP_STEPS:
        raise TaskRoadmapGenerationError(
            f"Gemini response must contain at least {MIN_ROADMAP_STEPS} valid steps"
        )

    return {"overview": overview, "steps": steps}


def get_task_roadmap_prompt_version() -> str:
    generation_contract = json.dumps(
        {
            "prompt": _load_prompt(),
            "response_schema": ROADMAP_RESPONSE_SCHEMA,
            "max_related_minutes": MAX_RELATED_MINUTES,
            "max_related_minutes_chars": MAX_RELATED_MINUTES_CHARS,
            "generation_policy": TASK_ROADMAP_GENERATION_POLICY_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(generation_contract.encode("utf-8")).hexdigest()


def task_roadmap_input_hash(
    *,
    task: dict[str, Any],
    related_minutes: list[dict[str, Any]],
) -> str:
    payload = json.dumps(
        {"task": task, "related_minutes": related_minutes},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_prompt() -> str:
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "task_roadmap_prompt.txt"
    return prompt_path.read_text(encoding="utf-8")


def _strip_code_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json")
    elif text.startswith("```"):
        text = text.removeprefix("```")
    if text.endswith("```"):
        text = text.removesuffix("```")
    return text.strip()


def _compact_related_minutes(
    related_minutes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    remaining_chars = MAX_RELATED_MINUTES_CHARS
    for minutes in reversed(related_minutes[-MAX_RELATED_MINUTES:]):
        if remaining_chars <= 0:
            break
        body = str(minutes.get("body") or "")
        compacted.append(
            {
                "minutes_id": minutes.get("minutes_id"),
                "title": minutes.get("title"),
                "body": body[:remaining_chars],
                "updated_at": minutes.get("updated_at"),
            }
        )
        remaining_chars -= min(len(body), remaining_chars)
    compacted.reverse()
    return compacted


def _clean_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:max_length] if text else None


def _is_gemini_quota_error(error: Exception) -> bool:
    if isinstance(error, ResourceExhausted):
        return True
    return str(error).lstrip().startswith("429 ")
