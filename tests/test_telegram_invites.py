"""Unit tests for Telegram invite-link construction."""

from __future__ import annotations

from publishers.telegram.invites import (
    add_to_channel_link,
    add_to_group_link,
    all_invite_links,
    dm_link,
)


def test_dm_link():
    assert dm_link("MyBot") == "https://t.me/MyBot"


def test_dm_link_strips_leading_at():
    assert dm_link("@MyBot") == "https://t.me/MyBot"


def test_add_to_group_as_member_uses_startgroup_true():
    url = add_to_group_link("MyBot", as_admin=False)
    assert url == "https://t.me/MyBot?startgroup=true"


def test_add_to_group_as_admin_uses_perms():
    url = add_to_group_link("MyBot", as_admin=True)
    assert url.startswith("https://t.me/MyBot?startgroup&admin=")
    # at least one expected flag is in the permission string
    assert "delete_messages" in url
    assert "pin_messages" in url


def test_add_to_channel_always_admin():
    poster = add_to_channel_link("MyBot", full_admin=False)
    full = add_to_channel_link("MyBot", full_admin=True)
    assert poster.startswith("https://t.me/MyBot?startchannel&admin=")
    assert full.startswith("https://t.me/MyBot?startchannel&admin=")
    assert "post_messages" in poster
    # Full admin variant has more flags than the basic poster.
    assert full.count("+") > poster.count("+")


def test_all_invite_links_returns_full_set():
    links = all_invite_links("MyBot")
    labels = [link.label for link in links]
    assert "Add to a group" in labels
    assert "Add to a group as admin" in labels
    assert "Add to a channel (poster)" in labels
    assert "Add to a channel (full admin)" in labels
    assert "Open the bot in Telegram" in labels
    for link in links:
        assert link.url.startswith("https://t.me/MyBot")
        assert link.description
        assert link.category in {"group", "channel", "dm"}


def test_dm_category_clarifies_bot_cannot_initiate():
    dm_links = [link for link in all_invite_links("MyBot") if link.category == "dm"]
    assert len(dm_links) == 1
    # The description must explain the /start requirement so users
    # do not assume the link can DM strangers.
    assert "/start" in dm_links[0].description
