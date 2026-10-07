# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Published view and Draft Relays in the document window.

What must hold: the Drafts panel has Drafts and Published; the Published
view reads the relays only once it is shown, for the account in use;
Draft Relays… in the drafts' action menu opens the sheet on the window
for that account (with the relays a membership adds), and a save says
in the status bar where drafts go now; Edit on an article opens it in a
tab bound to it (its text, title, summary, cover, hashtags and the date
it first went out, so publishing it again replaces it, and saving it as
a draft offers the same article), once (a second Edit brings that tab
forward, unless it was saved as a draft of another article since), and
the tab keeps that through a restart for an update; a note opens no tab; the view's status sentences reach the status bar; the
view follows the theme; every control of the panel has a name.

The window is built in a child process with its own home folder
(tests/app_process.py); no relay is asked: the list of draft relays is a
stand-in, and the Published view's reading is recorded, not done.
"""

from tests.app_process import run_window_script

SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
app = QApplication(sys.argv[:1])
import main_window
import workspace_restore
from nostr import crypto, events
from nostr.article_details import ArticleDetails
from nostr.draft_relays import MoveOutcome, SaveOutcome
from nostr.outbox.writer import WRITTEN, WriteOutcome
from nostr.profiles import Profile
from nostr.published import PublishedItem
from nostr.ui.draft_relays_sheet import DraftRelaysSheet
from tests.accessibility import unnamed_controls


class Lists:
    # Stands in for PrivateDraftRelays: no relay is asked.
    made = []

    def __init__(self, **deps):
        Lists.made.append(deps)
        self.last_failure = None

    def read(self, on_done):
        on_done([])

    def save(self, relays, on_done, *, on_step=None, leave_unread=False):
        on_done(SaveOutcome(MoveOutcome(), WriteOutcome(WRITTEN)))

    def setParent(self, parent):
        self.parent = parent


main_window.PrivateDraftRelays = Lists
w = main_window.MainWindow()
w.show()
app.processEvents()
panel, view = w._drafts_panel, w._published_view
r = {"views": panel.views()}
read_for = []
view.model.set_account = lambda profile: read_for.append(
    profile.user_pubkey if profile is not None else None)

sk = crypto.generate_secret_key()
pk = events.sign_event({"kind": 1, "content": "", "tags": [], "created_at": 1}, sk)["pubkey"]
alice = Profile(user_pubkey=pk, bunker_pubkey="b" * 64, bunker_relays=[],
                local_secret_hex="c" * 64, display_name="Alice")
w._profile_store.upsert(alice)
w._profile_store.set_default(pk)
panel.set_active_profile(alice)
w._follow_account_in_published()
r["read_while_hidden"] = list(read_for)
w._show_drafts_panel()
app.processEvents()
r["read_with_drafts_shown"] = list(read_for)
panel.show_view("published")
app.processEvents()
r["read_once_shown"] = list(read_for)
r["unnamed"] = unnamed_controls(panel)

# Draft Relays...
panel._draft_relays_action.trigger()
app.processEvents()
sheets = [s for s in w.findChildren(DraftRelaysSheet) if s.isVisible()]
r["sheets"] = len(sheets)
if sheets:
    sheet = sheets[0]
    deps = Lists.made[0]
    r["sheet_modal"] = sheet.windowModality() == Qt.WindowModality.WindowModal
    r["sheet_account"] = deps["profile"].user_pubkey == pk
    r["sheet_entitled"] = deps["entitled_relays"] == w._entitled_relays
    sheet._field.setText("wss://drafts.example")
    sheet._add.click()
    sheet.buttons["save"].click()
    app.processEvents()
    r["sheet_closed"] = not isValid(sheet) or not sheet.isVisible()   # and deleted
    r["status_after_save"] = w.statusBar().currentMessage()

# Edit on an article
article = events.sign_event({
    "kind": 30023, "created_at": 1_800_000_000, "content": "# Hello\n\nThe body.",
    "tags": [["d", "my-post"], ["title", "My Post"], ["summary", "In short"],
             ["image", "https://img.example/c.png"], ["t", "nostr"],
             ["published_at", "1700000000"]]}, sk)
item = PublishedItem(event=article, found_on=("wss://r.example",))
tabs = w.tabs.count()
view.open_requested.emit(item)
app.processEvents()
ed = w.current_editor()
details = w._article_details_of(ed)
r["tabs_added"] = w.tabs.count() - tabs
r["tab_text"] = w.tabs.tabText(w.tabs.currentIndex())
r["details"] = [details.identifier, details.title, details.summary, details.image,
                list(details.hashtags), details.published_at]
r["markdown"] = w._publish_text(ed, "markdown")
r["stash_defaults"] = list(w._stash_defaults_for(ed, None)[:3])
r["kept_for_restart"] = ArticleDetails.from_tags(
    workspace_restore.capture_editor_tab(ed).article) == details
index = w.tabs.currentIndex()
w.tabs.setCurrentIndex(0)
view.open_requested.emit(item)
app.processEvents()
r["second_edit"] = [w.tabs.count() - tabs, w.tabs.currentIndex() == index]
# Saved as a draft under another identifier, the tab is that other article.
ed._draft_binding = main_window.DraftBinding(identifier="another-post", inner_kind=30023,
                                             title="Another post")
view.open_requested.emit(item)
app.processEvents()
r["after_saved_as_another"] = w.tabs.count() - tabs
note = events.sign_event({"kind": 1, "created_at": 1_800_000_100, "content": "hi",
                          "tags": []}, sk)
count = w.tabs.count()
view.open_requested.emit(PublishedItem(event=note))
r["note_tabs"] = w.tabs.count() - count

view.status_message.emit("Asked 3 relays to delete it.")
r["status_from_view"] = w.statusBar().currentMessage()
w._set_theme(not w.is_dark_theme, announce=False)
r["view_follows_theme"] = view.is_dark == w.is_dark_theme

for i in range(w.tabs.count()):
    e = w._editor_from_widget(w.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_the_published_view_and_draft_relays_in_the_window(tmp_path):
    r = run_window_script(SCRIPT, tmp_path)
    assert r["views"] == ["drafts", "published"]
    # The Published view reads the relays once it is shown, not before.
    assert r["read_while_hidden"] == [] and r["read_with_drafts_shown"] == []
    assert len(r["read_once_shown"]) == 1 and r["read_once_shown"][0]
    assert r["unnamed"] == []
    # Draft Relays...: a sheet on the window, for the account in use.
    assert r["sheets"] == 1 and r["sheet_modal"]
    assert r["sheet_account"] and r["sheet_entitled"]
    assert r["sheet_closed"]
    assert r["status_after_save"] == "Drafts are now saved only on the relay you chose."
    # Edit: a tab bound to the article.
    assert r["tabs_added"] == 1 and r["tab_text"] == "My Post"
    assert r["details"] == ["my-post", "My Post", "In short", "https://img.example/c.png",
                            ["nostr"], 1700000000]
    assert r["markdown"].startswith("# Hello") and "The body." in r["markdown"]
    assert r["stash_defaults"] == ["My Post", "my-post", "In short"]
    assert r["kept_for_restart"]
    assert r["second_edit"] == [1, True]
    assert r["after_saved_as_another"] == 2
    assert r["note_tabs"] == 0
    assert r["status_from_view"] == "Asked 3 relays to delete it."
    assert r["view_follows_theme"]
