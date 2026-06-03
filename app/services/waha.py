from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings

logger = logging.getLogger("waha_ai_bot")


class WAHAClient:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self.http = http
        self.settings = settings

    def _headers(self, content_type: bool = True) -> dict[str, str]:
        headers = {"X-Api-Key": self.settings.waha_api_key, "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.settings.waha_base_url.rstrip('/')}{path}"

    async def send_text(self, chat_id: str, text: str) -> bool:
        payload = {
            "session": self.settings.waha_session,
            "chatId": chat_id,
            "text": text,
            "linkPreview": False,
        }
        result = await self.post("/api/sendText", payload, retries=self.settings.waha_send_retries, required=True)
        return result is not None

    async def send_seen(self, chat_id: str, message_id: str) -> None:
        await self.post(
            "/api/sendSeen",
            {"session": self.settings.waha_session, "chatId": chat_id, "messageIds": [message_id]},
            retries=1,
            required=False,
        )

    async def start_typing(self, chat_id: str) -> None:
        await self.post(
            "/api/startTyping",
            {"session": self.settings.waha_session, "chatId": chat_id},
            retries=1,
            required=False,
        )

    async def stop_typing(self, chat_id: str) -> None:
        await self.post(
            "/api/stopTyping",
            {"session": self.settings.waha_session, "chatId": chat_id},
            retries=1,
            required=False,
        )

    async def ping(self) -> dict[str, Any]:
        try:
            response = await self.http.get(self._url("/ping"))
        except httpx.RequestError as exc:
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        return {"ok": response.status_code == 200, "status_code": response.status_code}

    async def session_status(self) -> dict[str, Any]:
        url = self._url(f"/api/sessions/{self.settings.waha_session}")
        try:
            response = await self.http.get(url, headers=self._headers(content_type=False))
        except httpx.RequestError as exc:
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        if response.status_code != 200:
            return {"ok": False, "status_code": response.status_code, "body": response.text[:300]}
        try:
            data = response.json()
        except json.JSONDecodeError:
            return {"ok": False, "status_code": response.status_code, "error": "invalid JSON from WAHA"}
        status = data.get("status")
        return {"ok": status == "WORKING", "status": status}

    async def resolve_lid(self, lid: str) -> str | None:
        url = self._url(f"/api/{self.settings.waha_session}/lids/{quote(lid, safe='')}")
        try:
            response = await self.http.get(url, headers=self._headers(content_type=False))
        except httpx.RequestError as exc:
            logger.warning("lid_lookup_failed lid=%s error=%s", lid, exc)
            return None
        if response.status_code != 200:
            logger.warning("lid_lookup_failed lid=%s status=%s body=%s", lid, response.status_code, response.text[:300])
            return None
        try:
            data = response.json()
        except json.JSONDecodeError:
            logger.warning("lid_lookup_failed lid=%s reason=invalid_json", lid)
            return None
        phone = data.get("pn")
        if not isinstance(phone, str) or not phone:
            logger.warning("lid_lookup_failed lid=%s reason=missing_pn", lid)
            return None
        logger.info("lid_lookup_resolved lid=%s pn=%s", lid, phone)
        return phone

    async def post(self, path: str, payload: dict[str, Any], retries: int, required: bool) -> dict[str, Any] | None:
        url = self._url(path)
        for attempt in range(1, retries + 1):
            try:
                response = await self.http.post(url, json=payload, headers=self._headers())
            except httpx.RequestError as exc:
                logger.warning("waha_request_error path=%s attempt=%s error=%s", path, attempt, exc)
                response = None
            else:
                if response.status_code in {200, 201}:
                    if not response.content:
                        return {}
                    try:
                        return response.json()
                    except json.JSONDecodeError:
                        return {}
                logger.warning(
                    "waha_non_success path=%s status=%s attempt=%s body=%s",
                    path,
                    response.status_code,
                    attempt,
                    response.text[:500],
                )
                if response.status_code in {401, 403, 404}:
                    break
                if response.status_code == 422 and attempt == 1:
                    await self.start_session()
            if attempt < retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))
        if required:
            logger.error("waha_post_failed path=%s payload_chat=%s", path, payload.get("chatId"))
        return None

    async def start_session(self) -> None:
        url = self._url(f"/api/sessions/{self.settings.waha_session}/start")
        try:
            response = await self.http.post(url, headers=self._headers(content_type=False))
            logger.info("session_start_attempt status=%s body=%s", response.status_code, response.text[:300])
        except httpx.RequestError as exc:
            logger.warning("session_start_request_error error=%s", exc)
