# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drafts made by EINUNDZWANZIG STANDUP, read in this app.

STANDUP wraps its article drafts (imports among them) with NIP-23's
article draft kind, 30024, inside the NIP-37 wrap, and says so in the
wrap's ``k`` tag. They are articles here: the row shows their title and
summary, and opening or deleting one works like an article this app
wrote.
"""

from __future__ import annotations

import json

from nostr.draft_store import DraftStore
from nostr.drafts import (
    DRAFT_WRAP_KIND,
    INNER_KIND_LONG_FORM,
    SUPPORTED_INNER_KINDS,
    parse_inner_event,
    parse_wrap_event,
)

PK = "a" * 64
D = "rss-0123456789abcdef"


def _standup_wrap(created_at: int = 100) -> dict:
    return {"kind": DRAFT_WRAP_KIND, "id": "w" * 64, "pubkey": PK, "created_at": created_at,
            "content": "ciphertext", "tags": [["d", D], ["k", "30024"]]}


def _standup_inner() -> str:
    return json.dumps({
        "kind": 30024, "content": "# Body heading\n\nText of the post.",
        "tags": [["d", D], ["title", "Imported title"], ["summary", "Short summary"],
                 ["published_at", "1700000000"], ["source", "https://blog.example.com/feed"],
                 ["client", "EINUNDZWANZIG HUB"]],
        "created_at": 100, "pubkey": PK})


def test_a_standup_article_draft_is_an_article_row():
    store = DraftStore()
    store.bind_profile(PK)
    store.upsert_skeleton(parse_wrap_event(_standup_wrap()))
    store.set_decrypted(D, inner=parse_inner_event(_standup_inner()), source_event_id="w" * 64)
    record = store.get(D)
    assert record.is_article
    assert record.title == "Imported title"
    assert record.snippet == "Short summary"
    assert ["source", "https://blog.example.com/feed"] in record.inner_tags


def test_a_standup_article_draft_can_be_deleted():
    # Deleting builds a tombstone for the record's kind, which must be
    # one this app can write.
    meta = parse_wrap_event(_standup_wrap())
    assert meta.inner_kind == INNER_KIND_LONG_FORM
    assert meta.inner_kind in SUPPORTED_INNER_KINDS
