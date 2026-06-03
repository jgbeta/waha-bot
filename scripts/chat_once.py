from __future__ import annotations

import asyncio
import sys

from openai import AsyncOpenAI

from app.config import Settings, load_system_prompt, validate_runtime_settings
from app.services.chatbot import ChatbotService


async def run(prompt: str) -> None:
    settings = Settings(bot_require_allowlist=False)
    validate_runtime_settings(settings)
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=settings.openai_timeout_seconds,
    )
    try:
        bot = ChatbotService(client, settings, load_system_prompt(settings))
        print(await bot.generate_reply("local-debug", [], prompt))
    finally:
        await client.close()


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: python -m scripts.chat_once "message"')
        return 2
    asyncio.run(run(" ".join(sys.argv[1:])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
