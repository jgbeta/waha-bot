from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import Settings
from app.logging import format_error
from app.models import IncomingMessage
from app.stores.base import StateStore


class SQLiteStore(StateStore):
    is_persistent = True

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db: aiosqlite.Connection | None = None
        self._memory_history: dict[str, list[dict[str, str]]] = defaultdict(list)

    async def init(self) -> None:
        sqlite_path = Path(self.settings.bot_sqlite_path)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(str(sqlite_path))
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA synchronous=NORMAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS inbound_jobs (
                message_id TEXT PRIMARY KEY,
                event_id TEXT,
                chat_id TEXT NOT NULL,
                body TEXT NOT NULL,
                timestamp INTEGER,
                raw_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                error TEXT,
                received_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await self.db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_inbound_jobs_event_id
            ON inbound_jobs(event_id)
            WHERE event_id IS NOT NULL AND event_id != ''
            """
        )
        await self.db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_inbound_jobs_status_received
            ON inbound_jobs(status, received_at)
            """
        )
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_history (
                chat_id TEXT PRIMARY KEY,
                messages_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        await self.db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_history_updated_at
            ON chat_history(updated_at)
            """
        )
        await self.db.execute("PRAGMA user_version = 1")
        await self.db.commit()

    async def close(self) -> None:
        if self.db is not None:
            await self.db.close()

    def _require_db(self) -> aiosqlite.Connection:
        if self.db is None:
            raise RuntimeError("SQLite store is not initialized")
        return self.db

    async def record_incoming(self, msg: IncomingMessage) -> bool:
        db = self._require_db()
        now = time.time()
        try:
            await db.execute(
                """
                INSERT INTO inbound_jobs
                    (message_id, event_id, chat_id, body, timestamp, raw_json, status, received_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    msg.message_id,
                    msg.event_id or None,
                    msg.chat_id,
                    msg.text,
                    msg.timestamp,
                    json.dumps(msg.raw, separators=(",", ":")),
                    now,
                    now,
                ),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def forget_incoming(self, msg: IncomingMessage) -> None:
        db = self._require_db()
        await db.execute("DELETE FROM inbound_jobs WHERE message_id = ?", (msg.message_id,))
        await db.commit()

    async def load_pending_jobs(self, limit: int = 20) -> list[IncomingMessage]:
        db = self._require_db()
        cursor = await db.execute(
            """
            SELECT message_id, event_id, chat_id, body, timestamp, raw_json
            FROM inbound_jobs
            WHERE status = 'queued'
            ORDER BY received_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        await cursor.close()
        jobs: list[IncomingMessage] = []
        for row in rows:
            try:
                raw = json.loads(row["raw_json"])
            except json.JSONDecodeError:
                raw = {}
            jobs.append(
                IncomingMessage(
                    event_id=row["event_id"] or "",
                    message_id=row["message_id"],
                    chat_id=row["chat_id"],
                    text=row["body"],
                    timestamp=row["timestamp"],
                    raw=raw if isinstance(raw, dict) else {},
                )
            )
        return jobs

    async def job_status(self, message_id: str) -> str | None:
        db = self._require_db()
        cursor = await db.execute("SELECT status FROM inbound_jobs WHERE message_id = ?", (message_id,))
        row = await cursor.fetchone()
        await cursor.close()
        return row["status"] if row else None

    async def mark_done(self, message_id: str) -> None:
        db = self._require_db()
        if self.settings.bot_retain_processed_message_body:
            await db.execute(
                "UPDATE inbound_jobs SET status = 'done', error = NULL, updated_at = ? WHERE message_id = ?",
                (time.time(), message_id),
            )
        else:
            await db.execute(
                """
                UPDATE inbound_jobs
                SET status = 'done', error = NULL, body = '', raw_json = '{}', updated_at = ?
                WHERE message_id = ?
                """,
                (time.time(), message_id),
            )
        await db.commit()

    async def mark_failed(self, message_id: str, error: str) -> None:
        db = self._require_db()
        await db.execute(
            "UPDATE inbound_jobs SET status = 'failed', error = ?, updated_at = ? WHERE message_id = ?",
            (error[:1000], time.time(), message_id),
        )
        await db.commit()

    async def load_history(self, chat_id: str) -> list[dict[str, str]]:
        if self.settings.bot_history_store == "none":
            return []
        if self.settings.bot_history_store == "memory":
            return list(self._memory_history.get(chat_id, []))
        db = self._require_db()
        cursor = await db.execute("SELECT messages_json FROM chat_history WHERE chat_id = ?", (chat_id,))
        row = await cursor.fetchone()
        await cursor.close()
        if not row:
            return []
        data = json.loads(row["messages_json"])
        if not isinstance(data, list):
            return []
        return [m for m in data if isinstance(m, dict) and "role" in m and "content" in m]

    async def save_history(self, chat_id: str, messages: list[dict[str, str]]) -> None:
        if self.settings.bot_history_store == "none":
            return
        if self.settings.bot_history_store == "memory":
            self._memory_history[chat_id] = list(messages)
            return
        db = self._require_db()
        await db.execute(
            """
            INSERT INTO chat_history (chat_id, messages_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                messages_json = excluded.messages_json,
                updated_at = excluded.updated_at
            """,
            (chat_id, json.dumps(messages, separators=(",", ":")), time.time()),
        )
        await db.commit()

    async def clear_history(self, chat_id: str) -> None:
        if self.settings.bot_history_store == "memory":
            self._memory_history.pop(chat_id, None)
            return
        db = self._require_db()
        await db.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
        await db.commit()

    async def log_message(self, dt: str, usr: str, msg: str, model_info: str = "") -> None:
        return None

    async def check_ready(self) -> dict[str, Any]:
        try:
            db = self._require_db()
            cursor = await db.execute("SELECT 1")
            await cursor.fetchone()
            await cursor.close()
        except Exception as exc:
            return {"ok": False, "error": format_error(exc)}
        return {"ok": True, "path": self.settings.bot_sqlite_path, "history_store": self.settings.bot_history_store}
