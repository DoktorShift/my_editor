"""Unit tests for Telegram permalink construction."""

from __future__ import annotations

from publishers.telegram.permalinks import is_public, permalink_for
from publishers.telegram.settings import Chat


def _chat(**kwargs):
    base = dict(id=0, type="", title="", username="")
    base.update(kwargs)
    return Chat(**base)


def test_public_channel_permalink_uses_username():
    chat = _chat(id=-1001234567890, type="channel", username="mychannel")
    assert permalink_for(chat, 42) == "https://t.me/mychannel/42"


def test_public_supergroup_permalink_uses_username():
    chat = _chat(id=-1009999999999, type="supergroup", username="bug_hunters")
    assert permalink_for(chat, 99) == "https://t.me/bug_hunters/99"


def test_private_supergroup_permalink_strips_100():
    chat = _chat(id=-1001234567890, type="supergroup", username="")
    assert permalink_for(chat, 7) == "https://t.me/c/1234567890/7"


def test_private_channel_permalink_strips_100():
    chat = _chat(id=-1002222333344, type="channel", username="")
    assert permalink_for(chat, 13) == "https://t.me/c/2222333344/13"


def test_legacy_basic_group_returns_none():
    # Legacy groups have negative ids that DO NOT start with -100.
    chat = _chat(id=-9999, type="group", username="")
    assert permalink_for(chat, 1) is None


def test_dm_returns_none():
    chat = _chat(id=12345, type="private", username="")
    assert permalink_for(chat, 1) is None


def test_zero_message_id_returns_none():
    chat = _chat(id=-1001234567890, type="channel", username="x")
    assert permalink_for(chat, 0) is None


def test_username_at_prefix_is_stripped():
    chat = _chat(id=-1001234567890, type="channel", username="@withat")
    assert permalink_for(chat, 5) == "https://t.me/withat/5"


def test_is_public_matches_username_presence():
    assert is_public(_chat(username="x")) is True
    assert is_public(_chat(username="")) is False
