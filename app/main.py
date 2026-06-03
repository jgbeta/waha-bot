from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from openai import AsyncOpenAI

from app.config import Settings, load_system_prompt, validate_runtime_settings
from app.logging import setup_logging
from app.models import IncomingMessage
from app.routers.health import router as health_router
from app.routers.webhook import router as webhook_router
from app.services.allowlist import AllowlistService, normalize_phone_to_chat_id
from app.services.chatbot import ChatbotService, trim_history
from app.services.waha import WAHAClient
from app.services.worker import BotContext, new_chat_locks, worker_loop
from app.stores import MemoryStore, SQLiteStore, create_store


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="WAHA AI Bot", version="0.2.0", lifespan=lifespan)
    if settings is not None:
        app.state.initial_settings = settings
    app.include_router(health_router)
    app.include_router(webhook_router)
    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = getattr(app.state, "initial_settings", None) or Settings()
    setup_logging(settings)
    validate_runtime_settings(settings)

    app.state.settings = settings
    app.state.http = httpx.AsyncClient(timeout=settings.waha_timeout_seconds)
    app.state.openai = AsyncOpenAI(
        api_key=settings.openai_api_key or "not-used",
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
    )
    app.state.queue = asyncio.Queue(maxsize=settings.bot_queue_maxsize)
    app.state.store = create_store(settings)
    app.state.chat_locks = new_chat_locks()
    app.state.inflight = set()

    await app.state.store.init()
    if app.state.store.is_persistent:
        for job in await app.state.store.load_pending_jobs(limit=settings.bot_queue_maxsize):
            try:
                app.state.queue.put_nowait(job)
            except asyncio.QueueFull:
                break

    app.state.waha = WAHAClient(app.state.http, settings)
    app.state.chatbot = ChatbotService(app.state.openai, settings, load_system_prompt(settings))
    app.state.allowlist = AllowlistService(settings, app.state.waha)
    app.state.bot_context = BotContext(settings, app.state.store, app.state.waha, app.state.chatbot)

    worker = asyncio.create_task(worker_loop(app), name="message-worker")
    app.state.worker = worker
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        await app.state.http.aclose()
        await app.state.store.close()


app = create_app()

__all__ = [
    "app",
    "create_app",
    "Settings",
    "IncomingMessage",
    "MemoryStore",
    "SQLiteStore",
    "normalize_phone_to_chat_id",
    "trim_history",
]
