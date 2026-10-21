# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the folder sidebar and the file list do with files, in one place.

Both panes rename, move, copy in, duplicate and trash files the same
way, ask the same questions and say the same things afterwards, so the
steps live here: each function asks what it must (a new name, whether
to change an extension, whether to move to the Trash), calls the
LibrarySet, and tells the person calmly when the disk said no.

Links always follow a file that is renamed or moved: the library sets
them right in every note, without asking, as the folder on disk is the
truth and a note should never point at nothing. Notes open with unsaved
changes are left to the editor (``skip_notes``), and the message after
the change says so.

Also here: the drag data both panes write and read (the real files as
URLs, so the Finder, Mail and Messages get the files themselves, and
the same paths in the app's own type, so a drop inside the app knows
they came from the library), and the folder glyph both panes draw.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import sys
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFileDialog, QInputDialog, QLineEdit, QMenu, QWidget

import alerts
import default_apps
import file_kinds
import folder_library
from folder_library import MoveIncomplete, OriginalLeft
from i18n import _, ngettext
from library_set import LinkReport

log = logging.getLogger(__name__)

# The app's own drag type: the dragged library files' paths, one a line.
MIME_PATHS = "application/x-myeditor-library-paths"

UnsavedNotes = Callable[[], Set[str]]


def no_unsaved_notes() -> Set[str]:
    return set()


# -- drag data -----------------------------------------------------------------

def mime_for(paths: Sequence[str]) -> QMimeData:
    """Drag data for ``paths``: file URLs and the app's own type."""
    data = QMimeData()
    data.setUrls([QUrl.fromLocalFile(p) for p in paths])
    data.setData(MIME_PATHS, "\n".join(paths).encode("utf-8"))
    return data


def dropped(mime: QMimeData) -> Tuple[List[str], List[str]]:
    """``(from_library, from_outside)``: the paths a drop carries, those
    dragged from the app's own lists apart from files from elsewhere."""
    if mime is None:
        return [], []
    if mime.hasFormat(MIME_PATHS):
        text = bytes(mime.data(MIME_PATHS)).decode("utf-8", "replace")
        return [p for p in text.split("\n") if p], []
    if mime.hasUrls():
        return [], [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile()]
    return [], []


def accepts(mime: QMimeData) -> bool:
    inside, outside = dropped(mime)
    return bool(inside or outside)


# -- drawing -------------------------------------------------------------------

def paint_folder(painter: QPainter, rect: QRectF, color: QColor, *, filled: bool = False) -> None:
    """A folder drawn into the square ``rect`` in ``color``: an outline,
    as the sidebar's glyphs are, or ``filled`` (white on a tinted tile)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.topLeft())
    scale = rect.width() / 18.0
    painter.scale(scale, scale)
    path = QPainterPath(QPointF(2.2, 13.8))
    path.lineTo(2.2, 5.2)
    path.quadTo(2.2, 4.0, 3.4, 4.0)
    path.lineTo(6.6, 4.0)
    path.lineTo(8.2, 5.7)
    path.lineTo(14.6, 5.7)
    path.quadTo(15.8, 5.7, 15.8, 6.9)
    path.lineTo(15.8, 13.8)
    path.quadTo(15.8, 15.0, 14.6, 15.0)
    path.lineTo(3.4, 15.0)
    path.quadTo(2.2, 15.0, 2.2, 13.8)
    pen = QPen(color, 1.4)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    if filled:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)
    else:
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.drawLine(QPointF(2.2, 8.0), QPointF(15.8, 8.0))
    painter.restore()


# -- questions (each one a seam the tests replace) -------------------------------

def ask_name(parent: Optional[QWidget], title: str, label: str, text: str,
             select: int) -> Optional[str]:
    """A new name, or None when the person backed out. The first
    ``select`` characters are selected, as the Finder selects the name
    without its extension."""
    dialog = QInputDialog(parent)
    dialog.setWindowTitle(title)
    dialog.setLabelText(label)
    dialog.setTextValue(text)
    dialog.setOkButtonText(_("Rename"))
    edit = dialog.findChild(QLineEdit)
    if edit is not None:
        edit.setAccessibleName(label)
        edit.setSelection(0, select)
    if dialog.exec() != QInputDialog.DialogCode.Accepted:
        return None
    name = dialog.textValue().strip()
    return name or None


def ask_extension_change(parent: Optional[QWidget], old_ext: str, new_ext: str) -> bool:
    """Whether to use the new extension, asked as the Finder asks: the
    old one is kept unless the person chooses otherwise."""
    if new_ext:
        title = _("Are you sure you want to change the extension from “{old}” to “{new}”?").format(
            old=old_ext, new=new_ext)
        use = _("Use {ext}").format(ext=new_ext)
    else:
        title = _("Are you sure you want to remove the extension “{old}”?").format(old=old_ext)
        use = _("Remove")
    return alerts.ask(
        parent, title=title,
        message=_("If you make this change, your file may open in a different app."),
        buttons=(alerts.Button(use, True, alerts.NORMAL),
                 alerts.Button(_("Keep {ext}").format(ext=old_ext), False, alerts.DEFAULT))) is True


def confirm_trash(parent: Optional[QWidget], paths: Sequence[str]) -> bool:
    """Ask before moving ``paths`` to the Trash (the Recycle Bin on
    Windows); Cancel is the default."""
    count = len(paths)
    name = os.path.basename(paths[0].rstrip("/\\")) if paths else ""
    # One item is named, and "it" is restored; several are counted, and
    # restored as "them" (whole sentences, in every language's plurals).
    if sys.platform == "win32":
        title = (_("Move “{name}” to the Recycle Bin?").format(name=name) if count == 1 else
                 ngettext("Move {n} item to the Recycle Bin?", "Move {n} items to the Recycle Bin?",
                          count).format(n=count))
        message = (_("You can restore it from the Recycle Bin until you empty it.") if count == 1
                   else ngettext("You can restore the {n} item from the Recycle Bin until you "
                                 "empty it.",
                                 "You can restore the {n} items from the Recycle Bin until you "
                                 "empty it.", count).format(n=count))
        action = _("Move to Recycle Bin")
    else:
        title = (_("Move “{name}” to the Trash?").format(name=name) if count == 1 else
                 ngettext("Move {n} item to the Trash?", "Move {n} items to the Trash?",
                          count).format(n=count))
        message = (_("You can restore it from the Trash until you empty it.") if count == 1
                   else ngettext("You can restore the {n} item from the Trash until you "
                                 "empty it.",
                                 "You can restore the {n} items from the Trash until you "
                                 "empty it.", count).format(n=count))
        action = _("Move to Trash")
    return alerts.confirm_destructive(parent, title=title, message=message, action=action)


def choose_folder(parent: Optional[QWidget], start: str) -> Optional[str]:
    # Titled as the command that opens it says it ("Move to…"), without
    # the ellipsis a title has no need of.
    folder = QFileDialog.getExistingDirectory(parent, _("Move to"), start)
    return folder or None


def choose_files(parent: Optional[QWidget], start: str) -> List[str]:
    paths, _filter = QFileDialog.getOpenFileNames(parent, _("Add Files"), start)
    return list(paths)


def error_message(error: BaseException) -> str:
    """What the disk said, as a sentence a person can act on; never the
    system's own wording or a list of paths."""
    if isinstance(error, MoveIncomplete):
        return _incomplete_message(error)
    if isinstance(error, ValueError):
        return str(error)                 # the library's own sentences, translated
    if isinstance(error, FileExistsError):
        name = os.path.basename(error.filename or "") if error.filename else ""
        return (_("An item named “{name}” already exists in this location. "
                  "Choose a different name.").format(name=name) if name
                else _("An item with this name already exists in this location."))
    if isinstance(error, PermissionError):
        return _("You don't have permission to change this item.")
    if isinstance(error, FileNotFoundError):
        return _("The item could not be found. It may have been moved or deleted.")
    if isinstance(error, shutil.Error):
        return _("Some of the items could not be copied.")
    if isinstance(error, OSError):
        if error.errno == errno.ENOSPC:
            return _("There is not enough space on the disk.")
        if error.errno == errno.EROFS:
            return _("The disk can only be read, not changed.")
        if error.errno == errno.ENAMETOOLONG:
            return _("The name is too long.")
        if error.errno in (errno.EBUSY, getattr(errno, "ETXTBSY", errno.EBUSY)):
            return _("The item is in use by another app.")
    return _("The item could not be changed.")


def _incomplete_message(error: MoveIncomplete) -> str:
    """Which items of a move stayed where they were, and why."""
    stayed = [(path, reason) for path, reason in error.failed
              if not isinstance(reason, OriginalLeft)]
    left = [path for path, reason in error.failed if isinstance(reason, OriginalLeft)]
    parts = []
    if len(stayed) == 1:
        parts.append(_("“{name}” could not be moved.").format(
            name=os.path.basename(stayed[0][0])))
    elif stayed:
        parts.append(ngettext("{n} item could not be moved: {names}.",
                              "{n} items could not be moved: {names}.", len(stayed)).format(
            n=len(stayed), names=", ".join(_("“{name}”").format(name=os.path.basename(path))
                                           for path, _reason in stayed)))
    if stayed:
        parts.append(error_message(stayed[0][1]))
    for path in left:
        parts.append(_("“{name}” was moved, but some of it could not be removed from where "
                       "it was.").format(name=os.path.basename(path)))
    return " ".join(parts)


def show_error(parent: Optional[QWidget], title: str, error: BaseException) -> None:
    """Tell the person what the disk said, in words."""
    alerts.inform(parent, title=title, message=error_message(error))


# -- messages ----------------------------------------------------------------------

def links_message(changed: Sequence[str], skipped: Sequence[str],
                  not_updated: Sequence[str] = ()) -> str:
    """What a rename or move did to the links of other notes ("" when
    nothing): how many were updated, how many open notes with unsaved
    changes were left to the editor, and how many could not be updated."""
    parts = []
    if changed:
        parts.append(ngettext("Updated links in {n} note.", "Updated links in {n} notes.",
                              len(changed)).format(n=len(changed)))
    if skipped:
        parts.append(ngettext("{n} open note with unsaved changes was not updated.",
                              "{n} open notes with unsaved changes were not updated.",
                              len(skipped)).format(n=len(skipped)))
    if not_updated:
        parts.append(ngettext("The links in {n} note could not be updated.",
                              "The links in {n} notes could not be updated.",
                              len(not_updated)).format(n=len(not_updated)))
    return " ".join(parts)


def _links_said(say: Callable[[str], None], libraries=None) -> Callable[[LinkReport], None]:
    """What tells the person, once the links followed, what that did. A
    window that points the links of its open notes with unsaved changes
    where their files went, in their text, says so on the library
    (``open_notes_follow_in_editor``): the library leaves those notes
    alone on the disk, and the message need not name them."""
    follow = bool(getattr(libraries, "open_notes_follow_in_editor", False))

    def said(report: LinkReport) -> None:
        left_open = [] if follow else report.left_open
        message = links_message(report.changed, left_open, report.not_updated)
        if message:
            say(message)
    return said


def goes_into_itself(paths: Iterable[str], folder: str) -> bool:
    """Whether putting ``paths`` into ``folder`` would put a folder inside
    itself (a drop there is refused before it is made)."""
    return any(folder_library.goes_into_itself(os.path.abspath(p), os.path.abspath(folder))
               for p in paths)


# -- operations ------------------------------------------------------------------

def _is_folder(path: str) -> bool:
    return os.path.isdir(path) and not file_kinds.is_package(path)


def _new_name_for(parent, path: str) -> Optional[str]:
    """Ask for ``path``'s new name. A note is named without its
    extension, which it keeps; any other file keeps its extension unless
    the person changes it on purpose and says so."""
    name = os.path.basename(path.rstrip("/\\"))
    if _is_folder(path):
        typed = ask_name(parent, _("Rename Folder"), _("Name:"), name, len(name))
        return typed
    stem, ext = os.path.splitext(name)
    kind = file_kinds.kind_of_path(path)
    if kind == file_kinds.NOTE:
        typed = ask_name(parent, _("Rename Note"), _("Name:"), stem, len(stem))
        if typed is None:
            return None
        new_ext = os.path.splitext(typed)[1]
        if new_ext.lower() in file_kinds.NOTE_EXTS:
            return typed
        if new_ext and file_kinds.kind_of_path(typed) != file_kinds.OTHER:
            if ask_extension_change(parent, ext, new_ext):
                return typed
        return typed + ext
    typed = ask_name(parent, _("Rename File"), _("Name:"), name, len(stem))
    if typed is None:
        return None
    new_ext = os.path.splitext(typed)[1]
    if ext and new_ext.lower() != ext.lower():
        if not ask_extension_change(parent, ext, new_ext):
            return typed + ext
    return typed


def rename(parent, libraries, path: str, unsaved: UnsavedNotes = no_unsaved_notes,
           say: Callable[[str], None] = lambda text: None,
           done: Optional[Callable[[str], None]] = None) -> Optional[str]:
    """Ask for a new name and rename ``path``. ``done(new path)`` hears
    where it is when the rename is made, and what the links did is said
    once they followed: at once, or after the library's earlier
    operations when it works in the background. The new path is returned
    when it is known at once, else None."""
    new_name = _new_name_for(parent, path)
    if not new_name or new_name == os.path.basename(path.rstrip("/\\")):
        return None
    title = _("Couldn't rename “{name}”").format(name=os.path.basename(path))
    known: List[str] = []

    def finished(result, error) -> None:
        if error is not None:
            show_error(parent, title, error)
        if result is not None:
            known.append(result.new_path)
            if done is not None:
                done(result.new_path)

    try:
        libraries.rename(path, new_name, skip_notes=unsaved,
                         on_links=_links_said(say, libraries), on_done=finished)
    except (OSError, ValueError) as error:
        show_error(parent, title, error)
        return None
    return known[0] if known else None


def move(parent, libraries, paths: Sequence[str], folder: str,
         unsaved: UnsavedNotes = no_unsaved_notes,
         say: Callable[[str], None] = lambda text: None,
         done: Optional[Callable[[List[str]], None]] = None) -> List[str]:
    """Move ``paths`` into ``folder``. ``done(paths where they are now)``
    hears where they went when the move is made (those that could not
    move are named in one calm message and left out); they are returned
    too when known at once."""
    paths = [p for p in paths if os.path.dirname(os.path.abspath(p)) != os.path.abspath(folder)]
    if not paths:
        return []
    title = (_("Couldn't move “{name}”").format(name=os.path.basename(paths[0]))
             if len(paths) == 1 else _("Couldn't move the items"))
    known: List[List[str]] = []

    def finished(result, error) -> None:
        if error is not None:
            show_error(parent, title, error)
        if result is None:
            return
        where = [result.moved[os.path.abspath(p)] for p in paths
                 if os.path.abspath(p) in result.moved]
        known.append(where)
        if done is not None and where:
            done(where)

    try:
        libraries.move(list(paths), folder, skip_notes=unsaved,
                       on_links=_links_said(say, libraries), on_done=finished)
    except (OSError, ValueError) as error:
        show_error(parent, title, error)
        return []
    return known[0] if known else []


def copy_in(parent, libraries, paths: Sequence[str], folder: str) -> List[str]:
    """Copy ``paths`` into ``folder`` (Finder names for taken names)."""
    paths = [p for p in paths if os.path.lexists(p)]
    if not paths:
        return []
    try:
        return libraries.copy_in(list(paths), folder)
    except (OSError, ValueError) as error:
        show_error(parent, _("Couldn't copy the items"), error)
        return []


def trash(parent, libraries, paths: Sequence[str]) -> bool:
    """Ask, then move ``paths`` to the Trash; whether they went."""
    paths = list(paths)
    if not paths or not confirm_trash(parent, paths):
        return False
    try:
        libraries.trash(paths)
    except OSError as error:
        # The platform's own word for it, as the tab's Delete File… says.
        show_error(parent, _("Couldn't move to the Recycle Bin") if sys.platform == "win32"
                   else _("Couldn't move to the Trash"), error)
        return False
    return True


def new_folder(parent, libraries, folder: str) -> Optional[str]:
    try:
        return libraries.new_folder(folder)
    except (OSError, ValueError) as error:
        show_error(parent, _("Couldn't make a folder"), error)
        return None


def new_note(parent, libraries, folder: str) -> Optional[str]:
    try:
        return libraries.new_note(folder)
    except (OSError, ValueError) as error:
        show_error(parent, _("Couldn't make a note"), error)
        return None


def add_files(parent, libraries, folder: str) -> List[str]:
    paths = choose_files(parent, os.path.expanduser("~"))
    return copy_in(parent, libraries, paths, folder) if paths else []


def copy_paths(paths: Iterable[str]) -> None:
    QGuiApplication.clipboard().setText("\n".join(paths))


def reveal(path: str) -> bool:
    return default_apps.reveal(path)


def fill_open_with(menu: QMenu, path: str, parent: Optional[QWidget], *,
                   apps: Optional[Sequence[default_apps.AppChoice]] = None,
                   also: Sequence[Tuple[str, Callable[[], None]]] = ()) -> None:
    """``menu`` as the Open With menu of ``path``, the file list's and a
    viewer's alike: the apps that open the file, the default one first,
    marked so and set apart from the others; then what ``also`` offers
    (a text, and what choosing it does); then Other \u2026 to choose any
    app. ``apps`` are the apps already found, else they are asked for."""
    menu.clear()
    if apps is None:
        apps = default_apps.other_apps(path)
    for number, choice in enumerate(apps):
        text = _("{app} (default)").format(app=choice.name) if number == 0 else choice.name
        act = menu.addAction(text)
        act.triggered.connect(lambda _checked=False, c=choice: default_apps.open_with(path, c))
        if number == 0 and len(apps) > 1:
            menu.addSeparator()
    if apps:
        menu.addSeparator()
    for text, slot in also:
        act = menu.addAction(text)
        act.triggered.connect(lambda _checked=False, s=slot: s())
    if also:
        menu.addSeparator()
    other = menu.addAction(_("Other\u2026"))
    other.setObjectName("open_with_other")
    other.triggered.connect(lambda: default_apps.choose_app_and_open(path, parent))
