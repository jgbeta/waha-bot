from __future__ import annotations

from typing import TYPE_CHECKING

from app.models import IncomingMessage

if TYPE_CHECKING:
    from app.services.worker import BotContext


async def handle_command(ctx: "BotContext", msg: IncomingMessage) -> str | None:
    if msg.text.casefold() == ctx.settings.bot_clear_command.casefold():
        await ctx.store.clear_history(msg.chat_id)
        return ctx.settings.bot_clear_reply
    return None
