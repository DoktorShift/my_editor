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
  nothing. Saved as a draft, the tab's model is what was saved, and
  Article Details goes on showing it (review H3).

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
    stand_in._author_of = lambda profile: MainWindow._author_of(stand_in, profile)
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


STASH_SCRIPT = r"""
import json, os, sys, types
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from main_window import StashChoice, StashKind
from nostr.article_details import ArticleDetails
from nostr.drafts import build_inner_event
w = main_window.MainWindow()
w.show()
w.nostr_state._active = True
w._update_nostr_commands()
PK = "a1" * 32
w._profile_store.default = lambda: types.SimpleNamespace(user_pubkey=PK)
ed = w.new_tab()
ed.textCursor().insertText("Body of the article.")
w.act_article_details.setChecked(True)
mine = ArticleDetails(title="My Title", summary="My summary", image="https://cdn.example/c.jpg",
                      hashtags=("Bitcoin",), coauthors=(("b2" * 32, "wss://r.example"),),
                      zaps=(("zap", PK, "", "1"), ("zap", "b2" * 32, "wss://r.example", "1")))
w._set_article_details(ed, mine)
choice = StashChoice(kind=StashKind.ARTICLE, identifier="my-title", title="My Title",
                     summary="My summary")
inner = build_inner_event(kind=30023, content="Body of the article.", pubkey_hex=PK,
                          tags=mine.with_changes(identifier="my-title").draft_tags("Body"))
w._on_draft_stashed(ed, choice, "e" * 64, 1_800_000_000, inner)
panel = w._article_details_panel
r = {"summary": panel.summary_edit.toPlainText(), "cover": panel.cover.text(),
     "tags": panel.tags_edit.text(), "rows": panel.coauthor_rows.count(),
     "zaps": panel.share_zaps.isChecked(), "fixed": panel.identifier_edit.isReadOnly()}
panel.title_edit.setText("My Title!")
d = w._article_details_of(ed)
r["after"] = [d.title, d.summary, d.image, list(d.hashtags), len(d.coauthors), len(d.zaps)]
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_after_save_as_draft_article_details_shows_and_keeps_what_was_saved(tmp_path):
    from tests.app_process import run_window_script
    r = run_window_script(STASH_SCRIPT, tmp_path)
    # As typed: the hashtags keep their case (the draft's t tags are lower case).
    assert (r["summary"], r["cover"], r["tags"]) == (
        "My summary", "https://cdn.example/c.jpg", "Bitcoin")
    assert r["rows"] == 1 and r["zaps"] is True and r["fixed"] is True
    assert r["after"] == ["My Title!", "My summary", "https://cdn.example/c.jpg",
                          ["Bitcoin"], 1, 2]


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


FIT_SCRIPT = r"""
import json, os, sys, types
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication, QWidget
app = QApplication(sys.argv[:1])
import i18n
i18n.install("de")
import main_window
from nostr.known_people import Person

w = main_window.MainWindow()
w.resize(1024, 640)
w.show()
w.nostr_state._active = True
w._update_nostr_commands()
w._profile_store.default = lambda: types.SimpleNamespace(
    user_pubkey="a1" * 32, display_name="Me", npub_short=lambda: "npub1me")
ed = w.new_tab()
w.act_article_details.setChecked(True)
app.processEvents()
panel = w._article_details_panel
panel._add_coauthor(Person(pubkey="b2" * 32, display_name="Bob"))
app.processEvents()
r = {}
for width in (260, 300):
    sizes = w._central_splitter.sizes()
    index = w._central_splitter.indexOf(panel)
    sizes[0] += sizes[index] - width
    sizes[index] = width
    w._central_splitter.setSizes(sizes)
    app.processEvents()
    cover = panel.cover
    body = panel.findChild(QWidget, "article_details_body")
    scroll = panel.findChild(QWidget, "article_details_scroll")
    parts = [child for child in cover.findChildren(QWidget) if child.isVisible()]
    overlaps = [(a.objectName() or type(a).__name__, b.objectName() or type(b).__name__)
                for i, a in enumerate(parts) for b in parts[i + 1:]
                if a.geometry().translated(a.parentWidget().mapTo(cover, a.pos()) - a.pos())
                   .intersects(b.geometry().translated(b.parentWidget().mapTo(cover, b.pos()) - b.pos()))]
    r[width] = {
        "panel": panel.width(),
        "with store": cover.pick_button is not None,
        "field": cover.url_edit.width(),
        "button": cover.pick_button.width() >= cover.pick_button.sizeHint().width(),
        "cover fits": cover.height() >= cover.heightForWidth(cover.width()),
        "body fits": body.height() >= body.layout().heightForWidth(body.width()),
        "scrolls": scroll.verticalScrollBar().isVisible(),
        "overlaps": overlaps,
    }
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_article_details_fits_in_german_and_scrolls_in_a_small_window(tmp_path):
    """Review M5: at its narrowest, in German, with the media library (the
    Choose or upload button), nothing is cut or squeezed over another
    control; in a window of 1024x640 the details scroll."""
    from tests.app_process import run_window_script
    r = run_window_script(FIT_SCRIPT, tmp_path)
    for width in ("260", "300"):
        shown = r[width]
        assert shown["panel"] == int(width) and shown["with store"] is True
        assert shown["field"] >= 180                      # an address can be read
        assert shown["button"] and shown["cover fits"] and shown["body fits"]
        assert shown["scrolls"] is True and shown["overlaps"] == []
