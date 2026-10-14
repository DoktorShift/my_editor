# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""build_article + slugify: pure builder + identifier-derivation tests."""

from __future__ import annotations

import pytest

from nostr import CLIENT_NAME
from nostr.events import verify_event
from nostr.publisher import build_article, slugify


PK = "79be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798"


# --------------------------------------------------------------------------- #
# slugify                                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("title,expected", [
    ("Hello World", "hello-world"),
    ("  Hello World  ", "hello-world"),
    ("Hello, World!", "hello-world"),
    ("Already-Hyphenated", "already-hyphenated"),
    ("UPPERCASE", "uppercase"),
    ("café & croissant", "cafe-croissant"),   # accents are kept as their letters
    ("Schöne Grüße aus Zürich", "schone-grusse-aus-zurich"),
    ("multiple   spaces", "multiple-spaces"),
    ("with/slashes/and:colons", "with-slashes-and-colons"),
])
def test_slugify_produces_expected(title, expected):
    assert slugify(title) == expected


NPUB = "npub1f2ktq2xcmh9wpmv0s6wqu6r9uvlvp7qr6sdqqk87x3dmnc9w0juswgtagx"


def test_a_slug_never_holds_a_key():
    """A mention or a key in the title leaves the slug readable: a Nostr
    entity, a shortened one ("npub1abcd…wxyz") and a long run of letters
    and digits (a hex key) are left out, never written into the link."""
    assert slugify(f"*Fafuui S s*nostr:{NPUB}") == "fafuui-s-s"
    assert slugify("Fafuui S s @npub1f2kt\u2026tagx") == "fafuui-s-s"
    assert slugify(f"Hello {'ab12' * 16} world") == "hello-world"
    assert slugify(f"Backup nsec1{'q' * 58}") == "backup"
    assert slugify(f"nostr:{NPUB}").startswith("article-")
    assert slugify("Hello @DrShift") == "hello-drshift"          # a name is a word


def test_a_slug_is_cut_at_a_word():
    title = "One Class, One Purpose: Refactoring the Import Pipeline for Sanity and Speed"
    slug = slugify(title)
    assert len(slug) <= 48 and title.lower().replace(",", "").replace(":", "").replace(
        " ", "-").startswith(slug) and not slug.endswith("-")
    assert slug == "one-class-one-purpose-refactoring-the-import"
    assert len(slugify("x" * 90)) == 48                           # one word: cut in it


def test_slugify_of_nothing_is_nothing():
    assert slugify("") == ""
    assert slugify("   ") == ""


@pytest.mark.parametrize("title, slug", [
    ("Note10 ideas for 2026", "note10-ideas-for-2026"),        # words, not keys
    ("note1 on writing", "note1-on-writing"),
    ("npub1 is a prefix", "npub1-is-a-prefix"),
    ("Łódź and Øl", "lodz-and-ol"),
    ("Smørrebrød, Ærø, Œuvre, Straße", "smorrebrod-aero-oeuvre-strasse"),
    ("Þingvellir ðe", "thingvellir-de"),
    ("Привет, мир", "привет-мир"),                            # kept as written, in lower case
    ("日本語のタイトル 2026", "日本語のタイトル-2026"),
    ("नमस्ते दुनिया", "नमस्ते-दुनिया"),                            # the vowel signs stay
    ("Ελληνικά Νέα", "ελληνικά-νέα"),
])
def test_a_slug_keeps_every_scripts_words(title, slug):
    """Review low 3: an ordinary word that starts like a key was dropped,
    letters with a stroke vanished, and a title in another script became
    "untitled" for every article, so they replaced one another on relays."""
    assert slugify(title) == slug


def test_a_title_with_no_words_gets_a_link_name_of_its_own():
    first, second = slugify("🌸🌸🌸"), slugify("👍🏽 !!!")
    assert first.startswith("article-") and second.startswith("article-")
    assert first != second and len(first) <= 20
    assert slugify("🌸🌸🌸") == first                         # the same title, the same name


def test_only_a_real_key_is_left_out():
    import secrets
    from nostr import bech32
    nsec = bech32.encode_nsec(secrets.token_hex(32))
    note = bech32.encode_note("ab" * 32)
    assert slugify(f"Backup {nsec} now") == "backup-now"
    assert slugify(f"See {note} today") == "see-today"
    assert slugify(f"{note[:-1]}x").startswith("note1")      # no key: a word


# --------------------------------------------------------------------------- #
# build_article                                                               #
# --------------------------------------------------------------------------- #

def test_build_article_minimal_has_required_d_and_client_tags():
    event = build_article("body", PK, slug="my-article")
    assert event["kind"] == 30023
    assert event["pubkey"] == PK
    assert event["content"] == "body"
    assert "sig" not in event
    # d-tag is required by NIP-23; client tag is our convention.
    assert ["d", "my-article"] in event["tags"]
    assert ["client", CLIENT_NAME] in event["tags"]


def test_build_article_rejects_empty_slug():
    with pytest.raises(ValueError):
        build_article("body", PK, slug="")
    with pytest.raises(ValueError):
        build_article("body", PK, slug="   ")


def test_build_article_full_metadata():
    event = build_article(
        "## Heading\n\nA paragraph.",
        PK,
        slug="hello-world",
        title="Hello World",
        summary="A friendly hello.",
        image="https://example.com/cover.png",
        published_at=1_700_000_000,
        hashtags=["Intro", "#meta", "  spaced  "],
    )
    tags = {tuple(t) for t in event["tags"]}
    assert ("title", "Hello World") in tags
    assert ("summary", "A friendly hello.") in tags
    assert ("image", "https://example.com/cover.png") in tags
    assert ("published_at", "1700000000") in tags
    # Hashtags are lowercased, stripped of '#', and skipped if blank.
    assert ("t", "intro") in tags
    assert ("t", "meta") in tags
    assert ("t", "spaced") in tags


def test_build_article_omits_empty_optional_tags():
    event = build_article("body", PK, slug="x", title="", summary="   ", image="")
    tag_keys = {t[0] for t in event["tags"]}
    assert "title" not in tag_keys
    assert "summary" not in tag_keys
    assert "image" not in tag_keys


def test_build_article_published_at_zero_is_included():
    # Falsy but explicitly provided, must still be emitted.
    event = build_article("body", PK, slug="x", published_at=0)
    assert ["published_at", "0"] in event["tags"]


def test_build_article_extra_tags_appended():
    event = build_article(
        "body",
        PK,
        slug="x",
        extra_tags=[["e", "abc123"]],
    )
    assert ["e", "abc123"] in event["tags"]


# --------------------------------------------------------------------------- #
# The NIP-23 cover image gate                                                 #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("cover", [
    "javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
    "//cdn.example.com/x.png",          # protocol-relative, no scheme
    "/img/hero.png",                    # never resolved against the feed base
    "https://user:pw@host.example/x.png",
    "http://169.254.169.254/x",         # link-local, the metadata service
    "http://10.0.0.1/x.png",
    "https://cdn.example.com/a b.png",  # whitespace inside a tag value
])
def test_an_unsafe_cover_drops_the_tag_and_still_publishes(cover):
    # An imported article's cover comes straight out of untrusted feed
    # HTML, and the article carries this app's client tag once signed.
    # Losing the cover is the right trade; losing the article is not.
    event = build_article("body", PK, slug="x", title="T", image=cover)
    assert not any(t[0] == "image" for t in event["tags"])
    assert ["title", "T"] in event["tags"]
    assert event["content"] == "body"


@pytest.mark.parametrize("cover", [
    "http://blog.example/cover.png",    # plain http is normal for blogs
    "https://cdn.example.com/cover.png",
    "https://cdn.example.com/cover.png?w=1200",
])
def test_a_reachable_cover_keeps_its_tag(cover):
    event = build_article("body", PK, slug="x", image=cover)
    assert ["image", cover] in event["tags"]


def test_build_article_signs_and_verifies():
    """Round-trip with a known key just like build_note does."""
    from nostr import crypto

    sk = bytes(31) + b"\x01"
    event = build_article("hello", PK, slug="hi", title="Hi", published_at=1_000_000)
    event["sig"] = crypto.sign_schnorr(sk, bytes.fromhex(event["id"])).hex()
    assert verify_event(event)


@pytest.mark.parametrize("title, slug", [
    ("I ❤️ Nostr", "i-nostr"),
    ("Hot ☕️ takes", "hot-takes"),
    ("1️⃣ first", "1-first"),
    ("Café au lait", "cafe-au-lait"),
    ("नमस्ते", "नमस्ते"),                                        # its marks follow its letters
    ("a‍b", "a-b"),
])
def test_a_mark_stays_only_with_the_letter_it_belongs_to(title, slug):
    """Review round 2, M2: an emoji's variation selector (U+FE0F), a
    keycap or a joiner was kept as a word of its own: "❤️" and "☀️" both
    became an invisible link name and replaced each other."""
    assert slugify(title) == slug


def test_titles_of_emoji_only_get_names_of_their_own():
    names = {slugify(title) for title in ("❤️", "☀️", "⭐️⭐️⭐️")}
    assert len(names) == 3 and all(name.startswith("article-") for name in names)


def test_every_addressable_event_the_app_builds_has_its_d_tag_first():
    """Review round 3, M3: an article put ["client", ...] before its d tag,
    and rnostr takes the first tag as the address, so there each article
    replaced the author's last one. Every addressable event the app
    builds names its address first: an article (also once a key is taken
    out of it), a draft and a draft's deletion. (The feed list is pinned
    in test_imports_subscriptions; the private relay list, 10013, is
    replaceable and has no d tag.)"""
    from nostr.drafts import build_draft_wrap, build_tombstone_wrap
    from nostr.publisher import build_note, without_secret_keys
    article = build_article(content="Body #nostr", slug="my-post", pubkey_hex=PK,
                            title="T", hashtags=["nostr"],
                            extra_tags=[["zap", PK, "", "1"]])
    wrap = build_draft_wrap(identifier="draft-1", inner_kind=30023, encrypted_content="x",
                            pubkey_hex=PK, client_name=CLIENT_NAME,
                            extra_tags=[["k", "30023"]])
    tombstone = build_tombstone_wrap(identifier="draft-1", inner_kind=30023, pubkey_hex=PK,
                                     client_name=CLIENT_NAME)
    for event in (article, without_secret_keys(article), wrap, tombstone):
        assert 30000 <= event["kind"] < 40000
        assert event["tags"][0][0] == "d", event["tags"]
        assert ["client", CLIENT_NAME] in event["tags"]
    assert build_note(content="hi", pubkey_hex=PK)["tags"][0] == ["client", CLIENT_NAME]
