# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The spreadsheets' part of the main window.

A spreadsheet MyEditor edits (.xlsx, .xlsm, .ods, .csv, .tsv) opens in a
sheet tab (sheet_tab.py). ``SheetWindow`` opens it there and does what
the tab asks of the window: open its file in its app, Open With, show
it in the Finder, Save As when its file went to the Trash, close. It
names the tab by its file, marked while there are unsaved changes, and
keeps the window's Undo and Redo in step with the sheet's. Saving asks
what each way a save can go needs (sheet_tab.SaveResult): the file open
in another app, changed by another app, gone, or holding parts MyEditor
cannot write back. Format > Table and Format > Sheet act on the sheet in
front.

The window owns one SheetWindow (``window._sheet_window``); which tab
is a sheet, and closing one, stay the window's.
"""

from __future__ import annotations

import os

import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QFileDialog

import default_apps
import file_kinds
import file_paths
from alerts import CANCEL, DEFAULT, DESTRUCTIVE, Button, ask, inform
from atomic_file import free_path
from i18n import _
from recent_files import add_recent


class SheetWindow(QObject):
    """Opens, names and saves ``window``'s sheet tabs."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window

    def open(self, path: str):
        """Open a spreadsheet MyEditor edits in a sheet tab and return it;
        None for the ones only shown (.xls, .numbers), and when the file
        is not there."""
        if file_kinds.extension(path) not in file_kinds.EDITABLE_SHEET_EXTS:
            return None
        window = self.window
        if not os.path.isfile(path):
            window._cannot_open(path)
            return None
        import sheet_tab
        sheet = sheet_tab.SheetTab(path, is_dark=window.is_dark_theme)
        self._wire(sheet)
        idx = window.tabs.addTab(sheet, os.path.basename(path))
        window.tabs.setTabToolTip(idx, path)
        window.tabs.setCurrentIndex(idx)
        window._attach_close_button(idx, sheet)
        window._watcher.addPath(path)
        window._remember_recent(path)
        sheet.setFocus()
        window._update_window_title()
        window._update_status_bar()
        return sheet

    def _wire(self, sheet) -> None:
        """What a sheet tab asks for, as a viewer's (open in its app,
        Open With, show in the Finder), and what the window shows of it:
        the tab's name and the window's with a mark while there are
        unsaved changes, and Undo and Redo as its steps go."""
        window = self.window
        sheet.open_requested.connect(lambda s=sheet: window._open_in_default_app(s._file_path))
        sheet.open_with_requested.connect(
            lambda position, s=sheet: window._show_open_with_menu(s._file_path, position))
        sheet.reveal_requested.connect(lambda s=sheet: window._reveal_file(s._file_path))
        sheet.set_reveal_label(default_apps.reveal_label())
        sheet.modification_changed.connect(lambda _m, s=sheet: self.name_tab(s))
        sheet.save_as_requested.connect(lambda s=sheet: self.save_as(s))
        sheet.close_requested.connect(lambda s=sheet: window.close_tab(window.tabs.indexOf(s)))
        for signal in (sheet.undo_stack.canUndoChanged, sheet.undo_stack.canRedoChanged,
                       sheet.undo_stack.indexChanged, sheet.busy_changed):
            signal.connect(lambda *_args, s=sheet: self._history_changed(s))
        window._name_default_app(sheet)

    def name_tab(self, sheet) -> None:
        """The sheet's tab named by its file, marked while it has unsaved
        changes, as a document's is."""
        tabs = self.window.tabs
        if not shiboken6.isValid(tabs) or not shiboken6.isValid(sheet):
            return
        index = tabs.indexOf(sheet)
        if index < 0:
            return
        name = os.path.basename(sheet._file_path)
        tabs.setTabText(index, f"{name}*" if sheet.is_modified() else name)
        tabs.setTabToolTip(index, sheet._file_path)
        if sheet is tabs.currentWidget():
            self.window._update_window_title()

    def _history_changed(self, sheet) -> None:
        window = self.window
        # Its stack may still speak as the window goes.
        if not shiboken6.isValid(window.tabs):
            return
        if sheet is window.tabs.currentWidget():
            window._update_undo_redo_buttons()
            window._update_editor_commands()

    # -- saving -------------------------------------------------------------------

    def save(self, sheet) -> bool:
        """File > Save in a sheet tab; what is asked next follows how the
        save went (sheet_tab.SaveResult)."""
        return self.settle_save(sheet, sheet.save())

    def settle_save(self, sheet, result) -> bool:
        """Whether the sheet is saved after ``result``, once the person
        answered what that way of going needs."""
        import sheet_tab
        window = self.window
        name = os.path.basename(sheet._file_path)
        if result.status == sheet_tab.SAVED:
            self.name_tab(sheet)
            window.status.showMessage(_("Saved."), 3000)
            return True
        if result.status == sheet_tab.IN_USE:
            inform(window, title=_("“{name}” is open in another app").format(name=name),
                   message=_("Close it there, then save again. Your changes are still "
                             "here."),
                   is_dark=window.is_dark_theme)
            return False
        if result.status == sheet_tab.DELETED:
            answer = ask(
                window, title=_("“{name}” was moved to the Trash or deleted").format(
                    name=name),
                message=_("Save it under a name to keep your changes."),
                buttons=(Button(_("Cancel"), "cancel", CANCEL),
                         Button(_("Save As…"), "save_as", DEFAULT)))
            if answer == "save_as":
                return self.save_as(sheet)
            return False
        if result.status == sheet_tab.CHANGED_ON_DISK:
            answer = ask(
                window, title=_("“{name}” was changed by another app").format(name=name),
                message=_("Saving replaces the version on the disk with yours. Revert "
                          "shows the version on the disk instead and drops your changes."),
                buttons=(Button(_("Revert"), "revert", DESTRUCTIVE),
                         Button(_("Cancel"), "cancel", CANCEL),
                         Button(_("Replace"), "replace", DEFAULT)),
                caution=True)
            if answer == "replace":
                return self.settle_save(sheet, sheet.save(overwrite_changes=True))
            if answer == "revert":
                sheet.reload()
                self.name_tab(sheet)
            return False
        if result.status == sheet_tab.NEEDS_COPY:
            answer = ask(
                window, title=_("“{name}” holds things MyEditor can’t save "
                                "in it").format(name=name),
                message=result.message or _("Save a copy to keep your changes. The file "
                                            "stays as it is."),
                buttons=(Button(_("Cancel"), "cancel", CANCEL),
                         Button(_("Save a Copy…"), "copy", DEFAULT)))
            if answer == "copy":
                return self.save_as(sheet)
            return False
        inform(window, title=_("Couldn't save “{name}”").format(name=name),
               message=result.message, is_dark=window.is_dark_theme)
        return False

    def save_as(self, sheet) -> bool:
        """File > Save As (and Save a Copy) in a sheet tab: its own kind,
        an Excel workbook, an OpenDocument spreadsheet, or the sheet on
        screen as a text table. A workbook saved as its own kind or as
        another workbook goes on in the new file; a CSV or TSV is an
        export of the sheet shown."""
        import sheet_io
        window = self.window
        # What this workbook can be saved as, its own format first
        # (sheet_io.save_formats: the same list a save follows).
        formats = sheet_io.save_formats(sheet_io.kind_of(sheet._file_path))
        filters = [f.label.format(patterns="*" + f.ext) for f in formats]
        suggested_ext = formats[0].ext
        suggested = free_path(os.path.splitext(sheet._file_path)[0] + suggested_ext)
        path, chosen = QFileDialog.getSaveFileName(window, _("Save As"), suggested,
                                                   ";;".join(filters), filters[0])
        if not path:
            return False
        chosen_ext = formats[filters.index(chosen)].ext if chosen in filters else suggested_ext
        path = sheet_io.save_as_path(file_paths.normalize(path), chosen_ext, suggested_ext,
                                     sheet_io.kind_of(sheet._file_path))
        old = sheet._file_path
        result = sheet.save_as(path)
        if result.ok and result.switched and sheet._file_path != old:
            window._unwatch(old)
            window._watcher.addPath(sheet._file_path)
            add_recent(sheet._file_path)
            window._populate_recent_menu()
        return self.settle_save(sheet, result)

    # -- Format > Table and Format > Sheet -----------------------------------------

    @staticmethod
    def table_command(sheet, name: str) -> None:
        """Format > Table's rows and columns in a sheet: around the cells
        selected, or the cell at the cursor."""
        block = sheet.selected_block()
        if block is None:
            row, col = sheet.current_cell()
            block = (row, col, row, col)
        r1, c1, r2, c2 = block
        {"add_row_above": lambda: sheet.insert_rows(r1, 1),
         "add_row_below": lambda: sheet.insert_rows(r2 + 1, 1),
         "add_column_before": lambda: sheet.insert_columns(c1, 1),
         "add_column_after": lambda: sheet.insert_columns(c2 + 1, 1),
         "delete_row": lambda: sheet.delete_rows(r1, r2 - r1 + 1),
         "delete_column": lambda: sheet.delete_columns(c1, c2 - c1 + 1),
         }.get(name, lambda: None)()

    def sheet_command(self, name: str) -> None:
        """Format > Sheet: the sheets of the workbook in front."""
        sheet = self.window.current_sheet()
        if sheet is None:
            return
        {"add": sheet.add_sheet, "duplicate": sheet.duplicate_sheet,
         "rename": sheet.rename_sheet, "delete": sheet.delete_sheet}[name]()
