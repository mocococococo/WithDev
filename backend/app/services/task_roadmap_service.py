import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import google.generativeai as genai

from app.core.config import get_settings


MIN_ROADMAP_STEPS = 3
MAX_ROADMAP_STEPS = 8


class TaskRoadmapGenerationError(Exception):
    pass


def generate_task_roadmap(
    *,
    task: dict[str, Any],
    related_minutes: list[dict[str, Any]],
    existing_steps: list[dict[str, Any]],
    deleted_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        raise TaskRoadmapGenerationError("Gemini settings are missing")

    prompt = (
        _load_prompt()
        .replace("{task}", json.dumps(task, ensure_ascii=False, default=str))
        .replace(
            "{related_minutes}",
            json.dumps(related_minutes, ensure_ascii=False, default=str),
        )
        .replace(
            "{existing_steps}",
            json.dumps(existing_steps, ensure_ascii=False, default=str),
        )
        .replace(
            "{deleted_steps}",
            json.dumps(deleted_steps, ensure_ascii=False, default=str),
        )
    )

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(
            prompt,
            request_options={"timeout": 60},
        )
    except Exception as exc:
        raise TaskRoadmapGenerationError("Gemini request failed") from exc

    if not getattr(response, "parts", None):
        raise TaskRoadmapGenerationError("Gemini response is empty")

    return parse_task_roadmap(response.text)


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

        existing_step_id = _parse_uuid(raw_step.get("existing_step_id"))
        steps.append(
            {
                "existing_step_id": existing_step_id,
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
    return hashlib.sha256(_load_prompt().encode("utf-8")).hexdigest()


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


def _clean_text(value: object, *, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:max_length] if text else None


def _parse_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None
