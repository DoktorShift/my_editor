# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins when an article says it was published (NIP-23 ``published_at``).

What must hold:

  ``published_at`` is the time of the first publication. Publishing a
  new version of an article (the same identifier) keeps it: from the
  draft the tab came from when that draft carries it, otherwise from the
  version already on the relays. Only an article never published before
  gets "now".
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.publisher import published_at_of  # noqa: E402
from tests.outbox_fakes import PK, FakeClient, FakePool, FakeSessionPool  # noqa: E402

FIRST = 1_700_000_000


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


# -- reading it from a version of the article -----------------------------------------

def test_the_tag_says_when_it_was_first_published():
    assert published_at_of({"tags": [["d", "x"], ["published_at", str(FIRST)]],
                            "created_at": FIRST + 500}) == FIRST


def test_a_version_without_the_tag_dates_from_when_it_was_made():
    assert published_at_of({"tags": [["d", "x"]], "created_at": FIRST + 500}) == FIRST + 500


@pytest.mark.parametrize("event", [None, {}, {"tags": [["published_at", "soon"]]},
                                   {"tags": [], "created_at": True}])
def test_no_usable_time_is_none(event):
    assert published_at_of(event) is None


def test_the_version_on_the_authors_relays_is_asked_for_by_its_identifier():
    from nostr.publisher import find_first_publication
    from tests.outbox_fakes import FakeRelayDirectory, HandPool, settle, signed
    directory = FakeRelayDirectory()
    directory.set(PK, ["wss://nos.lol"])
    pool = HandPool()
    found = []
    owner = QObject()
    find_first_publication(pool, directory, PK, "my-post", found.append, parent=owner)
    settle()
    sub = pool.subs[0]
    assert sub.filters[0]["kinds"] == [30023] and sub.filters[0]["#d"] == ["my-post"]
    for url in sub.urls:
        sub.answer(url, signed(30023, [["d", "my-post"], ["published_at", str(FIRST)]]))
    settle()
    assert found == [FIRST]


# -- the publish dialog ---------------------------------------------------------------

class CapturedJob(QObject):
    """Stands in for PublishJob: keeps the event it was given."""

    status_changed = Signal(str)
    signed = Signal(str)
    completed = Signal(list)
    failed = Signal(str)
    made = []

    def __init__(self, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.event = kwargs["unsigned_event"]
        CapturedJob.made.append(self)

    def start(self):
        pass

    def cancel(self):
        pass


@pytest.fixture
def captured(monkeypatch):
    import nostr.ui.publish_article_dialog as module
    CapturedJob.made = []
    monkeypatch.setattr(module, "PublishJob", CapturedJob)
    return CapturedJob.made


def _dialog(tmp_path, *, slug="my-post", first_published=None, lookup=None):
    from nostr.avatar_store import AvatarStore
    from nostr.known_people import KnownPeople
    from nostr.profiles import Profile as StoredProfile
    from nostr.profiles import ProfileStore
    from nostr.search import Nip50SearchClient
    from nostr.ui.publish_article_dialog import PublishArticleDialog
    from tests.outbox_fakes import FakeRelayDirectory, HandPool

    store = ProfileStore(path=tmp_path / "profiles.json")
    profile = StoredProfile(user_pubkey=PK, bunker_pubkey="b" * 64,
                            bunker_relays=["wss://bunker.example"], local_secret_hex="0" * 64)
    store.upsert(profile)
    people = KnownPeople(path=tmp_path / "people.json")
    return PublishArticleDialog(
        body_markdown="# Hello\n\nBody.", active_profile=profile, store=store,
        relay_pool=FakePool(), relay_directory=FakeRelayDirectory(),
        session_pool=FakeSessionPool(FakeClient()), known_people=people,
        search_client=Nip50SearchClient(HandPool(), people), avatars=AvatarStore(),
        default_title="Hello", default_slug=slug, first_published=first_published,
        published_at_lookup=lookup)


def _published_at(job) -> int:
    return int(next(t[1] for t in job.event["tags"] if t[0] == "published_at"))


def test_a_draft_that_knows_its_first_publication_keeps_it(tmp_path, captured):
    asked = []
    dialog = _dialog(tmp_path, first_published=FIRST,
                     lookup=lambda *args: asked.append(args))
    dialog._on_publish()
    assert asked == []                               # nothing to look up
    assert _published_at(captured[0]) == FIRST


def test_an_edit_keeps_the_date_of_the_version_on_the_relays(tmp_path, captured):
    asked = []
    dialog = _dialog(tmp_path, lookup=lambda author, slug, done: asked.append(
        (author, slug, done)))
    dialog._on_publish()
    assert captured == [] and not dialog._publish_btn.isEnabled()   # asking first
    author, slug, done = asked[0]
    assert (author, slug) == (PK, "my-post")
    done(FIRST)
    assert _published_at(captured[0]) == FIRST


def test_a_first_publication_is_dated_now(tmp_path, captured):
    dialog = _dialog(tmp_path, lookup=lambda author, slug, done: done(None))
    before = int(time.time())
    dialog._on_publish()
    assert before <= _published_at(captured[0]) <= int(time.time())


def test_another_identifier_does_not_take_the_drafts_date(tmp_path, captured):
    asked = []
    dialog = _dialog(tmp_path, first_published=FIRST,
                     lookup=lambda author, slug, done: (asked.append(slug), done(None)))
    dialog._slug_edit.setText("a-new-post")
    dialog._on_publish()
    assert asked == ["a-new-post"]
    assert _published_at(captured[0]) > FIRST


def test_an_answer_after_closing_publishes_nothing(tmp_path, captured):
    asked = []
    dialog = _dialog(tmp_path, lookup=lambda author, slug, done: asked.append(done))
    dialog._on_publish()
    dialog.reject()
    asked[0](FIRST)
    assert captured == []
