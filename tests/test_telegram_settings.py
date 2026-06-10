"""Unit tests for TelegramSettings persistence and bot/chat lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from publishers.telegram.settings import (
    BOT_COLORS,
    Chat,
    SCHEMA_VERSION,
    TelegramSettings,
    Topic,
)


@pytest.fixture
def store(tmp_path: Path) -> TelegramSettings:
    return TelegramSettings(path=tmp_path / "telegram.json")


def _add(store, name="ProductBot", uid=1):
    return store.add_bot(
        display_name=name,
        token=f"{uid}:tokenfor{uid}",
        telegram_username=name.lower(),
        telegram_first_name=name,
        telegram_user_id=uid,
    )


def test_empty_store_has_no_bots(store):
    assert store.bots == []
    assert store.default_bot is None


def test_add_bot_persists_and_returns_record(store):
    bot = _add(store)
    assert bot.id.startswith("b_")
    assert bot.color in BOT_COLORS
    assert store.bots == [bot]
    assert store.default_bot.id == bot.id


def test_add_two_bots_uses_distinct_colors(store):
    a = _add(store, "A", 1)
    b = _add(store, "B", 2)
    assert a.color != b.color


def test_duplicate_user_id_is_rejected(store):
    _add(store, "A", 1)
    with pytest.raises(ValueError):
        _add(store, "Different name same id", 1)


def test_remove_bot_drops_record_and_reassigns_default(store):
    a = _add(store, "A", 1)
    b = _add(store, "B", 2)
    assert store.default_bot.id == a.id
    store.remove_bot(a.id)
    assert [bot.id for bot in store.bots] == [b.id]
    assert store.default_bot.id == b.id


def test_set_default_explicitly(store):
    a = _add(store, "A", 1)
    b = _add(store, "B", 2)
    store.set_default_bot(b.id)
    assert store.default_bot.id == b.id


def test_replace_token_clears_invalid_flag(store):
    bot = _add(store, "A", 1)
    store.mark_bot_invalid(bot.id)
    assert store.bot_by_id(bot.id).invalid is True
    store.replace_token(bot.id, "1:newtoken")
    assert store.bot_by_id(bot.id).invalid is False
    assert store.bot_by_id(bot.id).token == "1:newtoken"


def test_upsert_chat_inserts_and_preserves_star_state(store):
    bot = _add(store)
    chat = Chat(id=-1001, type="channel", title="Updates", username="updates")
    store.upsert_chat(bot.id, chat)
    fresh = store.bot_by_id(bot.id).chat_by_id(-1001)
    assert fresh is not None
    fresh.starred = True
    store.set_chat_starred(bot.id, -1001, True)
    # An upsert with a partial Chat must NOT wipe star state.
    store.upsert_chat(bot.id, Chat(id=-1001, type="channel", title="Updates v2",
                                    username="updates"))
    final = store.bot_by_id(bot.id).chat_by_id(-1001)
    assert final.starred is True
    assert final.title == "Updates v2"


def test_upsert_chat_merges_topics(store):
    bot = _add(store)
    chat = Chat(id=-1002, type="supergroup", title="Forum", is_forum=True,
                topics=[Topic(id=1, name="General")])
    store.upsert_chat(bot.id, chat)
    chat_v2 = Chat(id=-1002, type="supergroup", title="Forum", is_forum=True,
                   topics=[Topic(id=2, name="Bugs")])
    store.upsert_chat(bot.id, chat_v2)
    final = store.bot_by_id(bot.id).chat_by_id(-1002)
    topic_ids = sorted(t.id for t in final.topics)
    assert topic_ids == [1, 2]


def test_set_last_update_id_only_advances_forward(store):
    bot = _add(store)
    store.set_last_update_id(bot.id, 100)
    store.set_last_update_id(bot.id, 50)
    assert store.bot_by_id(bot.id).last_update_id == 100


def test_save_round_trips_through_disk(store, tmp_path):
    bot = _add(store, "Disk", 99)
    chat = Chat(id=-1003, type="channel", title="x", username="x",
                topics=[Topic(id=10, name="hi")], is_forum=True)
    store.upsert_chat(bot.id, chat)

    # Re-open from the same path.
    fresh = TelegramSettings(path=store._path)
    assert len(fresh.bots) == 1
    rebuilt = fresh.bots[0]
    assert rebuilt.display_name == "Disk"
    assert rebuilt.telegram_user_id == 99
    assert rebuilt.chats[0].id == -1003
    assert rebuilt.chats[0].topics[0].name == "hi"


def test_atomic_save_does_not_leave_orphan_temp_files(store, tmp_path):
    _add(store, "A", 1)
    files = list(tmp_path.iterdir())
    assert any(f.name == "telegram.json" for f in files)
    assert not any(f.name.endswith(".tmp") for f in files)


def test_set_chat_starred_is_idempotent(store):
    bot = _add(store)
    store.upsert_chat(bot.id, Chat(id=-1004, type="group", title="x"))
    store.set_chat_starred(bot.id, -1004, True)
    store.set_chat_starred(bot.id, -1004, True)  # no-op write
    assert store.bot_by_id(bot.id).chat_by_id(-1004).starred is True


def test_mark_chat_sent_only_advances_forward(store):
    bot = _add(store)
    store.upsert_chat(bot.id, Chat(id=-1005, type="group", title="x"))
    store.mark_chat_sent(bot.id, -1005, 100)
    store.mark_chat_sent(bot.id, -1005, 50)
    assert store.bot_by_id(bot.id).chat_by_id(-1005).last_sent_at == 100
