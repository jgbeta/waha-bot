from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    queue = getattr(request.app.state, "queue", None)
    return {
        "ok": True,
        "store": settings.bot_store,
        "history_store": settings.bot_history_store,
        "queue_size": queue.qsize() if queue is not None else None,
        "session": settings.waha_session,
        "ready_path": "/ready",
    }


@router.get("/ready")
async def ready(request: Request, response: Response) -> dict[str, Any]:
    checks = {
        "store": await request.app.state.store.check_ready(),
        "waha_ping": await request.app.state.waha.ping(),
        "waha_session": await request.app.state.waha.session_status(),
        "openai_config": {
            "ok": True,
            "model": request.app.state.settings.openai_model,
            "base_url": request.app.state.settings.openai_base_url,
        },
    }
    ok = all(check.get("ok") is True for check in checks.values())
    if not ok:
        response.status_code = 503
    return {"ok": ok, "checks": checks}
