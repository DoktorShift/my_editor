# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every file a folder holds opens in the main window, in the tab its kind
has: notes, text and scripts in the editor, PDFs in the PDF reader, and
pictures, films, sound, office files and anything else in a file viewer
(viewer_tabs.py). A viewer's tab behaves as the PDF reader's does: it is
named by its file, has nothing to save, offers Copy only, shows its file
again when another app saves it, and lets go of what it holds when it
closes. It comes back after an update restart as it was."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import workspace  # noqa: E402
import workspace_restore  # noqa: E402
from editor import HtmlEditor  # noqa: E402
from tests.office_samples import make_docx, make_zip, picture_bytes  # noqa: E402
from tests.waiting import wait_until  # noqa: E402
from tests.window_fixture import close_window, make_window  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    """A main window with settings, recent files and the last session of
    its own, so nothing of the person's is read or written."""
    QApplication.instance() or QApplication([])
    w = make_window(tmp_path, monkeypatch)
    yield w
    close_window(w)


def _files(tmp_path):
    picture = tmp_path / "Holiday photo.png"
    picture.write_bytes(picture_bytes())
    letter = make_docx(tmp_path / "Letter.docx")
    archive = make_zip(tmp_path / "Archive.zip", {"a.txt": "hello"})
    script = tmp_path / "tool.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    note = tmp_path / "Note.md"
    note.write_text("# Note\n", encoding="utf-8")
    return str(picture), letter, archive, str(script), str(note)


def test_each_file_opens_in_the_tab_its_kind_has(window, tmp_path):
    from media_tabs import ImageViewerTab
    from viewer_tabs import DocumentReadingTab, FileInfoTab
    picture, letter, archive, script, note = _files(tmp_path)
    assert isinstance(window.open_path(picture), ImageViewerTab)
    assert isinstance(window.open_path(letter), DocumentReadingTab)
    assert isinstance(window.open_path(archive), FileInfoTab)
    # A script and a note are text, written in the editor as always.
    assert isinstance(window.open_path(script), HtmlEditor)
    assert isinstance(window.open_path(note), HtmlEditor)


def test_a_file_of_no_known_kind_opens_as_text_when_it_is_text(window, tmp_path):
    from viewer_tabs import FileInfoTab
    words = tmp_path / "README"
    words.write_text("Some words\n", encoding="utf-8")
    blob = tmp_path / "data.bin"
    blob.write_bytes(b"\x00\x01\x02binary")
    assert isinstance(window.open_path(str(words)), HtmlEditor)
    assert isinstance(window.open_path(str(blob)), FileInfoTab)


def test_a_viewer_tab_is_read_only_like_the_pdf_reader(window, tmp_path):
    picture, *_ = _files(tmp_path)
    viewer = window.open_path(picture)
    assert window.current_reader() is viewer
    assert window.current_file_viewer() is viewer
    assert window._tab_file_path(viewer) == picture
    assert window.windowTitle().startswith("Holiday photo.png")
    assert window.save() is True
    assert "read-only" in window.status.currentMessage()
    window._update_clipboard_commands()
    assert window.act_copy.isEnabled()
    assert not window.act_cut.isEnabled() and not window.act_paste.isEnabled()
    assert not window.act_password.isEnabled()
    # Copy copies the file, as the Finder does, and a picture's pixels
    # (once they are read: a picture is read on a worker thread).
    assert wait_until(lambda: not viewer.is_loading())
    window._edit_focused("copy")
    data = QApplication.clipboard().mimeData()
    assert [url.toLocalFile() for url in data.urls()] == [picture]
    assert data.hasImage()
    QApplication.clipboard().clear()


def test_the_find_bar_goes_away_on_a_viewer_tab(window, tmp_path):
    picture, *_ = _files(tmp_path)
    window.show()
    window.new_tab()
    window.findbar.setVisible(True)
    window.open_path(picture)
    assert not window.findbar.isVisibleTo(window)


def test_a_viewer_shows_its_file_again_when_another_app_saves_it(window, tmp_path, monkeypatch):
    picture, *_ = _files(tmp_path)
    viewer = window.open_path(picture)
    reloads = []
    monkeypatch.setattr(viewer, "schedule_reload", lambda: reloads.append(True))
    window._on_file_changed(picture)
    assert reloads == [True]


def test_closing_a_viewer_tab_lets_go_of_its_file(window, tmp_path, monkeypatch):
    picture, *_ = _files(tmp_path)
    viewer = window.open_path(picture)
    released = []
    monkeypatch.setattr(viewer, "release", lambda: released.append(True))
    assert picture in window._watcher.files()
    window.close_tab(window.tabs.indexOf(viewer))
    assert released == [True]
    assert window.tabs.indexOf(viewer) < 0
    assert picture not in window._watcher.files()


def test_a_file_already_open_is_found_not_opened_again(window, tmp_path):
    picture, *_ = _files(tmp_path)
    first = window.open_path(picture)
    count = window.tabs.count()
    assert window.open_path(picture) is None
    assert window.tabs.count() == count and window.tabs.currentWidget() is first


def test_a_viewer_tab_renames_its_file_and_stays_in_place(window, tmp_path, monkeypatch):
    import main_window
    picture, letter, *_ = _files(tmp_path)
    viewer = window.open_path(picture)
    window.open_path(letter)
    index = window.tabs.indexOf(viewer)
    monkeypatch.setattr(main_window.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Beach.png", True)))
    window._rename_viewer_tab(index, viewer)
    renamed = str(tmp_path / "Beach.png")
    assert os.path.isfile(renamed) and not os.path.exists(picture)
    assert window._tab_file_path(window.tabs.widget(index)) == renamed
    assert window.tabs.tabText(index) == "Beach.png"


def test_dropping_an_office_file_opens_it_and_a_picture_goes_into_the_note(window, tmp_path):
    from viewer_tabs import FileViewerTab
    picture, letter, *_ = _files(tmp_path)
    ed = window.new_tab()
    window._handle_dropped_urls([QUrl.fromLocalFile(letter)])
    assert isinstance(window.tabs.currentWidget(), FileViewerTab)
    window.tabs.setCurrentIndex(window._editor_widget_index(ed))
    window._handle_dropped_urls([QUrl.fromLocalFile(picture)])
    assert window.tabs.currentWidget() is not None
    assert window.current_editor() is ed
    assert "￼" in ed.toPlainText()          # the picture, in the text


def test_a_viewer_tab_is_written_down_and_comes_back_after_an_update(window, tmp_path):
    picture, letter, *_ = _files(tmp_path)
    window.open_path(picture)
    window.open_path(letter)
    capture = workspace_restore.capture_tabs(window)
    tabs, active = capture.tabs()
    files = [t for t in tabs if t.kind == workspace.FILE]
    assert [t.path for t in files] == [picture, letter]
    record = tmp_path / "workspace.json"
    assert workspace.write_workspace(workspace.Workspace(tabs=tabs, active=active), str(record))
    taken = workspace.take_workspace(str(record))
    for i in reversed(range(window.tabs.count())):
        window.close_tab(i)
    workspace_restore.resume(window, taken, draft_type=dict)
    reopened = [window._tab_file_path(window.tabs.widget(i)) for i in range(window.tabs.count())]
    assert picture in reopened and letter in reopened


def test_a_file_tab_without_a_path_is_not_read_back():
    assert workspace._parse_tab({"kind": "file"}) is None
    assert workspace._parse_tab({"kind": "file", "path": "/x.png"}).kind == workspace.FILE


SESSION_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtWidgets import QApplication
app = QApplication(sys.argv[:1])
import main_window
from tests.office_samples import make_docx, picture_bytes
main_window.MainWindow._maybe_auto_check_for_updates = lambda self: None
home = os.path.expanduser("~")
picture = os.path.join(home, "Photo.png")
with open(picture, "wb") as f:
    f.write(picture_bytes())
letter = make_docx(os.path.join(home, "Letter.docx"))
first = main_window.MainWindow()
first.open_path(picture)
first.open_path(letter)
first.close()
app.processEvents()
second = main_window.MainWindow()
r = {"paths": [second._tab_file_path(second.tabs.widget(i)) for i in range(second.tabs.count())],
     "kinds": [type(second.tabs.widget(i)).__name__ for i in range(second.tabs.count())],
     "picture": picture, "letter": letter}
second.close()
print("RESULT " + json.dumps(r))
"""


def test_viewer_tabs_come_back_with_the_last_session(tmp_path):
    from tests.app_process import run_window_script
    r = run_window_script(SESSION_SCRIPT, tmp_path)
    assert r["picture"] in r["paths"] and r["letter"] in r["paths"]
    assert "ImageViewerTab" in r["kinds"] and "DocumentReadingTab" in r["kinds"]


def test_insert_into_note_links_a_picture_where_it_lies(window, tmp_path):
    from PySide6.QtGui import QImage, QTextDocument
    folder = tmp_path / "Trip notes"
    (folder / "Holiday pictures").mkdir(parents=True)
    picture = folder / "Holiday pictures" / "On the beach.png"
    picture.write_bytes(picture_bytes(40, 30))
    note = folder / "Day one.md"
    note.write_text("# Day one\n\nWe went.\n", encoding="utf-8")
    window._library.add_root(str(folder))      # files of a sidebar folder are linked
    ed = window.open_path(str(note))
    viewer = window.open_path(str(picture))
    viewer.insert_into_note_requested.emit()
    assert window.current_editor() is ed
    shown = ed.loadResource(QTextDocument.ImageResource,
                            QUrl("Holiday pictures/On the beach.png"))
    assert isinstance(shown, QImage) and (shown.width(), shown.height()) == (40, 30)
    assert window.save()
    text = note.read_text(encoding="utf-8")
    assert "![On the beach](<Holiday pictures/On the beach.png>)" in text
    assert not (folder / "Day one_media").exists()


def test_a_file_dropped_on_a_saved_note_from_its_folder_is_linked(window, tmp_path):
    folder = tmp_path / "Project"
    (folder / "Docs").mkdir(parents=True)
    letter = make_docx(folder / "Docs" / "Cover letter.docx")
    note = folder / "Plan.md"
    note.write_text("Plan\n", encoding="utf-8")
    window._library.add_root(str(folder))
    ed = window.open_path(str(note))
    count = window.tabs.count()
    window._handle_dropped_urls([QUrl.fromLocalFile(letter)])
    assert window.tabs.count() == count
    assert window.save()
    assert "[Cover letter.docx](<Docs/Cover letter.docx>)" in note.read_text(encoding="utf-8")
    assert ed.document().isModified() is False


def test_insert_into_an_untitled_note_takes_the_picture_in_as_before(window, tmp_path):
    picture, *_ = _files(tmp_path)
    ed = window.new_tab()
    viewer = window.open_path(picture)
    viewer.insert_into_note_requested.emit()
    assert window.current_editor() is ed
    assert "￼" in ed.toPlainText()


def test_a_viewer_names_the_app_that_opens_its_file(window, tmp_path, monkeypatch):
    import default_apps
    monkeypatch.setattr(default_apps, "known_default_app", lambda path: (False, None))
    picture, *_ = _files(tmp_path)
    viewer = window.open_path(picture)
    assert wait_until(lambda: viewer._default_app == "Preview")
    assert viewer._open_btn.text() == "Open in Preview"
    assert viewer._reveal_label == default_apps.reveal_label()


def test_open_with_lists_the_default_app_first_and_other(window, tmp_path, monkeypatch):
    import default_apps
    from PySide6.QtCore import QPoint
    choices = [default_apps.AppChoice("Preview", "/Applications/Preview.app"),
               default_apps.AppChoice("Pixelmator", "/Applications/Pixelmator.app")]
    monkeypatch.setattr(default_apps, "other_apps", lambda path: choices)
    opened = []
    monkeypatch.setattr(default_apps, "open_with", lambda path, c: opened.append(c.name))
    picture, *_ = _files(tmp_path)
    viewer = window.open_path(picture)
    viewer.open_with_requested.emit(QPoint(0, 0))
    words = [a.text() for a in window._open_with_menu.actions() if not a.isSeparator()]
    assert words == ["Preview (default)", "Pixelmator", "Other…"]
    window._open_with_menu.actions()[0].trigger()
    window._open_with_menu.close()
    assert opened == ["Preview"]


def test_import_as_note_opens_the_note_it_made(window, tmp_path, monkeypatch):
    import office_preview
    _picture, letter, *_ = _files(tmp_path)
    made = str(tmp_path / "Letter.md")

    def import_as_markdown(path):
        with open(made, "w", encoding="utf-8") as f:
            f.write("Dear reader\n")
        return made

    monkeypatch.setattr(office_preview, "import_as_markdown", import_as_markdown)
    viewer = window.open_path(letter)
    viewer.import_as_note_requested.emit()
    assert wait_until(lambda: window.current_path() == made)
    assert "Dear reader" in window.current_editor().toPlainText()


def test_an_r_markdown_note_takes_a_linked_picture_as_markdown(window, tmp_path):
    folder = tmp_path / "Analysis"
    (folder / "plots").mkdir(parents=True)
    plot = folder / "plots" / "fit 1.png"
    plot.write_bytes(picture_bytes())
    note = folder / "report.Rmd"
    note.write_text("Results\n", encoding="utf-8")
    window._library.add_root(str(folder))
    window.open_path(str(note))
    viewer = window.open_path(str(plot))
    viewer.insert_into_note_requested.emit()
    assert window.save()
    assert "![fit 1](<plots/fit 1.png>)" in note.read_text(encoding="utf-8")
    assert not (folder / "report_media").exists()


def test_a_picture_beside_a_note_outside_the_library_is_taken_in(window, tmp_path):
    """Without a sidebar folder holding both, a dropped picture goes into
    the note as before (its media folder on save), never linked in place."""
    folder = tmp_path / "home"
    (folder / "Downloads").mkdir(parents=True)
    picture = folder / "Downloads" / "p.png"
    picture.write_bytes(picture_bytes())
    note = folder / "n.md"
    note.write_text("Text\n", encoding="utf-8")
    ed = window.open_path(str(note))
    window._handle_dropped_urls([QUrl.fromLocalFile(str(picture))])
    assert window.save()
    text = note.read_text(encoding="utf-8")
    assert "Downloads/p.png" not in text and "n_media/" in text
    assert ed.document().isModified() is False
