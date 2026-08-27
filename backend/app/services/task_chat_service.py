import json
import logging
import re
from collections.abc import Iterable
from typing import Any

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted

from app.core.config import get_settings


MAX_CHAT_HISTORY_MESSAGES = 12
MAX_CHAT_MESSAGE_CHARS = 2_000
MAX_CHAT_HISTORY_MESSAGE_CHARS = 4_000
MAX_TEAM_MINUTES = 10
MAX_TEAM_MINUTES_CHARS = 24_000
GEMINI_REQUEST_TIMEOUT_SECONDS = 45
TASK_CHAT_MAX_ATTEMPTS = 2
TASK_CHAT_RETRY_INSTRUCTION = """

再出力指示:
前回の出力はAPIの検証条件を満たしませんでした。次の条件を必ず守って再出力してください。
- 指定されたJSONオブジェクト以外は出力しない。
- answer は空でない文字列とし、改行を含める場合も正しいJSON文字列にする。
- source_minutes_ids は必ず配列にする。
- answer は原則1,000文字以内にする。
"""
TASK_CHAT_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "タスクを前に進めるための、具体的で実行可能な日本語の回答",
        },
        "source_minutes_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "回答の根拠として実際に参照した議事録ID",
        },
    },
    "required": ["answer", "source_minutes_ids"],
}
logger = logging.getLogger(__name__)


class TaskChatError(Exception):
    def __init__(self, message: str, *, reason: str = "invalid_response") -> None:
        super().__init__(message)
        self.reason = reason


def generate_task_chat_answer(
    *,
    task: dict[str, Any],
    message: str,
    history: list[dict[str, str]],
    team_minutes: list[dict[str, Any]],
    related_minutes_ids: set[str] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        raise TaskChatError("Gemini settings are missing", reason="configuration_error")

    selected_minutes = select_relevant_minutes(
        task=task,
        message=message,
        team_minutes=team_minutes,
        related_minutes_ids=related_minutes_ids or set(),
    )
    compact_history = _compact_history(history)
    prompt = _load_prompt().format(
        task=json.dumps(task, ensure_ascii=False, default=str),
        team_minutes=json.dumps(selected_minutes, ensure_ascii=False, default=str),
        conversation_history=json.dumps(compact_history, ensure_ascii=False, default=str),
        user_message=message,
    )

    available_sources = {
        str(minutes.get("minutes_id")): minutes
        for minutes in selected_minutes
        if minutes.get("minutes_id")
    }
    try:
        genai.configure(api_key=settings.gemini_api_key)
        model = genai.GenerativeModel(settings.gemini_model)
    except Exception as exc:
        logger.warning(
            "Task chat Gemini client setup failed task_id=%s error_type=%s",
            task.get("task_id"),
            type(exc).__name__,
        )
        raise TaskChatError("Gemini request failed", reason="request_failed") from exc

    for attempt in range(1, TASK_CHAT_MAX_ATTEMPTS + 1):
        attempt_prompt = (
            prompt if attempt == 1 else f"{prompt}{TASK_CHAT_RETRY_INSTRUCTION}"
        )
        try:
            response = model.generate_content(
                attempt_prompt,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=2048,
                    response_mime_type="application/json",
                    response_schema=TASK_CHAT_RESPONSE_SCHEMA,
                ),
                request_options=genai.types.RequestOptions(
                    retry=None,
                    timeout=GEMINI_REQUEST_TIMEOUT_SECONDS,
                ),
            )
        except Exception as exc:
            reason = "quota_exceeded" if _is_gemini_quota_error(exc) else "request_failed"
            logger.warning(
                "Task chat Gemini request failed "
                "task_id=%s attempt=%s reason=%s error_type=%s",
                task.get("task_id"),
                attempt,
                reason,
                type(exc).__name__,
            )
            raise TaskChatError("Gemini request failed", reason=reason) from exc

        try:
            try:
                response_parts = getattr(response, "parts", None)
            except Exception as exc:
                raise TaskChatError("Gemini response parts are unavailable") from exc
            if not response_parts:
                raise TaskChatError(
                    "Gemini returned an empty response",
                    reason="empty_response",
                )
            try:
                response_text = response.text
            except Exception as exc:
                raise TaskChatError("Gemini response text is unavailable") from exc
            return parse_task_chat_response(
                response_text,
                available_sources=available_sources,
            )
        except TaskChatError as exc:
            logger.warning(
                "Task chat Gemini response rejected "
                "task_id=%s attempt=%s reason=%s finish_reason=%s response_chars=%s",
                task.get("task_id"),
                attempt,
                exc.reason,
                _response_finish_reason(response),
                _response_char_count(response),
            )
            if attempt == TASK_CHAT_MAX_ATTEMPTS:
                raise

    raise TaskChatError("Gemini response retry exhausted")


def parse_task_chat_response(
    value: str,
    *,
    available_sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    text = _strip_code_fence(value)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise TaskChatError("Gemini response does not contain a JSON object")

    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise TaskChatError("Gemini response JSON is invalid") from exc

    if not isinstance(payload, dict):
        raise TaskChatError("Gemini response must be a JSON object")
    answer = _clean_text(payload.get("answer"), max_length=12_000)
    if answer is None:
        raise TaskChatError("Gemini response is missing an answer")

    source_ids: list[str] = []
    raw_source_ids = payload.get("source_minutes_ids")
    if isinstance(raw_source_ids, list):
        for value in raw_source_ids:
            source_id = str(value).strip()
            if source_id in available_sources and source_id not in source_ids:
                source_ids.append(source_id)

    return {
        "answer": answer,
        "sources": [available_sources[source_id] for source_id in source_ids],
    }


def select_relevant_minutes(
    *,
    task: dict[str, Any],
    message: str,
    team_minutes: list[dict[str, Any]],
    related_minutes_ids: set[str],
) -> list[dict[str, Any]]:
    search_text = " ".join(
        str(value or "")
        for value in (message, task.get("title"), task.get("body"))
    )
    search_terms = _search_terms(search_text)

    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for recency_index, minutes in enumerate(team_minutes):
        minutes_id = str(minutes.get("minutes_id") or "")
        title = str(minutes.get("title") or "")
        body = str(minutes.get("body") or "")
        title_lower = title.lower()
        body_lower = body.lower()
        score = 0
        if minutes_id in related_minutes_ids:
            score += 10_000
        for term in search_terms:
            if term in title_lower:
                score += 8
            if term in body_lower:
                score += 2
        ranked.append((score, -recency_index, minutes))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected: list[dict[str, Any]] = []
    remaining_chars = MAX_TEAM_MINUTES_CHARS
    for _score, _recency, minutes in ranked:
        if len(selected) >= MAX_TEAM_MINUTES or remaining_chars <= 0:
            break
        body = str(minutes.get("body") or "")
        compact_body = body[:remaining_chars]
        selected.append(
            {
                "minutes_id": str(minutes.get("minutes_id")),
                "title": minutes.get("title"),
                "body": compact_body,
                "updated_at": minutes.get("updated_at"),
            }
        )
        remaining_chars -= len(compact_body)
    return selected


def _search_terms(value: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[a-zA-Z0-9_]{2,}|[一-龯ぁ-んァ-ヶー]{2,}", value.lower()):
        terms.add(token)
        if re.search(r"[一-龯ぁ-んァ-ヶー]", token):
            terms.update(_ngrams(token, sizes=(2, 3)))
    return set(sorted(terms, key=len, reverse=True)[:200])


def _ngrams(value: str, *, sizes: Iterable[int]) -> set[str]:
    return {
        value[index : index + size]
        for size in sizes
        for index in range(max(0, len(value) - size + 1))
    }


def _compact_history(history: list[dict[str, str]]) -> list[dict[str, str]]:
    compacted: list[dict[str, str]] = []
    for item in history[-MAX_CHAT_HISTORY_MESSAGES:]:
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        compacted.append(
            {
                "role": role,
                "content": content[:MAX_CHAT_HISTORY_MESSAGE_CHARS],
            }
        )
    return compacted


def _load_prompt() -> str:
    from pathlib import Path

    prompt_path = Path(__file__).resolve().parents[1] / "prompts" / "task_chat_prompt.txt"
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


def _is_gemini_quota_error(error: Exception) -> bool:
    if isinstance(error, ResourceExhausted):
        return True
    return str(error).lstrip().startswith("429 ")


def _response_finish_reason(response: object) -> str:
    try:
        candidates = getattr(response, "candidates", None)
    except Exception:
        return "unknown"
    if not candidates:
        return "unknown"
    try:
        finish_reason = getattr(candidates[0], "finish_reason", None)
    except Exception:
        return "unknown"
    if finish_reason is None:
        return "unknown"
    return str(getattr(finish_reason, "name", finish_reason))


def _response_char_count(response: object) -> int:
    try:
        text = getattr(response, "text", "")
    except Exception:
        return 0
    return len(text) if isinstance(text, str) else 0
