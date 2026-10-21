# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder library's part of the main window.

The sidebar of folders and the file list sit at the left of the text
(library_sidebar.py, file_list.py), over the folders on disk
(library_set.py). ``LibraryWindow`` builds them into the window and does
what they ask of it: a file clicked in the list shows in the tab the last
click opened (a preview, as in the Finder and Notes), a double-click keeps
it, Space shows it in Quick Look. Tabs follow the files the library
renames, moves and puts in the Trash, and the open notes with unsaved
changes have their links follow in their text. The File menu's library
commands (New Folder, Rename, Duplicate, Move to Trash, Show in Finder,
Quick Look) act on what the list or the sidebar has selected while it
has the keys, else on the tab's file.

The window owns one LibraryWindow (``window._library_window``) and keeps
the library, the sidebar and the file list as its own too (``_library``,
``_library_sidebar``, ``_file_list``); everything about tabs stays the
window's.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from PySide6.QtCore import QEvent, QEventLoop, QObject, Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QFileDialog, QProgressDialog, QWidget

import file_kinds
import file_paths
import library_actions
import link_rewrite
import open_note_links
import regular_files
from alerts import inform
from constants import APP_DISPLAY_NAME
from file_list import FileListPane
from i18n import N_, _, ngettext
from library_set import LibrarySet
from library_sidebar import LibrarySidebar
from settings import load_settings, save_setting
from side_panels import LEFT
from viewer_tabs import FileViewerTab

# The side panels' names (side_panels.py; their widths are kept by name).
SIDEBAR_PANEL = "sidebar"
FILE_LIST_PANEL = "file_list"
# Settings keys: the folders in the sidebar (as the Finder keeps its
# locations), and whether the sidebar and the file list are shown.
LIBRARY_FOLDERS_KEY = "library_folders"
SIDEBAR_SHOWN_KEY = "sidebar_shown"
FILE_LIST_SHOWN_KEY = "file_list_shown"


class _EscapeCloses(QObject):
    """Escape in the file list puts Quick Look away while it is up, as in
    the Finder; ``panel()`` gives the panel, or None."""

    def __init__(self, panel, view: QWidget) -> None:
        super().__init__(view)
        self._panel = panel
        view.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt's name)
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            panel = self._panel()
            if panel is not None and panel.isVisible():
                panel.close()
                return True
        return False


class LibraryWindow(QObject):
    """The folders in the sidebar (restored from the settings), the
    sidebar and the file list at the left of ``window``'s text, and what
    they do there."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        # The notes follow a rename or move on a worker thread, so the
        # window answers while a large library's notes are read.
        self.library = LibrarySet(parent=window, links_in_background=True)
        stored = load_settings().get(LIBRARY_FOLDERS_KEY)
        for root in stored if isinstance(stored, list) else []:
            if isinstance(root, str) and os.path.isdir(root):
                self.library.add_root(root)
        self.library.roots_changed.connect(self._save_roots)
        self.library.moved.connect(self._follow_moves)
        self.library.rewritten.connect(self._reload_rewritten_notes)
        self.library.trashed.connect(self.close_trashed_tabs)
        self.library.links_progress.connect(self.links_progress)
        self.library.links_updated.connect(self.links_done)
        # The open notes with unsaved changes follow in their text
        # (_follow_links_in_open_notes): the library need not name them.
        self.library.open_notes_follow_in_editor = True
        sidebar = self.sidebar = LibrarySidebar(self.library, is_dark=window.is_dark_theme)
        files = self.files = FileListPane(self.library, is_dark=window.is_dark_theme)
        for pane in (sidebar, files):
            pane.unsaved_notes = self.unsaved_notes
            pane.status_message.connect(self.says)
        sidebar.selection_changed.connect(self.sidebar_selection)
        sidebar.add_folder_requested.connect(self.add_folder)
        sidebar.note_created.connect(self.open_new_note)
        files.folder_opened.connect(lambda path: sidebar.select("folder", path))
        files.file_selected.connect(self.show_from_list)
        files.file_activated.connect(self.open_from_list)
        files.quick_look_requested.connect(self.toggle_quick_look)
        files.new_note_requested.connect(self._new_note_in)
        files.insert_into_note_requested.connect(window._insert_file_into_note)
        files.import_as_note_requested.connect(window._import_as_note)
        files.open_as_text_requested.connect(window._open_file_as_text)
        # Escape in the list puts Quick Look away, as in the Finder.
        self._list_keys = _EscapeCloses(lambda: self.quick_look, files.view)
        window._side_panels.register(
            SIDEBAR_PANEL, sidebar, side=LEFT, collapse_rank=1,
            default_width=LibrarySidebar.DEFAULT_WIDTH,
            on_fold=lambda _folded: self.sync_commands())
        window._side_panels.register(
            FILE_LIST_PANEL, files, side=LEFT, collapse_rank=0,
            default_width=FileListPane.DEFAULT_WIDTH,
            on_fold=lambda _folded: self.sync_commands())
        self.preview_tab = None        # the tab the last list click opened
        # While a file clicked in the list opens: a look, not an opening
        # (the window leaves it out of Open Recent).
        self.previewing = False
        self.quick_look = None
        self._links_progress_text = ""
        # The sidebar chose its row quietly as it was built; the list
        # shows that choice now that it is wired to it.
        self.sidebar_selection(*sidebar.current())

    def roots(self) -> tuple:
        """The folders added to the sidebar (library_set.py, settings key
        library_folders). A note in one of them links any file in it
        where it lies (file_links.link_root)."""
        return tuple(self.library.roots())

    def show_panes_at_start(self) -> None:
        """The sidebar as it was left (shown the first time, with its
        invitation to add a folder), the file list too (shown the first
        time once there is a folder). The first launch makes the window
        wider by them, so the text keeps its room."""
        window = self.window
        settings = load_settings()
        has_folders = bool(self.library.roots())
        wanted = [name for name, key, default in (
            (SIDEBAR_PANEL, SIDEBAR_SHOWN_KEY, True),
            (FILE_LIST_PANEL, FILE_LIST_SHOWN_KEY, has_folders))
            if settings.get(key, default) is True]
        extra = 0
        for name in wanted:
            window._side_panels.open(name)
            extra += window._side_panels.width_of(name) + window._central_splitter.handleWidth()
        if extra and not getattr(window, "_had_window_geometry", False):
            room = window._screen_room()
            window.resize(min(window.width() + extra, max(window.width(), room.w)),
                          window.height())
        self.sync_commands()

    def update_theme(self, is_dark: bool) -> None:
        self.sidebar.update_theme(is_dark)
        self.files.update_theme(is_dark)
        if self.quick_look is not None:
            self.quick_look.update_theme(is_dark)

    def close(self) -> None:
        """As the window closes: Quick Look goes, the list stops reading,
        and the folders' indexes being built stop (nothing waits for them
        as the app quits)."""
        if self.quick_look is not None:
            self.quick_look.close()
        self.files.shutdown()
        for folder in self.library.libraries():
            folder.stop_indexing()

    def _save_roots(self) -> None:
        save_setting(LIBRARY_FOLDERS_KEY, list(self.library.roots()))

    def unsaved_notes(self) -> set:
        """The open notes with unsaved changes: a rename or move leaves
        their links to the editor."""
        return {ed._file_path for ed in self.window._all_editors()
                if getattr(ed, "_file_path", None) and ed.document().isModified()}

    def says(self, text: str) -> None:
        if text:
            self.window.status.showMessage(text, 8000)

    # -- links that follow a rename or a move ----------------------------------------

    def links_progress(self, done: int, total: int) -> None:
        """How far the notes are while their links follow a rename or a
        move (the message after it says what was done)."""
        if total and done < total:
            self._links_progress_text = ngettext(
                "Updating links: {done} of {total} note read…",
                "Updating links: {done} of {total} notes read…", total).format(
                done=done, total=total)
            self.window.status.showMessage(self._links_progress_text)

    def links_done(self, report) -> None:
        """The links followed: those of the open notes with unsaved changes
        follow in their text, and the progress goes, whatever came of it
        (what was done, if anything, has been said by then)."""
        self._follow_links_in_open_notes(getattr(report, "left_open", ()) or ())
        status = self.window.status
        if self._links_progress_text and status.currentMessage() == self._links_progress_text:
            status.clearMessage()
        self._links_progress_text = ""

    def finish_link_updates(self) -> None:
        """Let the links being updated after a rename or move finish before
        the window goes, with the standard progress while they do (a
        library of thousands of notes takes a moment); at once when there
        is nothing to wait for."""
        library = self.library
        if not library.links_pending():
            return
        progress = QProgressDialog(_("Updating links before closing…"), "", 0, 0,
                                   self.window)
        progress.setWindowTitle(APP_DISPLAY_NAME)
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        try:
            while library.links_pending():
                QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
                time.sleep(0.01)
            library.wait_for_links()
        finally:
            progress.close()
            progress.deleteLater()

    # -- the sidebar and the panes -------------------------------------------------

    def sidebar_selection(self, kind: str, path: str) -> None:
        """The sidebar's choice in the file list: a folder, or a Library
        section across every folder."""
        if kind == "folder":
            if path:
                self.files.show_folder(path)
        elif kind:
            self.files.show_section(kind)
        self.update_file_commands()

    def add_folder(self) -> None:
        """File > Add Folder to Sidebar…: a folder chosen, kept in the
        sidebar from now on, and shown."""
        path = QFileDialog.getExistingDirectory(self.window, _("Add Folder to Sidebar"),
                                                os.path.expanduser("~"))
        if path:
            self.add_root(file_paths.normalize(path))

    def add_root(self, path: str) -> bool:
        if not self.library.add_root(path):
            inform(self.window, title=_("“{name}” can’t be added").format(
                       name=os.path.basename(path.rstrip("/\\")) or path),
                   message=_("It is in the sidebar already, or inside a folder that is."),
                   is_dark=self.window.is_dark_theme)
            return False
        for name in (SIDEBAR_PANEL, FILE_LIST_PANEL):
            self.set_pane_shown(name, True)
        self.sidebar.select("folder", os.path.abspath(path), announce=True)
        return True

    def set_pane_shown(self, name: str, shown: bool) -> None:
        """Show or hide the sidebar or the file list, and remember it."""
        window = self.window
        if shown:
            window._side_panels.open(name)
        else:
            pane = self.sidebar if name == SIDEBAR_PANEL else self.files
            if pane.isAncestorOf(QApplication.focusWidget()):
                target = window._pane_focus_target(window._editor_side)
                if target is not None:
                    target.setFocus()
            window._side_panels.close(name)
        folded = window.__dict__.get("_folded_for_split") or []
        if name in folded:
            folded.remove(name)              # shown or hidden by hand now
        save_setting(SIDEBAR_SHOWN_KEY if name == SIDEBAR_PANEL else FILE_LIST_SHOWN_KEY,
                     bool(shown))
        self.sync_commands()

    def toggle_pane(self, name: str) -> None:
        self.set_pane_shown(name, not self.window._side_panels.is_open(name))

    def sync_commands(self) -> None:
        """Show or Hide Sidebar and File List, named for what they do next,
        and the toolbar's sidebar button checked while the sidebar is."""
        window = self.window
        panels = window._side_panels
        window.act_file_list.setText(_("Hide File List") if panels.is_open(FILE_LIST_PANEL)
                                     else _("Show File List"))
        window._update_window_commands()
        button = getattr(window, "_sidebar_toggle", None)
        if button is not None:
            button.setChecked(panels.is_open(SIDEBAR_PANEL))
            window._file_list_toggle.setChecked(panels.is_open(FILE_LIST_PANEL))

    # -- the File menu's library commands ---------------------------------------------

    def file_command_targets(self) -> list:
        """The files the File menu's library commands act on: those
        selected in the file list while it has the keys (or a folder chosen
        in the sidebar while it has them), else the file of the tab in
        front."""
        if self._from_list():
            return list(self.files.selected_paths())
        if self._from_sidebar():
            kind, path = self.sidebar.current()
            return [path] if kind == "folder" and path else []
        window = self.window
        path = window._tab_file_path(window._tab_in_front())
        return [path] if path and os.path.exists(path) else []

    def in_library(self, path: str) -> bool:
        return self.library.library_for(path) is not None

    def _from_list(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is not None and self.files.isAncestorOf(focus)

    def _from_sidebar(self) -> bool:
        focus = QApplication.focusWidget()
        return focus is not None and self.sidebar.isAncestorOf(focus)

    def focus_moved(self, _old, now) -> None:
        """The focus moved somewhere in the app: only a move inside this
        window changes what its File menu can act on. A window left behind
        does no work for every focus change elsewhere (it looks again when
        the focus comes back into it)."""
        if now is not None and now.window() is self.window:
            self.update_file_commands()

    def update_file_commands(self, *_args) -> None:
        """The File menu's library commands, on only where they act."""
        window = self.window
        targets = self.file_command_targets()
        one = len(targets) == 1
        library = all(self.in_library(p) for p in targets) and bool(targets)
        window.act_new_folder.setEnabled(self._new_folder_place() is not None)
        window.act_rename_file.setEnabled(one)
        window.act_duplicate_file.setEnabled(library and not any(
            os.path.isdir(p) and not file_kinds.is_package(p) for p in targets))
        # Only from the list or the sidebar: its key (Command-Delete, Delete)
        # edits text everywhere else. A tab's file goes to the Trash from
        # the tab's own menu.
        window.act_trash_file.setEnabled(bool(targets)
                                         and (self._from_list() or self._from_sidebar()))
        window.act_reveal_file.setEnabled(one)
        window.act_quick_look.setEnabled(one and self._from_list())

    def _new_folder_place(self) -> Optional[str]:
        """Where New Folder makes one: the folder the file list shows, or
        the folder of the tab's file when it lies in a sidebar folder."""
        window = self.window
        if window._side_panels.is_open(FILE_LIST_PANEL):
            folder = self.files.current_folder()
            if folder:
                return folder
        path = window._tab_file_path(window._tab_in_front())
        if path and self.in_library(path):
            return os.path.dirname(path)
        return None

    def new_folder_command(self) -> None:
        """File > New Folder: made where the list is, and named right away."""
        place = self._new_folder_place()
        if not place:
            return
        made = library_actions.new_folder(self.window, self.library, place)
        if not made:
            return
        self.files.refresh()
        self.files.select_path(made)
        self.files.rename_path(made)

    def rename_command(self) -> None:
        targets = self.file_command_targets()
        if len(targets) != 1:
            return
        path = targets[0]
        if self._from_sidebar():
            self.sidebar.rename_folder(path)
            return
        window = self.window
        index = window._tab_index_of(path)
        if self._from_list() or index < 0:
            if self.in_library(path):
                self.files.rename_path(path)
            return
        widget = window.tabs.widget(index)
        if self.in_library(path):
            window._library_file_action(widget, path, rename=True)
        elif window._editor_from_widget(widget) is not None:
            window._rename_tab(index, window._editor_from_widget(widget))
        else:
            window._rename_viewer_tab(index, widget)

    def duplicate_command(self) -> None:
        targets = [p for p in self.file_command_targets() if self.in_library(p)]
        if targets:
            self.files.duplicate_paths(targets)

    def trash_command(self) -> None:
        targets = self.file_command_targets()
        if not targets:
            return
        if self._from_list():
            self.files.trash_selected()
        elif self._from_sidebar():
            self.sidebar.trash_folder(targets[0])

    def reveal_command(self) -> None:
        targets = self.file_command_targets()
        if targets:
            self.window._reveal_file(targets[0])

    def quick_look_command(self) -> None:
        targets = self.file_command_targets()
        if len(targets) == 1 and self._from_list():
            self.toggle_quick_look(targets[0])

    # -- showing files from the list ------------------------------------------------

    def _is_preview_tab(self, widget) -> bool:
        """Whether ``widget`` is the tab the last list click opened, still
        unchanged: the next click shows its file in it instead."""
        window = self.window
        if widget is None or widget is not self.preview_tab:
            return False
        if window.tabs.indexOf(widget) < 0:
            return False
        ed = window._editor_from_widget(widget)
        if ed is not None and ed.document().isModified():
            return False
        sheet = window._sheet_from_widget(widget)
        return sheet is None or not sheet.is_modified()

    def show_from_list(self, path: str) -> None:
        """A file clicked (or reached with the arrow keys) in the list: shown
        in the tab the last click opened, so tabs do not pile up, unless
        that tab was kept (opened with a double-click or Return, or edited).
        A file open already is brought to the front. With Quick Look up,
        Quick Look shows it instead. The keys stay in the list."""
        if self.quick_look is not None and self.quick_look.isVisible():
            self.quick_look.show_path(path)
            return
        window, tabs = self.window, self.window.tabs
        focus = QApplication.focusWidget()
        index = window._tab_index_of(path)
        if index >= 0:
            tabs.setCurrentIndex(index)
        else:
            previous = self.preview_tab if self._is_preview_tab(self.preview_tab) else None
            place = tabs.indexOf(previous) if previous is not None else -1
            self.previewing = True
            try:
                window.open_path(path)
            finally:
                self.previewing = False
            shown = tabs.currentWidget()
            if window._tab_file_path(shown) and file_paths.same_file(
                    window._tab_file_path(shown), path):
                if previous is not None:
                    window._discard_tab(tabs.indexOf(previous))
                    tabs.tabBar().moveTab(tabs.indexOf(shown), min(place, tabs.count() - 1))
                self.preview_tab = shown
                self._keep_on_first_edit(shown)
        if focus is not None and self.files.isAncestorOf(focus):
            focus.setFocus()

    def keep_preview_tab(self, widget) -> None:
        """The tab the list opened is kept (double-click, Return, an edit):
        an opening now, so its file goes in Open Recent."""
        if self.preview_tab is widget:
            self.preview_tab = None
            path = self.window._tab_file_path(widget)
            if path:
                self.window._remember_recent(path)

    def _keep_on_first_edit(self, widget) -> None:
        """The first change in a tab the list opened keeps the tab."""
        def keep(modified, w=widget):
            if modified:
                self.keep_preview_tab(w)
        ed = self.window._editor_from_widget(widget)
        if ed is not None:
            ed.document().modificationChanged.connect(keep)
        sheet = self.window._sheet_from_widget(widget)
        if sheet is not None:
            sheet.modification_changed.connect(keep)

    def open_from_list(self, path: str) -> None:
        """A file double-clicked, or Return, in the list: what MyEditor
        edits or reads (notes, text, spreadsheets, PDFs) opens in a tab
        that stays; anything else opens in the app this computer opens it
        with."""
        window = self.window
        kind = file_kinds.kind_of_path(path)
        ext = file_kinds.extension(path)
        own = (kind in (file_kinds.NOTE, file_kinds.TEXT, file_kinds.PDF)
               or kind == file_kinds.SHEET and ext in file_kinds.EDITABLE_SHEET_EXTS
               or kind == file_kinds.OTHER and os.path.isfile(path)
               and regular_files.reads_as_text(path))
        if not own:
            window._open_in_default_app(path)
            return
        index = window._tab_index_of(path)
        if index < 0:
            window.open_path(path)
            index = window._tab_index_of(path)
        if index >= 0:
            window.tabs.setCurrentIndex(index)
            self.keep_preview_tab(window.tabs.widget(index))
            target = window._pane_focus_target(window._editor_side)
            if target is not None:
                target.setFocus()

    def toggle_quick_look(self, path: str) -> None:
        """Space in the list: Quick Look of the file, or Quick Look away."""
        panel = self.quick_look
        if panel is not None and panel.isVisible():
            panel.close()
            return
        if panel is None:
            from quick_look import QuickLookPanel
            panel = self.quick_look = QuickLookPanel(self.window,
                                                     is_dark=self.window.is_dark_theme)
            panel.content_created.connect(self._wire_quick_look_content)
        panel.show_path(path)

    def _wire_quick_look_content(self, content) -> None:
        """What Quick Look shows asks for what a tab's does: its app's name
        on its button, Open in it, Open With, Show in Finder, Insert into
        Note, Import as Note."""
        window = self.window
        if isinstance(content, FileViewerTab):
            window._wire_file_viewer(content)
            return
        sheet = window._sheet_from_widget(content)
        if sheet is not None:
            sheet.open_requested.connect(lambda s=sheet: window._open_in_default_app(s._file_path))
            sheet.open_with_requested.connect(
                lambda position, s=sheet: window._show_open_with_menu(s._file_path, position))
            sheet.reveal_requested.connect(lambda s=sheet: window._reveal_file(s._file_path))
            window._name_default_app(sheet)

    def _new_note_in(self, folder: str) -> None:
        path = library_actions.new_note(self.window, self.library, folder)
        if path:
            self.open_new_note(path)

    def open_new_note(self, path: str) -> None:
        """A note just made in the library: open, its tab kept, ready for
        typing, and selected in the list."""
        window = self.window
        ed = window.open_path(path)
        if ed is None:
            index = window._tab_index_of(path)
            ed = window._editor_from_widget(window.tabs.widget(index)) if index >= 0 else None
        self.files.select_path(path)
        if ed is not None:
            ed.setFocus()

    # -- files that moved, changed or went ------------------------------------------

    def _follow_moves(self, moves: dict) -> None:
        """Open tabs whose files were renamed or moved go on with them (the
        links of those with unsaved changes follow once the library says
        which it left to the editor: links_done). Names compare as the
        disk compares them (a Mac's decomposed "ü" is the composed one a
        tab was opened with, and case does not count there)."""
        panel = self.quick_look
        shown = panel.path() if panel is not None else None
        moved = open_note_links.new_place(shown, moves) if shown else None
        if moved:
            panel.show_path(file_paths.normalize(moved))
        window = self.window
        for i in range(window.tabs.count()):
            path = window._tab_file_path(window.tabs.widget(i))
            new = open_note_links.new_place(path, moves) if path else None
            if new:
                window._retarget_tab(i, file_paths.normalize(new))

    def _reload_rewritten_notes(self, notes: list) -> None:
        """Notes whose links the library pointed at moved files: an open
        one without unsaved changes shows what is on the disk now (one
        with changes was left alone; the library said so)."""
        window = self.window
        rewritten = {os.path.abspath(n) for n in notes}
        for i in range(window.tabs.count()):
            w = window.tabs.widget(i)
            ed = window._editor_from_widget(w)
            path = getattr(ed, "_file_path", None)
            if (ed is None or not path or os.path.abspath(path) not in rewritten
                    or ed.document().isModified()):
                continue
            if window._reload_from_disk(ed, quiet=True):
                window._saved_stamps[path] = regular_files.file_stamp(path)

    def close_trashed_tabs(self, paths: list) -> None:
        """A file put in the Trash from the library: its read-only tab, or
        its tab without unsaved changes, closes; a document with unsaved
        changes stays open (its bar says the file is gone, and Save
        writes it again). Quick Look of one of them goes too."""
        panel = self.quick_look
        shown = panel.path() if panel is not None else None
        if shown and any(link_rewrite.is_within(shown, gone) for gone in paths):
            panel.close()
        window = self.window
        for i in reversed(range(window.tabs.count())):
            w = window.tabs.widget(i)
            path = window._tab_file_path(w)
            if not path or not any(link_rewrite.is_within(path, old) for old in paths):
                continue
            ed = window._editor_from_widget(w)
            sheet = window._sheet_from_widget(w)
            if (ed is not None and ed.document().isModified()
                    or sheet is not None and sheet.is_modified()):
                continue
            window._discard_tab(i)

    def _follow_links_in_open_notes(self, left_open) -> None:
        """The open notes with unsaved changes the library left to the
        editor (link_rewrite.OpenNote: where the note is and was, and the
        moves) have their links follow in their text (open_note_links.py),
        one step of Undo each: the library writes no file whose editor
        holds changes."""
        window = self.window
        for open_note in left_open:
            for ed in window._all_editors():
                path = getattr(ed, "_file_path", None)
                if not path or not window._keeps_pictures(ed):
                    continue
                if any(link_rewrite.is_within(path, other) and link_rewrite.is_within(other, path)
                       for other in (open_note.note, open_note.was)):
                    self._rewrite_links_in_editor(ed, open_note.was, open_note.note,
                                                  open_note.moves)

    def _rewrite_links_in_editor(self, ed, old_note: str, new_note: str, moves: dict) -> bool:
        """Point the relative links in ``ed`` (a note that was at
        ``old_note`` and is at ``new_note``) where their files are after
        ``moves``; whether anything changed."""
        doc = ed.document()
        if self.window._editor_kind(ed) != "rich":
            text = ed.toPlainText()
            changes = open_note_links.text_changes(text, old_note, new_note, moves)
            if not changes:
                return False

            def utf16(index: int) -> int:
                return len(text[:index].encode("utf-16-le")) // 2

            with ed.undo_step(N_("Update Links")):
                cursor = QTextCursor(doc)
                for start, end, replacement in reversed(changes):
                    cursor.setPosition(utf16(start))
                    cursor.setPosition(utf16(end), QTextCursor.MoveMode.KeepAnchor)
                    cursor.insertText(replacement)
            return True
        edits = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                fmt = fragment.charFormat()
                if fmt.isImageFormat():
                    image = fmt.toImageFormat()
                    new = open_note_links.destination_after(image.name(), old_note,
                                                            new_note, moves)
                    if new:
                        image.setName(new)
                        edits.append((fragment.position(), fragment.length(), image))
                elif fmt.isAnchor():
                    new = open_note_links.destination_after(fmt.anchorHref(), old_note,
                                                            new_note, moves)
                    if new:
                        anchor = QTextCharFormat(fmt)
                        anchor.setAnchorHref(new)
                        edits.append((fragment.position(), fragment.length(), anchor))
                it += 1
            block = block.next()
        if not edits:
            return False
        with ed.undo_step(N_("Update Links")):
            for position, length, fmt in edits:
                cursor = QTextCursor(doc)
                cursor.setPosition(position)
                cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
                cursor.setCharFormat(fmt)
        return True
