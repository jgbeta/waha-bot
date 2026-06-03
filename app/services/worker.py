from __future__ import annotations

import asyncio
import datetime
import logging
from collections import defaultdict
from dataclasses import dataclass

from fastapi import FastAPI

from app.config import Settings
from app.models import IncomingMessage
from app.services.chatbot import ChatbotService, trim_history
from app.services.handlers import handle_command
from app.services.waha import WAHAClient
from app.stores.base import StateStore

logger = logging.getLogger("waha_ai_bot")


def now_iso() -> str:
    return str(datetime.datetime.now().isoformat(sep=" ", timespec="seconds"))


@dataclass
class BotContext:
    settings: Settings
    store: StateStore
    waha: WAHAClient
    chatbot: ChatbotService


async def worker_loop(app: FastAPI) -> None:
    queue: asyncio.Queue[IncomingMessage] = app.state.queue
    store: StateStore = app.state.store
    while True:
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            if store.is_persistent:
                await process_persistent_backlog(app)
            continue

        try:
            await process_one(app, msg)
        except Exception:
            logger.exception("unhandled_worker_error message_id=%s", msg.message_id)
            await store.mark_failed(msg.message_id, "unhandled worker error")
        finally:
            queue.task_done()


async def process_persistent_backlog(app: FastAPI) -> None:
    store: StateStore = app.state.store
    inflight: set[str] = app.state.inflight
    for msg in await store.load_pending_jobs(limit=10):
        if msg.message_id in inflight:
            continue
        await process_one(app, msg)


async def process_one(app: FastAPI, msg: IncomingMessage) -> None:
    store: StateStore = app.state.store
    inflight: set[str] = app.state.inflight
    if msg.message_id in inflight:
        return

    status = await store.job_status(msg.message_id)
    if status in {"done", "failed"}:
        return

    inflight.add(msg.message_id)
    try:
        lock = app.state.chat_locks[msg.chat_id]
        async with lock:
            await handle_message(app, msg)
    finally:
        inflight.discard(msg.message_id)


async def handle_message(app: FastAPI, msg: IncomingMessage) -> None:
    ctx: BotContext = app.state.bot_context
    settings = ctx.settings

    if settings.bot_send_seen:
        await ctx.waha.send_seen(msg.chat_id, msg.message_id)

    command_reply = await handle_command(ctx, msg)
    if command_reply is not None:
        sent = await ctx.waha.send_text(msg.chat_id, command_reply)
        if sent:
            await ctx.store.mark_done(msg.message_id)
        else:
            await ctx.store.mark_failed(msg.message_id, "failed to send command reply")
        return

    if not settings.bot_autoreply_enabled:
        logger.info("autoreply_disabled message_id=%s chat_id=%s", msg.message_id, msg.chat_id)
        await ctx.store.mark_done(msg.message_id)
        return

    if settings.bot_send_typing:
        await ctx.waha.start_typing(msg.chat_id)

    history = await ctx.store.load_history(msg.chat_id)
    await ctx.store.log_message(now_iso(), settings.pg_contact_name, msg.text, "")

    try:
        reply = await ctx.chatbot.generate_reply(msg.chat_id, history, msg.text)
    except Exception:
        logger.exception("openai_failed chat_id=%s message_id=%s", msg.chat_id, msg.message_id)
        reply = settings.bot_error_reply
    finally:
        if settings.bot_send_typing:
            await ctx.waha.stop_typing(msg.chat_id)

    if settings.bot_dry_run:
        logger.info("dry_run_reply chat_id=%s message_id=%s reply=%s", msg.chat_id, msg.message_id, reply[:500])
        await ctx.store.mark_done(msg.message_id)
        return

    sent = await ctx.waha.send_text(msg.chat_id, reply)
    if not sent:
        await ctx.store.mark_failed(msg.message_id, "failed to send WAHA reply")
        return

    await ctx.store.log_message(now_iso(), "llm", reply, settings.openai_model)

    history = await ctx.store.load_history(msg.chat_id)
    history.extend([
        {"role": "user", "content": msg.text},
        {"role": "assistant", "content": reply},
    ])
    history = trim_history(history, settings.bot_history_max_messages)
    await ctx.store.save_history(msg.chat_id, history)
    await ctx.store.mark_done(msg.message_id)


def new_chat_locks() -> defaultdict[str, asyncio.Lock]:
    return defaultdict(asyncio.Lock)
