# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The folders a person keeps in the sidebar, as the Finder keeps its
locations: one FolderLibrary per folder, and what the sidebar's Library
section shows across all of them.

People add the places they work in (a notes folder, a project, a
shared drive); the sidebar lists each one with its subfolders, and
Recents, Notes, Media and Documents gather the files of all of them.
Removing a folder from the sidebar only forgets it here: nothing on the
disk changes. Which folders are kept from one launch to the next is the
host's business (it saves ``roots()``). No file is ever in two sidebar
folders: a folder inside one already is not added, and a folder that
holds some takes their place.

Operations go to the library of the folder they act in. A move between
two folders of the sidebar is one move to a person, so the links of
every note in every folder follow it, not only those in the folder the
files went to; a note's ``<stem>_media`` folder goes with it as always
(FolderLibrary.move). Every note is read once for that.

Reading every note can take a while in a large library, so the window
has the links updated in the background (``links_in_background``): the
files move at once, the notes follow on a worker thread of their own
(``links_progress`` says how far it got, ``on_links`` and
``links_updated`` what it did). One update runs at a time, in order, and
the next operation that changes the disk waits for the one before to
finish, so each sees the disk as the moves before it left it.
"""

from __future__ import annotations

import logging
import os
import heapq
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from PySide6.QtCore import QObject, QTimer, Signal

import background
import link_rewrite
from folder_library import Entry, FolderLibrary, MoveIncomplete, MoveResult, RenameResult, \
    natural_key
from i18n import _

log = logging.getLogger(__name__)

# How often the link worker says how far it got, at most (seconds).
PROGRESS_INTERVAL = 0.1


class LibrarySet(QObject):
    """Every folder in the sidebar, and the files of all of them."""

    changed = Signal(str)          # a folder whose listing changed; "" = rescan everything
    index_ready = Signal()         # one of the indexes (re)built
    roots_changed = Signal()       # a folder was added to or removed from the sidebar
    # What a rename or move did, for the window's tabs to follow: old path
    # -> new path (files and folders, a note's media folder included), and
    # the notes whose links were rewritten on the disk.
    moved = Signal(dict)
    rewritten = Signal(list)
    trashed = Signal(list)         # the paths that went to the Trash
    # While links are updated in the background: notes read, of how many;
    # and what each update did (a LinkReport) when it is done.
    links_progress = Signal(int, int)
    links_updated = Signal(object)

    def __init__(self, roots: Iterable[str] = (), parent=None, *,
                 links_in_background: bool = False):
        super().__init__(parent)
        self._libraries: Dict[str, FolderLibrary] = {}
        # Lists and trashes what is in no sidebar folder (a library
        # without a folder of its own, so it watches and indexes nothing).
        self._loose = FolderLibrary(parent=self)
        self._in_background = links_in_background
        # One update of links at a time, in order.
        self._workers = background.Workers(self, threads=1)
        self._queue: deque = deque()             # the renames and moves waiting their turn
        self._active: Optional[_Operation] = None
        self._hurry = False
        self._job_number = 0
        for root in roots:
            self.add_root(root)

    # -- the folders --------------------------------------------------------

    def roots(self) -> List[str]:
        """The folders in the sidebar, in the order they were added."""
        return list(self._libraries)

    def libraries(self) -> List[FolderLibrary]:
        return list(self._libraries.values())

    def add_root(self, path: str) -> bool:
        """Keep the folder ``path`` in the sidebar. False (and nothing
        changes) when it is no folder, is in the sidebar already, or is
        inside a folder that is, also by way of a symbolic link. A folder
        that holds sidebar folders takes their place: they are its
        subfolders now, and no file is listed or indexed twice."""
        if not path or not os.path.isdir(path):
            return False
        root = os.path.abspath(path)
        real = os.path.realpath(root)
        if any(_overlap(root, real, other) for other in self._libraries):
            return False
        inside = [other for other in self._libraries if _overlap(other, os.path.realpath(other),
                                                                 root, real)]
        for other in inside:
            self._forget(other)
        library = FolderLibrary(parent=self)
        library.changed.connect(self.changed)
        library.index_ready.connect(self.index_ready)
        self._libraries[root] = library
        library.set_root(root)
        self.roots_changed.emit()
        if inside:
            self.changed.emit("")
        return True

    def remove_root(self, path: str) -> bool:
        """Forget the folder ``path`` (only here: the disk is not touched)."""
        root = self._key(path)
        if root is None:
            return False
        self._forget(root)
        self.roots_changed.emit()
        self.changed.emit("")
        self.index_ready.emit()
        return True

    def _forget(self, root: str) -> None:
        library = self._libraries.pop(root)
        library.changed.disconnect(self.changed)
        library.index_ready.disconnect(self.index_ready)
        library.set_root(None)
        library.deleteLater()

    def library_for(self, path: str) -> Optional[FolderLibrary]:
        """The library of the sidebar folder that holds ``path`` (the
        nearest one, should one folder be inside another), or None."""
        if not path:
            return None
        found = [root for root in self._libraries if link_rewrite.is_within(path, root)]
        if not found:
            return None
        return self._libraries[max(found, key=len)]

    def root_of(self, path: str) -> Optional[str]:
        library = self.library_for(path)
        return library.root() if library is not None else None

    def _key(self, path: str) -> Optional[str]:
        for root in self._libraries:
            if link_rewrite.is_within(path, root) and link_rewrite.is_within(root, path):
                return root
        return None

    # -- what is in them ----------------------------------------------------

    def entries(self, folder: str) -> List[Entry]:
        return (self.library_for(folder) or self._loose).entries(folder)

    def subfolders(self, folder: str) -> List[str]:
        return [e.path for e in self.entries(folder) if e.is_dir]

    def _files(self) -> List[Entry]:
        """Every indexed file (no file is in two sidebar folders)."""
        files: List[Entry] = []
        for library in self._libraries.values():
            files.extend(library.indexed())
        return files

    def all_files(self, kinds: Optional[Set[str]] = None) -> List[Entry]:
        """Every indexed file, or those of ``kinds``, in natural order: each
        index is sorted on the worker that built it, so here they are only
        merged."""
        libraries = list(self._libraries.values())
        if len(libraries) == 1:
            return libraries[0].all_files(kinds)
        merged = heapq.merge(*[[(library.name_key(e), e.path, e) for e in library.all_files(kinds)]
                               for library in libraries])
        return [entry for _key, _path, entry in merged]

    def recent(self, limit: int = 50) -> List[Entry]:
        """The files changed most lately, newest first (by time only: no
        name is compared)."""
        return heapq.nlargest(max(0, limit), self._files(), key=lambda e: e.mtime)

    def counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for library in self._libraries.values():
            for kind, count in library.counts().items():
                counts[kind] = counts.get(kind, 0) + count
        return counts

    def folder_counts(self) -> Tuple[Dict[str, int], Dict[str, int]]:
        """Files directly in each sidebar folder, and files anywhere under
        each of its folders, as the indexes counted them."""
        direct: Dict[str, int] = {}
        under: Dict[str, int] = {}
        for root, library in self._libraries.items():
            direct[root], below = library.folder_counts()
            under.update(below)
        return direct, under

    def is_indexing(self) -> bool:
        return any(library.is_indexing() for library in self._libraries.values())

    def references_to(self, paths: List[str]) -> List[str]:
        notes: Set[str] = set()
        for library in self._libraries.values():
            notes.update(library.references_to(paths))
        return sorted(notes, key=natural_key)

    def watch(self, folders: List[str]) -> None:
        """Watch ``folders`` (those on screen), each by its own library."""
        shown: Dict[str, List[str]] = {root: [] for root in self._libraries}
        for folder in folders:
            library = self.library_for(folder)
            if library is not None:
                shown[library.root()].append(folder)
        for root, library in self._libraries.items():
            library.watch(shown[root])

    def refresh(self) -> None:
        for library in self._libraries.values():
            library.refresh()

    # -- operations ---------------------------------------------------------

    def _library_or_raise(self, path: str) -> FolderLibrary:
        library = self.library_for(path)
        if library is None:
            raise ValueError(_outside(path))
        return library

    def new_note(self, folder: str, title: Optional[str] = None) -> str:
        return self._library_or_raise(folder).new_note(folder, title)

    def new_folder(self, folder: str, name: Optional[str] = None) -> str:
        return self._library_or_raise(folder).new_folder(folder, name)

    def copy_in(self, paths: List[str], folder: str) -> List[str]:
        return self._library_or_raise(folder).copy_in(paths, folder)

    def trash(self, paths: List[str]) -> None:
        by_library: Dict[Optional[FolderLibrary], List[str]] = {}
        for path in paths:
            by_library.setdefault(self.library_for(path), []).append(path)
        failure: Optional[BaseException] = None
        for library, group in by_library.items():
            try:
                (library or self._loose).trash(group)
            except OSError as error:
                failure = failure or error
        gone = [path for path in paths if not os.path.lexists(path)]
        if gone:
            self.trashed.emit(gone)
        if failure is not None:
            raise failure

    def rename(self, path: str, new_name: str, *, skip_notes=frozenset(),
               on_links: Optional[Callable[["LinkReport"], None]] = None,
               on_done: Optional[Callable] = None) -> Optional[RenameResult]:
        """Rename ``path``; the links follow in every sidebar folder.
        ``skip_notes``: the open notes with unsaved changes, a set or a
        callable asked again right before each note is written (they are
        left to the editor). ``on_links`` hears what the links did,
        ``on_done(result, error)`` how the rename went. Done here and now
        (the result is returned, an error raised unless ``on_done`` takes
        it), or, when this set works in the background, after the
        operations before it (None is returned)."""
        library = self._library_or_raise(path)
        old = os.path.abspath(path)

        def disk():
            result = library.rename(old, new_name, rewrite_links=False)
            return result, result.moves, None

        def final(result, report, _failure):
            return RenameResult(result.new_path, report.changed, result.moves)

        return self._submit(_Operation(disk, final, skip_notes, on_links, on_done, library))

    def move(self, paths: List[str], folder: str, *, skip_notes=frozenset(),
             on_links: Optional[Callable[["LinkReport"], None]] = None,
             on_done: Optional[Callable] = None) -> Optional[MoveResult]:
        """Move ``paths`` into ``folder``, in one sidebar folder or from
        one to another; the links follow in every sidebar folder, for
        what did move also when some items stayed (MoveIncomplete). The
        rest as ``rename``."""
        library = self.library_for(folder) or (self.library_for(paths[0]) if paths else None)
        if library is None:
            raise ValueError(_outside(folder))
        paths = list(paths)

        def disk():
            try:
                result = library.move(paths, folder, rewrite_links=False)
                failed: List[Tuple[str, BaseException]] = []
            except MoveIncomplete as error:
                result, failed = error.result, error.failed
            return result, result.moved, failed

        def final(result, report, failed):
            done = MoveResult(result.moved, report.changed)
            return (done, MoveIncomplete(done, failed)) if failed else done

        return self._submit(_Operation(disk, final, skip_notes, on_links, on_done, library))

    # -- the operations, in order -------------------------------------------------

    def links_pending(self) -> bool:
        """Whether a rename or move (or the links that follow it) is still
        on its way."""
        return self._active is not None or bool(self._queue)

    def wait_for_links(self) -> None:
        """Finish every operation on its way now, the links included (as
        the window closes)."""
        self._hurry = True
        try:
            while self._active is not None or self._queue:
                if self._active is None:
                    self._advance()
                    continue
                if self._active.pending is None:
                    self._workers.wait()
                else:
                    self._write_some()
        finally:
            self._hurry = False

    def _submit(self, operation: "_Operation"):
        if self._in_background:
            self._queue.append(operation)
            QTimer.singleShot(0, self, self._advance)
            return None
        self._start(operation)
        if operation.pending is not None:
            self._write_some()
        if operation.on_done is None and operation.error is not None:
            raise operation.error
        return operation.result_for_caller

    def _advance(self) -> None:
        """Begin the next operation when none is on its way."""
        if self._active is not None or not self._queue:
            return
        self._start(self._queue.popleft())

    def _start(self, operation: "_Operation") -> None:
        """The operation's change on the disk, here on the GUI thread (so
        the window's tabs follow it before anything is saved again), and
        then the notes: read on the worker, or here when this set does
        everything at once."""
        self._active = operation
        try:
            operation.result, moves, operation.failed = operation.disk()
        except (OSError, ValueError) as error:
            operation.error = error
            self._finish(operation)
            return
        operation.moves = dict(moves)
        if not moves:
            self._finish(operation)
            return
        aliases = self._aliases(moves)
        self._refresh_others(operation.library, moves, aliases)
        self.moved.emit(dict(moves))
        roots = self.roots()

        def plan(progress=None):
            return link_rewrite.plan_rewrite(roots, moves, progress, aliases=aliases)

        if not self._in_background:
            try:
                operation.pending = deque(plan())
            except Exception:                                   # noqa: BLE001
                log.exception("Reading the notes after a move failed")
                operation.pending = deque()
            return
        self._job_number += 1
        operation.number = number = self._job_number

        def read(report):
            try:
                return plan(_Throttled(report))
            except Exception:                                   # noqa: BLE001
                log.exception("Reading the notes after a move failed")
                return []

        self._workers.run(read, lambda changes: self._deliver(number, changes),
                          receiver=self, progress=self.links_progress.emit)

    def _deliver(self, number: int, changes) -> None:
        """The notes the worker read for operation ``number``: their new
        links are written here, a few at a time."""
        operation = self._active
        if operation is None or operation.number != number:
            return
        operation.pending = deque(changes)
        if not self._hurry:
            self._write_some()

    def _write_some(self) -> None:
        """Write the planned notes for a moment of the GUI thread, then let
        the window go on (in the background), until all are written. Each
        note is written in its turn (atomic_file.one_writer), and only
        when the editor does not have it open with unsaved changes, asked
        now: on this thread nothing is typed between that answer and the
        write. The notes written are announced before the window goes on,
        so an open one shows its new links before anyone types into it."""
        operation = self._active
        if operation is None or operation.pending is None:
            return
        started = time.monotonic()
        before = len(operation.report.changed)
        unsaved = operation.skip_notes() if callable(operation.skip_notes) \
            else operation.skip_notes
        unsaved = set(unsaved or ())
        while operation.pending and (self._hurry or not self._in_background
                                     or time.monotonic() - started < WRITE_SLICE):
            link_rewrite.apply_change(operation.pending.popleft(), operation.moves, unsaved,
                                      operation.report)
        written = operation.report.changed[before:]
        if written:
            self.rewritten.emit(list(written))
        if operation.pending:
            if not self._hurry:
                QTimer.singleShot(0, self, self._write_some)
            return
        self._finish(operation)

    def _finish(self, operation: "_Operation") -> None:
        self._active = None
        rewrite = operation.report
        report = LinkReport(list(rewrite.changed), list(rewrite.not_updated),
                            list(rewrite.left_open), dict(operation.moves))
        error = operation.error
        if operation.result is not None:
            made = operation.final(operation.result, rewrite, operation.failed)
            if isinstance(made, tuple):
                made, error = made
            operation.result_for_caller = made
            if error is not None and operation.error is None:
                operation.error = error
            if operation.moves:
                if operation.on_links is not None:
                    operation.on_links(report)
                self.links_updated.emit(report)
        if operation.on_done is not None:
            operation.on_done(operation.result_for_caller, error)
        if self._in_background and not self._hurry:
            QTimer.singleShot(0, self, self._advance)

    def _aliases(self, moves: Dict[str, str]) -> List[Tuple[str, str]]:
        """``moves`` as the other sidebar folders name them: a file moved
        by way of a symbolic link (A/toB/pic.png, really B/pic.png) is
        linked to by B's notes by its name there."""
        roots = [(root, os.path.realpath(root)) for root in self._libraries]

        def names(path: str) -> Dict[str, str]:
            real = os.path.join(os.path.realpath(os.path.dirname(path)), os.path.basename(path))
            found = {}
            for root, real_root in roots:
                if link_rewrite.is_within(real, real_root):
                    rest = os.path.relpath(real, real_root)
                    found[root] = root if rest == os.curdir else os.path.join(root, rest)
            return found

        aliases: List[Tuple[str, str]] = []
        for old, new in moves.items():
            old_names, new_names = names(old), names(new)
            for root in dict.fromkeys(list(old_names) + list(new_names)):
                pair = (old_names.get(root, old), new_names.get(root, new))
                if pair != (old, new) and pair not in aliases:
                    aliases.append(pair)
        return aliases

    def _refresh_others(self, done: FolderLibrary, moves: Dict[str, str],
                        aliases: List[Tuple[str, str]]) -> None:
        """The other sidebar folders whose listings lost or gained what
        moved (by its own name, or by way of a symbolic link) read again."""
        paths = list(moves) + list(moves.values()) + [p for pair in aliases for p in pair]
        for root, other in self._libraries.items():
            if other is not done and any(link_rewrite.is_within(p, root) for p in paths):
                other.refresh()


WRITE_SLICE = 0.03      # how long the notes are written for at a time (seconds)


class _Operation:
    """A rename or move, and the links that follow it, from the change on
    the disk to the last note written."""

    def __init__(self, disk, final, skip_notes, on_links, on_done, library):
        self.disk = disk
        self.final = final
        self.skip_notes = skip_notes
        self.on_links = on_links
        self.on_done = on_done
        self.library = library
        self.result = None
        self.result_for_caller = None
        self.failed = None
        self.error: Optional[BaseException] = None
        self.moves: Dict[str, str] = {}
        self.number = 0
        self.pending: Optional[deque] = None     # the planned notes, once read
        self.report = link_rewrite.Rewrite()


@dataclass
class LinkReport:
    """What updating the links after a rename or move did: the notes it
    changed on the disk, those it could not (a note in an older encoding
    that cannot hold a new name, one that changed meanwhile or could not
    be written), the open notes with unsaved changes it left to the editor
    (link_rewrite.OpenNote: each with the moves to apply to the open
    document), and the moves themselves (old path to new path)."""
    changed: List[str] = field(default_factory=list)
    not_updated: List[str] = field(default_factory=list)
    left_open: List[link_rewrite.OpenNote] = field(default_factory=list)
    moves: Dict[str, str] = field(default_factory=dict)


class _Throttled:
    """``report(done, total)`` at most every PROGRESS_INTERVAL seconds, and
    always for the last one: the notes are read faster than anyone reads
    a count."""

    def __init__(self, report) -> None:
        self._report = report
        self._said = 0.0

    def __call__(self, done: int, total: int) -> None:
        now = time.monotonic()
        if done == total or now - self._said >= PROGRESS_INTERVAL:
            self._said = now
            self._report(done, total)


def _unique(items: List[str]) -> List[str]:
    return list(dict.fromkeys(items))


def _overlap(path: str, real: str, folder: str, folder_real: Optional[str] = None) -> bool:
    """Whether ``path`` (which really is ``real``) is ``folder`` or inside
    it, by the names or by where symbolic links lead."""
    if link_rewrite.is_within(path, folder):
        return True
    return link_rewrite.is_within(real, folder_real or os.path.realpath(folder))


def _outside(path: str) -> str:
    return _("“{name}” is in no folder of the sidebar.").format(
        name=os.path.basename(path.rstrip("/\\")) or path)
