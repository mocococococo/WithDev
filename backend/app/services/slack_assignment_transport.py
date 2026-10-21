import hashlib
import hmac
import time
from datetime import date, datetime, time as datetime_time
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx

from app.services.slack_service import SlackApiError, _parse_slack_payload

TOKYO = ZoneInfo("Asia/Tokyo")
ACTION_PREFIX = "withdev_assignment_"


def verify_slack_signature(*, body: bytes, timestamp: str, signature: str, secret: str) -> bool:
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_due_date(value: str) -> datetime:
    parsed = date.fromisoformat(value)
    return datetime.combine(parsed, datetime_time(23, 59, 59), tzinfo=TOKYO)


def assignment_blocks(*, text: str, attempt_id: UUID, due_at: datetime | None) -> list[dict]:
    picker = {
        "type": "datepicker", "action_id": ACTION_PREFIX + "date",
        "placeholder": {"type": "plain_text", "text": "希望期限を選ぶ"},
    }
    if due_at:
        picker["initial_date"] = due_at.astimezone(TOKYO).date().isoformat()
    return [
        {"type": "section", "text": {"type": "plain_text", "text": text[:3000]}},
        {"type": "context", "elements": [{"type": "plain_text", "text": "期限を変更する場合は日付を選び、「担当する」で確定してください。相談はこのスレッドへ返信できます。"}]},
        {"type": "actions", "block_id": str(attempt_id), "elements": [picker]},
        {"type": "actions", "elements": [
            {"type": "button", "action_id": ACTION_PREFIX + action,
             "text": {"type": "plain_text", "text": label}, "value": str(attempt_id),
             **({"style": "primary"} if action == "accept" else {})}
            for action, label in [("accept", "担当する"), ("decline", "今回は難しい")]
        ]},
    ]


async def open_assignment_dm(*, token: str, email: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://slack.com/api/users.lookupByEmail", params={"email": email},
            headers={"Authorization": f"Bearer {token}"},
        )
        user = _parse_slack_payload(response).get("user")
        if not isinstance(user, dict) or not isinstance(user.get("id"), str) or user.get("deleted") or user.get("is_bot"):
            raise SlackApiError("slack user is unavailable")
        response = await client.post(
            "https://slack.com/api/conversations.open", json={"users": user["id"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        channel = _parse_slack_payload(response).get("channel")
        if not isinstance(channel, dict) or not isinstance(channel.get("id"), str):
            raise SlackApiError("slack dm is unavailable")
        return user["id"], channel["id"]
