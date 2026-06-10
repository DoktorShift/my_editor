"""Unit tests for the chat-discovery merge logic.

We test ``_merge_updates`` directly so the suite doesn't depend on
Qt's event loop or QNetworkAccessManager.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from publishers.telegram.chats import _merge_updates
from publishers.telegram.settings import TelegramSettings


@pytest.fixture
def store_with_bot(tmp_path: Path):
    store = TelegramSettings(path=tmp_path / "tg.json")
    bot = store.add_bot(
        display_name="Test",
        token="1:abc",
        telegram_username="testbot",
        telegram_first_name="Test",
        telegram_user_id=1,
    )
    return store, bot.id


def test_merge_empty_updates_is_noop(store_with_bot):
    store, bot_id = store_with_bot
    new_c, new_t, hi = _merge_updates([], store, bot_id)
    assert (new_c, new_t, hi) == (0, 0, 0)


def test_merge_message_in_group_creates_chat(store_with_bot):
    store, bot_id = store_with_bot
    updates = [{
        "update_id": 101,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "chat": {"id": -1001, "type": "supergroup", "title": "Hello"},
        },
    }]
    new_c, new_t, hi = _merge_updates(updates, store, bot_id)
    assert (new_c, new_t, hi) == (1, 0, 101)
    chat = store.bot_by_id(bot_id).chats[0]
    assert chat.id == -1001
    assert chat.title == "Hello"


def test_merge_channel_post_creates_chat(store_with_bot):
    store, bot_id = store_with_bot
    updates = [{
        "update_id": 222,
        "channel_post": {
            "message_id": 1,
            "date": 1700000100,
            "chat": {
                "id": -1002, "type": "channel",
                "title": "Updates", "username": "updates",
            },
        },
    }]
    new_c, _new_t, hi = _merge_updates(updates, store, bot_id)
    assert new_c == 1
    chat = store.bot_by_id(bot_id).chat_by_id(-1002)
    assert chat.username == "updates"
    assert hi == 222


def test_merge_my_chat_member_creates_chat(store_with_bot):
    store, bot_id = store_with_bot
    updates = [{
        "update_id": 300,
        "my_chat_member": {
            "date": 1700000200,
            "chat": {"id": -1003, "type": "group", "title": "Crew"},
        },
    }]
    new_c, _new_t, _hi = _merge_updates(updates, store, bot_id)
    assert new_c == 1


def test_merge_forum_topic_discovery(store_with_bot):
    store, bot_id = store_with_bot
    updates = [{
        "update_id": 400,
        "message": {
            "message_id": 1,
            "date": 1700000300,
            "message_thread_id": 55,
            "forum_topic_created": {"name": "Bugs"},
            "chat": {
                "id": -1004, "type": "supergroup", "title": "Forum",
                "is_forum": True,
            },
        },
    }]
    new_c, new_t, _hi = _merge_updates(updates, store, bot_id)
    assert (new_c, new_t) == (1, 1)
    chat = store.bot_by_id(bot_id).chat_by_id(-1004)
    assert chat.is_forum
    assert chat.topics[0].id == 55
    assert chat.topics[0].name == "Bugs"


def test_merge_duplicate_chat_does_not_double_count(store_with_bot):
    store, bot_id = store_with_bot
    upd = {
        "update_id": 500,
        "message": {
            "message_id": 1, "date": 1700000400,
            "chat": {"id": -1005, "type": "group", "title": "Same"},
        },
    }
    _merge_updates([upd], store, bot_id)
    new_c, _new_t, _hi = _merge_updates([upd], store, bot_id)
    assert new_c == 0


def test_merge_highest_update_id_reflected(store_with_bot):
    store, bot_id = store_with_bot
    updates = [
        {"update_id": 10, "message": {"date": 1, "chat":
            {"id": -100, "type": "group", "title": "A"}}},
        {"update_id": 7, "message": {"date": 1, "chat":
            {"id": -100, "type": "group", "title": "A"}}},
        {"update_id": 99, "message": {"date": 1, "chat":
            {"id": -100, "type": "group", "title": "A"}}},
    ]
    _new_c, _new_t, hi = _merge_updates(updates, store, bot_id)
    assert hi == 99


def test_private_chat_title_falls_back_to_name(store_with_bot):
    store, bot_id = store_with_bot
    updates = [{
        "update_id": 600,
        "message": {
            "date": 1,
            "chat": {"id": 999, "type": "private", "first_name": "Alice",
                     "last_name": "B"},
        },
    }]
    _merge_updates(updates, store, bot_id)
    chat = store.bot_by_id(bot_id).chat_by_id(999)
    assert chat is not None
    assert "Alice" in chat.title
