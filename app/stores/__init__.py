from __future__ import annotations

from app.config import Settings
from app.stores.base import StateStore
from app.stores.memory import MemoryStore
from app.stores.sqlite import SQLiteStore


def create_store(settings: Settings) -> StateStore:
    if settings.bot_store == "sqlite":
        return SQLiteStore(settings)
    if settings.bot_store == "postgres":
        from app.stores.postgres import PostgreSQLStore

        return PostgreSQLStore(settings)
    return MemoryStore(settings)


__all__ = ["StateStore", "MemoryStore", "SQLiteStore", "create_store"]
