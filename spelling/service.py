# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spell checking for the editor's documents, kept current as they change.

:class:`SpellChecker` is the app's one link to the system spell checker,
shared by every document: it checks text, suggests, learns and ignores
words, and says when the system checker stops working.

:class:`DocumentSpelling` follows one ``QTextDocument`` and knows the
misspelled words of each of its blocks. It never blocks typing: blocks
are checked in small slices from the event loop, the visible ones
first, and only blocks that changed are checked again. Everything runs
on the thread that made it (the system checkers, Windows COM above all,
expect that). It holds data only: drawing the underlines, the context
menu and the Spelling menu belong to the editor, which reads
:meth:`DocumentSpelling.misspellings` when ``misspellingsChanged`` says
some block changed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QTextBlock, QTextDocument, QTextFormat

from . import words
from .backends import AUTOMATIC, SpellBackend, TextCheck, create_backend
from .words import Scan, Span, State

_KNOWN_LIMIT = 50_000       # words remembered per session before starting over


@dataclass(frozen=True)
class Misspelling:
    """A misspelled word."""

    start: int
    """Where it starts, in UTF-16 units (what QTextCursor counts) from the
    start of its block, or of the text that was checked."""
    length: int
    """How long it is, in UTF-16 units."""
    word: str
    """The word, as Suggestions, Learn Spelling and Ignore Spelling take it."""
    language: str
    """The language it was checked in (``AUTOMATIC`` when the system
    could not tell the language of its paragraph)."""

    @property
    def end(self) -> int:
        return self.start + self.length

    def touches(self, offset: int) -> bool:
        return self.start <= offset <= self.end


class SpellChecker(QObject):
    """The system spell checker, shared by every document."""

    availabilityChanged = Signal(bool)
    """The system checker started or stopped working."""
    wordAccepted = Signal(str)
    """A word was learned or ignored: it is no longer misspelled."""

    def __init__(self, backend: Optional[SpellBackend] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._backend = backend if backend is not None else create_backend()
        self._known: Dict[Tuple[str, str], bool] = {}
        self._available: Optional[bool] = None
        self._default: Optional[str] = None

    @property
    def backend(self) -> SpellBackend:
        return self._backend

    def is_available(self) -> bool:
        """Whether the system checker works (the Spelling commands are
        dimmed when it does not). Reaches it on first use."""
        self._backend.is_available()
        return self._notice()

    def languages(self) -> List[str]:
        """The languages the system can check, as BCP 47 tags."""
        found = self._backend.languages()
        self._notice()
        return found

    def default_language(self) -> str:
        """``AUTOMATIC`` where the system tells languages apart (macOS),
        otherwise the system language; "" when there is none."""
        if self._default is None:
            self._default = self._backend.default_language() or ""
            self._notice()
        return self._default

    def check(self, word: str, language: Optional[str] = None) -> bool:
        """Whether ``word`` is spelled right (a word that cannot be
        checked is)."""
        return self._right(word, self._language(language))

    def suggestions(self, word: str, language: Optional[str] = None) -> List[str]:
        """What ``word`` might have meant, best first. A word typed with a
        typographic apostrophe gets suggestions with one too."""
        found = self._backend.suggestions(_for_checker(word), self._language(language))
        self._notice()
        if "’" in word:
            found = [suggestion.replace("'", "’") for suggestion in found]
        return found

    def learn(self, word: str, language: Optional[str] = None) -> bool:
        """Add ``word`` to the person's dictionary for good (the system's,
        shared with other apps where the system has one)."""
        return self._accept(word, self._backend.learn(_for_checker(word), self._language(language)))

    def ignore(self, word: str, language: Optional[str] = None) -> bool:
        """Accept ``word`` everywhere until the app quits."""
        return self._accept(word, self._backend.ignore(_for_checker(word), self._language(language)))

    def find_misspellings(self, text: str, language: Optional[str] = None) -> List[Misspelling]:
        """The misspelled words of a short plain text (a title, a
        summary), now. Lines are read the way a Markdown document's are;
        offsets count UTF-16 units from the start of ``text``."""
        language = self._language(language)
        offsets = words.utf16_offsets(text)
        out: List[Misspelling] = []
        state = words.START
        position = 0
        for line in text.split("\n"):
            found = words.scan(line, state)
            state = found.state
            for start, end, word, checked_in in self._misspelled(line, found, language):
                first = words.to_utf16(offsets, position + start)
                out.append(Misspelling(first, words.to_utf16(offsets, position + end) - first,
                                       word, checked_in))
            position += len(line) + 1
        return out

    # -- inside ---------------------------------------------------------------

    def _misspelled(self, text: str, found: Scan,
                    language: str) -> List[Tuple[int, int, str, str]]:
        """``(start, end, word, language)`` of each misspelled word of
        ``text`` that :func:`words.scan` found, as Python indices (for
        DocumentSpelling and find_misspellings)."""
        if not found.words or not self._available_now():
            return []
        result = self._backend.check_text(words.masked(text, found.skipped), language)
        if not self._notice():
            return []
        if result is not None:
            return _within_words(text, found.words, result)
        return self._word_by_word(text, found.words, self._concrete(language))

    def _language(self, language: Optional[str]) -> str:
        return language if language else self.default_language()

    def _concrete(self, language: str) -> str:
        """The language a checker that reads word by word checks in."""
        if language and language != AUTOMATIC:
            return language
        fallback = self._backend.default_language() or ""
        return "" if fallback == AUTOMATIC else fallback

    def _available_now(self) -> bool:
        return self._backend.problem is None

    def _notice(self) -> bool:
        """Note whether the backend still works; say so when that changed."""
        available = self._backend.problem is None
        if available != self._available:
            known_before = self._available is not None
            self._available = available
            if not available:
                self._known.clear()
            if known_before:
                self.availabilityChanged.emit(available)
        return available

    def _right(self, word: str, language: str) -> bool:
        key = (language, _for_checker(word))
        if not self._backend.is_own_thread():
            # The neutral answer another thread gets is not remembered as
            # what the word is.
            return self._backend.check(key[1], language)
        known = self._known.get(key)
        if known is None:
            known = self._backend.check(key[1], language)
            if not self._notice():
                return True
            if len(self._known) >= _KNOWN_LIMIT:
                self._known.clear()
            self._known[key] = known
        return known

    def _word_by_word(self, text: str, spans: Sequence[Span],
                      language: str) -> List[Tuple[int, int, str, str]]:
        out: List[Tuple[int, int, str, str]] = []
        for start, end in spans:
            if not self._available_now():
                return []
            word = text[start:end]
            if self._right(word, language):
                continue
            # Abbreviations are listed with their dot ("bzw.", "etc.").
            if text[end:end + 1] == "." and self._right(word + ".", language):
                continue
            if words.is_hyphenated(word):
                # A compound the dictionary does not list is fine when its
                # parts are words: only the parts that are not are wrong.
                for part_start, part_end in words.hyphen_parts(text, start, end):
                    part = text[part_start:part_end]
                    if not self._right(part, language):
                        out.append((part_start, part_end, words.clean(part), language))
                continue
            out.append((start, end, words.clean(word), language))
        return out if self._available_now() else []

    def _accept(self, word: str, taken: bool) -> bool:
        self._notice()
        if taken:
            accepted = _for_checker(word).casefold()
            for key in [key for key in self._known if key[1].casefold() == accepted]:
                del self._known[key]
            self.wordAccepted.emit(words.clean(word))
        return taken


class DocumentSpelling(QObject):
    """The misspelled words of one document, kept current as it changes."""

    misspellingsChanged = Signal(int, int)
    """The misspellings of the blocks from the first to the last block
    number (both included) changed; read them with ``misspellings``."""

    SLICE_SECONDS = 0.008
    """How long one slice of checking may take before typing gets its turn."""

    TYPING_PAUSE_SECONDS = 1.5
    """After this long without typing, the word being typed counts as
    finished (about the pause between two sentences)."""

    def __init__(self, checker: SpellChecker, document: QTextDocument, *,
                 language: Optional[str] = None,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        # A child of its document: it lives exactly as long as the document.
        super().__init__(document)
        self._checker = checker
        self._document = document
        self._language = language or None
        self._clock = clock
        self._closed = False
        # Per block, in block order: the state at its end (always current),
        # its misspellings as found, those the editor was last given (all
        # but the word being typed), the text they were found in, and
        # whether it must be checked again.
        self._states: List[State] = []
        self._found: List[Tuple[Misspelling, ...]] = []
        self._shown: List[Tuple[Misspelling, ...]] = []
        self._texts: List[Optional[str]] = []
        self._dirty = bytearray()
        self._visible = (0, -1)
        self._sweep = 0
        # The word being typed: where the last edit ended, while the
        # editor's cursor is there and typing has not paused.
        self._cursor: Optional[int] = None
        self._edit_end: Optional[int] = None
        self._typing: Optional[int] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._run)
        self._pause = QTimer(self)
        self._pause.setSingleShot(True)
        self._pause.timeout.connect(self._typing_paused)
        # A document only reports its changes once it has a layout; every
        # editor's document has one, a document made on its own gets it now.
        document.documentLayout()
        document.contentsChange.connect(self._changed)
        checker.availabilityChanged.connect(self._availability_changed)
        checker.wordAccepted.connect(self._word_accepted)
        self._restart()

    # -- what the editor asks -----------------------------------------------

    def language(self) -> str:
        """The language this document is checked in."""
        return self._language or self._checker.default_language()

    def set_language(self, language: Optional[str]) -> None:
        """Check in ``language`` from now on (``AUTOMATIC``, a BCP 47 tag,
        or None for the system's default); every block is checked again."""
        language = language or None
        if language != self._language:
            self._language = language
            self._mark_all()

    def set_visible_blocks(self, first: int, last: int) -> None:
        """The blocks on screen, from the first to the last block number:
        they are checked before the rest."""
        self._visible = (first, last)
        self._schedule()

    def set_cursor_position(self, position: int) -> None:
        """Where the editor's cursor is: call it whenever the cursor moves.
        The word being typed there is not finished, so it is left out of
        ``misspellings`` until it is: a space, punctuation or Return
        after it, the cursor moving away, or a pause in typing. Then
        ``misspellingsChanged`` names its block."""
        if self._closed:
            return
        self._cursor = position
        if position != self._edit_end:
            self._edit_end = None
            self._pause.stop()
        self._update_typing()

    def misspellings(self, block: QTextBlock) -> Tuple[Misspelling, ...]:
        """The misspelled words of ``block``, empty until it is checked,
        without the word being typed at the cursor."""
        number = block.blockNumber()
        if self._closed or not 0 <= number < len(self._found) or self._stale(number, block):
            return ()
        return self._without_typing(block, self._found[number])

    def misspelling_at(self, position: int) -> Optional[Misspelling]:
        """The misspelled word at a document position (for the context
        menu), checking its block right now if it is not checked yet.
        Its ``start`` counts from the start of the block."""
        if self._closed:
            return None
        block = self._document.findBlock(position)
        if not block.isValid():
            return None
        number = block.blockNumber()
        if (number < len(self._dirty) and self._stale(number, block)
                and self._checker.is_available()):
            if self._check(number):
                self.misspellingsChanged.emit(number, number)
        if self._closed or self._stale(number, block):
            return None
        offset = position - block.position()
        # The word asked about, also while it is being typed.
        for misspelling in self._found[number]:
            if misspelling.touches(offset):
                return misspelling
        return None

    def check_all(self) -> None:
        """Check every block that is not checked yet, now."""
        while not self._closed and self._run(deadline=None):
            pass

    def is_checking(self) -> bool:
        """Whether some block still waits to be checked (never while the
        system checker does not work)."""
        return (not self._closed and self._dirty.find(1) >= 0
                and self._checker.is_available())

    def close(self) -> None:
        """Stop following the document (spell checking was turned off)."""
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._pause.stop()
        for signal, slot in ((self._document.contentsChange, self._changed),
                             (self._checker.availabilityChanged, self._availability_changed),
                             (self._checker.wordAccepted, self._word_accepted)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    # -- following the document ---------------------------------------------

    def _restart(self) -> None:
        count = self._document.blockCount()
        self._found = [()] * count
        self._shown = [()] * count
        self._texts = [None] * count
        self._dirty = bytearray(b"\x01") * count
        self._states = []
        self._states_from(0, count - 1)
        self._sweep = 0
        self._schedule()

    def _changed(self, position: int, removed: int, added: int) -> None:
        if self._closed:
            return
        document = self._document
        count = document.blockCount()
        first = _block_number(document, position)
        last = _block_number(document, position + added)
        old_last = last - (count - len(self._found))
        if not (first - 1 <= old_last < len(self._found)) or first > last:
            self._restart()
            return
        # The blocks the change replaced are checked again (and then always
        # named to the editor).
        new = last - first + 1
        old_text = self._texts[first] if first <= old_last else None
        old_end = self._states[old_last] if old_last >= 0 else words.START
        self._found[first:old_last + 1] = [()] * new
        self._shown[first:old_last + 1] = [()] * new
        self._texts[first:old_last + 1] = [None] * new
        self._dirty[first:old_last + 1] = b"\x01" * new
        self._states[first:old_last + 1] = [words.START] * new
        if len(self._found) != count:
            self._restart()
            return
        # Qt reports some undos short (seen after rich text pasted over a
        # selection): a block after the range whose text is not the one it
        # was checked in changed too.
        end = last
        while end + 1 < count:
            remembered = self._texts[end + 1]
            if remembered is None or remembered == document.findBlockByNumber(end + 1).text():
                break
            end += 1
            self._dirty[end] = 1
        self._states_from(first, end, old_end if end == last else None)
        # Typing, unless only the formatting changed (Bold over a selection).
        if not (removed == added and old_text == document.findBlockByNumber(first).text()):
            self._edit_end = position + added
            self._pause.start(int(self.TYPING_PAUSE_SECONDS * 1000))
        self._update_typing()
        self._schedule()

    def _stale(self, number: int, block: QTextBlock) -> bool:
        """Whether a block must be checked before its misspellings count.
        One whose text changed without the document saying so is marked
        to be checked again here, so it heals as soon as it is read."""
        if self._dirty[number]:
            return True
        if self._texts[number] == block.text():
            return False
        self._dirty[number] = 1
        self._schedule()
        return True

    def _states_from(self, first: int, last: int, old_end: Optional[State] = None) -> None:
        """Bring the states up to date from block ``first``: every block
        up to ``last`` (the ones that changed, whose old blocks ended in
        ``old_end``), then on while a block starts in another state than
        it did before. Such a block is checked again: a fence opened or
        closed above it turns it into code or back into prose."""
        block = self._document.findBlockByNumber(first)
        state = words.START if first == 0 else self._states[first - 1]
        number = first
        old_start: Optional[State] = None
        while block.isValid():
            if number > last:
                if state == old_start:
                    break
                self._dirty[number] = 1
            after = _advanced(block, state)
            if number < len(self._states):
                before = self._states[number]
                self._states[number] = after
            else:
                before = None
                self._states.append(after)
            old_start = old_end if number == last else before
            state = after
            block = block.next()
            number += 1

    def _update_typing(self) -> None:
        """Note where the word being typed is; when that changed, tell the
        editor about each block whose shown misspellings changed with it
        (the word just finished now has its underline)."""
        typing = (self._edit_end
                  if self._edit_end is not None and self._edit_end == self._cursor else None)
        if typing == self._typing:
            return
        before, self._typing = self._typing, typing
        for number in sorted({n for n in (self._block_at(before), self._block_at(typing))
                              if n is not None}):
            block = self._document.findBlockByNumber(number)
            if self._dirty[number] or self._texts[number] != block.text():
                continue                    # it is checked again, and told then
            shown = self._without_typing(block, self._found[number])
            if shown != self._shown[number]:
                self._shown[number] = shown
                self.misspellingsChanged.emit(number, number)

    def _typing_paused(self) -> None:
        self._edit_end = None
        self._update_typing()

    def _block_at(self, position: Optional[int]) -> Optional[int]:
        if position is None:
            return None
        block = self._document.findBlock(position)
        if not block.isValid() or block.blockNumber() >= len(self._found):
            return None
        return block.blockNumber()

    def _without_typing(self, block: QTextBlock,
                        found: Tuple[Misspelling, ...]) -> Tuple[Misspelling, ...]:
        """``found`` without the word being typed, if it is in ``block``."""
        if self._typing is None or not found:
            return found
        offset = self._typing - block.position()
        if not 0 <= offset < block.length():
            return found
        return tuple(m for m in found if not m.touches(offset))

    def _mark_all(self) -> None:
        self._dirty = bytearray(b"\x01") * len(self._found)
        self._schedule()

    def _availability_changed(self, available: bool) -> None:
        if available:
            self._mark_all()
            return
        self._timer.stop()
        self._found = [()] * len(self._found)
        if any(self._shown):
            self._shown = [()] * len(self._found)
            self.misspellingsChanged.emit(0, len(self._found) - 1)
        self._dirty = bytearray(b"\x01") * len(self._found)

    def _word_accepted(self, word: str) -> None:
        accepted = _for_checker(word).casefold()
        for number, found in enumerate(self._found):
            if any(_for_checker(m.word).casefold() == accepted for m in found):
                self._dirty[number] = 1
        self._schedule()

    # -- checking -----------------------------------------------------------

    def _schedule(self) -> None:
        if not self._closed and self._dirty.find(1) >= 0 and not self._timer.isActive():
            self._timer.start()

    def _run(self, deadline: Optional[float] = -1.0) -> bool:
        """Check blocks until the slice is used up (``deadline`` None:
        until all are). Returns whether blocks are left."""
        if self._closed or not self._checker.is_available():
            return False
        if deadline is not None and deadline < 0:
            deadline = self._clock() + self.SLICE_SECONDS
        changed: List[int] = []
        while True:
            number = self._next()
            if number is None:
                break
            if self._check(number):
                changed.append(number)
            if not self._checker.is_available():
                return False
            if deadline is not None and self._clock() >= deadline:
                break
        if changed:
            self.misspellingsChanged.emit(min(changed), max(changed))
        left = self._dirty.find(1) >= 0
        if left and deadline is not None:
            self._timer.start()
        return left

    def _next(self) -> Optional[int]:
        """The next block to check: a visible one, or the next one after
        the last checked, around the document."""
        first, last = self._visible
        for number in range(max(first, 0), min(last, len(self._dirty) - 1) + 1):
            if self._dirty[number]:
                return number
        number = self._dirty.find(1, self._sweep)
        if number < 0:
            number = self._dirty.find(1)
        if number < 0:
            return None
        self._sweep = number
        return number

    def _check(self, number: int) -> bool:
        """Check one block. Returns whether the editor must redraw it: the
        misspellings it is shown changed, or its text did."""
        block = self._document.findBlockByNumber(number)
        text = block.text()
        found: Tuple[Misspelling, ...] = ()
        if not _code_block(block):
            state = words.START if number == 0 else self._states[number - 1]
            scanned = words.scan(text, state, _code_spans(block, text))
            offsets = words.utf16_offsets(text)
            found = tuple(
                Misspelling(words.to_utf16(offsets, start),
                            words.to_utf16(offsets, end) - words.to_utf16(offsets, start),
                            word, language)
                for start, end, word, language
                in self._checker._misspelled(text, scanned, self.language()))
        # A block whose text changed is always named: underlines kept as
        # text cursors (extra selections) grow with text typed right after
        # them, and need redrawing even when the misspellings are the same.
        edited = self._texts[number] != text
        self._dirty[number] = 0
        self._texts[number] = text
        self._found[number] = found
        shown = self._without_typing(block, found)
        if shown == self._shown[number] and not edited:
            return False
        self._shown[number] = shown
        return True


# -- helpers --------------------------------------------------------------------

def _for_checker(word: str) -> str:
    """A word as a system checker is asked: composed, without soft
    hyphens, with a plain apostrophe (not every dictionary knows ’)."""
    return words.clean(word).replace("’", "'")


def _within_words(text: str, spans: Sequence[Span],
                  result: TextCheck) -> List[Tuple[int, int, str, str]]:
    """What a system checker flagged in a whole paragraph, kept only
    where it lies inside a word the scanner would check, and is one."""
    out: List[Tuple[int, int, str, str]] = []
    index = 0
    for start, end in sorted(result.misspelled):
        while index < len(spans) and spans[index][1] <= start:
            index += 1
        if index == len(spans):
            break
        if spans[index][0] <= start and end <= spans[index][1]:
            piece = text[start:end]
            if words.checkable(piece):
                out.append((start, end, words.clean(piece), result.language))
    return out


def _block_number(document: QTextDocument, position: int) -> int:
    block = document.findBlock(position)
    return block.blockNumber() if block.isValid() else document.blockCount() - 1


def _code_block(block: QTextBlock) -> bool:
    """A code block Qt read from Markdown (the same test markdown_writer
    uses to write one back)."""
    fmt = block.blockFormat()
    return bool(fmt.property(QTextFormat.Property.BlockCodeFence)
                or fmt.hasProperty(QTextFormat.Property.BlockCodeLanguage))


def _advanced(block: QTextBlock, state: State) -> State:
    if _code_block(block):
        return words.passed_code(state)
    return words.advance(block.text(), state)


def _code_spans(block: QTextBlock, text: str) -> List[Span]:
    """What the block's formatting marks as not prose: inline code (a
    fixed-pitch font, as markdown_writer reads it) and mentions (links
    to a nostr: address, whose text is a name)."""
    spans: List[Span] = []
    base = block.position()
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            fmt = fragment.charFormat()
            if fmt.fontFixedPitch() or (fmt.isAnchor()
                                        and fmt.anchorHref().lower().startswith("nostr:")):
                start = fragment.position() - base
                spans.append((start, start + fragment.length()))
        iterator += 1
    offsets = words.utf16_offsets(text) if spans else None
    if offsets is None:
        return spans
    return [(words.from_utf16(offsets, start), words.from_utf16(offsets, end))
            for start, end in spans]
