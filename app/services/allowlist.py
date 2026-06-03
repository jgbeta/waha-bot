from __future__ import annotations

import re

from cachetools import TTLCache

from app.config import Settings, split_csv
from app.services.waha import WAHAClient


def normalize_phone_to_chat_id(value: str) -> str:
    cleaned = value.strip()
    if "@" in cleaned:
        return cleaned
    digits = re.sub(r"\D+", "", cleaned)
    return f"{digits}@c.us" if digits else ""


def normalize_chat_id(value: str) -> str:
    return value.strip()


def allowed_chat_ids_from_settings(settings: Settings) -> set[str]:
    values: list[str] = []
    values.extend(normalize_chat_id(value) for value in split_csv(settings.bot_allowed_chat_ids))
    if settings.bot_allowed_chat_id:
        values.append(normalize_chat_id(settings.bot_allowed_chat_id))
    values.extend(normalize_phone_to_chat_id(value) for value in split_csv(settings.bot_allowed_phones))
    if settings.bot_allowed_phone:
        values.append(normalize_phone_to_chat_id(settings.bot_allowed_phone))
    return {value for value in values if value}


class AllowlistService:
    def __init__(self, settings: Settings, waha: WAHAClient) -> None:
        self.settings = settings
        self.waha = waha
        self._lid_cache: TTLCache[str, str] = TTLCache(maxsize=1000, ttl=settings.bot_lid_cache_ttl_seconds)

    async def is_allowed_chat(self, chat_id: str) -> bool:
        if chat_id.endswith("@g.us") and not self.settings.bot_allow_groups:
            return False

        allowed = allowed_chat_ids_from_settings(self.settings)
        if not allowed and not self.settings.bot_require_allowlist:
            return True
        if chat_id in allowed:
            return True
        if chat_id.endswith("@lid"):
            mapped = await self.resolve_lid_phone(chat_id)
            if mapped:
                return normalize_phone_to_chat_id(mapped) in allowed
        return False

    async def resolve_lid_phone(self, lid: str) -> str | None:
        cached = self._lid_cache.get(lid)
        if cached:
            return cached
        mapped = await self.waha.resolve_lid(lid)
        if mapped:
            self._lid_cache[lid] = mapped
        return mapped
