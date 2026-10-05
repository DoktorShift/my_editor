#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The window side of workspace.py: the open tabs, written down before an
update restart and reopened after it.

workspace.py is the record and stays pure stdlib. This module reads the
record off the window's tabs and back into new ones, and decides what to
tell the person about the version a launch runs. MainWindow keeps thin
calls into it and provides what its own tab openers already do:

    tabs                          the QTabWidget
    open_path(path)               the new tab's editor or PDF viewer, or None
    new_tab()                     the new untitled tab's editor
    show_welcome_tab()            the welcome tab's editor
    _restore_one_backup(record)   the recovered tab's editor, or None
    _editor_from_widget(widget)   the editor inside a tab, or None
    _pdf_viewer_from_widget(w)    the PDF viewer a tab is, or None
    _compose_tab_title(editor)    the title a tab shows

Unsaved content always lives in its crash-recovery backup (recovery.py);
the record only points at it.
"""

import dataclasses
import os
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor

from recovery import (
    classify_backup, document_is_empty, is_restorable, load_backup_content, read_backup,
)
from update_check import version_tuple
from workspace import DOCUMENT, PDF, WELCOME, TabState


# -- writing the tabs down -----------------------------------------------------

def capture_editor_tab(ed) -> Optional[TabState]:
    """One editor tab, or None when its unsaved content could not be kept.

    Unsaved and untitled content is written to the tab's crash-recovery
    backup now, and the record points at that file.
    """
    path = getattr(ed, "_file_path", None)
    doc = ed.document()
    modified = doc.isModified()
    if getattr(ed, "_is_welcome", False) and not modified:
        return TabState(kind=WELCOME)
    backup_file = None
    if modified or not path:
        backup = getattr(ed, "_backup", None)
        if backup is not None and backup.write_now():
            backup_file = os.path.abspath(backup.path)
        elif modified or not document_is_empty(doc):
            # Content exists that is in no file and no backup.
            return None
    cursor = ed.textCursor()
    binding = getattr(ed, "_draft_binding", None)
    return TabState(
        kind=DOCUMENT,
        path=path,
        backup_file=backup_file,
        modified=modified,
        cursor=cursor.position(),
        anchor=cursor.anchor(),
        scroll=ed.verticalScrollBar().value(),
        draft=dataclasses.asdict(binding) if binding is not None else None,
    )


def capture_tabs(window, *, allow_unprotected: bool):
    """``(tabs, active)`` for every tab, or None when an unsaved tab could
    not be written down and ``allow_unprotected`` is False."""
    tabs, active = [], 0
    for i in range(window.tabs.count()):
        widget = window.tabs.widget(i)
        viewer = window._pdf_viewer_from_widget(widget)
        if viewer is not None:
            viewer.save_view_state()
            tab = TabState(kind=PDF, path=viewer._file_path)
        else:
            ed = window._editor_from_widget(widget)
            if ed is None:
                continue
            tab = capture_editor_tab(ed)
            if tab is None:
                if not allow_unprotected:
                    return None
                # The person chose Don't Save for this tab.
                tab = TabState(kind=DOCUMENT, path=getattr(ed, "_file_path", None))
        if i == window.tabs.currentIndex():
            active = len(tabs)
        tabs.append(tab)
    return tuple(tabs), active


# -- reopening them --------------------------------------------------------------

def resume(window, ws, *, draft_type) -> None:
    """Reopen every tab of the workspace ``ws``, in order, and make the tab
    that was active current again.

    Each tab is reopened on its own: one that cannot come back (a file
    deleted meanwhile) never costs the others. A backup that could not be
    reopened stays on disk, where the crash-recovery sweep finds it.
    ``draft_type`` is the class a tab's Nostr draft link is rebuilt as.
    """
    active = None
    for index, tab in enumerate(ws.tabs):
        try:
            widget = resume_tab(window, tab, draft_type=draft_type)
        except Exception:
            continue
        if index == ws.active and widget is not None:
            active = widget
    if active is not None:
        index = _tab_index(window, active)
        if index >= 0:
            window.tabs.setCurrentIndex(index)


def resume_tab(window, tab: TabState, *, draft_type):
    """Reopen one tab. Returns its editor (or PDF viewer), or None when it
    could not come back."""
    if tab.kind == PDF:
        if tab.path and os.path.isfile(tab.path):
            return window.open_path(tab.path)
        return None
    if tab.kind == WELCOME:
        return window.show_welcome_tab()

    record = read_backup(tab.backup_file) if tab.backup_file else None
    if record is not None and not is_restorable(record):
        return None   # a newer build wrote it; leave it for that build
    if tab.path and record is not None and (
            not os.path.isfile(tab.path) or classify_backup(record) == "stale"):
        # The file moved on, or went away, while MyEditor was closed:
        # restore the work the way crash recovery does, as a copy.
        return window._restore_one_backup(record)
    if tab.path:
        if not os.path.isfile(tab.path):
            return None
        ed = window._editor_from_widget(window.open_path(tab.path))
        if ed is None:
            return None   # already open, or it could not be read
    elif record is not None or not tab.backup_file:
        ed = window.new_tab()
    else:
        return None   # its backup is gone: there is nothing to bring back

    if record is not None:
        load_backup_content(ed, record, modified=tab.modified)
        ed._backup.take_over(record["_backup_file"])
    if tab.draft:
        ed._draft_binding = _dataclass_from(draft_type, tab.draft)
    index = _tab_index(window, ed)
    if index >= 0:
        window.tabs.setTabText(index, window._compose_tab_title(ed))
    restore_cursor_and_scroll(ed, tab)
    return ed


def restore_cursor_and_scroll(ed, tab: TabState) -> None:
    last = max(0, ed.document().characterCount() - 1)
    cursor = ed.textCursor()
    cursor.setPosition(min(tab.anchor, last))
    cursor.setPosition(min(tab.cursor, last), QTextCursor.MoveMode.KeepAnchor)
    ed.setTextCursor(cursor)
    # The scroll range exists only once the document is laid out.
    QTimer.singleShot(0, lambda: ed.verticalScrollBar().setValue(tab.scroll))


def _tab_index(window, widget) -> int:
    """The index of the tab that is, or holds, ``widget``; -1 if none."""
    for i in range(window.tabs.count()):
        page = window.tabs.widget(i)
        if page is widget or window._editor_from_widget(page) is widget:
            return i
    return -1


def _dataclass_from(cls, data: dict):
    """``cls`` built from the fields of ``data`` it knows, or None."""
    known = {f.name for f in dataclasses.fields(cls)}
    try:
        return cls(**{k: v for k, v in data.items() if k in known})
    except TypeError:
        return None


# -- after a version change ------------------------------------------------------

@dataclass(frozen=True)
class VersionNews:
    """What to tell the person about the version this launch runs."""

    updated: bool          # True: this is a newer version. False: an update didn't install.
    version: str           # the version now running, or the one that didn't install
    notes: str = ""        # release notes (Markdown) for What's New, if known
    release_url: str = ""


def version_news(ws, last_run, current: str, *, release_page: str) -> Optional[VersionNews]:
    """Whether this launch is news, and which.

    ``ws`` is the workspace an update restart left (or None); ``last_run``
    the version the previous launch recorded. A restart that came back on
    an older version than it was meant to reach did not install. Otherwise
    a restart, or any launch on a newer version than last time (updated by
    hand, through the guide or a package manager), is an update.
    ``release_page`` is where a manual update's notes are.
    """
    now = version_tuple(current)
    if ws is not None and ws.to_version:
        target = version_tuple(ws.to_version)
        if now is not None and target is not None and now < target:
            return VersionNews(updated=False, version=ws.to_version)
        return VersionNews(updated=True, version=current, notes=ws.release_notes,
                           release_url=ws.release_url)
    before = version_tuple(last_run) if isinstance(last_run, str) else None
    if before is not None and now is not None and before < now:
        return VersionNews(updated=True, version=current, release_url=release_page)
    return None
