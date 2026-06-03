from __future__ import annotations

import json
import re
import time
from typing import Any

from app.config import Settings
from app.logging import format_error
from app.models import IncomingMessage
from app.stores.base import StateStore


PG_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def quote_pg_identifier(value: str) -> str:
    if not PG_IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(
            "PostgreSQL table names must be simple identifiers, optionally schema-qualified "
            "(for example lang_chat or public.lang_chat)."
        )
    return ".".join(f'"{part}"' for part in value.split("."))


def split_pg_table_name(value: str) -> tuple[str | None, str]:
    parts = value.split(".")
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], parts[1]


def require_psycopg():
    try:
        import psycopg
    except ModuleNotFoundError as exc:
        raise RuntimeError("PostgreSQL support requires psycopg. In Docker, set BOT_STORE=postgres and rebuild with docker compose up -d --build. For local Python, install requirements-postgres.txt.") from exc
    return psycopg


class PostgreSQLStore(StateStore):
    is_persistent = True
    _chat_columns = {"dt", "usr", "msg", "type", "model_info"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._table_identifier = quote_pg_identifier(settings.pg_table)
        self._table_schema, self._table_name = split_pg_table_name(settings.pg_table)
        self._conn_kwargs: dict[str, Any] = {
            "host": settings.pg_host,
            "port": settings.pg_port,
            "user": settings.pg_user,
            "password": settings.pg_password,
            "dbname": settings.pg_dbname,
        }

    async def init(self) -> None:
        psycopg = require_psycopg()
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS waha_inbound_jobs (
                    message_id TEXT PRIMARY KEY,
                    event_id TEXT,
                    chat_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    timestamp BIGINT,
                    raw_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    error TEXT,
                    received_at DOUBLE PRECISION NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL
                )
            """)
            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_waha_inbound_event_id
                ON waha_inbound_jobs (event_id)
                WHERE event_id IS NOT NULL AND event_id != ''
            """)
            await self._ensure_chat_table(conn)

    async def _ensure_chat_table(self, conn: Any) -> None:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table_identifier} (
                dt TEXT NOT NULL,
                usr TEXT NOT NULL,
                msg TEXT NOT NULL,
                type TEXT NOT NULL,
                model_info TEXT NOT NULL
            )
            """
        )
        cur = await conn.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = COALESCE(%s, current_schema())
              AND table_name = %s
            """,
            (self._table_schema, self._table_name),
        )
        columns = {row[0] for row in await cur.fetchall()}
        missing = self._chat_columns - columns
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise RuntimeError(f"PostgreSQL table {self.settings.pg_table!r} is missing columns: {missing_list}")

    async def close(self) -> None:
        pass

    async def record_incoming(self, msg: IncomingMessage) -> bool:
        psycopg = require_psycopg()
        try:
            async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
                await conn.execute(
                    """
                    INSERT INTO waha_inbound_jobs
                        (message_id, event_id, chat_id, body, timestamp, raw_json, status, received_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'queued', %s, %s)
                    """,
                    (
                        msg.message_id,
                        msg.event_id or None,
                        msg.chat_id,
                        msg.text,
                        msg.timestamp,
                        json.dumps(msg.raw, separators=(",", ":")),
                        time.time(),
                        time.time(),
                    ),
                )
            return True
        except psycopg.errors.UniqueViolation:
            return False

    async def forget_incoming(self, msg: IncomingMessage) -> None:
        psycopg = require_psycopg()
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            await conn.execute("DELETE FROM waha_inbound_jobs WHERE message_id = %s", (msg.message_id,))

    async def load_pending_jobs(self, limit: int = 20) -> list[IncomingMessage]:
        psycopg = require_psycopg()
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            cur = await conn.execute(
                """
                SELECT message_id, event_id, chat_id, body, timestamp, raw_json
                FROM waha_inbound_jobs
                WHERE status = 'queued'
                ORDER BY received_at ASC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cur.fetchall()
        return [
            IncomingMessage(row[1] or "", row[0], row[2], row[3], row[4], json.loads(row[5]))
            for row in rows
        ]

    async def job_status(self, message_id: str) -> str | None:
        psycopg = require_psycopg()
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            cur = await conn.execute("SELECT status FROM waha_inbound_jobs WHERE message_id = %s", (message_id,))
            row = await cur.fetchone()
        return row[0] if row else None

    async def mark_done(self, message_id: str) -> None:
        psycopg = require_psycopg()
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            if self.settings.bot_retain_processed_message_body:
                await conn.execute(
                    "UPDATE waha_inbound_jobs SET status = 'done', error = NULL, updated_at = %s WHERE message_id = %s",
                    (time.time(), message_id),
                )
            else:
                await conn.execute(
                    "UPDATE waha_inbound_jobs SET status = 'done', error = NULL, body = '', raw_json = '{}', updated_at = %s WHERE message_id = %s",
                    (time.time(), message_id),
                )

    async def mark_failed(self, message_id: str, error: str) -> None:
        psycopg = require_psycopg()
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            await conn.execute(
                "UPDATE waha_inbound_jobs SET status = 'failed', error = %s, updated_at = %s WHERE message_id = %s",
                (error[:1000], time.time(), message_id),
            )

    async def load_history(self, chat_id: str) -> list[dict[str, str]]:
        if self.settings.bot_history_store != "postgres":
            return []
        psycopg = require_psycopg()
        limit = self.settings.bot_history_max_messages * 2
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            cur = await conn.execute(
                f"SELECT usr, msg FROM {self._table_identifier} WHERE type = 'conversation' ORDER BY dt DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        return [
            {"role": "assistant" if usr == "llm" else "user", "content": msg}
            for usr, msg in reversed(rows)
        ]

    async def save_history(self, chat_id: str, messages: list[dict[str, str]]) -> None:
        pass

    async def clear_history(self, chat_id: str) -> None:
        if self.settings.bot_history_store != "postgres":
            return
        psycopg = require_psycopg()
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            await conn.execute(f"DELETE FROM {self._table_identifier} WHERE type = 'conversation'")

    async def log_message(self, dt: str, usr: str, msg: str, model_info: str = "") -> None:
        if self.settings.bot_history_store != "postgres":
            return
        psycopg = require_psycopg()
        async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
            await conn.execute(
                f"INSERT INTO {self._table_identifier} (dt, usr, msg, type, model_info) VALUES (%s, %s, %s, 'conversation', %s)",
                (dt, usr, msg, model_info),
            )

    async def check_ready(self) -> dict[str, Any]:
        try:
            psycopg = require_psycopg()
            async with await psycopg.AsyncConnection.connect(autocommit=True, **self._conn_kwargs) as conn:
                await conn.execute("SELECT 1")
                await self._ensure_chat_table(conn)
        except Exception as exc:
            return {"ok": False, "error": format_error(exc)}
        return {"ok": True, "table": self.settings.pg_table}
