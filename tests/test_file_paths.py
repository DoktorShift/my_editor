# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One spelling for each file, however it arrived.

What must hold:

  A path is kept absolute, with the system's own separators: one with
  forward slashes (as Qt's dialogs and drops give them on Windows), with
  ``..`` steps, or relative to the current folder, takes the one
  spelling.

  Two spellings of one file are the same file, and so is another case
  of its name exactly where the disk ignores case; two files are not.

  A file already open under another spelling is not opened again: its
  tab becomes current and says so.

  Save As keeps the one spelling, whatever the dialog answered.

  The recent files list holds each file once, however it was spelled.

  Renaming a file to another case of its own name renames it; a name
  another file has is refused.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import file_paths  # noqa: E402
import main_window as main_window_module  # noqa: E402
import recent_files  # noqa: E402
from main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def note(tmp_path):
    (tmp_path / "sub").mkdir()
    path = tmp_path / "note.md"
    path.write_text("text", encoding="utf-8")
    return path


def spellings(note, monkeypatch):
    """Other ways the same file reaches the app."""
    folder = note.parent
    monkeypatch.chdir(folder)
    return [
        note.as_posix(),                                  # Qt's spelling on Windows
        str(folder / "sub" / ".." / note.name),           # with a step back
        (folder / "sub").as_posix() + "/../" + note.name,  # both at once
        note.name,                                        # relative to the current folder
    ]


# -- the spelling ---------------------------------------------------------------------

def test_every_spelling_of_a_file_becomes_the_one_spelling(note, monkeypatch):
    for spelled in spellings(note, monkeypatch):
        assert file_paths.normalize(spelled) == str(note), spelled


def test_the_one_spelling_stays_as_it_is(note):
    assert file_paths.normalize(str(note)) == str(note)
    assert file_paths.normalize("") == ""


def test_spellings_of_one_file_are_the_same_file(note, monkeypatch):
    for spelled in spellings(note, monkeypatch):
        assert file_paths.same_file(spelled, str(note)), spelled


def test_two_files_are_not_the_same_file(note, tmp_path):
    other = tmp_path / "other.md"
    other.write_text("text", encoding="utf-8")
    assert not file_paths.same_file(str(note), str(other))
    assert not file_paths.same_file(str(note), str(tmp_path / "missing.md"))
    assert not file_paths.same_file(str(note), None)
    assert not file_paths.same_file(None, None)


def test_another_case_is_the_same_file_exactly_where_the_disk_ignores_case(note):
    other_case = str(note.parent / note.name.upper())
    disk_ignores_case = os.path.exists(other_case)
    assert file_paths.same_file(str(note), other_case) == disk_ignores_case


# -- opening ------------------------------------------------------------------------------

class _Tabs:
    def __init__(self, paths):
        self.paths = list(paths)
        self.current = None

    def count(self):
        return len(self.paths)

    def widget(self, index):
        return self.paths[index]

    def setCurrentIndex(self, index):
        self.current = index


class _OpenWindow:
    """The slice of MainWindow that finds a file already open."""

    open_path = MainWindow.open_path

    def __init__(self, paths):
        self.tabs = _Tabs(paths)
        self.already_open = 0

    def _tab_file_path(self, widget):
        return widget

    def _bar_from_widget(self, widget):
        return SimpleNamespace(show_already_open=self._said_already_open)

    def _said_already_open(self):
        self.already_open += 1


def test_a_file_open_under_another_spelling_is_not_opened_again(note, tmp_path, monkeypatch):
    for spelled in spellings(note, monkeypatch):
        window = _OpenWindow([str(tmp_path / "other.md"), str(note)])
        assert window.open_path(spelled) is None, spelled
        assert window.tabs.current == 1
        assert window.already_open == 1


# -- saving -------------------------------------------------------------------------------

class _SaveAsWindow:
    """The slice of MainWindow that Save As uses."""

    save_as = MainWindow.save_as

    def __init__(self):
        self.saved, self.watched, self.current = [], [], []
        self._watcher = SimpleNamespace(addPath=self.watched.append,
                                        removePath=lambda path: None)

    def current_editor(self):
        return None

    def current_path(self):
        return None

    def _save_to(self, path):
        self.saved.append(path)
        return True

    def set_current_path(self, path):
        self.current.append(path)

    def _populate_recent_menu(self):
        pass

    def _update_knit_actions(self):
        pass


def test_save_as_keeps_the_one_spelling_whatever_the_dialog_answers(note, monkeypatch):
    answered = (note.parent / "sub").as_posix() + "/../saved.md"
    monkeypatch.setattr(main_window_module, "QFileDialog", SimpleNamespace(
        getSaveFileName=lambda *a, **k: (answered, ".md (*.md)")))
    remembered = []
    monkeypatch.setattr(main_window_module, "add_recent", remembered.append)
    window = _SaveAsWindow()

    assert window.save_as()

    expected = str(note.parent / "saved.md")
    assert window.saved == window.watched == window.current == remembered == [expected]


# -- the recent files ---------------------------------------------------------------------

@pytest.fixture
def recent(tmp_path, monkeypatch):
    path = tmp_path / "recent.json"
    monkeypatch.setattr(recent_files, "_RECENT_FILE", str(path))
    return path


def test_the_recent_files_hold_each_file_once_however_it_was_spelled(recent, note, monkeypatch):
    other = str(note.parent / "other.md")
    recent.write_text(json.dumps([note.as_posix(), other, str(note)]), encoding="utf-8")
    assert recent_files.load_recent() == [str(note), other]

    for spelled in spellings(note, monkeypatch):
        recent_files.add_recent(spelled)
        assert recent_files.load_recent() == [str(note), other], spelled

    recent_files.add_recent(other)
    assert recent_files.load_recent() == [other, str(note)]


# -- renaming -----------------------------------------------------------------------------

class _RenameWindow:
    """The slice of MainWindow that renames a file."""

    _rename_tab = MainWindow._rename_tab

    def __init__(self):
        self.titles = []
        self.tabs = SimpleNamespace(setTabText=lambda index, text: self.titles.append(text))
        self._watcher = SimpleNamespace(addPath=lambda path: None,
                                        removePath=lambda path: None)

    def _update_window_title(self):
        pass


def _rename(monkeypatch, path, new_name):
    told = []
    monkeypatch.setattr(main_window_module, "inform",
                        lambda parent, **kw: told.append(kw.get("title", "")))
    monkeypatch.setattr(main_window_module.QInputDialog, "getText",
                        lambda *a, **k: (new_name, True))
    moved = []
    ed = SimpleNamespace(_file_path=str(path),
                         _backup=SimpleNamespace(update_file_path=moved.append))
    window = _RenameWindow()
    window._rename_tab(0, ed)
    return ed, window, told, moved


def test_another_case_of_its_own_name_renames_the_file(note, monkeypatch):
    ed, window, told, moved = _rename(monkeypatch, note, "Note.md")
    renamed = str(note.parent / "Note.md")
    assert told == []
    assert "Note.md" in os.listdir(note.parent) and "note.md" not in os.listdir(note.parent)
    assert ed._file_path == renamed and moved == [renamed]
    assert window.titles == ["Note.md"]


def test_a_name_another_file_has_is_refused(note, monkeypatch):
    (note.parent / "taken.md").write_text("another file", encoding="utf-8")
    ed, window, told, moved = _rename(monkeypatch, note, "taken.md")
    assert told == ["“taken.md” already exists"]
    assert ed._file_path == str(note) and moved == []
    assert (note.parent / "taken.md").read_text(encoding="utf-8") == "another file"
