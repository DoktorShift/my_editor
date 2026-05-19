"""Parsers for NIP-22 / NIP-32 / NIP-57 / NIP-09 / NIP-02 / NIP-51 events.

The contract every parser shares: return ``None`` on anything that
doesn't match the spec, never raise. These tests pin down the
malformed-input cases as carefully as the happy paths because a
relay is part of the trust boundary.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from plugin_marketplace.social import (
    PluginAnchor,
    RATING_LABEL_NAMESPACE,
    PLUGIN_LISTING_KIND,
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    NIP57_ZAP_RECEIPT_KIND,
    NIP09_DELETION_KIND,
    parse_comment,
    parse_contact_list,
    parse_deletion,
    parse_mute_list,
    parse_rating,
    parse_zap_receipt,
)
from plugin_marketplace.social.parser import NIP57_ZAP_REQUEST_KIND
from tests._bolt11_builder import build_test_bolt11


# Pubkeys + ids used across the file.
AUTHOR_PUBKEY = "a" * 64
RATER_PUBKEY = "b" * 64
ZAPPER_PUBKEY = "c" * 64
PLUGIN_ID = "hello_world"

ANCHOR = PluginAnchor(
    kind=PLUGIN_LISTING_KIND,
    author_pubkey=AUTHOR_PUBKEY,
    plugin_id=PLUGIN_ID,
)
ANCHOR_COORD = ANCHOR.coord


def _hex_id(prefix: str) -> str:
    """Cheap synthetic event id. Real ids are sha256 hex; the parser
    only requires 64 lowercase hex chars."""
    return (prefix * 64)[:64]


# ──────────────────────────────────────────────────────────────────────
# Anchor parsing
# ──────────────────────────────────────────────────────────────────────

def test_anchor_round_trips():
    assert PluginAnchor.parse(ANCHOR_COORD) == ANCHOR


@pytest.mark.parametrize("coord", [
    "",
    ":a:b",
    "abc:def:ghi",  # kind not int
    "30700:short:hello",  # pubkey wrong length
    f"30700:{AUTHOR_PUBKEY[:-1]}X:hello",  # non-hex pubkey
    f"30700:{AUTHOR_PUBKEY}:",  # empty plugin id
])
def test_anchor_rejects_garbage(coord):
    assert PluginAnchor.parse(coord) is None


# ──────────────────────────────────────────────────────────────────────
# NIP-32 ratings
# ──────────────────────────────────────────────────────────────────────

def _rating_event(stars: int, *, override=None) -> dict:
    event = {
        "id": _hex_id("1"),
        "kind": NIP32_LABEL_KIND,
        "pubkey": RATER_PUBKEY,
        "created_at": 1_700_000_000,
        "tags": [
            ["L", RATING_LABEL_NAMESPACE],
            ["l", str(stars), RATING_LABEL_NAMESPACE],
            ["a", ANCHOR_COORD, "wss://relay.example"],
            ["p", AUTHOR_PUBKEY, "wss://relay.example"],
        ],
        "content": "",
    }
    if override:
        event.update(override)
    return event


def test_rating_happy_path():
    event = _rating_event(5)
    rating = parse_rating(event, ANCHOR)
    assert rating is not None
    assert rating.stars.value == 5
    assert rating.anchor == ANCHOR
    assert rating.author_pubkey == RATER_PUBKEY


def test_rating_rejects_wrong_kind():
    event = _rating_event(5, override={"kind": 1})
    assert parse_rating(event, ANCHOR) is None


def test_rating_rejects_wrong_namespace():
    event = _rating_event(5)
    event["tags"][0] = ["L", "some.other.namespace"]
    assert parse_rating(event, ANCHOR) is None


def test_rating_rejects_missing_a_tag():
    event = _rating_event(5)
    event["tags"] = [t for t in event["tags"] if t[0] != "a"]
    assert parse_rating(event, ANCHOR) is None


def test_rating_rejects_anchor_for_different_plugin():
    other_anchor = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR_PUBKEY, "other_plugin")
    event = _rating_event(5)
    # Reset the ``a`` tag to point at a different plugin.
    event["tags"] = [
        t if t[0] != "a" else ["a", other_anchor.coord, ""]
        for t in event["tags"]
    ]
    assert parse_rating(event, ANCHOR) is None


@pytest.mark.parametrize("bad_label", ["0", "6", "abc", "-1", "1.5"])
def test_rating_rejects_out_of_range(bad_label):
    event = _rating_event(5)
    event["tags"][1] = ["l", bad_label, RATING_LABEL_NAMESPACE]
    assert parse_rating(event, ANCHOR) is None


def test_rating_with_review_text_preserves_content():
    event = _rating_event(4)
    event["content"] = "Solid plugin"
    rating = parse_rating(event, ANCHOR)
    assert rating is not None and rating.content == "Solid plugin"


# ──────────────────────────────────────────────────────────────────────
# NIP-22 comments
# ──────────────────────────────────────────────────────────────────────

def _comment_event(*, override=None, reply_parent=None) -> dict:
    """Build a top-level comment by default; supply ``reply_parent``
    to convert it into a reply on a given event id + author."""
    event = {
        "id": _hex_id("2"),
        "kind": NIP22_COMMENT_KIND,
        "pubkey": RATER_PUBKEY,
        "created_at": 1_700_000_100,
        "tags": [
            ["A", ANCHOR_COORD, "wss://relay.example"],
            ["K", str(PLUGIN_LISTING_KIND)],
            ["P", AUTHOR_PUBKEY, "wss://relay.example"],
            ["a", ANCHOR_COORD, "wss://relay.example"],
            ["k", str(PLUGIN_LISTING_KIND)],
            ["p", AUTHOR_PUBKEY, "wss://relay.example"],
        ],
        "content": "Great plugin.",
    }
    if reply_parent is not None:
        parent_id, parent_author = reply_parent
        event["tags"] = [
            t for t in event["tags"] if t[0] not in ("a", "k", "p")
        ]
        event["tags"].extend([
            ["e", parent_id, "wss://relay.example", parent_author],
            ["k", str(NIP22_COMMENT_KIND)],
            ["p", parent_author, "wss://relay.example"],
        ])
    if override:
        event.update(override)
    return event


def test_top_level_comment_happy_path():
    comment = parse_comment(_comment_event(), ANCHOR)
    assert comment is not None
    assert comment.parent_event_id is None
    assert comment.anchor == ANCHOR


def test_comment_rejects_wrong_root_kind():
    event = _comment_event()
    event["tags"] = [t if t[0] != "K" else ["K", "1"] for t in event["tags"]]
    assert parse_comment(event, ANCHOR) is None


def test_comment_rejects_wrong_root_anchor():
    other_anchor = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR_PUBKEY, "other")
    event = _comment_event()
    event["tags"] = [
        t if t[0] != "A" else ["A", other_anchor.coord, ""]
        for t in event["tags"]
    ]
    assert parse_comment(event, ANCHOR) is None


def test_comment_rejects_empty_content():
    event = _comment_event(override={"content": "   \n  "})
    assert parse_comment(event, ANCHOR) is None


def test_reply_comment_carries_parent_event_id():
    parent_id = _hex_id("3")
    parent_author = AUTHOR_PUBKEY
    event = _comment_event(reply_parent=(parent_id, parent_author))
    reply = parse_comment(event, ANCHOR)
    assert reply is not None
    assert reply.parent_event_id == parent_id
    assert reply.parent_author_pubkey == parent_author


def test_reply_requires_lowercase_k_eq_1111():
    parent_id = _hex_id("3")
    event = _comment_event(reply_parent=(parent_id, AUTHOR_PUBKEY))
    # Corrupt the lowercase k tag.
    event["tags"] = [
        t if t[0] != "k" else ["k", "1"]
        for t in event["tags"]
    ]
    assert parse_comment(event, ANCHOR) is None


# ──────────────────────────────────────────────────────────────────────
# NIP-57 zap receipts
# ──────────────────────────────────────────────────────────────────────

def _zap_request(*, amount_msat=21_000) -> dict:
    return {
        "kind": NIP57_ZAP_REQUEST_KIND,
        "pubkey": ZAPPER_PUBKEY,
        "created_at": 1_700_000_300,
        "tags": [
            ["amount", str(amount_msat)],
            ["a", ANCHOR_COORD, "wss://relay.example"],
            ["p", AUTHOR_PUBKEY, "wss://relay.example"],
        ],
        "content": "thanks for the plugin!",
        "id": _hex_id("4"),
        "sig": "deadbeef" * 16,
    }


def _zap_receipt(
    *,
    request=None,
    override=None,
    bolt11_amount_msat=None,
    bolt11_description_hash_hex=None,
) -> dict:
    """Build a NIP-57 zap receipt fixture.

    ``bolt11_amount_msat`` defaults to the zap request's amount tag so
    the cross-check in the parser passes; pass an int to force a
    mismatch and exercise the strict-mode rejection.

    ``bolt11_description_hash_hex`` defaults to the actual sha256 of
    the embedded request, again so the strict-mode parser accepts the
    receipt. Pass a different hex to simulate a tampered receipt.
    """
    request = request or _zap_request()
    raw_request = json.dumps(request, separators=(",", ":"))
    if bolt11_amount_msat is None:
        amount_tag = next((t for t in request["tags"] if t and t[0] == "amount"), None)
        bolt11_amount_msat = int(amount_tag[1]) if amount_tag else 21_000
    if bolt11_description_hash_hex is None:
        bolt11_description_hash_hex = hashlib.sha256(raw_request.encode()).hexdigest()
    invoice = build_test_bolt11(
        amount_msat=bolt11_amount_msat if bolt11_amount_msat > 0 else None,
        description_hash_hex=bolt11_description_hash_hex,
    )
    event = {
        "id": _hex_id("5"),
        "kind": NIP57_ZAP_RECEIPT_KIND,
        "pubkey": "d" * 64,  # the LNURL service's pubkey
        "created_at": 1_700_000_400,
        "tags": [
            ["bolt11", invoice],
            ["description", raw_request],
            ["p", AUTHOR_PUBKEY],
            ["P", ZAPPER_PUBKEY],
        ],
        "content": "",
    }
    if override:
        event.update(override)
    return event


def test_zap_receipt_happy_path():
    receipt = parse_zap_receipt(_zap_receipt(), ANCHOR)
    assert receipt is not None
    assert receipt.zapper_pubkey == ZAPPER_PUBKEY
    assert receipt.amount_sats == 21
    assert receipt.content == "thanks for the plugin!"
    assert receipt.anchor == ANCHOR


def test_zap_receipt_rejects_missing_description():
    event = _zap_receipt()
    event["tags"] = [t for t in event["tags"] if t[0] != "description"]
    assert parse_zap_receipt(event, ANCHOR) is None


def test_zap_receipt_rejects_unparsable_description():
    event = _zap_receipt()
    event["tags"] = [
        t if t[0] != "description" else ["description", "{not valid json"]
        for t in event["tags"]
    ]
    assert parse_zap_receipt(event, ANCHOR) is None


def test_zap_receipt_rejects_description_targeting_other_plugin():
    other_anchor = PluginAnchor(PLUGIN_LISTING_KIND, AUTHOR_PUBKEY, "other")
    request = _zap_request()
    request["tags"] = [
        t if t[0] != "a" else ["a", other_anchor.coord, ""]
        for t in request["tags"]
    ]
    event = _zap_receipt(request=request)
    assert parse_zap_receipt(event, ANCHOR) is None


def test_zap_receipt_rejects_missing_amount_tag():
    request = _zap_request()
    request["tags"] = [t for t in request["tags"] if t[0] != "amount"]
    event = _zap_receipt(request=request)
    assert parse_zap_receipt(event, ANCHOR) is None


def test_zap_receipt_rejects_amount_below_one_sat():
    """Amounts under 1000 msat (= 0 sats) are rounded down to 0 and
    rejected. NIP-57 amounts are in millisats."""
    request = _zap_request(amount_msat=500)
    event = _zap_receipt(request=request)
    assert parse_zap_receipt(event, ANCHOR) is None


def test_zap_receipt_rejects_negative_amount():
    request = _zap_request(amount_msat=-1000)
    event = _zap_receipt(request=request)
    assert parse_zap_receipt(event, ANCHOR) is None


def test_zap_receipt_rejects_bolt11_amount_mismatch():
    """Bolt11 amount must match the zap request's amount tag per NIP-57."""
    receipt = _zap_receipt(bolt11_amount_msat=42_000)   # request asks for 21k
    assert parse_zap_receipt(receipt, ANCHOR) is None


def test_zap_receipt_rejects_amount_less_bolt11():
    """An amount-less bolt11 in a zap receipt is a forgery surface.

    NIP-57 requires the receipt's bolt11 amount to match the zap
    request's ``amount`` tag. An ``any amount`` invoice would let a
    relay claim any value it likes, so we reject it outright.
    """
    receipt = _zap_receipt(bolt11_amount_msat=0)   # 0 -> None / "any amount"
    assert parse_zap_receipt(receipt, ANCHOR) is None


def test_zap_receipt_rejects_description_hash_mismatch():
    """Bolt11's ``h`` field MUST equal sha256(description tag value)."""
    bogus_hash = "0" * 64
    receipt = _zap_receipt(bolt11_description_hash_hex=bogus_hash)
    assert parse_zap_receipt(receipt, ANCHOR) is None


def test_zap_receipt_rejects_when_signer_is_wrong_lnurl_pubkey():
    """Strict mode: receipt MUST be signed by the LNURL service we paid."""
    receipt = _zap_receipt()
    parsed = parse_zap_receipt(
        receipt, ANCHOR, expected_lnurl_pubkey="e" * 64,
    )
    assert parsed is None


def test_zap_receipt_accepts_when_signer_matches_lnurl_pubkey():
    receipt = _zap_receipt()
    parsed = parse_zap_receipt(
        receipt, ANCHOR, expected_lnurl_pubkey="d" * 64,
    )
    assert parsed is not None


def test_zap_receipt_rejects_inline_description_bolt11():
    """A bolt11 with the ``d`` field (inline description) instead of
    ``h`` cannot be cryptographically bound to the zap request."""
    request = _zap_request()
    raw_request = json.dumps(request, separators=(",", ":"))
    # Build a bolt11 with NO description hash so the parser must reject.
    invoice = build_test_bolt11(
        amount_msat=21_000, description_hash_hex=None,
    )
    receipt = {
        "id": _hex_id("5"),
        "kind": NIP57_ZAP_RECEIPT_KIND,
        "pubkey": "d" * 64,
        "created_at": 1_700_000_400,
        "tags": [
            ["bolt11", invoice],
            ["description", raw_request],
            ["p", AUTHOR_PUBKEY],
        ],
        "content": "",
    }
    assert parse_zap_receipt(receipt, ANCHOR) is None


# ──────────────────────────────────────────────────────────────────────
# NIP-09 deletions
# ──────────────────────────────────────────────────────────────────────

def _deletion_event(*, target_ids=None, target_coords=None) -> dict:
    # ``target_ids or default`` would mistakenly substitute the default
    # when the test deliberately passes ``[]`` to assert rejection.
    if target_ids is None:
        target_ids = [_hex_id("6")]
    if target_coords is None:
        target_coords = []
    tags = []
    for tid in target_ids:
        tags.append(["e", tid])
    for coord in target_coords:
        tags.append(["a", coord])
    return {
        "id": _hex_id("7"),
        "kind": NIP09_DELETION_KIND,
        "pubkey": RATER_PUBKEY,
        "created_at": 1_700_000_500,
        "tags": tags,
        "content": "withdrew my review",
    }


def test_deletion_happy_path():
    target = _hex_id("8")
    deletion = parse_deletion(_deletion_event(target_ids=[target]))
    assert deletion is not None
    assert target in deletion.target_event_ids
    assert deletion.author_pubkey == RATER_PUBKEY


def test_deletion_rejects_event_without_targets():
    event = _deletion_event(target_ids=[])
    assert parse_deletion(event) is None


def test_deletion_collects_addressable_targets():
    deletion = parse_deletion(_deletion_event(
        target_ids=[], target_coords=[ANCHOR_COORD],
    ))
    assert deletion is not None
    assert ANCHOR_COORD in deletion.target_coords


# ──────────────────────────────────────────────────────────────────────
# NIP-02 contact lists
# ──────────────────────────────────────────────────────────────────────

def test_contact_list_parses_p_tags():
    event = {
        "id": _hex_id("9"),
        "kind": 3,
        "pubkey": AUTHOR_PUBKEY,
        "created_at": 1_700_000_700,
        "tags": [
            ["p", "f" * 64, "wss://r1", "alice"],
            ["p", "e" * 64],
            ["p", "not-a-pubkey"],     # rejected
        ],
        "content": "",
    }
    contacts = parse_contact_list(event)
    assert contacts is not None
    assert contacts.owner_pubkey == AUTHOR_PUBKEY
    assert "f" * 64 in contacts.follows
    assert "e" * 64 in contacts.follows
    assert len(contacts.follows) == 2  # bad p tag dropped


# ──────────────────────────────────────────────────────────────────────
# NIP-51 mute lists
# ──────────────────────────────────────────────────────────────────────

def test_mute_list_parses_p_tags():
    event = {
        "id": _hex_id("a"),
        "kind": 10000,
        "pubkey": AUTHOR_PUBKEY,
        "created_at": 1_700_000_800,
        "tags": [
            ["p", "f" * 64],
            ["p", "e" * 64],
        ],
        "content": "",
    }
    mute = parse_mute_list(event)
    assert mute is not None
    assert "f" * 64 in mute.muted_pubkeys
    assert "e" * 64 in mute.muted_pubkeys
