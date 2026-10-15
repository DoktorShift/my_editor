# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins an article's co-authors and shared zaps (nostr/article_details.py,
the publisher, a file's front matter, and Article Details).

What must hold:

  A co-author is tagged ["p", <pubkey>, <relay>, "author"], the marker
  standup writes and reads back, so both apps agree on who wrote it. A
  co-author also mentioned in the text has one p tag, the one with the
  marker (with the mention's relay when the co-author's has none).

  A co-author is credited and receives no zaps until given a share: whole
  percentages set with a slider or exactly, the author keeping what the
  others leave (nostr/zap_shares.py), written as zap tags of four parts
  each, the author's first, every one naming a relay. A co-author whose
  profile has no lightning address can't be paid: their share stays at 0
  and their row says why. A split another app made stays as it set it
  until a share is changed here. The author is never their own
  co-author, and nobody is added twice.

  A Markdown file keeps co-authors and zap shares in its front matter.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import front_matter  # noqa: E402
from nostr import bech32  # noqa: E402
from nostr.article_details import (  # noqa: E402
    ArticleDetails, from_front_matter, front_matter_with)
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
                                name_of=lambda pubkey: {ME: "Me", BOB: "Bob", CAROL: "Carol"}[pubkey])
    sent = []
    panel.changed.connect(sent.append)
    panel.show_article(details, fixed_identifier=False)
    return panel, sent, asked


def rows(panel):
    return panel.authors.rows()


def test_a_co_author_is_added_and_removed_in_article_details():
    panel, sent, asked = panel_with(ArticleDetails(title="T"))
    assert not panel.authors.even_button.isVisibleTo(panel)
    panel.authors.add_button.click()
    asked[-1](Person(pubkey=BOB, display_name="Bob", relay_hint=RELAY))
    assert sent[-1].coauthors == ((BOB, RELAY),) and sent[-1].zaps == ()   # credited, unpaid
    panel.authors.add_button.click()
    asked[-1](Person(pubkey=BOB))                            # twice: nothing
    asked[-1](Person(pubkey=ME))                             # the author: nothing
    assert len(sent) == 1
    assert unnamed_controls(panel) == []
    rows(panel)[BOB].remove_button.click()
    assert sent[-1].coauthors == ()


def test_tab_goes_through_each_share_and_remove_then_the_buttons():
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, ""), (CAROL, ""))))
    chain, widget = [], panel.hashtags.entry

    def tab_stop(widget):
        # As Tab itself goes: a widget focused through another (a spin box's
        # field) is no stop of its own.
        return (widget.focusPolicy() & Qt.FocusPolicy.TabFocus and widget.isVisibleTo(panel)
                and widget.focusProxy() is None)
    for _step in range(8):
        widget = widget.nextInFocusChain()
        while not tab_stop(widget):
            widget = widget.nextInFocusChain()
        chain.append(widget.accessibleName() or widget.text())
    assert chain == ["Share for Bob", "Share for Bob", "Remove Bob",
                     "Share for Carol", "Share for Carol", "Remove Carol",
                     "Add Co-Author…", "Split Evenly"]


def test_a_share_is_set_with_the_slider_or_exactly_and_you_keep_the_rest():
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, RELAY),)))
    rows(panel)[BOB].slider.setValue(30)
    assert sent[-1].zaps == (("zap", ME, "wss://mine.example", "70"), ("zap", BOB, RELAY, "30"))
    assert panel.authors.you_share.text() == "70%" and rows(panel)[BOB].field.value() == 30
    rows(panel)[BOB].field.setValue(45)
    assert [tag[3] for tag in sent[-1].zaps] == ["55", "45"]
    assert panel.authors.sentence.text() == "Me 55% · Bob 45%"
    assert not panel.authors.note.isHidden()               # who pays a split, honestly
    rows(panel)[BOB].slider.setValue(0)
    assert sent[-1].zaps == () and panel.authors.note.isHidden()


def test_a_share_is_held_within_what_the_others_leave():
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, ""), (CAROL, ""))))
    rows(panel)[BOB].slider.setValue(70)
    rows(panel)[CAROL].slider.setValue(90)
    assert rows(panel)[CAROL].slider.value() == 30 and rows(panel)[CAROL].field.value() == 30
    assert panel.authors.you_share.text() == "0%"
    assert [tag[1] for tag in sent[-1].zaps] == [BOB, CAROL]  # you keep nothing: no tag


def test_split_evenly_and_a_removed_co_author_gives_their_share_back():
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, ""), (CAROL, ""))))
    panel.authors.even_button.click()
    assert [tag[3] for tag in sent[-1].zaps] == ["34", "33", "33"]
    rows(panel)[CAROL].remove_button.click()
    assert [(tag[1], tag[3]) for tag in sent[-1].zaps] == [(ME, "67"), (BOB, "33")]


def test_a_split_another_app_made_stays_until_a_share_is_changed_here():
    foreign = (("zap", ME, "wss://mine.example", "3"), ("zap", BOB, "", "1"))
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, ""),),
                                                   zaps=foreign))
    assert rows(panel)[BOB].field.value() == 25 and panel.authors.you_share.text() == "75%"
    panel.authors.add_button.click()
    asked[-1](Person(pubkey=CAROL))
    assert sent[-1].zaps == foreign                          # left as the other app set them
    rows(panel)[BOB].slider.setValue(50)
    assert [tag[3] for tag in sent[-1].zaps] == ["50", "50"]


def test_a_co_author_who_cant_receive_zaps_gets_no_share_and_says_why():
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, RELAY),)),
                                    zappable={CAROL: False})
    panel.authors.add_button.click()
    asked[-1](Person(pubkey=CAROL))
    carol = rows(panel)[CAROL]
    assert not carol.slider.isEnabled() and not carol.unpaid.isHidden()
    panel.authors.even_button.click()
    assert [tag[1] for tag in sent[-1].zaps] == [ME, BOB]


def test_an_answer_that_comes_later_takes_the_share_back():
    checks = []
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, RELAY),)),
                                    checks=checks)
    rows(panel)[BOB].slider.setValue(40)
    assert [tag[1] for tag in sent[-1].zaps] == [ME, BOB]     # not known yet: paid
    [(pubkey, answer)] = checks
    answer(False)
    assert sent[-1].zaps == () and not rows(panel)[BOB].unpaid.isHidden()
    assert not panel.authors.even_button.isVisibleTo(panel)   # nobody left to share with


@pytest.mark.parametrize("content, able", [
    ('{"name": "Bob", "lud16": "bob@wallet.example"}', True),
    ('{"name": "Bob", "lud06": "lnurl1dp68gurn8ghj7"}', True),
    ('{"name": "Bob", "lud16": "  "}', False),
    ('{"name": "Bob"}', False),
    ("not json", False),
    (None, False),                                       # the relays have no profile
])
def test_a_lightning_address_in_the_profile_is_what_receives_zaps(content, able):
    from nostr.metadata import can_receive_zaps
    from nostr.outbox.lookup import Lookup, LookupState
    from tests.outbox_fakes import FakeRelayDirectory
    asked, answers = [], []

    def query(pool, relays, *, kind, author, on_done, **_rest):
        asked.append((relays, kind, author))
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


def test_a_profile_no_relay_answered_is_asked_again_with_backoff():
    """No relay answering is no answer: the lookup is asked again, waiting
    longer each time as the relays' own reconnecting does, up to its
    longest wait; meanwhile the line under Share zaps says nothing."""
    import time
    from PySide6.QtCore import QCoreApplication
    from nostr.metadata import can_receive_zaps
    from nostr.outbox.lookup import Lookup, LookupState
    from tests.outbox_fakes import FakeRelayDirectory
    asked, answers = [], []

    def query(pool, relays, *, kind, author, on_done, **_rest):
        # perf_counter: Windows' monotonic clock ticks only every 15.6 ms.
        asked.append(time.perf_counter())
        if len(asked) < 3:
            return on_done(Lookup(LookupState.UNKNOWN))
        on_done(Lookup(LookupState.FOUND, {"content": '{"lud16": "b@w.example"}',
                                           "created_at": 1}))

    can_receive_zaps(None, FakeRelayDirectory(), BOB, RELAY, answers.append, query=query,
                     first_wait_ms=20, longest_wait_ms=40)
    deadline = time.monotonic() + 3
    while not answers and time.monotonic() < deadline:
        QCoreApplication.processEvents()
    assert answers == [True] and len(asked) == 3
    # Longer each time: the second wait at least the longest one, the first
    # at least the first one. Lower bounds only: a busy machine can stretch
    # a wait (the first one past the second), never shorten it.
    assert asked[1] - asked[0] >= 0.015 and asked[2] - asked[1] >= 0.030

    asked.clear()
    never = []
    can_receive_zaps(None, FakeRelayDirectory(), BOB, RELAY, never.append,
                     query=lambda *a, on_done, **k: (asked.append(1),
                                                     on_done(Lookup(LookupState.UNKNOWN))),
                     first_wait_ms=5, longest_wait_ms=20)
    deadline = time.monotonic() + 3
    while not never and time.monotonic() < deadline:
        QCoreApplication.processEvents()
    assert never == [None] and len(asked) == 4                     # 5, 10, 20 ms, then given up


def test_while_no_relay_answers_the_share_stays_and_nothing_is_said():
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, RELAY),)),
                                    zappable={BOB: None})
    rows(panel)[BOB].slider.setValue(20)
    assert [tag[1] for tag in sent[-1].zaps] == [ME, BOB]
    assert rows(panel)[BOB].unpaid.isHidden()


def test_a_co_author_not_found_out_is_asked_again_next_time():
    checks = []
    panel, sent, asked = panel_with(ArticleDetails(title="T", coauthors=((BOB, RELAY),)),
                                    checks=checks)
    [(pubkey, answer)] = checks
    answer(None)                                   # every try went unanswered
    panel.show_article(ArticleDetails(title="Other", coauthors=((BOB, RELAY),)),
                       fixed_identifier=False)
    assert [pubkey for pubkey, _answer in checks] == [BOB, BOB]
