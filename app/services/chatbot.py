from __future__ import annotations

import asyncio

from openai import AsyncOpenAI

from app.config import Settings


class ChatbotService:
    def __init__(self, client: AsyncOpenAI, settings: Settings, system_prompt: str) -> None:
        self.client = client
        self.settings = settings
        self.system_prompt = system_prompt

    async def generate_reply(self, chat_id: str, history: list[dict[str, str]], user_text: str) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(trim_history(history, self.settings.bot_history_max_messages))
        messages.append({"role": "user", "content": user_text})

        last_error: Exception | None = None
        for attempt in range(1, self.settings.openai_retries + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.settings.openai_model,
                    messages=messages,
                    max_tokens=self.settings.openai_max_tokens,
                )
                content = response.choices[0].message.content
                if not content:
                    raise RuntimeError("OpenAI returned an empty reply")
                return content.strip()
            except Exception as exc:
                last_error = exc
                if attempt < self.settings.openai_retries:
                    await asyncio.sleep(min(2 ** attempt, 10))
        raise RuntimeError("OpenAI request failed") from last_error


def trim_history(messages: list[dict[str, str]], max_messages: int) -> list[dict[str, str]]:
    clean = [
        {"role": item["role"], "content": item["content"]}
        for item in messages
        if item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str)
    ]
    return clean[-max_messages:]
