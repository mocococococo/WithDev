import json
from pathlib import Path
from typing import Any

import google.generativeai as genai

from app.core.config import get_settings


class TaskGenerationError(Exception):
    pass


def generate_task_actions_from_minutes(
    *,
    minutes_body: str,
    existing_tasks: list[dict[str, Any]],
    team_members: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        raise TaskGenerationError("Gemini settings are missing")

    prompt = (
        _load_prompt()
        .replace("{minutes}", minutes_body)
        .replace("{existing_tasks}", json.dumps(existing_tasks, ensure_ascii=False, default=str))
        .replace("{team_members}", json.dumps(team_members, ensure_ascii=False, default=str))
    )

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(prompt)
    except Exception as exc:
        raise TaskGenerationError("Gemini request failed") from exc

    if not getattr(response, "parts", None):
        raise TaskGenerationError("Gemini response is empty")

    return _parse_actions(response.text)


def _load_prompt() -> str:
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "tasks_prompt.txt"
    return prompt_path.read_text(encoding="utf-8")


def _parse_actions(value: str) -> list[dict[str, Any]]:
    text = _strip_code_fence(value)
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end < start:
        raise TaskGenerationError("Gemini response does not contain a JSON array")

    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise TaskGenerationError("Gemini response JSON is invalid") from exc

    if not isinstance(payload, list):
        raise TaskGenerationError("Gemini response must be a JSON array")

    actions: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            actions.append(item)
    return actions


def _strip_code_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json")
    elif text.startswith("```"):
        text = text.removeprefix("```")

    if text.endswith("```"):
        text = text.removesuffix("```")

    return text.strip()
