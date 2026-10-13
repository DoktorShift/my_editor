# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins one model for the details of the article in a tab
(MainWindow._article_details_of and _set_article_details, the publish
window's details(), recovery.tab_identity, the workspace).

What must hold:

  Details set in a tab (Article Details, the publish window, a Markdown
  file's front matter) win over those its draft holds, wherever they are
  read: saving the draft, publishing, the preview. Setting them marks the
  tab changed, as typing does; setting what it already has changes
  nothing. A draft saved with them holds them from then on.

  The publish window hands back what was changed in it, and only that: a
  window opened and closed again leaves the tab as it was.

  A restart and a crash keep them, with the tab's draft and the article
  it edits.

  Hashtags are written separated by commas, and read without "#", each
  once whatever its case, in the case written.
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import recovery  # noqa: E402
import workspace  # noqa: E402
import workspace_restore  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from nostr.article_details import ArticleDetails, hashtags_from  # noqa: E402

PK = "ab" * 32
DRAFT_TAGS = [["d", "my-post"], ["title", "From the draft"], ["summary", "Drafted"],
              ["t", "bitcoin"]]
OWN = ArticleDetails(identifier="my-post", title="Set in the tab", summary="Own summary",
                     image="https://cdn.example/cover.jpg", hashtags=("nostr",))


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


def window_with_draft(tags=DRAFT_TAGS):
    from main_window import MainWindow
    record = types.SimpleNamespace(inner_tags=tags)
    stand_in = types.SimpleNamespace(_draft_store=types.SimpleNamespace(get=lambda _d: record))
    stand_in._article_details_of = lambda ed: MainWindow._article_details_of(stand_in, ed)
    stand_in._set_article_details = lambda ed, d: MainWindow._set_article_details(
        stand_in, ed, d)
    stand_in.current_editor = lambda: None
    return stand_in


def bound_editor():
    from main_window import DraftBinding
    from nostr.drafts import INNER_KIND_LONG_FORM
    ed = HtmlEditor()
    ed._draft_binding = DraftBinding(identifier="my-post", inner_kind=INNER_KIND_LONG_FORM,
                                     title="From the draft")
    ed.document().setModified(False)
    return ed


def test_details_set_in_the_tab_win_over_the_drafts():
    window, ed = window_with_draft(), bound_editor()
    assert window._article_details_of(ed).summary == "Drafted"
    window._set_article_details(ed, OWN)
    assert window._article_details_of(ed) == OWN
    assert ed.document().isModified()


def test_setting_what_the_tab_has_changes_nothing():
    window, ed = window_with_draft(), bound_editor()
    window._set_article_details(ed, window._article_details_of(ed))
    assert getattr(ed, "_article_details", None) is None
    assert not ed.document().isModified()


def test_saving_the_draft_again_takes_the_tabs_title_and_details():
    from main_window import MainWindow
    window, ed = window_with_draft(), bound_editor()
    window._set_article_details(ed, OWN)
    sent = []
    window._profile_store = types.SimpleNamespace(default=lambda: types.SimpleNamespace(
        user_pubkey=PK))
    window._confirm_images_uploaded = lambda _ed: True
    window._publish_text = lambda _ed, _flavor: "The text."
    window._build_inner_for_choice = lambda profile, choice, content, details: (
        MainWindow._build_inner_for_choice(window, profile, choice, content, details=details))
    window._fire_draft_publish_job = lambda _ed, _p, choice, inner: sent.append((choice, inner))
    MainWindow._restash_with_binding(window, ed, ed._draft_binding)
    choice, inner = sent[0]
    assert choice.title == "Set in the tab" and choice.summary == "Own summary"
    tags = inner["tags"]
    assert ["image", "https://cdn.example/cover.jpg"] in tags and ["t", "nostr"] in tags


def test_a_saved_article_draft_holds_the_details_from_then_on():
    from main_window import MainWindow
    from nostr.ui.stash_kind_dialog import StashChoice, StashKind
    window, ed = window_with_draft(), bound_editor()
    window._set_article_details(ed, OWN)
    window._editor_widget_index = lambda _ed: 0
    window._profile_store = types.SimpleNamespace(default=lambda: None)
    window._update_tab_title = lambda: None
    window._draft_store.upsert_from_inner = lambda **_kw: None
    MainWindow._on_draft_stashed(
        window, ed, StashChoice(kind=StashKind.ARTICLE, identifier="my-post",
                                title="Set in the tab", summary="Own summary"),
        "e" * 64, 1, {})
    assert ed._article_details is None


def test_the_publish_window_hands_back_only_what_changed_in_it(monkeypatch):
    from nostr.ui import publish_article_dialog as module
    dialog = module.PublishArticleDialog.__new__(module.PublishArticleDialog)
    fields = {}

    class Field:
        def __init__(self, text=""):
            self._text = text

        def text(self):
            return self._text

        def setText(self, text):
            self._text = text

    for name in ("_slug_edit", "_title_edit", "_summary_edit", "_image_edit", "_tags_edit"):
        fields[name] = Field()
        setattr(dialog, name, fields[name])
    dialog._details = ArticleDetails(identifier="my-post", title="T", hashtags=("a",))
    dialog._published_at_used = None
    fields["_slug_edit"].setText("my-post")
    fields["_title_edit"].setText("T")
    fields["_tags_edit"].setText("a")
    dialog._details_shown = dialog.details()
    assert not dialog.details_changed()
    fields["_tags_edit"].setText("#A, b,  b ,")
    assert dialog.details_changed()
    assert dialog.details().hashtags == ("A", "b")                # the case written
    dialog._tags_edit.setText("a")
    dialog._published_at_used = 1_700_000_000
    assert dialog.details_changed() and dialog.details().published_at == 1_700_000_000


def test_a_restart_and_a_crash_keep_them():
    ed = HtmlEditor()
    ed._article_details = OWN
    identity = recovery.tab_identity(ed)
    assert identity["details"] == OWN.tags()
    raw = {"kind": workspace.DOCUMENT, "details": identity["details"]}
    assert workspace._parse_tab(raw).details == OWN.tags()
    assert workspace._parse_tab({**raw, "details": [["d", 1]]}).details is None
    again = HtmlEditor()
    workspace_restore.restore_identity(again, None, None, "", identity["details"],
                                       draft_type=dict)
    assert again._article_details == OWN
    broken = HtmlEditor()
    workspace_restore.restore_identity(broken, None, None, "", [["d", 1]], draft_type=dict)
    assert getattr(broken, "_article_details", None) is None


def test_hashtags_are_lowercase_without_the_hash_each_once():
    assert hashtags_from("Bitcoin, #nostr, ,  NOSTR , art") == ("Bitcoin", "nostr", "art")
    assert hashtags_from("") == ()


def test_a_restart_and_a_crash_keep_a_files_front_matter_as_written():
    import front_matter
    written = "---\ntitle: A # mine\nparams:\n  toc: true\n---\n\n"
    entries, _body = front_matter.split(written + "Text")
    ed = HtmlEditor()
    ed._front_matter = entries
    identity = recovery.tab_identity(ed)
    assert identity["front_matter"] == written                     # every byte of it
    raw = {"kind": workspace.DOCUMENT, "front_matter": identity["front_matter"]}
    assert workspace._parse_tab(raw).front_matter == identity["front_matter"]
    again = HtmlEditor()
    workspace_restore.restore_identity(again, None, None, "", None, identity["front_matter"],
                                       draft_type=dict)
    assert again._front_matter == entries
