"""Tests for EngagementPublisher tag construction.

We exercise the *tag layout* the publisher hands to ``build_event``
before signing. Signing + relay I/O is mocked through a fake bunker
pool and relay pool so the tests are deterministic and offline.
"""

from __future__ import annotations

from typing import List, Tuple

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from plugin_marketplace.social import (
    EngagementCache,
    EngagementPublisher,
    PluginAnchor,
)
from plugin_marketplace.social.parser import (
    NIP09_DELETION_KIND,
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    PLUGIN_LISTING_KIND,
    RATING_LABEL_NAMESPACE,
)


AUTHOR_PUBKEY = "a" * 64
USER_PUBKEY = "b" * 64
ANCHOR = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR_PUBKEY, "plugin")
# Anchor used in publish_listing tests: the author MUST match the
# active profile per NIP-09's same-author rule. Tests use this to
# exercise the success path; mismatched anchors are exercised as
# rejection paths.
SELF_ANCHOR = PluginAnchor(PLUGIN_LISTING_KIND, USER_PUBKEY, "plugin")


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cache(tmp_path):
    return EngagementCache(path=tmp_path / "engagement.sqlite")


class _FakeProfile:
    user_pubkey = USER_PUBKEY


class _FakePublishJob(QObject):
    """Mimics nostr.relay.PublishJob enough for the publisher to wire signals."""
    first_accept = Signal(str)
    all_done = Signal(list)


class _FakeRelayPool:
    def __init__(self):
        self.publishes: List[Tuple[List[str], dict]] = []

    def publish(self, urls, event):
        self.publishes.append((list(urls), event))
        return _FakePublishJob()


class _FakeBunkerClient:
    def __init__(self):
        self.signed: List[dict] = []

    def sign_event(self, unsigned, *, on_success, on_failure):
        # Pretend the bunker added a signature + id without changing tags.
        signed = dict(unsigned)
        signed["id"] = "0" * 64
        signed["sig"] = "0" * 128
        self.signed.append(signed)
        on_success(signed)


class _FakeBunkerPool:
    def __init__(self):
        self.client = _FakeBunkerClient()

    def get(self, profile, on_success, on_failure):
        on_success(self.client)


@pytest.fixture
def publisher(qapp, cache):
    pool = _FakeRelayPool()
    bunker = _FakeBunkerPool()
    pub = EngagementPublisher(
        relay_pool=pool, bunker_pool=bunker, cache=cache,
    )
    # Surface internals so tests can inspect the signed event.
    pub._test_relay_pool = pool      # type: ignore[attr-defined]
    pub._test_bunker_pool = bunker   # type: ignore[attr-defined]
    return pub


# ──────────────────────────────────────────────────────────────────────
# rate()

def test_rate_event_carries_nip32_namespace_and_label(publisher):
    publisher.rate(profile=_FakeProfile(), anchor=ANCHOR, stars=4, content="ok")
    signed = publisher._test_bunker_pool.client.signed[0]
    assert signed["kind"] == NIP32_LABEL_KIND
    tags = signed["tags"]
    # L declares the namespace; l carries the value AND the namespace.
    assert ["L", RATING_LABEL_NAMESPACE] in tags
    assert any(
        t[0] == "l" and t[1] == "4" and t[2] == RATING_LABEL_NAMESPACE
        for t in tags
    )
    # The plugin anchor and its author both end up on the event.
    assert any(t[0] == "a" and t[1] == ANCHOR.coord for t in tags)
    assert any(t[0] == "p" and t[1] == AUTHOR_PUBKEY for t in tags)


def test_rate_rejects_out_of_range_stars(publisher):
    with pytest.raises(ValueError):
        publisher.rate(profile=_FakeProfile(), anchor=ANCHOR, stars=0)
    with pytest.raises(ValueError):
        publisher.rate(profile=_FakeProfile(), anchor=ANCHOR, stars=6)


# ──────────────────────────────────────────────────────────────────────
# comment()

def test_comment_top_level_emits_uppercase_root_and_lowercase_parent(publisher):
    publisher.comment(profile=_FakeProfile(), anchor=ANCHOR, body="great!")
    signed = publisher._test_bunker_pool.client.signed[0]
    tags = signed["tags"]
    # Uppercase root scope.
    assert any(t[0] == "A" and t[1] == ANCHOR.coord for t in tags)
    assert any(t[0] == "K" and t[1] == str(PLUGIN_LISTING_KIND) for t in tags)
    assert any(t[0] == "P" and t[1] == AUTHOR_PUBKEY for t in tags)
    # Lowercase parent mirrors root for top-level comments.
    assert any(t[0] == "a" and t[1] == ANCHOR.coord for t in tags)
    assert any(t[0] == "k" and t[1] == str(PLUGIN_LISTING_KIND) for t in tags)
    assert any(t[0] == "p" and t[1] == AUTHOR_PUBKEY for t in tags)


def test_comment_reply_uses_lowercase_e_pointing_at_parent_comment(publisher):
    parent_id = "1" * 64
    parent_author = "2" * 64
    publisher.comment(
        profile=_FakeProfile(), anchor=ANCHOR, body="thanks!",
        parent_event_id=parent_id, parent_author_pubkey=parent_author,
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    tags = signed["tags"]
    # Root scope still names the plugin.
    assert any(t[0] == "A" and t[1] == ANCHOR.coord for t in tags)
    # Parent scope points at the parent comment, kind 1111.
    assert any(
        t[0] == "e" and t[1] == parent_id and (len(t) < 4 or t[3] == parent_author)
        for t in tags
    )
    assert any(t[0] == "k" and t[1] == str(NIP22_COMMENT_KIND) for t in tags)
    assert any(t[0] == "p" and t[1] == parent_author for t in tags)


def test_comment_strips_blank_body(publisher):
    with pytest.raises(ValueError):
        publisher.comment(profile=_FakeProfile(), anchor=ANCHOR, body="   ")


def test_comment_kind_is_stringified_in_K_and_k_tags(publisher):
    """Relays index ``#K`` and ``#k`` as strings; integer tag values
    silently get dropped on many implementations."""
    publisher.comment(profile=_FakeProfile(), anchor=ANCHOR, body="hi")
    signed = publisher._test_bunker_pool.client.signed[0]
    for tag in signed["tags"]:
        if not tag:
            continue
        if tag[0] in ("K", "k"):
            assert isinstance(tag[1], str)


# ──────────────────────────────────────────────────────────────────────
# delete()

def test_delete_emits_e_tag_when_given_event_id(publisher):
    publisher.delete(
        profile=_FakeProfile(), anchor=ANCHOR,
        event_id="3" * 64, target_kind=NIP22_COMMENT_KIND,
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    assert signed["kind"] == NIP09_DELETION_KIND
    tags = signed["tags"]
    assert ["e", "3" * 64] in tags
    assert ["k", str(NIP22_COMMENT_KIND)] in tags


def test_delete_emits_a_tag_for_addressable_target(publisher):
    publisher.delete(
        profile=_FakeProfile(), anchor=ANCHOR,
        target_coord=ANCHOR.coord, target_kind=PLUGIN_LISTING_KIND,
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    tags = signed["tags"]
    assert ["a", ANCHOR.coord] in tags
    assert ["k", str(PLUGIN_LISTING_KIND)] in tags


def test_delete_emits_both_tags_when_caller_supplies_both(publisher):
    publisher.delete(
        profile=_FakeProfile(), anchor=ANCHOR,
        event_id="3" * 64,
        target_coord=ANCHOR.coord,
        target_kind=PLUGIN_LISTING_KIND,
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    tags = signed["tags"]
    assert ["e", "3" * 64] in tags
    assert ["a", ANCHOR.coord] in tags


def test_delete_requires_at_least_one_target(publisher):
    with pytest.raises(ValueError):
        publisher.delete(profile=_FakeProfile(), anchor=ANCHOR)


def test_delete_advisory_k_tag_uses_supplied_kind(publisher):
    """The audit found delete() was hard-coding ``["k","1985"]`` for
    every deletion. Make sure the target_kind is honored end-to-end."""
    publisher.delete(
        profile=_FakeProfile(), anchor=ANCHOR,
        event_id="3" * 64, target_kind=NIP22_COMMENT_KIND,
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    k_tags = [t for t in signed["tags"] if t and t[0] == "k"]
    assert k_tags == [["k", str(NIP22_COMMENT_KIND)]]


# ──────────────────────────────────────────────────────────────────────
# publish_listing (kind:30700 author tools)

def test_publish_listing_emits_d_tag_with_plugin_id(publisher):
    publisher.publish_listing(
        profile=_FakeProfile(), anchor=SELF_ANCHOR,
        name="Hello", version="1.0.0",
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    assert signed["kind"] == PLUGIN_LISTING_KIND
    assert ["d", ANCHOR.plugin_id] in signed["tags"]
    assert ["name", "Hello"] in signed["tags"]
    assert ["version", "1.0.0"] in signed["tags"]


def test_publish_listing_includes_download_when_url_and_sha_provided(publisher):
    publisher.publish_listing(
        profile=_FakeProfile(), anchor=SELF_ANCHOR,
        name="Hello", version="1.0.0",
        download_url="https://example.com/pkg.zip",
        sha256="ab" * 32,
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    download_tags = [t for t in signed["tags"] if t and t[0] == "download"]
    assert download_tags == [["download", "https://example.com/pkg.zip", "ab" * 32]]


def test_publish_listing_includes_topics_and_screenshots(publisher):
    publisher.publish_listing(
        profile=_FakeProfile(), anchor=SELF_ANCHOR,
        name="Hello", version="1.0.0",
        topics=["editor", "fun"],
        screenshots=["https://example.com/s1.png"],
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    assert ["t", "editor"] in signed["tags"]
    assert ["t", "fun"] in signed["tags"]
    assert ["image", "https://example.com/s1.png"] in signed["tags"]


def test_publish_listing_rejects_wrong_anchor_kind(publisher):
    bad_anchor = PluginAnchor(30023, USER_PUBKEY, "plugin")
    with pytest.raises(ValueError):
        publisher.publish_listing(
            profile=_FakeProfile(), anchor=bad_anchor, name="Hello",
        )


def test_publish_listing_rejects_empty_name(publisher):
    with pytest.raises(ValueError):
        publisher.publish_listing(
            profile=_FakeProfile(), anchor=SELF_ANCHOR, name="   ",
        )


def test_publish_listing_rejects_mismatched_author(publisher):
    """An anchor whose author isn't the active profile's pubkey can't
    be published. Relays would reject the signature anyway, but the
    publisher surfaces the mistake before prompting the signer."""
    foreign = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR_PUBKEY, "plugin")
    with pytest.raises(ValueError) as excinfo:
        publisher.publish_listing(
            profile=_FakeProfile(), anchor=foreign, name="Hello",
        )
    assert "active profile" in str(excinfo.value)


def test_publish_listing_rejects_plugin_id_with_colon(publisher):
    bad_anchor = PluginAnchor(PLUGIN_LISTING_KIND, USER_PUBKEY, "with:colon")
    with pytest.raises(ValueError):
        publisher.publish_listing(
            profile=_FakeProfile(), anchor=bad_anchor, name="Hello",
        )


def test_delete_listing_emits_a_tag_and_k_tag(publisher):
    publisher.delete_listing(
        profile=_FakeProfile(), anchor=SELF_ANCHOR, reason="taking it offline",
    )
    signed = publisher._test_bunker_pool.client.signed[0]
    assert signed["kind"] == NIP09_DELETION_KIND
    assert ["a", SELF_ANCHOR.coord] in signed["tags"]
    assert ["k", str(PLUGIN_LISTING_KIND)] in signed["tags"]
    assert signed["content"] == "taking it offline"
