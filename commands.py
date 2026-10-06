# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One list of everything the editor can do, behind every way of doing it.

The menus, the keyboard shortcuts, the Keyboard Shortcuts window and,
later, the toolbar and the slash menu all read the same list, so a
command cannot be in one and missing from (or named differently in)
another, and two commands can never claim the same shortcut.

A :class:`Command` describes one thing: its title (title case, as menus
show it), its group (the menu it belongs to, and the section it is
listed under in the Keyboard Shortcuts window), its shortcut, extra
words to find it by, and whether it needs a Nostr account. The
:class:`CommandRegistry` turns each into one QAction, owned by the
window and added to it, so its shortcut works even while the menu bar
is hidden (full screen, compact themes).

Nostr commands follow the Nostr state (see nostr_state.py): a surface
that should only show them once Nostr is in use asks
:meth:`CommandRegistry.available`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Union

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QKeySequence

ShortcutSpec = Union[None, str, QKeySequence, QKeySequence.StandardKey]

# The groups, in the order the Keyboard Shortcuts window lists them. They
# are keys, so they stay English; the window translates them where it
# shows them (shortcuts_dialog.py).
FILE = "File"
EDIT = "Editing"
FORMAT = "Formatting"
SEARCH = "Search"
VIEW = "View"
NOSTR = "Nostr"
HELP = "Help"
GROUP_ORDER: Tuple[str, ...] = (FILE, EDIT, FORMAT, SEARCH, VIEW, NOSTR, HELP)


@dataclass(frozen=True)
class Command:
    """One thing the editor can do."""

    id: str                          # "file.save": stable, never shown
    title: str                       # "Save As…": what menus show
    group: str                       # one of GROUP_ORDER
    shortcut: ShortcutSpec = None
    checkable: bool = False
    nostr: bool = False              # needs a Nostr account to mean anything
    keywords: Tuple[str, ...] = ()   # other words people use for it
    # The words the Keyboard Shortcuts window uses, when they differ from
    # the menu title ("Toggle theme" for "Toggle Dark/Light Theme").
    listed_as: str = ""


def platform_keys(mac: ShortcutSpec, other: ShortcutSpec, *,
                  platform: Optional[str] = None) -> ShortcutSpec:
    """The shortcut for this computer: ``mac`` on a Mac, ``other`` elsewhere.

    For the few commands whose keys differ between the platforms' own
    conventions (Apple's Human Interface Guidelines on a Mac; on Windows
    and Linux, Ctrl+Alt is AltGr, which types characters on many
    keyboards). Qt writes Command as Ctrl and Option as Alt on a Mac, so
    ``mac`` is "Ctrl+Alt+1" for Option-Command-1.
    """
    return mac if (platform or sys.platform) == "darwin" else other


def all_key_texts(shortcut: ShortcutSpec) -> List[str]:
    """Every key combination a shortcut answers to: a standard key (Copy,
    Find Next) has the platform's whole set, anything else one."""
    if isinstance(shortcut, QKeySequence.StandardKey):
        texts = [s.toString(QKeySequence.SequenceFormat.PortableText)
                 for s in QKeySequence.keyBindings(shortcut)]
        return [t for t in texts if t]
    keys = key_text(shortcut)
    return [keys] if keys else []


def key_text(shortcut: ShortcutSpec) -> str:
    """``Ctrl+Shift+S``: a shortcut the way the Keyboard Shortcuts window
    writes it (Qt maps Ctrl to Command on a Mac)."""
    if shortcut is None:
        return ""
    sequence = shortcut if isinstance(shortcut, QKeySequence) else QKeySequence(shortcut)
    return sequence.toString(QKeySequence.SequenceFormat.PortableText)


@dataclass
class _Entry:
    command: Command
    action: QAction
    hidden_from_list: bool = False
    extra_keys: List[str] = field(default_factory=list)


class CommandRegistry(QObject):
    """The commands of one window, in the order they were added."""

    def __init__(self, window, parent: Optional[QObject] = None) -> None:
        super().__init__(parent if parent is not None else window)
        self._window = window
        self._entries: Dict[str, _Entry] = {}
        self._by_keys: Dict[str, str] = {}

    # -- adding ------------------------------------------------------------

    def add(self, command: Command, *, triggered: Optional[Callable] = None,
            toggled: Optional[Callable[[bool], None]] = None,
            checked: Optional[bool] = None, enabled: Optional[bool] = None,
            listed: bool = True) -> QAction:
        """Make ``command`` an action of the window and return it.

        ``triggered`` runs on a click or the shortcut; ``toggled`` gets the
        new state of a checkable command. ``listed=False`` keeps it out of
        the Keyboard Shortcuts window (for an alias of a listed command).
        Raises ValueError for a second command with the same id or the
        same shortcut: a shortcut that means two things means neither.
        """
        if command.id in self._entries:
            raise ValueError(f"command {command.id!r} is already registered")
        if command.group not in GROUP_ORDER:
            raise ValueError(f"command {command.id!r} has an unknown group {command.group!r}")
        all_keys = all_key_texts(command.shortcut)
        for keys in all_keys:
            if keys in self._by_keys:
                raise ValueError(f"{keys} is taken by {self._by_keys[keys]!r}; "
                                 f"{command.id!r} cannot have it too")
        action = QAction(command.title, self._window)
        action.setObjectName(command.id)
        if isinstance(command.shortcut, QKeySequence.StandardKey):
            # Every combination the platform uses for it (F3 and Command-G
            # for Find Next on a Mac), not only the first.
            action.setShortcuts(command.shortcut)
        elif command.shortcut is not None:
            action.setShortcut(command.shortcut if isinstance(command.shortcut, QKeySequence)
                               else QKeySequence(command.shortcut))
        if command.checkable:
            action.setCheckable(True)
            if checked is not None:
                action.setChecked(checked)
        if enabled is not None:
            action.setEnabled(enabled)
        if triggered is not None:
            action.triggered.connect(lambda _checked=False: triggered())
        if toggled is not None:
            action.toggled.connect(toggled)
        # Window-wide, so the shortcut works without the menu bar.
        self._window.addAction(action)
        self._entries[command.id] = _Entry(command, action, hidden_from_list=not listed)
        for keys in all_keys:
            self._by_keys[keys] = command.id
        return action

    # -- reading -----------------------------------------------------------

    def action(self, command_id: str) -> QAction:
        return self._entries[command_id].action

    def command(self, command_id: str) -> Command:
        return self._entries[command_id].command

    def __contains__(self, command_id: str) -> bool:
        return command_id in self._entries

    def commands(self, group: Optional[str] = None) -> List[Command]:
        return [e.command for e in self._entries.values()
                if group is None or e.command.group == group]

    def available(self, *, nostr: bool) -> List[Command]:
        """What a surface that follows the Nostr state may offer: every
        command, and the Nostr ones only while Nostr is in use."""
        return [c for c in self.commands() if nostr or not c.nostr]

    def find(self, text: str, *, nostr: bool) -> List[Command]:
        """Commands whose title or keywords contain every word of ``text``,
        for a slash menu or a command search. Enabled ones only."""
        words = [w for w in text.lower().split() if w]
        found = []
        for command in self.available(nostr=nostr):
            if not self.action(command.id).isEnabled():
                continue
            haystack = " ".join((command.title, command.listed_as, *command.keywords)).lower()
            if all(word in haystack for word in words):
                found.append(command)
        return found

    def shortcut_groups(self, extra: Iterable[Tuple[str, Iterable[Tuple[str, str]]]] = (),
                        *, nostr: bool = True) -> List[Tuple[str, List[Tuple[str, str]]]]:
        """``[(group, [(keys, words), ...]), ...]`` for the Keyboard
        Shortcuts window: every listed command that has a shortcut, plus
        ``extra`` rows for keys that are not commands (Tab indents, Esc
        closes the find bar), each group in GROUP_ORDER, then any group
        only ``extra`` has."""
        rows: Dict[str, List[Tuple[str, str]]] = {}
        for entry in self._entries.values():
            command = entry.command
            keys = key_text(command.shortcut)
            if not keys or entry.hidden_from_list or (command.nostr and not nostr):
                continue
            words = command.listed_as or command.title.rstrip("…").rstrip(".").rstrip()
            rows.setdefault(command.group, []).append((keys, words))
        for group, items in extra:
            rows.setdefault(group, []).extend(items)
        ordered = [g for g in GROUP_ORDER if g in rows]
        ordered += [g for g in rows if g not in GROUP_ORDER]
        return [(group, rows[group]) for group in ordered]
