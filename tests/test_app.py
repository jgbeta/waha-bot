import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, validate_runtime_settings
from app.models import IncomingMessage
from app.routers.webhook import webhook
from app.services.allowlist import AllowlistService, allowed_chat_ids_from_settings
from app.services.worker import BotContext, handle_message
from app.stores.memory import MemoryStore


def settings(**overrides):
    values = {
        "_env_file": None,
        "waha_api_key": "test-waha-key",
        "openai_api_key": "test-openai-key",
        "bot_store": "memory",
        "bot_history_store": "memory",
        "bot_allowed_phone": "12025550123",
        "bot_send_seen": False,
        "bot_send_typing": False,
    }
    values.update(overrides)
    return Settings(**values)


def request_for(app, payload: dict):
    body = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return SimpleRequest(app, body, receive)


class SimpleRequest:
    def __init__(self, app, body, receive):
        self.app = app
        self._body = body
        self._receive = receive

    async def body(self):
        return self._body


def message_payload(message_id="msg-1", chat_id="12025550123@c.us", body="hello"):
    return {
        "id": f"evt-{message_id}",
        "event": "message",
        "session": "default",
        "payload": {
            "id": message_id,
            "from": chat_id,
            "fromMe": False,
            "body": body,
            "timestamp": 1710000000,
        },
    }


def test_runtime_validation_rejects_placeholder_openai_key():
    cfg = settings(openai_api_key="REPLACE_WITH_YOUR_OPENAI_API_KEY")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validate_runtime_settings(cfg)


def test_runtime_validation_requires_allowlist_when_enabled():
    cfg = settings(bot_allowed_phone=None, bot_allowed_phones="", bot_allowed_chat_id=None, bot_allowed_chat_ids="")
    with pytest.raises(ValueError, match="BOT_REQUIRE_ALLOWLIST"):
        validate_runtime_settings(cfg)


def test_allowed_chat_ids_combines_legacy_and_csv_values():
    cfg = settings(
        bot_allowed_phone="+57 300 000 0000",
        bot_allowed_phones="12025550100,+1 415 555 0100",
        bot_allowed_chat_id="12025550999@c.us",
        bot_allowed_chat_ids="120363000000000000@g.us",
    )
    assert allowed_chat_ids_from_settings(cfg) == {
        "12025550123@c.us",
        "12025550100@c.us",
        "14155550100@c.us",
        "12025550999@c.us",
        "120363000000000000@g.us",
    }


class FakeWAHA:
    def __init__(self, *phones):
        self.phones = list(phones)
        self.calls = []

    async def resolve_lid(self, lid):
        self.calls.append(lid)
        if self.phones:
            return self.phones.pop(0)
        return None


def test_lid_sender_is_allowed_when_waha_maps_to_allowed_phone():
    async def run():
        cfg = settings(bot_lid_cache_ttl_seconds=60)
        allowlist = AllowlistService(cfg, FakeWAHA("12025550123@c.us"))

        first = await allowlist.is_allowed_chat("62590675898548@lid")
        second = await allowlist.is_allowed_chat("62590675898548@lid")

        assert first is True
        assert second is True
        assert allowlist.waha.calls == ["62590675898548@lid"]

    asyncio.run(run())


def test_lid_sender_is_denied_when_mapping_does_not_match():
    async def run():
        allowlist = AllowlistService(settings(), FakeWAHA("9999999999@c.us"))
        assert await allowlist.is_allowed_chat("62590675898548@lid") is False

    asyncio.run(run())


def test_group_sender_is_denied_by_default():
    async def run():
        allowlist = AllowlistService(settings(bot_allowed_chat_ids="120363000000000000@g.us"), FakeWAHA())
        assert await allowlist.is_allowed_chat("120363000000000000@g.us") is False

    asyncio.run(run())


def test_webhook_accepts_allowlisted_message_and_dedupes():
    async def run():
        cfg = settings()
        store = MemoryStore(cfg)
        await store.init()
        app = SimpleNamespace(
            state=SimpleNamespace(
                settings=cfg,
                store=store,
                queue=asyncio.Queue(),
                allowlist=AllowlistService(cfg, FakeWAHA()),
            )
        )

        payload = message_payload()
        first = await webhook(request_for(app, payload))
        second = await webhook(request_for(app, payload))

        assert first == {"ok": True, "queued": True}
        assert second == {"ok": True, "duplicate": True}
        assert app.state.queue.qsize() == 1

    asyncio.run(run())


class FakeStore:
    def __init__(self):
        self.history = [{"role": "user", "content": "previous"}]
        self.logs = []
        self.saved_history = None
        self.done = []
        self.failed = []

    async def load_history(self, chat_id):
        return list(self.history)

    async def log_message(self, dt, usr, msg, model_info=""):
        self.logs.append((usr, msg, model_info))

    async def save_history(self, chat_id, messages):
        self.saved_history = list(messages)

    async def mark_done(self, message_id):
        self.done.append(message_id)

    async def mark_failed(self, message_id, error):
        self.failed.append((message_id, error))

    async def clear_history(self, chat_id):
        self.history = []


class FakeWahaForWorker:
    def __init__(self):
        self.sent = []

    async def send_seen(self, chat_id, message_id):
        return None

    async def start_typing(self, chat_id):
        return None

    async def stop_typing(self, chat_id):
        return None

    async def send_text(self, chat_id, text):
        self.sent.append((chat_id, text))
        return True


class FakeChatbot:
    def __init__(self):
        self.calls = []

    async def generate_reply(self, chat_id, history, user_text):
        self.calls.append((chat_id, list(history), user_text))
        return "reply"


def test_handle_message_uses_prior_history_once():
    async def run():
        cfg = settings(bot_history_max_messages=10, pg_contact_name="Juan")
        store = FakeStore()
        waha = FakeWahaForWorker()
        chatbot = FakeChatbot()
        app = SimpleNamespace(state=SimpleNamespace(bot_context=BotContext(cfg, store, waha, chatbot)))
        msg = IncomingMessage("evt-1", "msg-1", "12025550123@c.us", "hello", 1710000000, {})

        await handle_message(app, msg)

        assert chatbot.calls == [("12025550123@c.us", [{"role": "user", "content": "previous"}], "hello")]
        assert waha.sent == [("12025550123@c.us", "reply")]
        assert store.logs == [("Juan", "hello", ""), ("llm", "reply", cfg.openai_model)]
        assert store.saved_history == [
            {"role": "user", "content": "previous"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "reply"},
        ]
        assert store.done == ["msg-1"]
        assert store.failed == []

    asyncio.run(run())
