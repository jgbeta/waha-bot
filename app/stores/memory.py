from __future__ import annotations

from collections import defaultdict

from cachetools import TTLCache

from app.config import Settings
from app.models import IncomingMessage
from app.stores.base import StateStore


class MemoryStore(StateStore):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._seen: TTLCache[str, bool] = TTLCache(maxsize=10000, ttl=settings.bot_dedupe_ttl_seconds)
        self._history: dict[str, list[dict[str, str]]] = defaultdict(list)

    async def init(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def record_incoming(self, msg: IncomingMessage) -> bool:
        keys = [key for key in (msg.message_id, msg.event_id) if key]
        if any(key in self._seen for key in keys):
            return False
        for key in keys:
            self._seen[key] = True
        return True

    async def forget_incoming(self, msg: IncomingMessage) -> None:
        for key in (msg.message_id, msg.event_id):
            if key:
                self._seen.pop(key, None)

    async def load_pending_jobs(self, limit: int = 20) -> list[IncomingMessage]:
        return []

    async def job_status(self, message_id: str) -> str | None:
        return None

    async def mark_done(self, message_id: str) -> None:
        return None

    async def mark_failed(self, message_id: str, error: str) -> None:
        return None

    async def load_history(self, chat_id: str) -> list[dict[str, str]]:
        if self.settings.bot_history_store != "memory":
            return []
        return list(self._history.get(chat_id, []))

    async def save_history(self, chat_id: str, messages: list[dict[str, str]]) -> None:
        if self.settings.bot_history_store == "memory":
            self._history[chat_id] = list(messages)

    async def clear_history(self, chat_id: str) -> None:
        self._history.pop(chat_id, None)

    async def log_message(self, dt: str, usr: str, msg: str, model_info: str = "") -> None:
        return None
