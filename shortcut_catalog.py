# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every key MyEditor answers to, as one list.

Help > Keyboard Shortcuts shows it, a toolbar button's tooltip names its
command's key with it, and the user guide's tables are written from it
(scripts/shortcuts_doc.py), so none of them can disagree with the keys
that work.

The window's commands come from its command list (commands.py), with the
keys their actions answer to on this computer. Keys that are not commands
come from the places that handle them: the PDF reader's own table
(pdf_viewer.reader_keys), and below, the editor's and the find bar's
keys and those of the lists (Drafts, Published, Imports, the Media
Library), each next to a pointer to the code that handles it.

Keys are written the way the computer in use writes them: on a Mac in
Apple's symbols and order (⌃⌥⇧⌘, as in menus), elsewhere as Ctrl+Shift+S
with the key names of the reader's keyboard (Strg, Umschalt in German),
and spoken the way a screen reader says them ("Bold, Command B").
"""

from __future__ import annotations

import sys
import unicodedata
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from PySide6.QtGui import QAction, QKeySequence

import commands
from i18n import _, pgettext

# The categories, in the order the window's sidebar lists them. They are
# keys, so they stay English; category_title gives the words shown.
FILE, EDIT, FORMAT, INSERT, VIEW = "File", "Edit", "Format", "Insert", "View"
NAVIGATION, NOSTR, PDF, LISTS = "Navigation", "Nostr", "PDF Reader", "Lists"
CATEGORIES: Tuple[str, ...] = (FILE, EDIT, FORMAT, INSERT, VIEW, NAVIGATION, NOSTR, PDF, LISTS)

# Where a command not placed below goes: its menu's category.
_GROUP_CATEGORY = {
    commands.FILE: FILE, commands.EDIT: EDIT, commands.SEARCH: EDIT, commands.FORMAT: FORMAT,
    commands.INSERT: INSERT, commands.VIEW: VIEW, commands.NAVIGATION: NAVIGATION,
    commands.NOSTR: NOSTR, commands.WINDOW: NAVIGATION, commands.HELP: NAVIGATION,
}


def is_mac() -> bool:
    return commands.sys.platform == "darwin"


def category_title(category: str) -> str:
    return {
        FILE: pgettext("shortcut category", "File"),
        EDIT: pgettext("shortcut category", "Edit"),
        FORMAT: pgettext("shortcut category", "Format"),
        INSERT: pgettext("shortcut category", "Insert"),
        VIEW: pgettext("shortcut category", "View"),
        NAVIGATION: pgettext("shortcut category", "Navigation"),
        NOSTR: "Nostr",                          # a name, the same in every language
        PDF: pgettext("shortcut category", "PDF Reader"),
        LISTS: pgettext("shortcut category", "Lists"),
    }[category]


@dataclass(frozen=True)
class Row:
    """One line of the list: what a key does."""

    category: str
    section: str                  # its section's title, in the reader's language
    title: str                    # what it does, in the reader's language
    keys: Tuple[str, ...]         # every combination it answers to ("Ctrl+Shift+S")
    shown: Tuple[str, ...] = ()   # the ones drawn: one, two to choose from, or a run's ends
    command: str = ""             # the command it runs, "" for a key a place handles itself
    reason: str = ""              # why it does nothing right now, "" when it works
    span: bool = False            # ``shown`` is a run's first and last (Command-1 to 8)
    words: str = ""               # more words it is found by


# -- the curated order --------------------------------------------------------------
#
# Each category's sections, most used first, and in each its commands in
# the order people reach for them (not alphabetical). An entry is a
# command id, or (keys, words) for a key the editor or the find bar
# handles itself (editor.py keyPressEvent, widgets.FindBar).

def _curated() -> Tuple[Tuple[str, str, Tuple], ...]:
    mac = is_mac()
    return (
        (FILE, _("Documents"), ("file.new", "file.open", "file.save", "file.save_as",
                                "file.close_tab", "file.quit")),
        (FILE, _("Printing"), ("file.print", "file.print_preview", "file.page_setup")),
        (FILE, "R Markdown", ("file.knit_html",)),
        (EDIT, _("Undo and Clipboard"), ("edit.undo", "edit.redo", "edit.cut", "edit.copy",
                                         "edit.paste", "edit.paste_plain", "edit.select_all")),
        (EDIT, _("Find"), ("search.find", "search.next", "search.previous", "search.replace",
                           "search.use_selection",
                           (("Return",), _("Next match")),
                           (("Shift+Return",), _("Previous match")),
                           (("Esc",), _("Close find bar")))),
        (EDIT, _("Writing"), ((("Tab",), _("Nest a list item, or start a list")),
                              (("Shift+Tab",), _("Move a list item out one level")),
                              (("Return",), _("New paragraph (on an empty list item: end "
                                              "the list)")),
                              (("Shift+Return",), _("Line break within the paragraph")),
                              (("Ctrl+Return",), _("Leave a code block or a table")),
                              (("Ctrl+Click",), _("Open a link")),
                              (("Alt+Backspace",) if mac else ("Ctrl+Backspace",),
                               _("Delete the word before the caret")),
                              "edit.check_spelling")),
        (FORMAT, _("Text Style"), ("format.bold", "format.italic", "format.underline",
                                   "format.strike", "format.code", "format.reset")),
        (FORMAT, _("Paragraph Style"), ("format.style.body", "format.style.h1",
                                        "format.style.h2", "format.style.h3")),
        (FORMAT, _("Lists and Quotes"), ("format.list.bullet", "format.list.number",
                                         "format.list.task", "format.list.tick",
                                         "format.indent", "format.outdent", "format.quote")),
        (INSERT, _("Insert"), ("insert.link", "insert.image", "insert.table", "insert.divider",
                               "insert.code_block", "insert.footnote")),
        (VIEW, _("Write and Read"), ("view.write", "view.preview", "view.split")),
        (VIEW, _("Show and Hide"), ("view.fullscreen", "view.toolbar", "view.line_numbers",
                                    "view.syntax_highlighting", "view.appearance.switch")),
        (NAVIGATION, _("Tabs"), ("nav.next_tab", "nav.previous_tab", "nav.tab_1",
                                 "nav.last_tab")),
        (NAVIGATION, _("Panes"), ("nav.next_pane", "nav.previous_pane")),
        (NAVIGATION, _("Windows"), ("help.shortcuts", "app.settings", "window.minimize")),
        (NOSTR, _("Publish"), ("nostr.publish_note", "nostr.publish_article")),
        (NOSTR, _("Write"), ("nostr.insert_image", "nostr.insert_reference",
                             "nostr.insert_audio_video")),
        (NOSTR, _("Panels and Windows"), ("nostr.drafts", "nostr.published",
                                          "nostr.refresh_drafts", "nostr.article_details",
                                          "nostr.media_library", "nostr.imports")),
    )


def list_keys() -> Tuple[Tuple[str, Tuple[str, ...], str], ...]:
    """The lists' keys: (section, keys, words). Drafts: drafts_panel.py
    (eventFilter, _on_item_activated) and drafts_preview.py (Space);
    Published: published_view.py (eventFilter); Imports:
    imports_post_list.py (keyPressEvent) and imports_window.py (Create
    Drafts, Undo); the Media Library: media_grid.py (keyPressEvent)."""
    delete = ("Backspace", "Del") if is_mac() else ("Del", "Backspace")
    drafts, published = _("Drafts"), _("Published")
    imports, media = _("Imports"), _("Media Library")
    return (
        (drafts, ("Return",), _("Open the draft")),
        (drafts, ("Space",), _("Preview the draft")),
        (drafts, delete, _("Delete the selected drafts")),
        (published, ("Return",), _("Open the note or article")),
        (published, delete, _("Delete from Nostr")),
        (imports, ("Space",), _("Check or uncheck the post")),
        (imports, ("Ctrl+A",), _("Check every post")),
        (imports, ("Esc",), _("Uncheck every post")),
        (imports, delete, _("Skip the post")),
        (imports, ("Ctrl+Z",), _("Bring back the post skipped last")),
        (imports, ("Ctrl+Return",), _("Create drafts from the checked posts")),
        (media, ("Return",), _("Open, or insert when choosing")),
        (media, ("Space",), _("Quick Look")),
        (media, delete, _("Delete")),
        (media, ("Ctrl+C",), _("Copy the link")),
        (media, ("Ctrl+V",), _("Upload the files copied")),
        (media, ("Ctrl+S",), _("Save As")),
    )


def category_note(category: str, *, nostr_in_use: bool) -> str:
    """A line over a category's list, or "": Nostr says it is optional
    rather than hiding its keys."""
    if category == NOSTR and not nostr_in_use:
        return _("Nostr is optional. These keys work while a Nostr account is in use.")
    if category == LISTS:
        return _("In each list, typing the first letters of a name selects it.")
    return ""


# Why a command that has a key does nothing now, where a short reason
# says more than "not here".
def _reason_for(command_id: str) -> str:
    return {
        "file.knit_html": _("For R Markdown files"),
        "nostr.refresh_drafts": _("While the Drafts panel is open"),
    }.get(command_id, _("Not available here"))


# -- the list ------------------------------------------------------------------------

def _action_keys(action: QAction) -> Tuple[str, ...]:
    texts = (k.toString(QKeySequence.SequenceFormat.PortableText) for k in action.shortcuts())
    return tuple(t for t in texts if t)


def _command_row(registry, command_id: str, category: str, section: str, *,
                 nostr_in_use: bool) -> Optional[Row]:
    if command_id not in registry:
        return None
    command = registry.command(command_id)
    action = registry.action(command_id)
    keys = _action_keys(action)
    if not keys:
        return None
    if isinstance(command.shortcut, tuple):
        shown = keys[:2]                # Clear Formatting: Ctrl+D or Ctrl+\
    else:
        shown = keys[:1]                # a standard key: the system's first
    reason = ""
    if command.nostr and not nostr_in_use:
        reason = _("Needs a Nostr account")
    elif not action.isEnabled():
        reason = _reason_for(command_id)
    title = command.listed_as or command.title.rstrip("…").rstrip(".").rstrip()
    words = " ".join((command.title, *command.keywords))
    return Row(category, section, title, keys, shown, command_id, reason, words=words)


def _tabs_row(registry, category: str, section: str) -> Optional[Row]:
    """Go to Tab 1 to 8, one row for the eight commands."""
    ids = [f"nav.tab_{n}" for n in range(1, 9)]
    if not all(i in registry for i in ids):
        return None
    keys = tuple(k for i in ids for k in _action_keys(registry.action(i)))
    return Row(category, section, _("Go to tab 1 to 8"), keys, (keys[0], keys[-1]),
               "nav.tab_1", span=True, words=_("number"))


def build_rows(registry, *, nostr_in_use: bool) -> List[Row]:
    """Every key, in the window's order: the curated sections, then any
    command not placed there under its menu's category, then the PDF
    reader's and the lists' keys."""
    from pdf_viewer import reader_keys

    rows: List[Row] = []
    placed = set()
    for category, section, entries in _curated():
        for entry in entries:
            if isinstance(entry, tuple):
                keys, words = entry
                rows.append(Row(category, section, words, keys, keys[:1]))
                continue
            if entry == "nav.tab_1":
                row = _tabs_row(registry, category, section)
                placed.update(f"nav.tab_{n}" for n in range(1, 9))
            else:
                row = _command_row(registry, entry, category, section,
                                   nostr_in_use=nostr_in_use)
                placed.add(entry)
            if row is not None:
                rows.append(row)
    more = _("More")
    for command in registry.commands():
        if command.id in placed:
            continue
        row = _command_row(registry, command.id, _GROUP_CATEGORY[command.group], more,
                           nostr_in_use=nostr_in_use)
        if row is not None:
            rows.append(row)
    for section, words, _act, tab_keys, view_keys in reader_keys():
        keys = tuple(tab_keys) + tuple(view_keys)
        rows.append(Row(PDF, _(section), words, keys, keys[:2]))
    for section, keys, words in list_keys():
        rows.append(Row(LISTS, section, words, keys, keys[:1]))
    return rows


# -- keys, as people see and hear them -----------------------------------------------

# Apple's modifier symbols, in the order Apple writes them (HIG, Keyboards).
# Qt names Command "Ctrl" and Control "Meta" on a Mac.
_MAC_MODIFIERS = (("Meta", "⌃"), ("Alt", "⌥"), ("Shift", "⇧"),
                  ("Ctrl", "⌘"))
_MAC_KEYS = {
    "Enter": "⌤", "Return": "↩", "Tab": "⇥", "Esc": "⎋",
    "Backspace": "⌫", "Del": "⌦", "Home": "↖", "End": "↘",
    "PgUp": "⇞", "PgDown": "⇟", "Up": "↑", "Down": "↓",
    "Left": "←", "Right": "→", "Space": "␣",
}
_MODIFIERS = ("Meta", "Ctrl", "Alt", "Shift")


def _key_names() -> Dict[str, str]:
    """Keys as the reader's keyboard labels them (Strg and Umschalt on a
    German one). Letters, digits and F-keys read the same everywhere."""
    return {
        "Ctrl": pgettext("key", "Ctrl"), "Shift": pgettext("key", "Shift"),
        "Alt": pgettext("key", "Alt"), "Meta": pgettext("key", "Win"),
        "Enter": pgettext("key", "Enter"), "Return": pgettext("key", "Enter"),
        "Space": pgettext("key", "Space"), "Tab": pgettext("key", "Tab"),
        "Esc": pgettext("key", "Esc"), "Home": pgettext("key", "Home"),
        "End": pgettext("key", "End"), "PgUp": pgettext("key", "PgUp"),
        "PgDown": pgettext("key", "PgDown"), "Del": pgettext("key", "Del"),
        "Backspace": pgettext("key", "Backspace"), "Click": pgettext("key", "Click"),
    }


def parts(keys: str) -> List[str]:
    """"Ctrl+Shift+D" as its keys; "Ctrl++" (the plus key) keeps its "+"."""
    found = [p.strip() for p in keys.split("+") if p.strip()]
    if keys.endswith("++") or keys == "+":
        found.append("+")
    return found


def key_names(keys: str, *, mac: Optional[bool] = None) -> List[str]:
    """The keys of "Ctrl+Shift+D", each named the way the computer in use
    labels it: on a Mac Apple's symbols in Apple's order (⇧⌘D), elsewhere
    the reader's keyboard's names (Strg, Umschalt)."""
    mac = is_mac() if mac is None else mac
    found = parts(keys)
    if not mac:
        names = _key_names()
        return [names.get(part, part) for part in found]
    held = [symbol for name, symbol in _MAC_MODIFIERS if name in found]
    click = _key_names()["Click"]
    rest = [_MAC_KEYS.get(part) or (click if part == "Click" else part)
            for part in found if part not in _MODIFIERS]
    return held + rest


def shown(keys: str, *, mac: Optional[bool] = None) -> str:
    """"⇧⌘S" on a Mac, "Ctrl+Shift+S" (Strg+Umschalt+S) elsewhere."""
    mac = is_mac() if mac is None else mac
    names = key_names(keys, mac=mac)
    return "".join(names) if mac else "+".join(names)


def spoken(keys: str, *, mac: Optional[bool] = None) -> str:
    """The keys as a screen reader should say them: "Shift Command S" on a
    Mac, "Control Shift S" elsewhere, never the symbols."""
    mac = is_mac() if mac is None else mac
    if mac:
        modifiers = {"Meta": pgettext("spoken key on a Mac", "Control"),
                     "Alt": pgettext("spoken key on a Mac", "Option"),
                     "Shift": pgettext("spoken key", "Shift"),
                     "Ctrl": pgettext("spoken key on a Mac", "Command")}
        order = ("Meta", "Alt", "Shift", "Ctrl")
    else:
        modifiers = {"Ctrl": pgettext("spoken key", "Control"),
                     "Alt": pgettext("spoken key", "Alt"),
                     "Shift": pgettext("spoken key", "Shift"),
                     "Meta": pgettext("spoken key", "Windows")}
        order = ("Meta", "Ctrl", "Alt", "Shift")
    named = {
        "Return": pgettext("spoken key", "Return"), "Enter": pgettext("spoken key", "Enter"),
        "Tab": pgettext("spoken key", "Tab"), "Space": pgettext("spoken key", "Space"),
        "Esc": pgettext("spoken key", "Escape"),
        "Backspace": (pgettext("spoken key on a Mac", "Delete") if mac
                      else pgettext("spoken key", "Backspace")),
        "Del": (pgettext("spoken key on a Mac", "Forward Delete") if mac
                else pgettext("spoken key", "Delete")),
        "Home": pgettext("spoken key", "Home"), "End": pgettext("spoken key", "End"),
        "PgUp": pgettext("spoken key", "Page Up"), "PgDown": pgettext("spoken key", "Page Down"),
        "Up": pgettext("spoken key", "Up Arrow"), "Down": pgettext("spoken key", "Down Arrow"),
        "Left": pgettext("spoken key", "Left Arrow"),
        "Right": pgettext("spoken key", "Right Arrow"),
        "+": pgettext("spoken key", "Plus"), "-": pgettext("spoken key", "Minus"),
        "=": pgettext("spoken key", "Equals"), "/": pgettext("spoken key", "Slash"),
        "\\": pgettext("spoken key", "Backslash"), ",": pgettext("spoken key", "Comma"),
        ";": pgettext("spoken key", "Semicolon"), "'": pgettext("spoken key", "Apostrophe"),
        "[": pgettext("spoken key", "Left Bracket"),
        "]": pgettext("spoken key", "Right Bracket"),
        "{": pgettext("spoken key", "Left Brace"), "}": pgettext("spoken key", "Right Brace"),
        "Click": pgettext("spoken key", "Click"),
    }
    found = parts(keys)
    words = [modifiers[name] for name in order if name in found]
    words += [named.get(part, part) for part in found if part not in _MODIFIERS]
    return " ".join(words)


def spoken_row(row: Row) -> str:
    """"Bold, Command B": what a screen reader says for ``row``."""
    if row.span and len(row.shown) == 2:
        keys = _("{first} to {last}").format(first=spoken(row.shown[0]),
                                             last=spoken(row.shown[1]))
    else:
        keys = _(", or ").join(spoken(k) for k in row.shown)
    text = f"{row.title}, {keys}"
    return f"{text}, {row.reason}" if row.reason else text


def tooltip_for(action: QAction, title: str = "") -> str:
    """"Bold (⌘B)": the command's name (or ``title``) and its key, written
    as the list writes it, or only the name when it has none."""
    title = title or action.text().replace("&", "").rstrip("…").rstrip()
    keys = _action_keys(action)
    if not keys:
        return title
    return _("{command} ({keys})").format(command=title, keys=shown(keys[0]))


# -- finding ---------------------------------------------------------------------------

def fold(text: str) -> str:
    """``text`` for comparing: no case, no accents ("Löschen" is "loschen")."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


_GLYPH_MODIFIERS = {"⌘": "Ctrl", "⌥": "Alt", "⇧": "Shift", "⌃": "Meta"}
_GLYPH_KEYS = {glyph: name for name, glyph in _MAC_KEYS.items()}
_NAMED_KEYS = {
    "tab": "Tab", "space": "Space", "leertaste": "Space", "esc": "Esc", "escape": "Esc",
    "return": "Return", "enter": "Return", "eingabe": "Return", "home": "Home",
    "pos1": "Home", "end": "End", "ende": "End", "pgup": "PgUp", "pageup": "PgUp",
    "pgdown": "PgDown", "pagedown": "PgDown", "pgdn": "PgDown", "del": "Del", "entf": "Del",
    "backspace": "Backspace", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "click": "Click", "klick": "Click", "plus": "+", "minus": "-",
}


def _modifier_words(mac: bool) -> Dict[str, FrozenSet[str]]:
    """Words for a modifier, and the Qt modifiers each may mean. On a Mac
    "ctrl" finds Command keys (Qt's name for it) and Control keys alike."""
    command = frozenset({"Ctrl"})
    control = frozenset({"Ctrl", "Meta"}) if mac else command
    words = {"cmd": command, "command": command, "befehl": command, "ctrl": control,
             "control": control, "strg": control, "steuerung": control,
             "alt": frozenset({"Alt"}), "opt": frozenset({"Alt"}), "option": frozenset({"Alt"}),
             "wahl": frozenset({"Alt"}), "shift": frozenset({"Shift"}),
             "umschalt": frozenset({"Shift"}), "meta": frozenset({"Meta"}),
             "win": frozenset({"Meta"}), "super": frozenset({"Meta"})}
    if not mac:
        words.update({"cmd": control, "command": control})
    return words


def parse_keys(query: str, *, mac: Optional[bool] = None
               ) -> Optional[Tuple[Tuple[FrozenSet[str], ...], str]]:
    """What a search for keys asks for: "cmd b", "⌘B", "ctrl+b", "ctrl
    shift p" as (the modifiers, each the Qt names it may mean; the key, or
    "" while only modifiers are typed). None when ``query`` names no
    modifier and no named key: then it is words to find."""
    mac = is_mac() if mac is None else mac
    words = _modifier_words(mac)
    text = query.strip()
    tokens: List[str] = []
    for chunk in text.replace("+", " + ").split():
        if chunk == "+":
            tokens.append(chunk)
            continue
        # "⇧⌘B": each symbol a modifier, then the key.
        while chunk and chunk[0] in _GLYPH_MODIFIERS:
            tokens.append(chunk[0])
            chunk = chunk[1:]
        if chunk:
            tokens.append(chunk)
    # A "+" between keys joins them; the last one, or one alone, is the key.
    joined = [t for i, t in enumerate(tokens)
              if t != "+" or i == len(tokens) - 1 or tokens[i + 1] == "+"]
    modifiers: List[FrozenSet[str]] = []
    key = ""
    named = False
    for token in joined:
        lowered = fold(token)
        if token in _GLYPH_MODIFIERS:
            modifiers.append(frozenset({_GLYPH_MODIFIERS[token]}))
        elif lowered in words and not key:
            modifiers.append(words[lowered])
        elif key:
            return None                      # two keys: words, not a combination
        elif token in _GLYPH_KEYS:
            key, named = _GLYPH_KEYS[token], True
        elif lowered in _NAMED_KEYS:
            key, named = _NAMED_KEYS[lowered], True
        elif len(lowered) > 1 and lowered[0] == "f" and lowered[1:].isdigit():
            key, named = lowered.upper(), True
        elif len(token) == 1:
            key = token.upper()
        else:
            return None
    if not modifiers and not named:
        return None
    return tuple(modifiers), key


def _combination_matches(keys: str, wanted: Tuple[FrozenSet[str], ...], key: str) -> bool:
    found = parts(keys)
    held = {p for p in found if p in _MODIFIERS}
    rest = [p for p in found if p not in _MODIFIERS]
    pressed = rest[-1] if rest else ""
    satisfied = all(any(m in held for m in choice) for choice in wanted)
    if not key:
        return satisfied
    same_key = pressed.upper() == key.upper() or {pressed, key} == {"Return", "Enter"}
    return satisfied and same_key and len(held) == len(wanted)


def matches(row: Row, query: str, *, mac: Optional[bool] = None) -> bool:
    """Whether ``row`` is what ``query`` looks for: words of its name (in
    any case, with or without accents), or its keys however they are
    written ("cmd b", "⌘B", "ctrl+b", "ctrl shift p")."""
    words = fold(query).split()
    if not words:
        return True
    haystack = fold(f"{row.title} {row.words} {row.section}")
    if all(word in haystack for word in words):
        return True
    wanted = parse_keys(query, mac=mac)
    if wanted is None:
        return False
    modifiers, key = wanted
    return any(_combination_matches(keys, modifiers, key) for keys in row.keys)


def rows_for_keys(rows: Iterable[Row], keys: str) -> List[Row]:
    """The rows that answer exactly ``keys`` ("Ctrl+Shift+P"), for Type a
    Shortcut."""
    return [row for row in rows if keys in row.keys]
