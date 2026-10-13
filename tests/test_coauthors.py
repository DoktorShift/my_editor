# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins an article's co-authors and shared zaps (nostr/article_details.py,
the publisher, a file's front matter, and Article Details).

What must hold:

  A co-author is tagged ["p", <pubkey>, <relay>, "author"], the marker
  standup writes and reads back, so both apps agree on who wrote it. A
  co-author also mentioned in the text has one p tag, the one with the
  marker (with the mention's relay when the co-author's has none).

  Zaps are shared only when chosen: then equally between the author and
  the co-authors, a zap tag of four parts each, as every reader of zap
  splits reads them, the author's with their relay. A co-author whose
  profile has no lightning address can't receive zaps: they get no share,
  and one plain line under the option says so (review M6). Zaps another app shared stay as it set them until
  they are changed here. The author is never their own co-author, and
  nobody is added twice.

  A Markdown file keeps co-authors and zap shares in its front matter.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import front_matter  # noqa: E402
from nostr import bech32  # noqa: E402
from nostr.article_details import (  # noqa: E402
    ArticleDetails, equal_zaps, from_front_matter, front_matter_with)
from nostr.known_people import Person  # noqa: E402
from nostr.publisher import build_article  # noqa: E402
from nostr.ui.article_details_panel import ArticleDetailsPanel  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402

ME = "a1" * 32
BOB = "b2" * 32
CAROL = "c3" * 32
RELAY = "wss://relay.example.com"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def test_co_authors_and_zap_shares_are_read_from_tags_and_written_back():
    tags = [["d", "x"], ["p", BOB, RELAY, "author"], ["p", CAROL],
            ["zap", ME, "", "1"], ["zap", BOB, RELAY, "1"]]
    details = ArticleDetails.from_tags(tags)
    assert details.coauthors == ((BOB, RELAY),)               # a mention's p is no co-author
    assert details.zaps == (("zap", ME, "", "1"), ("zap", BOB, RELAY, "1"))
    assert ArticleDetails.from_tags(details.tags()) == details
    assert ["p", BOB, RELAY, "author"] in details.draft_tags("")


def test_zaps_are_shared_equally_and_only_with_co_authors():
    assert equal_zaps(ME, "", ()) == ()
    assert equal_zaps(ME, "", ((BOB, RELAY), (ME, ""))) == (
        ("zap", ME, "", "1"), ("zap", BOB, RELAY, "1"))


def test_a_co_author_also_mentioned_has_one_tag_with_the_marker():
    details = ArticleDetails(identifier="x", coauthors=((BOB, ""),))
    mention = "nostr:" + bech32.encode_nprofile(BOB, [RELAY])
    event = build_article(content=f"With {mention} today.", pubkey_hex=ME, slug="x",
                          title="T", extra_tags=details.published_tags("..."))
    people = [tag for tag in event["tags"] if tag[0] == "p"]
    assert people == [["p", BOB, RELAY, "author"]]
    plain = build_article(content="Body", pubkey_hex=ME, slug="x", title="T",
                          extra_tags=ArticleDetails(identifier="x").published_tags("Body"))
    assert not [tag for tag in plain["tags"] if tag[0] in ("p", "zap")]


def test_a_files_front_matter_keeps_them():
    details = ArticleDetails(title="T", coauthors=((BOB, RELAY), (CAROL, "")),
                             zaps=(("zap", ME, "", "1"), ("zap", BOB, RELAY, "2")))
    text = front_matter.join(front_matter_with(details, None), "Body")
    assert bech32.encode_npub(CAROL) in text and "zap_split:" in text
    again = from_front_matter(front_matter.split(text)[0])
    assert again.coauthors == details.coauthors and again.zaps == details.zaps


class Profile:
    user_pubkey = ME


def panel_with(details, zappable=None, checks=None):
    asked = []
    zappable = zappable or {}

    def can_receive_zaps(pubkey, hint, answer):
        if checks is not None:
            checks.append((pubkey, answer))     # answered later, as a relay would
        else:
            answer(zappable.get(pubkey, True))

    panel = ArticleDetailsPanel(profile=lambda: Profile(),
                                can_receive_zaps=can_receive_zaps,
                                relay_hint=lambda pubkey: {ME: "wss://mine.example"}.get(pubkey, ""),
                                pick_person=lambda anchor, chosen: asked.append(chosen),
                                name_of=lambda pubkey: {BOB: "Bob", CAROL: "Carol"}[pubkey])
    sent = []
    panel.changed.connect(sent.append)
    panel.show_article(details, fixed_identifier=False)
    return panel, sent, asked


def test_a_co_author_is_added_and_removed_in_article_details():
    panel, sent, asked = panel_with(ArticleDetails(title="T"))
    assert not panel.share_zaps.isEnabled()
    panel.add_coauthor_button.click()
    asked[-1](Person(pubkey=BOB, display_name="Bob", relay_hint=RELAY))
    assert sent[-1].coauthors == ((BOB, RELAY),) and sent[-1].zaps == ()
    panel.add_coauthor_button.click()
    asked[-1](Person(pubkey=BOB))                            # twice: nothing
    asked[-1](Person(pubkey=ME))                             # the author: nothing
    assert len(sent) == 1
    remove = [b for b in panel.findChildren(type(panel.add_coauthor_button))
              if b.accessibleName() == "Remove Bob"]
    assert remove and unnamed_controls(panel) == []
    remove[0].click()
    assert sent[-1].coauthors == ()


def test_shared_zaps_follow_the_co_authors_equally():
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, RELAY),)))
    panel.share_zaps.setChecked(True)
    assert sent[-1].zaps == (("zap", ME, "wss://mine.example", "1"), ("zap", BOB, RELAY, "1"))
    panel.add_coauthor_button.click()
    asked[-1](Person(pubkey=CAROL))
    assert [tag[1] for tag in sent[-1].zaps] == [ME, BOB, CAROL]
    panel.share_zaps.setChecked(False)
    assert sent[-1].zaps == ()


def test_zaps_another_app_shared_stay_until_changed_here():
    foreign = (("zap", ME, "wss://mine.example", "3"), ("zap", BOB, "", "1"))
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, ""),),
                                                   zaps=foreign))
    assert panel.share_zaps.isChecked() and not panel.zaps_note.isHidden()
    panel.add_coauthor_button.click()
    asked[-1](Person(pubkey=CAROL))
    assert sent[-1].zaps == foreign                          # left as the other app set them
    panel.share_zaps.setChecked(False)
    assert sent[-1].zaps == ()


def test_a_co_author_who_cant_receive_zaps_gets_no_share():
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, RELAY),)),
                                    zappable={CAROL: False})
    panel.share_zaps.setChecked(True)
    panel.add_coauthor_button.click()
    asked[-1](Person(pubkey=CAROL))
    assert [tag[1] for tag in sent[-1].zaps] == [ME, BOB]
    assert not panel.zaps_note.isHidden()
    assert panel.zaps_note.text() == ("Carol can’t receive zaps: their profile has no "
                                      "lightning address. They get no share.")
    panel.share_zaps.setChecked(False)
    panel.share_zaps.setChecked(True)
    assert [tag[1] for tag in sent[-1].zaps] == [ME, BOB]


def test_an_answer_that_comes_later_takes_the_share_back():
    checks = []
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, RELAY),)),
                                    checks=checks)
    panel.share_zaps.setChecked(True)
    assert [tag[1] for tag in sent[-1].zaps] == [ME, BOB]     # not known yet: shared
    [(pubkey, answer)] = checks
    answer(False)
    assert sent[-1].zaps == () and panel.zaps_note.text().startswith("Bob can’t")
    assert not panel.share_zaps.isEnabled()                    # nobody left to share with


@pytest.mark.parametrize("content, able", [
    ('{"name": "Bob", "lud16": "bob@wallet.example"}', True),
    ('{"name": "Bob", "lud06": "lnurl1dp68gurn8ghj7"}', True),
    ('{"name": "Bob", "lud16": "  "}', False),
    ('{"name": "Bob"}', False),
    ("not json", False),
    (None, False),                                       # the relays have no profile
    ("unreachable", None),                               # no relay answered: not known
])
def test_a_lightning_address_in_the_profile_is_what_receives_zaps(content, able):
    from nostr.metadata import can_receive_zaps
    from nostr.outbox.lookup import Lookup, LookupState
    from tests.outbox_fakes import FakeRelayDirectory
    asked, answers = [], []

    def query(pool, relays, *, kind, author, on_done, **_rest):
        asked.append((relays, kind, author))
        if content == "unreachable":
            return on_done(Lookup(LookupState.UNKNOWN))
        event = None if content is None else {"content": content, "created_at": 1}
        on_done(Lookup(LookupState.FOUND if event else LookupState.ABSENT, event))

    can_receive_zaps(None, FakeRelayDirectory(), BOB, RELAY, answers.append, query=query)
    assert answers == [able]
    assert asked[0][1:] == (0, BOB) and asked[0][0][0] == RELAY


def standup_author(tags, signer):
    """standup's own rule (event_parser._get_author_pubkey): the first
    ["p", x, _, "author"] tag is the writer; else the signer."""
    for tag in tags:
        if tag[0] == "p" and len(tag) >= 4 and tag[3] == "author":
            return tag[1]
    return signer


def test_the_signer_is_written_first_so_standup_credits_the_writer():
    details = ArticleDetails(identifier="x", coauthors=((BOB, RELAY),))
    event = build_article(content="Body", pubkey_hex=ME, slug="x", title="T",
                          extra_tags=details.published_tags("Body", author=(ME, RELAY)))
    authors = [tag for tag in event["tags"] if tag[0] == "p"]
    assert authors == [["p", ME, RELAY, "author"], ["p", BOB, RELAY, "author"]]
    assert standup_author(event["tags"], ME) == ME
    draft = details.draft_tags("Body", author=(ME, RELAY))
    assert standup_author(draft, ME) == ME
    # Read back, the signer is no co-author of their own article.
    assert ArticleDetails.from_tags(event["tags"], signer=ME).coauthors == ((BOB, RELAY),)
    # Without co-authors, no author tag at all.
    alone = ArticleDetails(identifier="x").published_tags("Body", author=(ME, RELAY))
    assert not [tag for tag in alone if tag[0] == "p"]
