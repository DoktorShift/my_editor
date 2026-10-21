# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folder on disk the sidebar shows, and what can be done with the
files in it.

The disk is the truth: there is no database and no copy. A folder's
listing is read when it is asked for; a recursive index of every file
(for "All Files", "Recent" and the counts per kind) is built on a
worker thread and handed to the GUI thread by a signal, so a large
folder never stops the window; what the sidebar counts and the lists
sort is worked out there too. The index is bounded (INDEX_LIMIT files,
FOLDER_LIMIT folders looked into), and never goes into hidden folders or into macOS document
packages, which are one file to a person.

Changes made elsewhere (the Finder, another app, a sync service) are
seen through QFileSystemWatcher, but only on the library folder and the
folders on screen (``watch``), and settled for a moment before
``changed`` is emitted, as a burst of writes is one change to a person.
The library's own operations emit ``changed`` at once.

Renaming or moving a note takes its pictures along: the ``<stem>_media``
folder the editor writes them to (rmarkdown.media_dir_for) is renamed
or moved with it, and the links of every note that pointed at what
moved are updated (link_rewrite.py). Nothing is ever replaced: an
operation that would replace a file raises FileExistsError before it
starts, and new files get Finder-style names ("Untitled 2.md"); the
rename that gives a file its name is one step that refuses to replace
(rename_exclusive), so another app that takes the name in the moment
between is not overwritten either. A folder never goes into itself,
also not by way of a symbolic link. A move to another disk copies,
checks the copy, and only then removes the original; an item the disk
refuses to move stays where it was, and MoveIncomplete names it after
the links of what did move were updated. Deleting moves to the
system's Trash.
"""

from __future__ import annotations

import errno
import heapq
import logging
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

import background
import file_kinds
import link_rewrite
from atomic_file import free_path
from i18n import _

log = logging.getLogger(__name__)

# The most files the recursive index takes in, and the most folders it
# looks into for them.
INDEX_LIMIT = 20_000
FOLDER_LIMIT = 20_000
# How many folders' last listings are kept to read them again quickly,
# and how old one may be before every item in it is looked at again.
LISTINGS_KEPT = 16
FULL_LOOK_SECONDS = 20.0
# How long the watcher waits for a folder to settle before it says it changed.
SETTLE_MS = 300
# A package's size is the sum of the files in it, counted over this many
# of its entries (files and folders) at most.
_PACKAGE_COUNT_LIMIT = file_kinds.PACKAGE_WALK_LIMIT
# The size of a package in a listing, before anything has counted it.
UNMEASURED = -1
# What a name may not hold on any of macOS, Windows and Linux.
_FORBIDDEN = re.compile(r'[\x00-\x1f\x7f:*?"<>|]')
_RESERVED = re.compile(r"\A(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?\Z", re.I)
_NAME_BYTES = 240


@dataclass(frozen=True)
class Entry:
    """A file or folder in the library, as the sidebar lists it."""
    path: str
    name: str
    kind: str          # one of file_kinds.KINDS, or file_kinds.FOLDER
    is_dir: bool       # a folder to go into; a document package is a file
    size: int          # bytes; UNMEASURED for a package in a listing
    mtime: float


@dataclass(frozen=True)
class RenameResult:
    new_path: str
    changed_notes: List[str] = field(default_factory=list)
    # What really moved on the disk: the item, and a note's media folder
    # when it was renamed with it (old path to new path).
    moves: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MoveResult:
    moved: Dict[str, str] = field(default_factory=dict)
    changed_notes: List[str] = field(default_factory=list)


def natural_key(name: str) -> tuple:
    """Sort key that reads numbers as numbers ("Note 2" before "Note 10")
    and ignores case and Unicode normalization."""
    text = unicodedata.normalize("NFC", name).casefold()
    chunks = re.split(r"(\d+)", text)
    return tuple(int(chunk) if i % 2 else chunk for i, chunk in enumerate(chunks)), name


def safe_name(text: str, fallback: str) -> str:
    """``text`` as a file name every system accepts: no path separators
    or characters Windows refuses, no leading dot (which would hide it),
    no trailing dot or space, not a reserved Windows device name, not
    longer than file systems allow. ``fallback`` when nothing is left."""
    name = unicodedata.normalize("NFC", text or "")
    name = name.replace("/", "-").replace("\\", "-")
    name = _FORBIDDEN.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip().lstrip(".").rstrip(". ")
    if _RESERVED.match(name):
        name = "_" + name
    while len(name.encode("utf-8")) > _NAME_BYTES:
        name = name[:-1].rstrip(". ")
    return name or fallback


# What Windows refuses in a name, as Explorer names it when a name
# holds one of these.
_WINDOWS_REFUSED = '\\ / : * ? " < > |'


def name_problem(name: str, platform: Optional[str] = None) -> Optional[str]:
    """Why ``name`` cannot be given to an item on this system (or on
    ``platform``, a ``sys.platform`` value), as a sentence for the
    person; None when it can. Every system refuses an empty name, "." and
    "..", a path separator and a NUL. Windows also refuses the characters
    Explorer names, control characters, a name that ends with a space or
    a period, and the names it keeps for its devices (CON, NUL, COM1 and
    on, with any extension); asked anyway, it answers with an error that
    says nothing about the name."""
    platform = sys.platform if platform is None else platform
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        return _("“{name}” cannot be used as a name.").format(name=name)
    if platform != "win32":
        return None
    if _FORBIDDEN.search(name):
        return _("A name can't contain any of these characters: {characters}").format(
            characters=_WINDOWS_REFUSED)
    if name[-1] in ". ":
        return _("A name can't end with a space or a period.")
    if _RESERVED.match(name.split(".")[0].rstrip(" ")):
        return _("“{name}” is a name Windows keeps for itself. Choose a different name.").format(
            name=name)
    return None


def _check_name(name: str) -> None:
    problem = name_problem(name)
    if problem is not None:
        raise ValueError(problem)


def media_folder_of(note: str) -> str:
    """The folder a note's pictures are saved to: ``<stem>_media`` next
    to it (as rmarkdown.media_dir_for)."""
    stem = os.path.splitext(os.path.basename(note))[0]
    return os.path.join(os.path.dirname(note), f"{stem}_media")


def _own_media(path: str) -> Optional[str]:
    """A note's existing media folder, spelled as the disk has it, None
    for anything else."""
    if not link_rewrite.is_note(path) or not os.path.isfile(path):
        return None
    media = media_folder_of(path)
    return _as_on_disk(media) if os.path.isdir(media) else None


def _as_on_disk(path: str) -> str:
    """``path`` with its last name spelled the way the disk has it: a disk
    that ignores case finds "trip_media" by "Trip_media", and what moves
    or is copied keeps the name it has."""
    folder, name = os.path.split(path)
    try:
        wanted = os.lstat(path)
        with os.scandir(folder or ".") as found:
            spelled = None
            for entry in found:
                if entry.name == name:
                    return path
                if spelled is None and entry.inode() == wanted.st_ino \
                        and entry.stat(follow_symlinks=False).st_dev == wanted.st_dev:
                    spelled = entry.path
    except OSError:
        return path
    return spelled or path


def _package_size(path: str) -> int:
    """A package's size, counted over at most _PACKAGE_COUNT_LIMIT of its
    entries, folders as well as files (file_kinds.package_size)."""
    return file_kinds.package_size(path, _PACKAGE_COUNT_LIMIT)


def _entry(item: os.DirEntry, measure_packages: bool = False) -> Optional[Entry]:
    """The Entry for a directory item, None when it cannot be read. A
    package's size is only measured when ``measure_packages`` (on the
    index's worker thread); a listing gives it UNMEASURED, as a listing
    is read on the GUI thread and a package can hold any number of
    folders."""
    try:
        stat = item.stat()
        is_dir = item.is_dir()
    except OSError:
        try:
            stat = item.stat(follow_symlinks=False)  # a link to nothing
            is_dir = False
        except OSError:
            return None
    if is_dir and file_kinds.extension(item.name) not in file_kinds.PACKAGE_EXTS:
        return Entry(item.path, item.name, file_kinds.FOLDER, True, 0, stat.st_mtime)
    if is_dir:
        size = _package_size(item.path) if measure_packages else UNMEASURED
    else:
        size = stat.st_size
    return Entry(item.path, item.name, file_kinds.kind_of_path(item.name), False, size,
                 stat.st_mtime)


def _sorted(entries: Iterable[Entry]) -> List[Entry]:
    """Folders first, then files, each in natural order."""
    return sorted(entries, key=lambda e: (not e.is_dir, natural_key(e.name)))


def build_index(root: str, limit: int, cancelled,
                folder_limit: Optional[int] = None) -> Tuple[List[Entry], bool]:
    """Every file under ``root``, nearest folders first, and whether the
    walk stopped short: at ``limit`` files, or after looking into
    ``folder_limit`` folders (FOLDER_LIMIT), which bounds a walk through
    a tree of empty folders. ``cancelled.is_set()`` (a background.Token,
    or a threading.Event) stops it early. Runs on a worker thread."""
    folder_limit = FOLDER_LIMIT if folder_limit is None else folder_limit
    files: List[Entry] = []
    folders = 0
    queue = deque([root])
    while queue:
        if cancelled.is_set():
            return files, False
        folder = queue.popleft()
        folders += 1
        if folders > folder_limit:
            return files, True
        try:
            with os.scandir(folder) as found:
                items = list(found)
        except OSError:
            continue
        for item in items:
            if file_kinds.is_hidden_name(item.name):
                continue
            entry = _entry(item, measure_packages=True)
            if entry is None:
                continue
            if entry.is_dir:
                if not item.is_symlink():          # never round a loop of links
                    queue.append(item.path)
            else:
                if len(files) >= limit:
                    return files, True
                files.append(entry)
    return files, False


@dataclass
class _Index:
    """The index and what the sidebar and the lists read from it, worked
    out on the worker thread so the GUI thread only looks them up: the
    files by name (natural order), how many there are of each kind, and
    how many lie directly in the folder and under each of its folders."""
    files: List[Entry] = field(default_factory=list)
    by_name: List[Entry] = field(default_factory=list)
    keys: Dict[str, tuple] = field(default_factory=dict)
    counts: Dict[str, int] = field(default_factory=dict)
    direct: int = 0
    under: Dict[str, int] = field(default_factory=dict)
    truncated: bool = False


def summarize(root: str, files: List[Entry], truncated: bool) -> _Index:
    keys = {entry.path: natural_key(entry.name) for entry in files}
    index = _Index(files, sorted(files, key=lambda e: keys[e.path]), keys,
                   truncated=truncated)
    prefix = root.rstrip(os.sep) + os.sep
    for entry in files:
        index.counts[entry.kind] = index.counts.get(entry.kind, 0) + 1
        rest = entry.path[len(prefix):] if entry.path.startswith(prefix) else entry.name
        first, sep, _deeper = rest.partition(os.sep)
        if not sep:
            index.direct += 1
        else:
            sub = prefix + first
            index.under[sub] = index.under.get(sub, 0) + 1
    return index


def _index_of(root: str, cancelled: background.Token) -> _Index:
    """The index of ``root``, built on a worker thread; an empty one (and
    a line in the log) when reading the folder failed."""
    try:
        files, truncated = build_index(root, INDEX_LIMIT, cancelled)
        return summarize(root, files, truncated)
    except Exception:                                   # noqa: BLE001
        log.exception("Indexing %s failed", root)
        return _Index()


class FolderLibrary(QObject):
    """One folder on disk, its listings, its index and its operations."""

    changed = Signal(str)          # a folder whose listing changed; "" = rescan everything
    index_ready = Signal()         # the recursive index (re)built

    def __init__(self, root: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._root: Optional[str] = None
        self._index = _Index()
        self._indexing = False
        self._generation = 0
        self._cancel = background.Token()
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._folder_changed)
        self._pending: Set[str] = set()
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self._settled)
        self._reindex = QTimer(self)
        self._reindex.setSingleShot(True)
        self._reindex.setInterval(SETTLE_MS)
        self._reindex.timeout.connect(self._start_index)
        self._shown: List[str] = []
        # The last listings read: folder -> ({name: (file number, Entry,
        # sort key)}, the names in order, when every item was last looked at).
        self._listings: Dict[str, tuple] = {}
        if root:
            self.set_root(root)

    # -- the folder -------------------------------------------------------

    def root(self) -> Optional[str]:
        return self._root

    def set_root(self, path: Optional[str]) -> None:
        """Show ``path`` (None: no folder); the index is built anew."""
        self._root = os.path.abspath(path) if path else None
        self._shown = []
        self._index = _Index()
        self._rewatch()
        self.changed.emit("")
        self._start_index()

    def refresh(self) -> None:
        """Read everything again, as after the disk changed unseen."""
        self.changed.emit("")
        self._start_index()

    def is_indexing(self) -> bool:
        return self._indexing

    def stop_indexing(self) -> None:
        """Stop building the index now (as the app quits: nothing waits
        for it). The next change on the disk builds it again."""
        self._cancel.cancel()

    def index_truncated(self) -> bool:
        """Whether the last index stopped at INDEX_LIMIT files or
        FOLDER_LIMIT folders."""
        return self._index.truncated

    # -- listings ---------------------------------------------------------

    def entries(self, folder: str) -> List[Entry]:
        """The files and folders directly in ``folder``, hidden ones left
        out, folders first, in natural order.

        The folder's names are read anew each time (the disk is the
        truth). A large folder is read again often (after every change in
        it), so what did not change is not asked about again: an item
        under the same name and the same file number (a save replaces a
        file, so it gets a new number) keeps its Entry and its place in
        the order, and every item is looked at in full again once its
        listing is FULL_LOOK_SECONDS old (for a file another app changed
        where it lies)."""
        try:
            with os.scandir(folder) as found:
                items = [item for item in found if not file_kinds.is_hidden_name(item.name)]
        except OSError:
            return []
        known, order, looked = self._listings.pop(folder, ({}, [], 0.0))
        now = time.monotonic()
        full = now - looked >= FULL_LOOK_SECONDS
        fresh: Dict[str, tuple] = {}
        same = True
        for item in items:
            try:
                number = item.inode()
            except OSError:
                number = None
            hit = known.get(item.name)
            if not full and hit is not None and number is not None and hit[0] == number:
                fresh[item.name] = hit
                continue
            entry = _entry(item)
            if entry is None:
                same = False
                continue
            key = (not entry.is_dir, natural_key(entry.name))
            if hit is None or hit[1] != entry or hit[2] != key:
                same = False
            fresh[item.name] = (number, entry, key)
        if not (same and len(fresh) == len(known)):
            order = [name for name, _value in sorted(fresh.items(), key=lambda kv: kv[1][2])]
        self._listings[folder] = (fresh, order, now if full else looked)
        while len(self._listings) > LISTINGS_KEPT:
            self._listings.pop(next(iter(self._listings)))
        return [fresh[name][1] for name in order]

    def subfolders(self, folder: str) -> List[str]:
        """The folders directly in ``folder`` (not packages, not hidden),
        in natural order."""
        return [e.path for e in self.entries(folder) if e.is_dir]

    def all_files(self, kinds: Optional[Set[str]] = None) -> List[Entry]:
        """Every file in the index, or those of ``kinds``, in natural order
        (sorted once, on the worker that built the index)."""
        files = self._index.by_name
        return list(files) if kinds is None else [e for e in files if e.kind in kinds]

    def indexed(self) -> List[Entry]:
        """Every file in the index, in the order the walk found them."""
        return self._index.files

    def name_key(self, entry: Entry) -> tuple:
        """``natural_key`` of ``entry``'s name, from the index when it is there."""
        return self._index.keys.get(entry.path) or natural_key(entry.name)

    def recent(self, limit: int = 50) -> List[Entry]:
        """The files changed most lately, newest first."""
        return heapq.nlargest(max(0, limit), self._index.files, key=lambda e: e.mtime)

    def counts(self) -> Dict[str, int]:
        """How many files of each kind the index holds."""
        return dict(self._index.counts)

    def folder_counts(self) -> Tuple[int, Dict[str, int]]:
        """How many indexed files lie directly in the folder, and how many
        under each of its folders."""
        return self._index.direct, dict(self._index.under)

    def watch(self, folders: List[str]) -> None:
        """Watch ``folders`` (those on screen) besides the library folder."""
        self._shown = [os.path.abspath(f) for f in folders if f]
        self._rewatch()

    # -- operations -------------------------------------------------------

    def new_note(self, folder: str, title: Optional[str] = None) -> str:
        """Make an empty note in ``folder``: "Untitled.md", or named after
        ``title``, numbered when the name is taken. Returns its path."""
        stem = safe_name(title, _("Untitled")) if title else _("Untitled")
        while True:
            path = free_path(os.path.join(folder, stem + ".md"))
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
            except FileExistsError:
                continue                      # made in the moment between
            os.close(fd)
            break
        self._done([folder])
        return path

    def new_folder(self, folder: str, name: Optional[str] = None) -> str:
        """Make a folder in ``folder``, "New Folder" unless ``name`` is
        given, numbered when the name is taken. Returns its path."""
        name = safe_name(name, _("New Folder")) if name else _("New Folder")
        while True:
            path = free_path(os.path.join(folder, name))
            try:
                os.mkdir(path)
            except FileExistsError:
                continue
            break
        self._done([folder])
        return path

    def rename(self, path: str, new_name: str, *, rewrite_links: bool = True,
               skip_notes: Set[str] = frozenset()) -> RenameResult:
        """Give ``path`` the name ``new_name`` in its folder, its media
        folder too when it is a note, and point the notes' links at the
        new name. Raises FileExistsError when the name is taken (also when
        another app takes it in the moment between), ValueError for a name
        that is no name, OSError."""
        _check_name(new_name)
        path = os.path.abspath(path)
        folder = os.path.dirname(path)
        new_path = os.path.join(folder, new_name)
        if new_path == path:
            return RenameResult(path, [])
        _refuse_taken(path, new_path)
        moves = {path: new_path}
        media = _own_media(path)
        new_media = media_folder_of(new_path) if media else None
        if media and new_media != media:
            if link_rewrite.is_note(new_path):
                _refuse_taken(media, new_media)
                moves[media] = new_media
        rename_exclusive(path, new_path)
        if media in moves:
            try:
                rename_exclusive(media, moves[media])
            except OSError as error:
                log.warning("Renamed %s but not its pictures: %s", path, error)
                del moves[media]
        changed = self._rewrite(moves, rewrite_links, skip_notes)
        self._done([folder])
        return RenameResult(new_path, changed, dict(moves))

    def move(self, paths: List[str], folder: str, *, rewrite_links: bool = True,
             skip_notes: Set[str] = frozenset()) -> MoveResult:
        """Move ``paths`` into ``folder``, notes with their media folders,
        and point the notes' links at the new places. Nothing moves when
        any of them would replace something (FileExistsError) or a folder
        would go into itself, also by way of a symbolic link (ValueError).
        An item the disk refuses to move stays where it is (its pictures
        with it) while the others move; then MoveIncomplete says which,
        after the links of what did move were updated."""
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), folder)
        given = _distinct(paths)
        # What is in a folder that moves goes with it.
        given = [p for p in given
                 if not any(o != p and link_rewrite.is_within(p, o) for o in given)]
        plan: List[Tuple[str, str, Optional[str]]] = []     # (old, new, the note it goes with)
        taken: Set[str] = set()

        def add(old: str, note: Optional[str] = None) -> None:
            new = os.path.join(folder, os.path.basename(old))
            if goes_into_itself(old, folder):
                raise ValueError(_("“{name}” cannot be moved into itself.").format(
                    name=os.path.basename(old)))
            if new in taken or os.path.lexists(new):
                raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), new)
            taken.add(new)
            plan.append((old, new, note))

        for path in given:
            if not os.path.lexists(path):
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)
            if link_rewrite.is_within(os.path.dirname(path), folder) \
                    and link_rewrite.is_within(folder, os.path.dirname(path)):
                continue                                # already there
            add(path)
            media = _own_media(path)
            if media and not any(_same_item(media, other) for other in given):
                add(media, path)
        moves: Dict[str, str] = {}
        failed: List[Tuple[str, BaseException]] = []
        stayed: Set[str] = set()
        for old, new, note in plan:
            if note in stayed:
                continue                    # the pictures stay with the note that stayed
            try:
                move_item(old, new)
            except OriginalLeft as error:
                log.warning("Moved %s, but its original could not be removed: %s", old, error)
                moves[old] = new
                failed.append((old, error))
            except OSError as error:
                log.warning("Cannot move %s: %s", old, error)
                failed.append((old, error))
                stayed.add(old)
            else:
                moves[old] = new
        changed = self._rewrite(moves, rewrite_links, skip_notes) if moves else []
        if plan:
            self._done(sorted({os.path.dirname(old) for old, _new, _note in plan}) + [folder])
        result = MoveResult(moves, changed)
        if failed:
            raise MoveIncomplete(result, failed)
        return result

    def copy_in(self, paths: List[str], folder: str) -> List[str]:
        """Copy files and whole folders into ``folder`` (from outside the
        library, or a duplicate), never replacing anything: a taken name
        is numbered as the Finder does, also when another app takes it in
        the moment the copy is made. A note brings its media folder. A
        folder is never copied into itself, also not by way of a symbolic
        link (ValueError, before anything is copied). Returns the new
        paths, in the order given."""
        folder = os.path.abspath(folder)
        given = _distinct(paths)
        for source in given:
            for item in (source, _own_media(source)):
                if item and goes_into_itself(item, folder):
                    raise ValueError(_("“{name}” cannot be copied into itself.").format(
                        name=os.path.basename(item)))
        made: List[str] = []
        taken: Set[str] = set()
        try:
            for source in given:
                media = _own_media(source)
                if media and not any(_same_item(media, other) for other in given):
                    target = self._copy_note_with_media(source, media, folder, taken)
                else:
                    target = copy_exclusive(source, folder, os.path.basename(source), taken)
                taken.add(target)
                made.append(target)
        finally:
            if made:
                self._done([folder])
        return made

    def _copy_note_with_media(self, note: str, media: str, folder: str,
                              taken: Set[str]) -> str:
        """Copy a note and its media folder into ``folder`` under names that
        belong together ("X 2.md" and "X 2_media"): a number is used only
        when both names are free, so a copy never takes someone else's
        folder for its pictures (nor would the Trash, which takes a note's
        media folder along). The copy's links follow its folder's name."""
        name = os.path.basename(note)
        while True:
            candidate = free_path(os.path.join(folder, name), taken)
            pictures = media_folder_of(candidate)
            if pictures in taken or os.path.lexists(pictures):
                taken.add(candidate)
                continue
            try:
                copy_exclusive(note, folder, os.path.basename(candidate), taken, numbered=False)
            except FileExistsError:
                taken.add(candidate)                # taken in the moment between
                continue
            try:
                copy_exclusive(media, folder, os.path.basename(pictures), taken, numbered=False)
            except FileExistsError:
                os.remove(candidate)                # its pictures' name went meanwhile
                taken.add(candidate)
                continue
            except (OSError, shutil.Error) as error:
                log.warning("Copied %s without its pictures: %s", candidate, error)
                return candidate
            taken.add(pictures)
            written_as = os.path.join(folder, os.path.basename(media))
            if written_as != pictures:
                link_rewrite.rewrite_note(candidate, {written_as: pictures})
            return candidate

    def trash(self, paths: List[str]) -> None:
        """Move ``paths`` to the system's Trash, a note's media folder with
        it (once, whatever the case of its name). Raises the first OSError
        after trying them all."""
        from send2trash import send2trash
        given = _distinct(paths)
        given = [p for p in given
                 if not any(o != p and link_rewrite.is_within(p, o) for o in given)]
        items = list(given)
        for path in given:
            media = _own_media(path)
            if media and not any(_same_item(media, item) for item in items):
                items.append(media)
        failure: Optional[BaseException] = None
        folders = []
        for path in items:
            try:
                send2trash(path)
                folders.append(os.path.dirname(path))
            except OSError as error:
                log.warning("Cannot move %s to the Trash: %s", path, error)
                failure = failure or error
        if folders:
            self._done(sorted(set(folders)))
        if failure is not None:
            raise failure

    def references_to(self, paths: List[str]) -> List[str]:
        """The notes with a link to any of ``paths`` (or to anything in
        them), other than notes that are themselves among them: the notes
        whose links a rename or move of ``paths`` changes."""
        targets = [os.path.abspath(p) for p in paths]
        if not targets:
            return []
        root = self._root or os.path.dirname(targets[0])
        found = link_rewrite.find_references(root, targets)
        notes = {note for linked in found.values() for note in linked
                 if not any(link_rewrite.is_within(note, t) for t in targets)}
        return sorted(notes, key=natural_key)

    # -- inside -----------------------------------------------------------

    def _rewrite(self, moves: Dict[str, str], rewrite_links: bool,
                 skip_notes: Set[str]) -> List[str]:
        if not rewrite_links or not moves:
            return []
        root = self._root or os.path.commonpath(
            [os.path.dirname(p) for p in list(moves) + list(moves.values())])
        try:
            return link_rewrite.rewrite_after_moves(root, moves, skip_notes)
        except Exception:                                   # noqa: BLE001
            log.exception("Updating links after a move failed")
            return []

    def _done(self, folders: List[str]) -> None:
        """After an operation of the library's own: the folders' listings
        changed now, the index follows shortly."""
        for folder in dict.fromkeys(os.path.abspath(f) for f in folders):
            self.changed.emit(folder)
        if self._root:
            self._reindex.start()

    def _rewatch(self) -> None:
        wanted = [p for p in dict.fromkeys(([self._root] if self._root else []) + self._shown)
                  if os.path.isdir(p)]
        current = self._watcher.directories()
        gone = [p for p in current if p not in wanted]
        if gone:
            self._watcher.removePaths(gone)
        new = [p for p in wanted if p not in current]
        if new:
            self._watcher.addPaths(new)

    def _folder_changed(self, folder: str) -> None:
        self._pending.add(folder)
        self._settle.start()

    def _settled(self) -> None:
        pending, self._pending = sorted(self._pending), set()
        for folder in pending:
            self.changed.emit(folder)
        self._rewatch()        # a folder made again after it was deleted
        self._start_index()

    def _start_index(self) -> None:
        self._reindex.stop()
        self._cancel.cancel()
        self._cancel = background.Token()
        self._generation += 1
        if not self._root:
            self._index, self._indexing = _Index(), False
            self.index_ready.emit()
            return
        self._indexing = True
        root, generation, token = self._root, self._generation, self._cancel
        background.run(lambda: _index_of(root, token),
                       lambda index: self._index_built(generation, index),
                       receiver=self, token=token)

    def _index_built(self, generation: int, index: _Index) -> None:
        if generation != self._generation:
            return                                   # a newer one is on its way
        self._index = index
        self._indexing = False
        self.index_ready.emit()


def _refuse_taken(path: str, new_path: str) -> None:
    """Raise FileExistsError when ``new_path`` is another item than
    ``path`` (a change of case only is the same item on a Mac)."""
    if os.path.lexists(new_path) and not _same_item(path, new_path):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), new_path)


def _same_item(one: str, other: str) -> bool:
    """Whether two paths name the same item on the disk: the same file,
    folder or link, however its name is written (case, normalization).
    The disk decides, so a volume that tells cases apart is not folded."""
    if one == other:
        return True
    try:
        first, second = os.lstat(one), os.lstat(other)
    except OSError:
        return False
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def _distinct(paths: Iterable[str]) -> List[str]:
    """``paths`` made absolute, each item once (by what it is on the disk)."""
    found: List[str] = []
    for path in map(os.path.abspath, paths):
        if not any(_same_item(path, other) for other in found):
            found.append(path)
    return found


def goes_into_itself(item: str, folder: str) -> bool:
    """Whether putting the folder ``item`` into ``folder`` would put it
    inside itself, the way the paths are written or the way they really
    lead (through symbolic links). A link itself moves as a link."""
    if not os.path.isdir(item) or os.path.islink(item):
        return False
    if link_rewrite.is_within(folder, item):
        return True
    return link_rewrite.is_within(os.path.realpath(folder), os.path.realpath(item))


# -- moving and copying without ever replacing ---------------------------------

class OriginalLeft(OSError):
    """An item moved to another disk whose copy is complete where it went,
    while some of the original could not be removed."""


class MoveIncomplete(OSError):
    """Some items of a move stayed where they were. ``result`` is what did
    move (and the notes whose links followed it), ``failed`` each item
    that stayed (or left part of itself behind) with what the disk said."""

    def __init__(self, result: MoveResult, failed: List[Tuple[str, BaseException]]):
        first_path, first_error = failed[0]
        super().__init__(getattr(first_error, "errno", None) or errno.EIO,
                         str(first_error), first_path)
        self.result = result
        self.failed = failed


_RENAME_EXCL = 0x00000004        # renamex_np on macOS
_RENAME_NOREPLACE = 1            # renameat2 on Linux
_AT_FDCWD = -100
_UNSUPPORTED = {errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}
_libc_cache: list = []


def _libc():
    if not _libc_cache:
        try:
            import ctypes
            _libc_cache.append(ctypes.CDLL(None, use_errno=True))
        except (OSError, ImportError, TypeError):
            _libc_cache.append(None)
    return _libc_cache[0]


def _native_rename_exclusive(old: str, new: str) -> bool:
    """Rename with the system's own call that refuses to replace
    (renamex_np with RENAME_EXCL on a Mac, renameat2 with RENAME_NOREPLACE
    on Linux). False when there is no such call or the disk does not
    support it; raises OSError for what the disk says otherwise."""
    import ctypes
    libc = _libc()
    if libc is None:
        return False
    if sys.platform == "darwin":
        call = getattr(libc, "renamex_np", None)
        arguments = (os.fsencode(old), os.fsencode(new), _RENAME_EXCL)
        types = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    elif sys.platform.startswith("linux"):
        call = getattr(libc, "renameat2", None)
        arguments = (_AT_FDCWD, os.fsencode(old), _AT_FDCWD, os.fsencode(new), _RENAME_NOREPLACE)
        types = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    else:
        return False
    if call is None:
        return False
    call.argtypes = types
    call.restype = ctypes.c_int
    if call(*arguments) == 0:
        return True
    code = ctypes.get_errno()
    if code in _UNSUPPORTED:
        return False
    raise OSError(code, os.strerror(code), old, None, new)


def rename_exclusive(old: str, new: str) -> None:
    """Rename (or move on the same disk) ``old`` to ``new`` in one step
    that never replaces anything at ``new``: FileExistsError when
    something is there, also when another app put it there a moment
    before. A change of case only (the same item) is a plain rename.
    EXDEV (another disk) is raised as the disk says it."""
    if os.path.lexists(new):
        if _same_item(old, new):
            os.rename(old, new)
            return
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), new)
    if sys.platform == "win32":
        os.rename(old, new)               # never replaces on Windows
        return
    if _native_rename_exclusive(old, new):
        return
    # A disk without such a call: a file gets a second name, which fails
    # when the name is taken, and then loses the first one.
    if not os.path.isdir(old) or os.path.islink(old):
        try:
            os.link(old, new, follow_symlinks=False)
        except FileExistsError:
            raise
        except OSError as error:
            if error.errno == errno.EXDEV:
                raise
        else:
            try:
                os.unlink(old)
            except OSError:
                os.unlink(new)
                raise
            return
    if os.path.lexists(new):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), new)
    os.rename(old, new)


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.unlink(path)


def _blocked_removal(path: str) -> Optional[str]:
    """The first place in ``path`` that could not be removed after a copy
    (a folder that may not be written in, a locked file), or None."""
    holders = [os.path.dirname(path) or "."]
    items = [path]
    if os.path.isdir(path) and not os.path.islink(path):
        for folder, dirs, files in os.walk(path):
            holders.append(folder)
            items.extend(os.path.join(folder, name) for name in dirs + files)
    for folder in holders:
        if not os.access(folder, os.W_OK | os.X_OK):
            return folder
    locked = getattr(stat, "UF_IMMUTABLE", 0) | getattr(stat, "SF_IMMUTABLE", 0) \
        | getattr(stat, "UF_APPEND", 0) | getattr(stat, "SF_APPEND", 0)
    if locked:
        for item in items:
            try:
                if getattr(os.lstat(item), "st_flags", 0) & locked:
                    return item
            except OSError:
                return item
    return None


def _shape(path: str) -> Dict[str, Tuple[int, int]]:
    """What is at ``path``, for comparing a copy with its original: every
    item by its path inside, with its type and, for a file, its size."""
    def described(item: str) -> Tuple[int, int]:
        info = os.lstat(item)
        return stat.S_IFMT(info.st_mode), info.st_size if stat.S_ISREG(info.st_mode) else 0

    shape = {"": described(path)}
    if os.path.isdir(path) and not os.path.islink(path):
        for folder, dirs, files in os.walk(path):
            for name in dirs + files:
                item = os.path.join(folder, name)
                shape[os.path.relpath(item, path)] = described(item)
    return shape


def _move_across_disks(old: str, new: str) -> None:
    """Move ``old`` to ``new`` on another disk: first make sure the
    original can be removed afterwards, then copy it next to ``new``
    under a hidden name, check that the copy is whole, give it its name
    (never replacing), and only then remove the original. A copy that
    fails, or turns out incomplete, is removed again and the original
    stays as it was."""
    blocked = _blocked_removal(old)
    if blocked is not None:
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), blocked)
    staging = tempfile.mkdtemp(prefix=".myeditor-moving-", dir=os.path.dirname(new))
    try:
        staged = os.path.join(staging, os.path.basename(new))
        _copy(old, staged)
        if _shape(staged) != _shape(old):
            raise OSError(errno.EIO, "The copy is not complete", staged)
        rename_exclusive(staged, new)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    try:
        _remove(old)
    except OSError as error:
        if os.path.isdir(new) and not os.path.islink(new) and os.path.lexists(old):
            # Part of the folder went already; the whole of it is in its
            # new place, so that is where it is.
            raise OriginalLeft(error.errno, error.strerror, error.filename) from error
        _remove(new)                      # the original is whole: the copy goes
        raise


def move_item(old: str, new: str) -> None:
    """Move ``old`` to ``new``, never replacing anything: a rename on the
    same disk, a checked copy and removal across disks. Raises OSError
    (the item stays where it was) or OriginalLeft."""
    try:
        rename_exclusive(old, new)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        _move_across_disks(old, new)


def copy_exclusive(source: str, folder: str, name: str, taken: Set[str],
                   numbered: bool = True) -> str:
    """Copy ``source`` into ``folder`` as ``name``, or as "name 2" and on
    when that is taken (or in ``taken``), never replacing anything: the
    copy is made under a hidden name first and then given its own in one
    step. Without ``numbered`` it is ``name`` or nothing (FileExistsError).
    Returns where it went; a failed copy leaves nothing behind."""
    staging = tempfile.mkdtemp(prefix=".myeditor-copying-", dir=folder)
    try:
        staged = os.path.join(staging, name)
        _copy(source, staged)
        while True:
            target = os.path.join(folder, name)
            if numbered:
                target = free_path(target, taken)
            try:
                rename_exclusive(staged, target)
                return target
            except FileExistsError:
                if not numbered:
                    raise
                taken.add(target)               # taken in the moment between
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _copy(source: str, target: str) -> None:
    if os.path.isdir(source) and not os.path.islink(source):
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target, follow_symlinks=False)
