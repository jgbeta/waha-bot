from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from app.config import Settings
from app.models import IncomingMessage
from app.stores.base import StateStore

logger = logging.getLogger("waha_ai_bot")
router = APIRouter()


@router.post("/webhook")
async def webhook(
    request: Request,
    x_webhook_hmac: str | None = Header(default=None),
    x_webhook_request_id: str | None = Header(default=None),
) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    raw = await request.body()
    verify_hmac(raw, x_webhook_hmac, settings)

    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    event_type = event.get("event")
    event_id = str(event.get("id") or x_webhook_request_id or "")

    if event_type == "session.status":
        payload = event.get("payload") or {}
        logger.info(
            "session_status session=%s status=%s event_id=%s",
            event.get("session"),
            payload.get("status"),
            event_id,
        )
        return {"ok": True, "handled": "session.status"}

    if event_type != "message":
        return {"ok": True, "ignored": f"event {event_type!r}"}

    payload = event.get("payload") or {}
    if payload.get("fromMe") is True:
        return {"ok": True, "ignored": "fromMe"}

    chat_id = str(payload.get("from") or "")
    text = str(payload.get("body") or "").strip()
    message_id = str(payload.get("id") or event_id or "")
    timestamp = payload.get("timestamp")

    if not chat_id or not message_id:
        logger.warning("ignored_message_missing_ids event_id=%s payload=%s", event_id, payload)
        return {"ok": True, "ignored": "missing chat_id or message_id"}

    if not text:
        logger.info("ignored_empty_or_media_message chat_id=%s message_id=%s", chat_id, message_id)
        return {"ok": True, "ignored": "empty body or media-only message"}

    if not await request.app.state.allowlist.is_allowed_chat(chat_id):
        logger.info("ignored_disallowed_chat chat_id=%s message_id=%s", chat_id, message_id)
        return {"ok": True, "ignored": "chat not allowlisted"}

    msg = IncomingMessage(
        event_id=event_id,
        message_id=message_id,
        chat_id=chat_id,
        text=text,
        timestamp=timestamp if isinstance(timestamp, int) else None,
        raw=event,
    )

    store: StateStore = request.app.state.store
    accepted = await store.record_incoming(msg)
    if not accepted:
        return {"ok": True, "duplicate": True}

    try:
        request.app.state.queue.put_nowait(msg)
    except asyncio.QueueFull as exc:
        logger.error("queue_full message_id=%s", message_id)
        await store.forget_incoming(msg)
        raise HTTPException(status_code=503, detail="Bot queue full") from exc

    return {"ok": True, "queued": True}


def verify_hmac(raw_body: bytes, header_value: str | None, settings: Settings) -> None:
    key = settings.waha_webhook_hmac_key
    if not key:
        return
    if not header_value:
        raise HTTPException(status_code=401, detail="Missing X-Webhook-Hmac")
    expected = hmac.new(key.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    if not hmac.compare_digest(expected, header_value.strip()):
        raise HTTPException(status_code=401, detail="Invalid webhook HMAC")
