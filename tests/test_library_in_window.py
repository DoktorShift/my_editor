# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder library in the main window: the folders in the sidebar come
back from the settings, a click in the file list shows the file in a tab
that the next click reuses (until it is kept), Space shows Quick Look,
renaming from a tab goes through the library so links and tabs follow,
and the sidebar and the file list are shown and hidden from the View
menu, a key and the sidebar button."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import library_actions  # noqa: E402
import settings  # noqa: E402
from tests.accessibility import unnamed_controls  # noqa: E402
from tests.office_samples import make_docx, picture_bytes  # noqa: E402
from tests.window_fixture import close_window, make_window  # noqa: E402


def settle(rounds=5):
    for _ in range(rounds):
        QApplication.processEvents()


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "Library"
    (root / "Pictures").mkdir(parents=True)
    (root / "Pictures" / "Beach.png").write_bytes(picture_bytes())
    (root / "Trip.md").write_text("# Trip\n\n![Beach](Pictures/Beach.png)\n", encoding="utf-8")
    (root / "Plan.md").write_text("Plan\n", encoding="utf-8")
    (root / "Ideas.txt").write_text("ideas\n", encoding="utf-8")
    make_docx(root / "Letter.docx")
    return str(root)


@pytest.fixture
def window(tmp_path, monkeypatch, folder):
    QApplication.instance() or QApplication([])
    w = make_window(tmp_path, monkeypatch, settings_values={"library_folders": [folder]})
    yield w
    if w._library_window.quick_look is not None:
        w._library_window.quick_look.close()
    close_window(w)


def test_the_folders_come_back_from_the_settings_and_are_kept(window, folder, tmp_path):
    assert window._library.roots() == [os.path.abspath(folder)]
    assert window._library_roots() == (os.path.abspath(folder),)
    other = tmp_path / "Projects"
    other.mkdir()
    assert window._library_window.add_root(str(other))
    assert settings.load_settings()["library_folders"] == [os.path.abspath(folder),
                                                           os.path.abspath(other)]


def test_with_no_folder_the_sidebar_invites_to_add_one(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    w = make_window(tmp_path, monkeypatch)
    try:
        assert w._side_panels.is_open("sidebar")
        assert not w._side_panels.is_open("file_list")
        assert w._library_sidebar.stack.currentWidget() is w._library_sidebar.empty
        assert w.act_window_sidebar.text() == "Hide Sidebar"
    finally:
        close_window(w)


def test_the_panes_sit_left_of_the_text_in_their_order(window):
    splitter = window._central_splitter
    order = [splitter.widget(i) for i in range(splitter.count())]
    assert order[:3] == [window._library_sidebar, window._file_list, window._editor_side]
    assert window._side_panels.is_open("file_list")


def test_a_click_shows_the_file_in_a_tab_the_next_click_reuses(window, folder):
    count = window.tabs.count()
    window._library_window.show_from_list(os.path.join(folder, "Plan.md"))
    assert window.tabs.count() == count + 1
    shown = window.tabs.currentWidget()
    window._library_window.show_from_list(os.path.join(folder, "Ideas.txt"))
    assert window.tabs.count() == count + 1
    assert window.tabs.indexOf(shown) < 0
    assert window.current_path() == os.path.join(folder, "Ideas.txt")


def test_a_double_click_keeps_the_tab_and_the_next_click_opens_another(window, folder):
    count = window.tabs.count()
    window._library_window.show_from_list(os.path.join(folder, "Plan.md"))
    window._library_window.open_from_list(os.path.join(folder, "Plan.md"))
    window._library_window.show_from_list(os.path.join(folder, "Ideas.txt"))
    assert window.tabs.count() == count + 2


def test_the_first_edit_keeps_the_tab(window, folder):
    count = window.tabs.count()
    window._library_window.show_from_list(os.path.join(folder, "Plan.md"))
    window.current_editor().insertPlainText("more ")
    window._library_window.show_from_list(os.path.join(folder, "Ideas.txt"))
    assert window.tabs.count() == count + 2


def test_a_file_myeditor_only_shows_opens_in_its_app_on_double_click(window, folder,
                                                                    monkeypatch):
    import default_apps
    opened = []
    monkeypatch.setattr(default_apps, "open_with_default", lambda path: opened.append(path)
                        or True)
    letter = os.path.join(folder, "Letter.docx")
    window._library_window.open_from_list(letter)
    assert opened == [letter]
    window._library_window.open_from_list(os.path.join(folder, "Plan.md"))
    assert opened == [letter] and window.current_path() == os.path.join(folder, "Plan.md")


def test_space_shows_quick_look_and_the_arrows_move_it(window, folder):
    count = window.tabs.count()
    window._library_window.toggle_quick_look(os.path.join(folder, "Letter.docx"))
    panel = window._library_window.quick_look
    assert panel.isVisible() and panel.path() == os.path.join(folder, "Letter.docx")
    window._library_window.show_from_list(os.path.join(folder, "Plan.md"))     # the arrow keys
    assert panel.path() == os.path.join(folder, "Plan.md")
    assert window.tabs.count() == count                         # no tab meanwhile
    assert panel.content() is not None
    window._library_window.toggle_quick_look(os.path.join(folder, "Plan.md"))  # Space again
    assert not panel.isVisible()


def test_the_list_reaches_quick_look_with_space(window, folder):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    files = window._file_list
    files.show_folder(folder)
    settle()
    assert files.select_path(os.path.join(folder, "Plan.md"))
    files.view.setCurrentIndex(files.model_.index(files.model_.row_of(
        os.path.join(folder, "Plan.md"))))
    files._key(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier))
    panel = window._library_window.quick_look
    assert panel is not None and panel.isVisible()


def test_renaming_from_a_tab_updates_the_links_and_the_tabs(window, folder, monkeypatch):
    picture = os.path.join(folder, "Pictures", "Beach.png")
    note = os.path.join(folder, "Trip.md")
    viewer = window.open_path(picture)
    window.open_path(note)
    monkeypatch.setattr(library_actions, "ask_name", lambda *a, **k: "Sea.png")
    window._library_file_action(viewer, picture, rename=True)
    # The rename takes its turn after the library's earlier operations,
    # and the notes follow on the library's worker thread.
    window._library.wait_for_links()
    renamed = os.path.join(folder, "Pictures", "Sea.png")
    assert os.path.isfile(renamed)
    paths = [window._tab_file_path(window.tabs.widget(i)) for i in range(window.tabs.count())]
    assert renamed in paths and picture not in paths
    assert "Pictures/Sea.png" in open(note, encoding="utf-8").read()


def test_the_links_follow_on_a_worker_thread_and_the_status_bar_says_so(window, folder,
                                                                         monkeypatch):
    import threading
    import link_rewrite
    threads = []
    real = link_rewrite.plan_rewrite

    def watched(*args, **kwargs):
        threads.append(threading.current_thread())
        return real(*args, **kwargs)

    monkeypatch.setattr(link_rewrite, "plan_rewrite", watched)
    note = os.path.join(folder, "Trip.md")
    ed = window.open_path(note)
    picture = os.path.join(folder, "Pictures", "Beach.png")
    monkeypatch.setattr(library_actions, "ask_name", lambda *a, **k: "Sea.png")
    library_actions.rename(window, window._library, picture, window._library_window.unsaved_notes,
                           say=window._library_window.says)
    window._library.wait_for_links()
    assert threads and threads[0] is not threading.main_thread()
    assert "Pictures/Sea.png" in open(note, encoding="utf-8").read()
    assert not ed.document().isModified()
    assert window.status.currentMessage() == "Updated links in 1 note."


def test_an_open_note_moved_in_the_library_follows_with_its_changes(window, folder):
    note = os.path.join(folder, "Plan.md")
    ed = window.open_path(note)
    ed.insertPlainText("typed ")
    window._library.move([note], os.path.join(folder, "Pictures"))
    window._library.wait_for_links()
    assert ed._file_path == os.path.join(folder, "Pictures", "Plan.md")
    assert ed.document().isModified()


def test_a_file_put_in_the_trash_closes_its_viewer(window, folder, monkeypatch):
    letter = os.path.join(folder, "Letter.docx")
    viewer = window.open_path(letter)
    window._library_window.close_trashed_tabs([letter])
    assert window.tabs.indexOf(viewer) < 0


def test_the_sidebar_and_the_file_list_are_shown_and_hidden_by_command(window):
    panels = window._side_panels
    window.act_window_sidebar.trigger()
    assert not panels.is_open("sidebar") and not window._sidebar_toggle.isChecked()
    assert window.act_window_sidebar.text() == "Show Sidebar"
    window._sidebar_toggle.click()
    assert panels.is_open("sidebar") and window._sidebar_toggle.isChecked()
    window.act_file_list.trigger()
    assert not panels.is_open("file_list") and window.act_file_list.text() == "Show File List"
    assert settings.load_settings()["file_list_shown"] is False


def test_the_sidebar_button_is_there_on_every_kind_of_tab(window, folder):
    window.show()
    window.open_path(os.path.join(folder, "Letter.docx"))
    settle()
    assert window._sidebar_toggle.isVisible()
    assert not window.format_bar.isVisible()


def test_the_window_with_its_panes_names_every_control(window):
    window.show()
    settle()
    assert unnamed_controls(window) == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes")
def test_a_pipe_named_like_a_note_is_not_read_and_the_window_says_so(window, folder,
                                                                     monkeypatch):
    import threading
    import main_window
    told = []
    monkeypatch.setattr(main_window, "inform", lambda parent, **kwargs: told.append(kwargs))
    pipe = os.path.join(folder, "Pipe.md")
    os.mkfifo(pipe)

    def writer_after_a_while():
        # Were the pipe read, this ends the read instead of hanging the test.
        threading.Event().wait(3)
        try:
            os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass                                # nobody reads it: as it should be

    threading.Thread(target=writer_after_a_while, daemon=True).start()
    count = window.tabs.count()
    assert window.open_path(pipe) is None
    assert window.tabs.count() == count
    assert told and told[0]["message"] == "This item is not a file MyEditor can read."


def test_looking_through_the_list_leaves_open_recent_alone(window, folder):
    import recent_files
    window._library_window.show_from_list(os.path.join(folder, "Plan.md"))
    window._library_window.show_from_list(os.path.join(folder, "Ideas.txt"))
    assert recent_files.load_recent() == []
    window._library_window.open_from_list(os.path.join(folder, "Ideas.txt"))      # kept: opened now
    assert recent_files.load_recent() == [os.path.join(folder, "Ideas.txt")]


def test_the_watcher_holds_exactly_the_open_files_after_a_move(window, folder):
    note = os.path.join(folder, "Plan.md")
    window.open_path(note)
    window._library.move([note], os.path.join(folder, "Pictures"))
    window._library.wait_for_links()
    settle()
    watched = set(window._watcher.files())
    assert note not in watched
    assert os.path.join(folder, "Pictures", "Plan.md") in watched


def test_moving_a_folder_of_open_files_keeps_the_watcher_exact(window, folder):
    sub = os.path.join(folder, "Sub")
    os.makedirs(sub)
    paths = []
    for name, data in (("Note.md", b"# N\n"), ("Pic.png", picture_bytes()),
                       ("Text.txt", b"plain\n")):
        path = os.path.join(sub, name)
        with open(path, "wb") as f:
            f.write(data)
        window.open_path(path)
        paths.append(path)
    dest = os.path.join(folder, "Dest")
    os.makedirs(dest)
    window._library.move([sub], dest)
    window._library.wait_for_links()
    settle()
    watched = {os.path.normpath(p) for p in window._watcher.files()}
    open_files = {os.path.normpath(window._tab_file_path(window.tabs.widget(i)))
                  for i in range(window.tabs.count())
                  if window._tab_file_path(window.tabs.widget(i))}
    assert watched == open_files
    assert all(p.startswith(os.path.join(dest, "Sub")) for p in watched)


def test_a_viewer_follows_a_rename_in_place_and_stays_the_preview(window, folder):
    letter = os.path.join(folder, "Letter.docx")
    window._library_window.show_from_list(letter)
    viewer = window.tabs.currentWidget()
    window._library.rename(letter, "Reply.docx")
    window._library.wait_for_links()
    settle()
    assert window.tabs.indexOf(viewer) >= 0                 # the same tab, not reopened
    assert viewer._file_path == os.path.join(folder, "Reply.docx")
    assert window._library_window.preview_tab is viewer
    assert window.tabs.tabText(window.tabs.indexOf(viewer)) == "Reply.docx"


def test_quick_look_follows_a_rename_and_goes_with_the_trash(window, folder):
    picture = os.path.join(folder, "Pictures", "Beach.png")
    window._library_window.toggle_quick_look(picture)
    window._library.rename(picture, "Sea.png")
    window._library.wait_for_links()
    settle()
    assert window._library_window.quick_look.path() == os.path.join(folder, "Pictures", "Sea.png")
    window._library_window.close_trashed_tabs([os.path.join(folder, "Pictures", "Sea.png")])
    assert not window._library_window.quick_look.isVisible()


def test_closing_the_window_lets_go_of_the_reading_views_folders(tmp_path, monkeypatch,
                                                                  folder):
    QApplication.instance() or QApplication([])
    w = make_window(tmp_path, monkeypatch, settings_values={"library_folders": [folder]})
    view = w.open_path(os.path.join(folder, "Letter.docx"))
    temp = view._temp_root
    assert os.path.isdir(temp)
    close_window(w)
    assert not os.path.exists(temp)


def test_reading_folders_left_by_a_crash_are_swept_after_a_day(tmp_path):
    import time
    import viewer_tabs
    old = tmp_path / (viewer_tabs.TEMP_PREFIX + "old")
    young = tmp_path / (viewer_tabs.TEMP_PREFIX + "young")
    other = tmp_path / "someone-elses"
    for path in (old, young, other):
        path.mkdir()
    now = time.time()
    os.utime(old, (now - 2 * 86400, now - 2 * 86400))
    os.utime(other, (now - 2 * 86400, now - 2 * 86400))
    assert viewer_tabs.sweep_stale_temp_folders(str(tmp_path), now) == 1
    assert not old.exists() and young.exists() and other.exists()


def test_closing_while_links_update_shows_progress_and_waits(window, monkeypatch):
    import library_window
    shown = []
    pending = iter([True, True, True, False])
    monkeypatch.setattr(window._library, "links_pending", lambda: next(pending, False))
    dialog_class = library_window.QProgressDialog

    def progress(*args, **kwargs):
        dialog = dialog_class(*args, **kwargs)
        shown.append(dialog.labelText())
        return dialog

    monkeypatch.setattr(library_window, "QProgressDialog", progress)
    window._library_window.finish_link_updates()
    assert shown == ["Updating links before closing…"]


SPLIT_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
home = os.path.expanduser("~")
with open(os.path.join(home, "screen.json"), "w") as f:
    json.dump({"windowFrameMargins": False,
               "screens": [{"name": "wide", "x": 0, "y": 0, "width": 1600, "height": 1000,
                            "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}, f)
# Qt splits the platform's options at every colon, a Windows drive's too.
os.chdir(home)
os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=screen.json"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window, panel_layout, settings
from preview_pane import SPLIT, SPLIT_MIN_WIDTH, WRITE
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None
main_window.MainWindow._screen_room = lambda self: panel_layout.Rect(0, 0, 1440, 900)
folder = os.path.join(home, "Library")
os.makedirs(folder)
with open(os.path.join(folder, "Plan.md"), "w", encoding="utf-8") as f:
    f.write("Plan\n")
settings.save_setting("library_folders", [folder])

def settle():
    for _ in range(5):
        app.processEvents()

w = main_window.MainWindow()
w.setGeometry(0, 0, 1440, 800)
w.show(); settle()
ed = w.open_path(os.path.join(folder, "Plan.md")); settle()
panels = w._side_panels
r = {"window": w.width(),
     "before": [panels.is_open("sidebar"), panels.is_open("file_list"),
                ed._view_splitter.width() < SPLIT_MIN_WIDTH, w.act_view_split.isEnabled()]}
w._choose_view(SPLIT); settle()
r["split"] = [panels.is_open("file_list"), w._shown_view(ed) == SPLIT,
              ed._view_splitter.width() >= SPLIT_MIN_WIDTH]
w._choose_view(WRITE); settle()
r["write"] = [panels.is_open("file_list"), panels.is_open("sidebar")]
w._choose_view(WRITE)
ed.document().setModified(False)
w.close()
print("RESULT " + json.dumps(r))
"""


def test_split_folds_the_file_list_and_the_sidebar_as_it_needs(tmp_path):
    # A window 1440 wide, on a screen of its own that has the room: the
    # Windows runner's screen is 1024 wide, narrower than Split needs at
    # all (preview_pane.SPLIT_MIN_WIDTH), and the system keeps a window
    # within its screen.
    from tests.app_process import run_window_script
    r = run_window_script(SPLIT_SCRIPT, tmp_path)
    assert r["window"] == 1440
    sidebar, file_list, too_narrow, split_enabled = r["before"]
    assert sidebar and file_list
    assert too_narrow                                    # no room for two columns
    assert split_enabled                                 # but folding makes it
    assert r["split"] == [False, True, True]
    assert r["write"] == [True, True]


def test_find_replace_and_save_as_are_on_only_where_they_act(window, folder, tmp_path):
    from PySide6.QtGui import QPainter, QPdfWriter
    pdf = str(tmp_path / "Doc.pdf")
    writer = QPdfWriter(pdf)
    painter = QPainter(writer)
    painter.drawText(100, 100, "hello")
    painter.end()
    csv = os.path.join(folder, "list.csv")
    with open(csv, "w", encoding="utf-8") as f:
        f.write("a,b\n1,2\n")

    def state():
        window._update_editor_commands()
        return [window.act_find.isEnabled(), window.act_replace.isEnabled(),
                window.act_save_as.isEnabled()]

    window.open_path(os.path.join(folder, "Letter.docx"))
    assert state() == [False, False, False]
    window.open_path(os.path.join(folder, "Pictures", "Beach.png"))
    assert state() == [False, False, False]
    window.open_path(pdf)
    assert state() == [True, False, False]
    window.open_path(os.path.join(folder, "Plan.md"))
    assert state() == [True, True, True]
    window.open_path(csv)
    assert state() == [False, False, True]
    assert not window.commands.action("format.sheet.add").isEnabled()   # one sheet only


def test_a_text_file_shown_another_way_opens_as_text_in_its_place(window, folder):
    from editor import HtmlEditor
    from sheet_tab import SheetTab
    csv = os.path.join(folder, "list.csv")
    with open(csv, "w", encoding="utf-8") as f:
        f.write("a,b\n1,2\n")
    svg = os.path.join(folder, "logo.svg")
    with open(svg, "w", encoding="utf-8") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"/>\n')
    sheet = window.open_path(csv)
    assert isinstance(sheet, SheetTab)
    place = window.tabs.indexOf(sheet)
    window._update_editor_commands()
    assert window.act_open_as_text.isEnabled()
    window.act_open_as_text.trigger()
    shown = window.tabs.widget(place)
    assert isinstance(window._editor_from_widget(shown), HtmlEditor)
    assert window.current_editor().toPlainText().rstrip("\n") == "a,b\n1,2"
    window.open_path(svg)
    window._open_file_as_text(svg)
    assert "<svg" in window.current_editor().toPlainText()
    window._update_editor_commands()
    assert not window.act_open_as_text.isEnabled()          # it is text now
    letter = os.path.join(folder, "Letter.docx")
    window.open_path(letter)
    window._update_editor_commands()
    assert not window.act_open_as_text.isEnabled()          # a docx is no text


def test_open_with_offers_myeditor_as_text_for_text_files(window, folder, monkeypatch):
    import default_apps
    from PySide6.QtCore import QPoint
    monkeypatch.setattr(default_apps, "other_apps", lambda path: [])
    csv = os.path.join(folder, "list.csv")
    with open(csv, "w", encoding="utf-8") as f:
        f.write("a,b\n")
    window._show_open_with_menu(csv, QPoint(0, 0))
    words = [a.text() for a in window._open_with_menu.actions() if not a.isSeparator()]
    window._open_with_menu.close()
    assert words == ["MyEditor (as Text)", "Other…"]


def test_the_view_menu_starts_with_the_panes(window):
    titles = [a.objectName() for a in window.m_view.actions() if not a.isSeparator()]
    assert titles[:2] == ["view.sidebar", "view.file_list"]


def test_the_file_list_has_its_own_button_beside_the_sidebars(window):
    button = window._file_list_toggle
    assert button.accessibleName() == "File List"
    assert button.isChecked() == window._side_panels.is_open("file_list")
    button.click()
    assert not window._side_panels.is_open("file_list") and not button.isChecked()
    button.click()
    assert window._side_panels.is_open("file_list") and button.isChecked()


def test_the_file_menu_acts_on_the_lists_selection_while_it_has_the_keys(window, folder,
                                                                      monkeypatch):
    window.show()
    files = window._file_list
    files.show_folder(folder)
    settle()
    plan = os.path.join(folder, "Plan.md")
    files.select_path(plan)
    files.view.setFocus()
    settle()
    window._library_window.update_file_commands()
    assert window._library_window.file_command_targets() == [plan]
    for action in (window.act_rename_file, window.act_trash_file, window.act_quick_look,
                   window.act_reveal_file, window.act_duplicate_file, window.act_new_folder):
        assert action.isEnabled(), action.objectName()
    window.act_quick_look.trigger()
    panel = window._library_window.quick_look
    assert panel is not None and panel.path() == plan
    window._library_window.quick_look.close()
    # In a document the keys type: Move to Trash's key is not taken from it.
    ed = window.open_path(os.path.join(folder, "Ideas.txt"))
    ed.setFocus()
    settle()
    window._library_window.update_file_commands()
    assert window._library_window.file_command_targets() == [os.path.join(folder, "Ideas.txt")]
    assert not window.act_trash_file.isEnabled() and not window.act_quick_look.isEnabled()
    assert window.act_rename_file.isEnabled()


def test_new_folder_is_made_where_the_list_is_and_named_at_once(window, folder,
                                                                monkeypatch):
    window._library_window.sidebar_selection("folder", folder)
    asked = []
    monkeypatch.setattr(library_actions, "ask_name",
                        lambda *a, **k: asked.append(a[1]) or "Projects")
    window.act_new_folder.trigger()
    window._library.wait_for_links()            # renames are queued
    settle()
    assert asked and os.path.isdir(os.path.join(folder, "Projects"))


def test_the_list_shows_what_the_sidebar_chose_at_launch(window, folder):
    kind, path = window._library_sidebar.current()
    files = window._file_list
    if kind == "folder":
        assert files.current_folder() == path
    else:
        assert files.current_section() == kind and files.current_folder() is None


def test_quick_look_names_the_app_and_acts_like_a_tab(window, folder, monkeypatch):
    import default_apps
    monkeypatch.setattr(default_apps, "known_default_app", lambda path: (True, "Pages"))
    revealed = []
    monkeypatch.setattr(default_apps, "reveal", lambda path: revealed.append(path) or True)
    letter = os.path.join(folder, "Letter.docx")
    window._library_window.toggle_quick_look(letter)
    content = window._library_window.quick_look.content()
    assert content._default_app == "Pages"
    assert content._reveal_label == default_apps.reveal_label()
    content.reveal_requested.emit()
    assert revealed == [letter]
    window._library_window.quick_look.close()


def test_the_lists_menu_opens_a_text_file_shown_another_way_as_text(window, folder):
    csv = os.path.join(folder, "list.csv")
    with open(csv, "w", encoding="utf-8") as f:
        f.write("a,b\n")
    menu = window._file_list.menu_for([csv])
    entry = [a for a in menu.actions() if a.objectName() == "open_as_text"]
    assert entry
    entry[0].trigger()
    assert window.current_path() == csv and window._editor_kind(window.current_editor())
    menu = window._file_list.menu_for([os.path.join(folder, "Letter.docx")])
    assert not [a for a in menu.actions() if a.objectName() == "open_as_text"]
