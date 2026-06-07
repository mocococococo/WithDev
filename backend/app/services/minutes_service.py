from pathlib import Path

import google.generativeai as genai

from app.core.config import get_settings


MAX_TEXT_LENGTH = 50_000


class MinutesGenerationError(Exception):
    pass


def generate_minutes_from_text(text: str) -> str:
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        raise MinutesGenerationError("Gemini settings are missing")

    prompt = _load_prompt().replace("{text}", text)

    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(prompt)
    except Exception as exc:
        raise MinutesGenerationError("Gemini request failed") from exc

    if not getattr(response, "parts", None):
        raise MinutesGenerationError("Gemini response is empty")

    body = _normalize_markdown_response(response.text)
    if not body:
        raise MinutesGenerationError("Gemini response body is empty")

    return body


def _load_prompt() -> str:
    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "minutes_prompt.txt"
    return prompt_path.read_text(encoding="utf-8")


def _normalize_markdown_response(value: str) -> str:
    text = value.strip()
    if text.startswith("```markdown"):
        text = text.removeprefix("```markdown")
    elif text.startswith("```md"):
        text = text.removeprefix("```md")
    elif text.startswith("```"):
        text = text.removeprefix("```")

    if text.endswith("```"):
        text = text.removesuffix("```")

    return text.strip()
