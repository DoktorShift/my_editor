# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins an article's details on their way through drafts and publishing
(nostr/article_details.py).

What must hold:

  An article draft's details (identifier, title, summary, cover,
  hashtags, first publication date, and the source and picture tags
  MyEditor carries without editing) survive every save of the draft:
  review F4 found that saving an imported draft kept only its
  identifier, title and summary, so publishing it then re-dated it and
  lost its cover and hashtags.

  Saved under another identifier, the draft is another article and has
  no first publication date of its own yet.

  The publish dialog offers the draft's details and publishes them,
  with the source; the identifier stays when the title is changed.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.article_details import ArticleDetails  # noqa: E402

FIRST = 1_600_000_000
PK = "ab" * 32
COVER = "https://cdn.example/cover.jpg"
PICTURE = "https://cdn.example/inline.png"
IMPORTED_TAGS = [
    ["client", "MyEditor"], ["d", "imported-post"], ["title", "Imported"],
    ["summary", "What it is about"], ["image", COVER], ["published_at", str(FIRST)],
    ["t", "bitcoin"], ["t", "nostr"], ["source", "https://blog.example/feed.xml"],
    ["imeta", f"url {PICTURE}", "m image/png"],
    ["imeta", "url https://cdn.example/removed.png", "m image/png"],
]


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def tag_values(tags, name):
    return [tag[1] for tag in tags if tag[0] == name]


def test_the_details_are_read_from_a_drafts_tags():
    details = ArticleDetails.from_tags(IMPORTED_TAGS)
    assert (details.identifier, details.title, details.summary, details.image) == (
        "imported-post", "Imported", "What it is about", COVER)
    assert details.hashtags == ("bitcoin", "nostr")
    assert details.published_at == FIRST
    assert [tag[0] for tag in details.carried] == ["source", "imeta", "imeta"]


def test_a_draft_keeps_every_detail_and_only_pictures_still_in_it():
    tags = ArticleDetails.from_tags(IMPORTED_TAGS).draft_tags(f"Text ![x]({PICTURE})")
    assert tag_values(tags, "d") == ["imported-post"]
    assert tag_values(tags, "image") == [COVER]
    assert tag_values(tags, "published_at") == [str(FIRST)]
    assert tag_values(tags, "t") == ["bitcoin", "nostr"]
    assert tag_values(tags, "source") == ["https://blog.example/feed.xml"]
    assert tag_values(tags, "imeta") == [f"url {PICTURE}"]          # the removed one is gone


@pytest.mark.parametrize("tags", [None, [], [["d"]], [["published_at", "soon"]], [[1, 2]]])
def test_tags_without_details_give_empty_details(tags):
    details = ArticleDetails.from_tags(tags)
    assert details.published_at is None and details.identifier == ""


# -- saving a draft again (Ctrl+S) ------------------------------------------------------

def _window_with_draft(tags):
    """The parts of the window the draft builders use."""
    from main_window import MainWindow
    record = types.SimpleNamespace(inner_tags=tags)
    stand_in = types.SimpleNamespace(_draft_store=types.SimpleNamespace(get=lambda _d: record))
    stand_in._article_details_of = lambda ed: MainWindow._article_details_of(stand_in, ed)
    return MainWindow, stand_in


def _bound_editor(identifier="imported-post", title="Imported, edited"):
    from main_window import DraftBinding
    from nostr.drafts import INNER_KIND_LONG_FORM
    return types.SimpleNamespace(_draft_binding=DraftBinding(
        identifier=identifier, inner_kind=INNER_KIND_LONG_FORM, title=title))


def test_saving_an_imported_draft_again_keeps_its_details():
    from nostr.ui.stash_kind_dialog import StashChoice, StashKind
    window_class, window = _window_with_draft(IMPORTED_TAGS)
    ed = _bound_editor()
    details = window._article_details_of(ed)
    assert details.title == "Imported, edited"                  # the tab's own title
    profile = types.SimpleNamespace(user_pubkey=PK)
    choice = StashChoice(kind=StashKind.ARTICLE, identifier="imported-post",
                         title=details.title, summary=details.summary)
    inner = window_class._build_inner_for_choice(window, profile, choice, "New text",
                                                 details=details)
    tags = inner["tags"]
    assert tag_values(tags, "published_at") == [str(FIRST)]
    assert tag_values(tags, "image") == [COVER]
    assert tag_values(tags, "t") == ["bitcoin", "nostr"]
    assert tag_values(tags, "source") == ["https://blog.example/feed.xml"]
    assert tag_values(tags, "title") == ["Imported, edited"]


def test_saved_under_another_identifier_it_has_no_first_publication_yet():
    from nostr.ui.stash_kind_dialog import StashChoice, StashKind
    window_class, window = _window_with_draft(IMPORTED_TAGS)
    details = window._article_details_of(_bound_editor())
    choice = StashChoice(kind=StashKind.ARTICLE, identifier="a-new-article", title="New")
    inner = window_class._build_inner_for_choice(
        window, types.SimpleNamespace(user_pubkey=PK), choice, "Text", details=details)
    assert tag_values(inner["tags"], "d") == ["a-new-article"]
    assert tag_values(inner["tags"], "published_at") == []
    assert tag_values(inner["tags"], "image") == [COVER]


# -- the publish dialog ------------------------------------------------------------------

def _dialog(tmp_path, details):
    from nostr.avatar_store import AvatarStore
    from nostr.known_people import KnownPeople
    from nostr.profiles import Profile as StoredProfile
    from nostr.profiles import ProfileStore
    from nostr.search import Nip50SearchClient
    from nostr.ui.publish_article_dialog import PublishArticleDialog
    from tests.outbox_fakes import FakeClient, FakePool, FakeRelayDirectory, FakeSessionPool
    from tests.outbox_fakes import HandPool, PK as OWNER

    store = ProfileStore(path=tmp_path / "profiles.json")
    profile = StoredProfile(user_pubkey=OWNER, bunker_pubkey="b" * 64,
                            bunker_relays=["wss://bunker.example"], local_secret_hex="0" * 64)
    store.upsert(profile)
    people = KnownPeople(path=tmp_path / "people.json")
    return PublishArticleDialog(
        body_markdown=f"# Hello\n\nBody ![x]({PICTURE}).", active_profile=profile,
        store=store, relay_pool=FakePool(), relay_directory=FakeRelayDirectory(),
        session_pool=FakeSessionPool(FakeClient()), known_people=people,
        search_client=Nip50SearchClient(HandPool(), people), avatars=AvatarStore(),
        default_title=details.title, default_slug=details.identifier,
        first_published=details.published_at, details=details,
        published_at_lookup=lambda *_args: None)


def test_the_publish_dialog_offers_and_publishes_the_drafts_details(tmp_path, monkeypatch):
    import nostr.ui.publish_article_dialog as module
    made = []

    class Captured:
        def __init__(self, **kwargs):
            made.append(kwargs["unsigned_event"])
            for name in ("status_changed", "signed", "completed", "failed"):
                setattr(self, name, types.SimpleNamespace(connect=lambda *_a: None))

        def start(self):
            pass

    monkeypatch.setattr(module, "PublishJob", Captured)
    details = ArticleDetails.from_tags(IMPORTED_TAGS)
    dialog = _dialog(tmp_path, details)
    assert dialog._summary_edit.text() == "What it is about"
    assert dialog._image_edit.text() == COVER
    assert dialog._tags_edit.text() == "bitcoin, nostr"
    dialog._title_edit.setText("A new title")
    assert dialog._slug_edit.text() == "imported-post"        # the article keeps its identifier
    dialog._on_publish()
    tags = made[0]["tags"]
    assert tag_values(tags, "published_at") == [str(FIRST)]
    assert tag_values(tags, "image") == [COVER]
    assert tag_values(tags, "t") == ["bitcoin", "nostr"]
    assert tag_values(tags, "source") == ["https://blog.example/feed.xml"]
    assert tag_values(tags, "imeta") == [f"url {PICTURE}"]
