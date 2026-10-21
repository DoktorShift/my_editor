# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A spreadsheet tab: Excel, OpenDocument and text tables, edited in place.

The tab looks and behaves the way Numbers, Excel and LibreOffice Calc
taught people: a formula bar on top (the cell's name, then what it holds,
a formula as its text), the grid with lettered columns and numbered rows,
and one tab per sheet along the bottom with "+" to add one.

Keys are the ones those apps share: typing replaces the cell, F2 or a
double-click edits it in place, Return and Enter confirm and move down
(Shift goes up), Tab and Shift+Tab move right and left, Escape cancels,
arrows move and Shift+arrows extend the selection. Delete clears, and Cut,
Copy and Paste move tab-separated text, which every spreadsheet app
reads. Everything is undone with Undo (``undo_stack``).

What is typed is read in the app's language: "12,5" is 12.5 in German,
"15 %" a percentage, "1.234,50 €" an amount of money, a date as the
language writes it or as 2026-10-21, "=..." a formula, and a leading
apostrophe keeps anything as text. A text table (.csv, .tsv) holds text
only: what is typed stays exactly as typed.

Saving never loses anything silently. ``save()`` returns a ``SaveResult``
that tells the window what to ask when it cannot save into the file (the
file is open in another app, it changed on the disk, or it holds parts
MyEditor cannot write and needs "Save a Copy…").
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import json
import logging
import math
import os
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

import shiboken6

from PySide6.QtCore import (
    QAbstractTableModel, QEvent, QEventLoop, QItemSelection, QItemSelectionModel, QModelIndex,
    QPoint, QRect, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPen, QUndoCommand,
    QUndoStack,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QProgressBar, QPushButton, QStackedWidget, QStyle, QStyledItemDelegate, QTabBar, QTableView,
    QToolButton, QVBoxLayout, QWidget,
)

import background
import file_kinds
import sheet_formulas as sf
import sheet_io
import media_files
import theme
import i18n
from i18n import _, display_locale, ngettext
from viewer_tabs import FileBar, lock_notice_text

_log = logging.getLogger(__name__)

# Files up to this size open at once; larger ones are read in the
# background while the tab shows how far it got.
_SYNC_LOAD_BYTES = 1_500_000

# Empty rows and columns shown past the last used ones; more appear as
# the person scrolls or moves towards the end.
_SPARE_ROWS = 100
_SPARE_COLS = 10
_MIN_ROWS = 200
_MIN_COLS = 26
_GROW_ROWS = 200
_GROW_COLS = 10

# Merged cells are drawn as one up to this many merges in a sheet.
_MAX_SPANS = 2000

_CELLS_MIME = "application/x-myeditor-cells"

# The most characters a cell holds (Excel's limit; LibreOffice reads it).
MAX_CELL_TEXT = 32767

_CONTROL_CHARACTERS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# The words a save ends with (SaveResult.status).
SAVED = "saved"
IN_USE = "in_use"
CHANGED_ON_DISK = "changed_on_disk"
DELETED = "deleted"
NEEDS_COPY = "needs_copy"
FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class SaveResult:
    """How a save went, and what the window asks next.

    ``SAVED``: written. ``IN_USE``: another app has the file open; say so
    and try later. ``DELETED``: the file went to the Trash or was deleted;
    offer Save As. ``CHANGED_ON_DISK``: ask whether to replace the newer
    file (``save(overwrite_changes=True)``) or reload. ``NEEDS_COPY``: the
    file holds ``parts`` a save would lose; ask "Save a Copy…" (default)
    or Cancel, and save the copy with ``save_as``. ``FAILED``: ``message``
    says why. ``switched`` tells whether the tab now shows ``path``."""

    status: str
    message: str = ""
    parts: Tuple[str, ...] = ()
    path: str = ""
    switched: bool = False

    @property
    def ok(self) -> bool:
        return self.status == SAVED


# --------------------------------------------------------------------------- #
# Words and numbers in the app's language                                     #
# --------------------------------------------------------------------------- #

def number_style(locale=None) -> sheet_io.NumberStyle:
    """Separators and month names of the app's language."""
    from PySide6.QtCore import QLocale
    locale = locale or display_locale()
    months = tuple(locale.monthName(m, QLocale.FormatType.LongFormat) for m in range(1, 13))
    days = tuple(locale.dayName(d, QLocale.FormatType.LongFormat) for d in range(1, 8))
    return sheet_io.NumberStyle(decimal=locale.decimalPoint() or ".",
                                group=locale.groupSeparator() or ",",
                                months=months, days=days)


def _date_order(locale=None) -> str:
    """"dmy", "mdy" or "ymd": how the language writes a short date."""
    from PySide6.QtCore import QLocale
    locale = locale or display_locale()
    pattern = locale.dateFormat(QLocale.FormatType.ShortFormat).lower()
    positions = {k: pattern.find(k) for k in "dmy"}
    return "".join(sorted(positions, key=lambda k: positions[k] if positions[k] >= 0 else 9))


def _date_separator(locale=None) -> str:
    from PySide6.QtCore import QLocale
    locale = locale or display_locale()
    pattern = locale.dateFormat(QLocale.FormatType.ShortFormat)
    found = re.search(r"[^dMy]", pattern)
    return found.group(0) if found else "/"


def locale_date_format(locale=None) -> str:
    """The language's short date as a number format with a four-digit
    year: "dd.mm.yyyy" in German, "m/d/yyyy" in English."""
    order, separator = _date_order(locale), _date_separator(locale)
    parts = {"d": "dd" if separator == "." else "d", "m": "mm" if separator == "." else "m",
             "y": "yyyy"}
    return separator.join(parts[k] for k in order)


_CURRENCY = "€$£¥"


@dataclasses.dataclass(frozen=True)
class ParsedInput:
    """What a person typed, read: a value or a formula, and the number
    format the typing asks for (None keeps the cell's)."""

    value: object = None
    formula: Optional[str] = None
    number_format: Optional[str] = None
    error: Optional[str] = None


def _plain_number(text: str, style: sheet_io.NumberStyle) -> Optional[Tuple[float, int]]:
    """A number written with the language's separators (or plainly, with a
    point), and how many decimals it was written with."""
    text = text.strip().replace("−", "-")
    for decimal, group in ((style.decimal, style.group), (".", ",")):
        pattern = (rf"^[+-]?(?:\d{{1,3}}(?:{re.escape(group)}\d{{3}})+|\d+)"
                   rf"(?:{re.escape(decimal)}\d*)?(?:[eE][+-]?\d+)?$")
        if group in (" ", " "):
            pattern = pattern.replace(re.escape(group), "[   ]")
        if re.match(pattern, text):
            cleaned = re.sub(r"[   ]" if group in (" ", " ")
                             else re.escape(group), "", text)
            cleaned = cleaned.replace(decimal, ".")
            try:
                number = float(cleaned)
            except ValueError:
                continue
            mantissa = cleaned.split("e")[0].split("E")[0]
            decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
            return number, decimals
        if decimal == "." and group == ",":
            break
    return None


def parse_input(text: str, *, style: Optional[sheet_io.NumberStyle] = None,
                current_format: str = "General", locale=None,
                language: str = "en") -> ParsedInput:
    """Read what a person typed into a cell of a workbook. A formula is
    read in ``language`` (German "=SUMME(A1;1,5)") and kept as files
    write it ("=SUM(A1,1.5)")."""
    style = style or number_style(locale)
    if text == "":
        return ParsedInput()
    if text.startswith("'"):
        return ParsedInput(value=text[1:], number_format=None)
    if text.startswith("=") and len(text) > 1:
        text = sf.delocalize_formula(text, language)
        problem = sf.formula_error(text)
        if problem is not None:
            return ParsedInput(formula=text, error=problem)
        return ParsedInput(formula=normalize_formula(text))
    stripped = text.strip()
    upper = stripped.upper()
    if upper in ("TRUE", "FALSE", style.true_text.upper(), style.false_text.upper()):
        return ParsedInput(value=upper in ("TRUE", style.true_text.upper()))
    current_kind = sheet_io.number_format_kind(current_format)
    # Percent: "15 %", "15%".
    match = re.match(r"^(.*?)\s*%$", stripped)
    if match:
        number = _plain_number(match.group(1), style)
        if number is not None:
            value, decimals = number
            fmt = current_format if current_kind == "percent" else \
                ("0%" if decimals == 0 else "0." + "0" * min(decimals, 10) + "%")
            return ParsedInput(value=sf._clean(round(value / 100, 15)), number_format=fmt)
    # Money: "1.234,50 €", "€ 5", "$5.00".
    match = re.match(rf"^([{_CURRENCY}])\s*(.+)$|^(.+?)\s*([{_CURRENCY}])$", stripped)
    if match:
        symbol = match.group(1) or match.group(4)
        amount = match.group(2) or match.group(3)
        number = _plain_number(amount, style)
        if number is not None:
            value, _decimals = number
            if current_kind == "currency" and symbol in current_format:
                fmt = current_format
            elif match.group(1):
                fmt = f'"{symbol}"#,##0.00'
            else:
                fmt = f'#,##0.00 "{symbol}"'
            return ParsedInput(value=sf._clean(value), number_format=fmt)
    number = _plain_number(stripped, style)
    if number is not None:
        value, _decimals = number
        if current_kind == "percent":
            # Typed into a percent cell, 15 means 15 %, as in Excel.
            return ParsedInput(value=sf._clean(round(value / 100, 15)))
        return ParsedInput(value=sf._clean(value))
    moment = parse_date(stripped, locale)
    if moment is not None:
        value, fmt = moment
        keep = current_kind in ("date", "time") and \
            (isinstance(value, _dt.time) == (current_kind == "time"))
        return ParsedInput(value=value, number_format=current_format if keep else fmt)
    return ParsedInput(value=text)


def parse_date(text: str, locale=None) -> Optional[Tuple[object, str]]:
    """A date, a time or both as typed: ISO ("2026-10-21", "2026-10-21
    14:30"), the language's short date ("21.10.2026", "10/21/2026"), or a
    time ("14:30", "14:30:15"). With the number format it suggests."""
    match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", text)
    if match:
        hour, minute, second = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
        if hour < 24 and minute < 60 and second < 60:
            return _dt.time(hour, minute, second), "hh:mm:ss" if match.group(3) else "hh:mm"
        return None
    date_part, _space, time_part = text.partition(" ")
    clock = None
    if time_part:
        match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", time_part.strip())
        if not match:
            return None
        clock = (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))
    day = None
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", date_part)
    if match:
        year, month, mday = (int(g) for g in match.groups())
        fmt = "yyyy-mm-dd"
        day = (year, month, mday)
    else:
        separator = _date_separator(locale)
        pieces = re.split(r"[./-]", date_part)
        if len(pieces) == 3 and all(p.isdigit() for p in pieces) and \
                re.search(re.escape(separator), date_part) and len(pieces[2]) in (2, 4):
            order = _date_order(locale)
            values = dict(zip(order, (int(p) for p in pieces)))
            year = values.get("y", 0)
            if year < 100:
                year += 2000 if year < 50 else 1900
            day = (year, values.get("m", 0), values.get("d", 0))
            fmt = locale_date_format(locale)
    if day is None:
        return None
    try:
        if clock:
            if clock[0] > 23 or clock[1] > 59 or clock[2] > 59:
                return None
            return _dt.datetime(*day, *clock), fmt + (" hh:mm:ss" if clock[2] else " hh:mm")
        return _dt.date(*day), fmt
    except ValueError:
        return None


def normalize_formula(formula: str) -> str:
    """A typed formula with function names and cell references in capitals,
    as spreadsheet apps write them; text in quotes stays as typed."""
    try:
        tokens = sf.tokenize(formula)
    except sf.FormulaSyntaxError:
        return formula
    out, last = [], 0
    for token in tokens:
        if token.kind not in ("func", "ref"):
            continue
        replacement = token.text.upper()
        if token.kind == "ref" and "!" in token.text:
            head, _bang, body = token.text.rpartition("!")
            replacement = head + "!" + body.upper()
        out.append(formula[last:token.start])
        out.append(replacement)
        last = token.end
    out.append(formula[last:])
    return "".join(out)


def edit_text(cell: Optional[sheet_io.Cell], *, text_table: bool,
              style: Optional[sheet_io.NumberStyle] = None, locale=None,
              date1904: bool = False, language: str = "en") -> str:
    """What the formula bar and the cell editor show for ``cell``: its
    formula in ``language``, or its value written so that typing it back
    gives the same value."""
    if cell is None:
        return ""
    if cell.formula is not None:
        return sf.localize_formula(cell.formula, language)
    value = cell.value
    if value is None:
        return ""
    if text_table:
        return str(value)
    style = style or number_style(locale)
    if isinstance(value, str):
        # Text that would be read as something else keeps its apostrophe.
        parsed = parse_input(value, style=style, locale=locale)
        if parsed.formula is not None or not isinstance(parsed.value, str) or \
                value.startswith("'"):
            return "'" + value
        return value
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, sf.CellError):
        return value.code
    kind = sheet_io.number_format_kind(cell.number_format)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)) or \
            (kind in ("date", "time") and isinstance(value, (int, float))):
        if isinstance(value, (int, float)):
            moment = sf.from_serial(value, date1904)
            value = moment.time() if kind == "time" else (
                moment if moment.hour or moment.minute or moment.second else moment.date())
        return _moment_text(value, locale)
    if isinstance(value, (int, float)):
        if kind == "percent":
            return _number_text(value * 100, style) + " %"
        return _number_text(value, style)
    return str(value)


def _number_text(value: float, style: sheet_io.NumberStyle) -> str:
    text = sf.number_text(sf._clean(round(value, 12)) if isinstance(value, float) else value)
    return text.replace(".", style.decimal) if "E" not in text else text


def _moment_text(value, locale=None) -> str:
    if isinstance(value, _dt.time):
        return value.strftime("%H:%M:%S" if value.second else "%H:%M")
    date = value.date() if isinstance(value, _dt.datetime) else value
    order, separator = _date_order(locale), _date_separator(locale)
    parts = {"d": f"{date.day:02d}" if separator == "." else str(date.day),
             "m": f"{date.month:02d}" if separator == "." else str(date.month),
             "y": f"{date.year:04d}"}
    text = separator.join(parts[k] for k in order) if order != "ymd" else date.isoformat()
    if isinstance(value, _dt.datetime) and (value.hour or value.minute or value.second):
        text += " " + value.strftime("%H:%M:%S" if value.second else "%H:%M")
    return text


def _looks_numeric(text: str, style: sheet_io.NumberStyle) -> bool:
    return _plain_number(text, style) is not None


# --------------------------------------------------------------------------- #
# Clipboard text                                                              #
# --------------------------------------------------------------------------- #

def to_tsv(rows: List[List[str]]) -> str:
    """Rows as tab-separated text as Excel puts it on the clipboard: a
    field with a tab, a line break or a quote is quoted."""
    lines = []
    for row in rows:
        fields = []
        for field in row:
            if any(ch in field for ch in '\t\n\r"'):
                field = '"' + field.replace('"', '""') + '"'
            fields.append(field)
        lines.append("\t".join(fields))
    return "\r\n".join(lines) + "\r\n"


def from_tsv(text: str) -> List[List[str]]:
    """Tab-separated text from any spreadsheet app as rows of fields."""
    rows, _final = sheet_io._parse_text_table(text, "\t")
    fields = [[field for field, _quoted in row] for row in rows]
    while fields and fields[-1] == []:
        fields.pop()
    return fields or [[""]]


# --------------------------------------------------------------------------- #
# Undo                                                                        #
# --------------------------------------------------------------------------- #

# How long Python lets the worker hold the interpreter before the window's
# thread may have it, while a job runs. Each time Qt calls into Python
# (a timer, an event, a cell to paint) the window's thread waits for the
# interpreter, and Python makes a waiting thread wait one switch interval
# (5 ms by default) before it asks the holder to let go. On a slow
# machine those waits added up to most of the job: inserting a row into
# 50 000 formulas held the window for 0.8 s although its own work took
# 16 ms. Asked after 0.2 ms, the window gets its turns; the worker takes
# as long as before.
_JOB_SWITCH_INTERVAL = 0.0002
_switch_holders = 0
_switch_before = 0.005


def _quick_switching(on: bool) -> None:
    """Shorten the switch interval while any job runs (on the GUI thread)."""
    global _switch_holders, _switch_before
    if on:
        if _switch_holders == 0:
            _switch_before = sys.getswitchinterval()
            sys.setswitchinterval(min(_switch_before, _JOB_SWITCH_INTERVAL))
        _switch_holders += 1
        return
    _switch_holders -= 1
    if _switch_holders == 0:
        sys.setswitchinterval(_switch_before)


class _Job:
    """Work a tab hands to a worker thread (SheetTab._run_job). The tab
    does not own it: it outlives a tab closed while it runs, and hears
    that it is done on the window's thread (background.run without a
    receiver)."""

    def __init__(self, tab: "SheetTab", label: str, done) -> None:
        self.tab, self.label, self.on_done = tab, label, done
        self.done = False
        self.result = None
        self.error: Optional[BaseException] = None
        self.loops: List[QEventLoop] = []

    def start(self, work) -> None:
        """``work(progress)`` on a thread of its own, which never holds up
        quitting the app."""
        _quick_switching(True)
        background.run(work, self._succeeded, self._failed, progress=self._progressed,
                       daemon=True, name="sheet-work")

    def _succeeded(self, result) -> None:
        self.result = result
        self._finished()

    def _failed(self, error: BaseException) -> None:
        self.error = error              # raised again by the call that ran the job
        self._finished()

    def _finished(self) -> None:
        self.done = True
        _quick_switching(False)
        if shiboken6.isValid(self.tab):
            self.tab._leave_job(self)
        for loop in self.loops:
            loop.quit()

    def _progressed(self, share: float) -> None:
        if not self.done and shiboken6.isValid(self.tab):
            self.tab._show_job_progress(float(share))


class _UndoStack(QUndoStack):
    """The tab's steps. Undo and Redo wait while the tab works on a large
    change (the window's Edit menu calls them directly)."""

    def __init__(self, tab: "SheetTab") -> None:
        super().__init__(tab)
        self._tab = tab

    def undo(self) -> None:
        if not self._tab.is_busy():
            super().undo()

    def redo(self) -> None:
        if not self._tab.is_busy():
            super().redo()

    def canUndo(self) -> bool:  # noqa: N802
        return not self._tab.is_busy() and super().canUndo()

    def canRedo(self) -> bool:  # noqa: N802
        return not self._tab.is_busy() and super().canRedo()


class _CellsCommand(QUndoCommand):
    """Cells changed in one step (typing, Paste, Clear). ``applied``: the
    change is in the sheet already (worked out and made on a worker
    thread), so the first redo, as the step is pushed, does nothing."""

    def __init__(self, tab: "SheetTab", sheet: sheet_io.Sheet, changes: Dict, text: str, *,
                 applied: bool = False) -> None:
        super().__init__(text)
        self.tab, self.sheet, self.changes = tab, sheet, changes
        self._applied = applied

    def redo(self) -> None:
        if self._applied:
            self._applied = False
            return
        self.tab._apply_cells(self.sheet, {pos: new for pos, (_old, new) in self.changes.items()})

    def undo(self) -> None:
        self.tab._apply_cells(self.sheet, {pos: old for pos, (old, _new) in self.changes.items()})


@contextlib.contextmanager
def _busy():
    """The wait cursor while a smaller change to rows, columns or sheets
    runs here (larger ones run on a worker thread: SheetTab._run_job)."""
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        yield
    finally:
        QApplication.restoreOverrideCursor()


class _StructureCommand(QUndoCommand):
    """A change to rows, columns or sheets: done by ``action`` (which
    returns a workbook snapshot), undone by restoring that snapshot."""

    def __init__(self, tab: "SheetTab", text: str, action, select: Optional[int] = None,
                 undo_select: Optional[int] = None) -> None:
        super().__init__(text)
        self.tab, self.action = tab, action
        self.snapshot = None
        self.error: Optional[str] = None
        self.select, self.undo_select = select, undo_select

    def redo(self) -> None:
        action = self.action
        try:
            self.snapshot = self.tab._run_job(_("Updating the sheet\u2026"),
                                              lambda _progress: action(),
                                              size=self.tab._book_size(), wait_cursor=True)
        except sheet_io.SheetFileError as error:
            # The workbook put itself back; this step never happened.
            self.error = str(error)
            self.setObsolete(True)
            return
        self.tab._structure_changed(self.select)

    def undo(self) -> None:
        if self.snapshot is not None:
            book, snapshot = self.tab.book, self.snapshot
            self.tab._run_job(_("Updating the sheet\u2026"),
                              lambda _progress: book.restore(snapshot),
                              size=self.tab._book_size(), wait_cursor=True)
        self.tab._structure_changed(self.undo_select)


class _WidthCommand(QUndoCommand):
    """A column made wider or narrower; dragging the divider is one step."""

    ID = 4711

    def __init__(self, tab: "SheetTab", sheet: sheet_io.Sheet, col: int, old: Optional[float],
                 new: float) -> None:
        super().__init__(_("Column Width"))
        self.tab, self.sheet, self.col, self.old, self.new = tab, sheet, col, old, new
        self.had_change = col in sheet.changed_widths

    def id(self) -> int:
        return self.ID

    def mergeWith(self, other) -> bool:  # noqa: N802
        if not isinstance(other, _WidthCommand) or other.sheet is not self.sheet or \
                other.col != self.col:
            return False
        self.new = other.new
        return True

    def redo(self) -> None:
        self.tab.book.set_width(self.sheet, self.col, self.new)
        self.tab._show_width(self.sheet, self.col)

    def undo(self) -> None:
        if self.old is None:
            self.sheet.widths.pop(self.col, None)
        else:
            self.sheet.widths[self.col] = self.old
        if not self.had_change:
            self.sheet.changed_widths.discard(self.col)
        self.sheet.changed = True
        self.tab._show_width(self.sheet, self.col)


# --------------------------------------------------------------------------- #
# The grid                                                                    #
# --------------------------------------------------------------------------- #

# Work on more cells than this (a save, a copy, a change to many cells, to
# rows or columns) runs on a worker thread while the tab waits, read-only,
# with a line saying what it does: Python takes about a second for a few
# hundred thousand cells, too long for the window to stand still. Less
# is done at once.
BACKGROUND_CELLS = 20_000
# More changed cells than this repaint the whole sheet.
_MANY_CELLS = 10_000

_READ_FLAGS = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
_EDIT_FLAGS = _READ_FLAGS | Qt.ItemFlag.ItemIsEditable


class SheetModel(QAbstractTableModel):
    """One sheet as Qt's table model: what is shown, never more than the
    rows on screen are looked at."""

    def __init__(self, tab: "SheetTab") -> None:
        super().__init__(tab)
        self.tab = tab
        self.sheet: Optional[sheet_io.Sheet] = None
        self._rows = _MIN_ROWS
        self._cols = _MIN_COLS

    def set_sheet(self, sheet: Optional[sheet_io.Sheet]) -> None:
        self.beginResetModel()
        self.sheet = sheet
        self._fit()
        self.endResetModel()

    def _fit(self) -> None:
        if self.sheet is None:
            self._rows, self._cols = 0, 0
            return
        rows, cols = self.sheet.used_size()
        self._rows = min(max(rows + _SPARE_ROWS, _MIN_ROWS, self._rows if self.sheet else 0),
                         sf.MAX_ROWS)
        self._cols = min(max(cols + _SPARE_COLS, _MIN_COLS), sf.MAX_COLS)

    def refit(self) -> None:
        """Make room for cells beyond the rows and columns shown."""
        if self.sheet is None:
            return
        rows, cols = self.sheet.used_size()
        self.ensure(rows + _SPARE_ROWS - 1, cols + _SPARE_COLS - 1)

    def reset(self) -> None:
        self.beginResetModel()
        self._rows = _MIN_ROWS
        self._fit()
        self.endResetModel()

    def ensure(self, row: int, col: int) -> None:
        """Make sure (row, col) is part of the grid."""
        row, col = min(row, sf.MAX_ROWS - 1), min(col, sf.MAX_COLS - 1)
        if row >= self._rows:
            new = min(max(row + 1, self._rows + _GROW_ROWS), sf.MAX_ROWS)
            self.beginInsertRows(QModelIndex(), self._rows, new - 1)
            self._rows = new
            self.endInsertRows()
        if col >= self._cols:
            new = min(max(col + 1, self._cols + _GROW_COLS), sf.MAX_COLS)
            self.beginInsertColumns(QModelIndex(), self._cols, new - 1)
            self._cols = new
            self.endInsertColumns()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._rows

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else self._cols

    def canFetchMore(self, parent) -> bool:  # noqa: N802
        return not parent.isValid() and self.sheet is not None and self._rows < sf.MAX_ROWS

    def fetchMore(self, parent) -> None:  # noqa: N802
        self.ensure(self._rows + _GROW_ROWS - 1, 0)

    def flags(self, index):
        # Asked for every cell Qt walks (a selection's cells, the cells
        # shown): the two answers are made once.
        return _READ_FLAGS if self.tab.read_only else _EDIT_FLAGS

    def cell(self, row: int, col: int) -> Optional[sheet_io.Cell]:
        if self.sheet is None:
            return None
        return self.sheet.cells.get((row, col))

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or self.sheet is None:
            return None
        row, col = index.row(), index.column()
        cell = self.sheet.cells.get((row, col))
        if role == Qt.ItemDataRole.DisplayRole:
            return self.tab.display_text(cell)
        if role == Qt.ItemDataRole.BackgroundRole:
            fill = self.tab.look_at(row, col).fill
            return self.tab.brush(fill) if fill else None
        if cell is None:
            return None
        if role == Qt.ItemDataRole.EditRole:
            return self.tab.edit_text_of(cell)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return self.tab.alignment_of(cell, self.tab.look_at(row, col))
        if role == Qt.ItemDataRole.ForegroundRole:
            if isinstance(cell.value, sf.CellError):
                return QColor(theme.error_text_color(self.tab.is_dark))
            return self.tab.text_color(self.tab.look_at(row, col))
        if role == Qt.ItemDataRole.FontRole:
            look = self.tab.look_at(row, col)
            if look.bold or look.italic:
                font = QFont(self.tab.grid.font())
                font.setBold(look.bold)
                font.setItalic(look.italic)
                return font
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            if cell.state == sf.STATE_APP:
                return _("Calculated by the spreadsheet app. MyEditor shows the result "
                         "saved in the file.")
            if cell.state == sf.STATE_CIRCULAR:
                return _("This formula refers to its own cell, directly or through other "
                         "cells.")
            if cell.state == sf.STATE_STALE:
                return _("MyEditor cannot calculate this formula. The spreadsheet app works "
                         "out its result when it opens the file.")
            return None
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return self.tab.display_text(cell)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal:
                return sf.column_letters(section)
            return str(section + 1)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignCenter
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole) -> bool:  # noqa: N802
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        return self.tab.commit_text(index.row(), index.column(), str(value))

    def cells_changed(self, keys) -> None:
        if self.sheet is None:
            return
        if len(keys) > _MANY_CELLS:
            # A whole sheet cleared or put back: everything is shown again,
            # rather than finding the corners among a few hundred thousand.
            rows, cols = self.sheet.used_size()
            if rows and cols:
                self.ensure(rows - 1, cols - 1)
            self.dataChanged.emit(self.index(0, 0), self.index(self._rows - 1, self._cols - 1))
            return
        rows = [r for uid, r, c in keys if uid == self.sheet.uid]
        cols = [c for uid, r, c in keys if uid == self.sheet.uid]
        if not rows:
            return
        self.ensure(max(rows), max(cols))
        self.dataChanged.emit(self.index(min(rows), min(cols)), self.index(max(rows), max(cols)))


class _CellEditor(QLineEdit):
    """The line edit in a cell while it is edited."""


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)) or \
        isinstance(value, (_dt.datetime, _dt.date, _dt.time))


def _fill_with_hashes(option) -> None:
    """A number wider than its cell shows as "###" filling the cell, as in
    Excel and LibreOffice: part of a number would read as another number
    ("1,434.…" for 1,434.00 €). Text is shortened as before."""
    widget = option.widget
    style = widget.style() if widget is not None else QApplication.style()
    margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, widget) + 1
    room = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, option,
                                widget).width() - 2 * margin
    metrics = QFontMetrics(option.font)
    if metrics.horizontalAdvance(option.text) <= room:
        return
    option.text = "#" * max(1, room // max(1, metrics.horizontalAdvance("#")))


def _padding(digit: int) -> int:
    """Excel's padding around a column's characters, for a digit width."""
    return 2 * math.ceil(digit / 4) + 1


def _screen_width(file_px: float, digit: int) -> int:
    """A column ``file_px`` wide in Excel's pixels (7 per digit, 5 of
    padding), on screen where a digit is ``digit`` pixels wide: the same
    number of digits fits."""
    if file_px <= 5:
        return max(1, int(round(file_px * _padding(digit) / 5)))
    return max(1, int(round((file_px - 5) / 7 * digit + _padding(digit))))


def _file_width(screen_px: float, digit: int) -> float:
    """The width in Excel's pixels of a column ``screen_px`` wide on screen
    (the inverse of _screen_width)."""
    padding = _padding(digit)
    if screen_px <= padding:
        return screen_px * 5 / padding
    return (screen_px - padding) / digit * 7 + 5


class _Delegate(QStyledItemDelegate):
    """Edits cells with a plain line edit; the keys that end editing are
    the tab's (Return moves down, Tab right)."""

    def __init__(self, tab: "SheetTab") -> None:
        super().__init__(tab)
        self.tab = tab

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = _CellEditor(parent)
        editor.setAccessibleName(_("Cell Contents"))
        editor.setFrame(False)
        editor.textEdited.connect(self.tab._mirror_editor)
        self.tab._editor = editor
        # The view the editor lives in: the main grid, or a frozen pane.
        view = parent.parentWidget() if parent is not None else None
        self.tab._editor_view = view if isinstance(view, QTableView) else self.tab.grid
        return editor

    def initStyleOption(self, option, index):  # noqa: N802
        super().initStyleOption(option, index)
        if self.tab.look_at(index.row(), index.column()).wrap:
            option.features |= option.ViewItemFeature.WrapText
        if option.text and option.rect.width() > 0:
            cell = self.tab.model.cell(index.row(), index.column())
            if cell is not None and _is_number(cell.value):
                _fill_with_hashes(option)

    def eventFilter(self, editor, event):  # noqa: N802
        # The view puts this filter on the editor; its keys are the tab's.
        if event.type() == QEvent.Type.KeyPress and editor is self.tab._editor:
            if self.tab._editor_key(event):
                return True
        return super().eventFilter(editor, event)

    def setEditorData(self, editor, index):  # noqa: N802
        start = self.tab._pending_start
        self.tab._pending_start = None
        if start is not None:
            editor.setText(start)
        else:
            self.tab._edit_mode = "edit"
            editor.setText(self.tab.edit_text_of(self.tab.model.cell(index.row(),
                                                                     index.column())))
        editor.deselect()
        editor.setCursorPosition(len(editor.text()))
        self.tab._mirror_editor(editor.text())

    def setModelData(self, editor, model, index):  # noqa: N802
        # The tab's keys commit through SheetTab._commit_editor; Qt's own
        # commit (clicking elsewhere) lands here, and Qt closes the editor.
        if not self.tab._committing:
            self.tab.commit_text(index.row(), index.column(), editor.text())

    def destroyEditor(self, editor, index):  # noqa: N802
        if self.tab._editor is editor:
            self.tab._editor = None
        super().destroyEditor(editor, index)


class _Header(QHeaderView):
    """The row or column numbers, marking those of the selection as Qt
    does, but from the selection's ranges. Qt asks its header's selection
    about every cell of each section shown (whether it highlights or
    not): after Select All on a sheet of 50 000 rows, every repaint of the
    column letters walked 400 000 cells and the window stood still for
    seconds. So the header keeps a selection of its own that stays empty,
    and reads the grid's for the marks."""

    def __init__(self, orientation: Qt.Orientation, parent=None) -> None:
        super().__init__(orientation, parent)
        self._cells_selection: Optional[QItemSelectionModel] = None
        self._quiet: Optional[QItemSelectionModel] = None

    def setSelectionModel(self, selection) -> None:  # noqa: N802
        if self._cells_selection is not None and shiboken6.isValid(self._cells_selection):
            try:
                self._cells_selection.selectionChanged.disconnect(self._repaint)
            except (RuntimeError, TypeError):
                pass
        self._cells_selection = selection
        if selection is None:
            super().setSelectionModel(selection)
            return
        selection.selectionChanged.connect(self._repaint)
        if self._quiet is None or self._quiet.model() is not selection.model():
            self._quiet = QItemSelectionModel(selection.model(), self)
        super().setSelectionModel(self._quiet)

    def _repaint(self, *_args) -> None:
        self.viewport().update()

    def initStyleOptionForIndex(self, option, logical: int) -> None:  # noqa: N802
        super().initStyleOptionForIndex(option, logical)
        model, selection = self.model(), self._cells_selection
        if model is None or selection is None or not shiboken6.isValid(selection):
            return
        across = self.orientation() == Qt.Orientation.Horizontal
        last = (model.rowCount() if across else model.columnCount()) - 1
        for span in selection.selection():
            first, final = (span.left(), span.right()) if across else (span.top(), span.bottom())
            if not first <= logical <= final:
                continue
            option.state |= QStyle.StateFlag.State_On
            start, end = (span.top(), span.bottom()) if across else (span.left(), span.right())
            if start == 0 and end == last:
                option.state |= QStyle.StateFlag.State_Sunken


class _Grid(QTableView):
    """The table: keys of a spreadsheet app, not of a list. The frozen
    rows and columns are more of these (``pane`` "rows", "cols" or
    "corner") laid over the main one, sharing its model and selection."""

    def __init__(self, tab: "SheetTab", parent=None, pane: Optional[str] = None) -> None:
        super().__init__(parent or tab)
        self.tab = tab
        self.pane = pane
        for orientation, install in ((Qt.Orientation.Horizontal, self.setHorizontalHeader),
                                     (Qt.Orientation.Vertical, self.setVerticalHeader)):
            header = _Header(orientation, self)
            install(header)
            header.setHighlightSections(False)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self.tab._grid_key(event):
            return
        super().keyPressEvent(event)

    def focusNextPrevChild(self, next_) -> bool:  # noqa: N802
        # Tab and Shift+Tab move between cells, as in every spreadsheet;
        # Ctrl+Tab still leaves the grid.
        modifiers = QGuiApplication.keyboardModifiers()
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            return super().focusNextPrevChild(next_)
        if self.tab._editor is None:
            self.tab._move(0, 1 if next_ else -1)
            return True
        return super().focusNextPrevChild(next_)

    def edit(self, index, trigger=QAbstractItemView.EditTrigger.AllEditTriggers, event=None):
        if self.tab.read_only and trigger != QAbstractItemView.EditTrigger.NoEditTriggers:
            return False
        started = super().edit(index, trigger, event)
        editor = self.tab._editor
        if started and editor is not None:
            # Qt selects the whole text of a new editor; a spreadsheet puts
            # the caret after it, so the next key adds to what is there.
            editor.deselect()
            editor.setCursorPosition(len(editor.text()))
        return started

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible):  # noqa: N802
        """Bring a cell into view, never under the frozen rows or columns
        (and never scroll for a cell that is frozen itself)."""
        if self.pane is not None:
            # A frozen pane only ever follows the main grid's scrolling.
            return
        frozen_rows, frozen_cols = self.tab._frozen_counts()
        vertical, horizontal = self.verticalScrollBar(), self.horizontalScrollBar()
        before = (vertical.value(), horizontal.value())
        super().scrollTo(index, hint)
        if not index.isValid():
            return
        if index.row() < frozen_rows:
            vertical.setValue(before[0])
        elif frozen_rows:
            covered = self.tab._frozen_extent("rows")
            top = self.visualRect(index).top()
            if top < covered:
                vertical.setValue(vertical.value() - (covered - top))
        if index.column() < frozen_cols:
            horizontal.setValue(before[1])
        elif frozen_cols:
            covered = self.tab._frozen_extent("cols")
            left = self.visualRect(index).left()
            if left < covered:
                horizontal.setValue(horizontal.value() - (covered - left))

    def updateGeometries(self) -> None:  # noqa: N802
        super().updateGeometries()
        if self.pane is None:
            self.tab._place_frozen()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.pane is None:
            self.tab._place_frozen()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        self.tab._paint_borders(self, event.rect())


def _read_workbook(path: str, progress) -> Tuple[Optional[sheet_io.Workbook], str, str]:
    """``(workbook, "", path)`` read on a worker thread, or ``(None,
    error, path)``: a reader thread always reports back."""
    try:
        return sheet_io.load_workbook(path, progress=progress), "", path
    except sheet_io.SheetFileError as error:
        return None, str(error), path
    except Exception as error:                              # noqa: BLE001
        _log.exception("%s could not be read", path)
        return None, str(error), path


def _close_unwanted(read) -> None:
    """The tab closed while its file was read: nobody wants the workbook."""
    if read[0] is not None:
        read[0].close()


# --------------------------------------------------------------------------- #
# The tab                                                                     #
# --------------------------------------------------------------------------- #

class SheetTab(QWidget):
    """A spreadsheet file, shown and edited."""

    open_requested = Signal()
    open_with_requested = Signal(QPoint)
    reveal_requested = Signal()
    modification_changed = Signal(bool)
    # True while the tab works on a large change on a worker thread
    # (read-only then), False once it is done.
    busy_changed = Signal(bool)
    # From the bar shown when the file is gone: the window saves it under a
    # name, or closes the tab.
    save_as_requested = Signal()
    close_requested = Signal()
    # The workbook arrived (True) or could not be read (False).
    loaded = Signal(bool)

    kind = "sheet"

    def __init__(self, path: str, is_dark: bool = False, parent=None, *,
                 background: Optional[bool] = None) -> None:
        super().__init__(parent)
        self._file_path = os.path.abspath(path)
        self.is_dark = is_dark
        self.load_ok = True
        self.load_error = ""
        self.book: Optional[sheet_io.Workbook] = None
        self.sheet: Optional[sheet_io.Sheet] = None
        self.read_only = False
        # Shown, never edited (Quick Look): set_view_only.
        self._view_only = False
        self._released = False
        self._reload_pending = False
        self._keep_mine = False
        self._editor: Optional[QLineEdit] = None
        self._pending_start: Optional[str] = None
        self._committing = False
        self._edit_mode = "edit"
        self._default_app: Optional[str] = None
        self._style = number_style()
        self._locale = display_locale()
        # Formulas are shown and typed in the app's language.
        self._language = i18n.language()
        self._width_guard = False
        self._lock_path: Optional[str] = None
        self._colors: Dict[str, QColor] = {}
        self._panes: Dict[str, QTableView] = {}
        self._frozen: Optional[Tuple[int, int]] = None
        self._editor_view: Optional[QTableView] = None
        self._grid_background = QColor("#FFFFFF")
        self._digit_key: Optional[str] = None
        self._digit = 7

        # The large change running on a worker thread, and what waits for
        # it to end (a deferred reload, a tab closed meanwhile).
        self._job: Optional[_Job] = None
        # Calls of _run_job still on the stack: until they return (an Undo
        # inside Qt's undo stack, say), the tab stays read-only and whole.
        self._job_frames = 0
        self._job_had_focus = False
        self._after_job: List[Callable[[], None]] = []
        self.undo_stack = _UndoStack(self)
        # A method, not a lambda: Qt drops the connection with the tab.
        self.undo_stack.cleanChanged.connect(self._on_clean_changed)

        self._build()
        self.update_theme(is_dark)
        try:
            size = os.path.getsize(self._file_path)
        except OSError:
            size = 0
        if background is None:
            background = size > _SYNC_LOAD_BYTES
        if background:
            self._load_in_background()
        else:
            self._load_now()

    # -- construction ------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # The slim bar on top: the file, and the app it belongs to.
        # The bar every file tab has (viewer_tabs.FileBar): the kind's
        # icon, the name and facts, "Open in {app}" with its chevron, and
        # the button that shows the file in the Finder.
        self._bar = FileBar()
        self._bar.open_requested.connect(self.open_requested)
        self._bar.open_with_requested.connect(self.open_with_requested)
        self._bar.reveal_requested.connect(self.reveal_requested)
        self._bar.set_file(self._file_path, [])
        layout.addWidget(self._bar)

        # The info line: what the file is, and what to know about it.
        self._info = QWidget()
        self._info.setObjectName("SheetInfo")
        info = QHBoxLayout(self._info)
        info.setContentsMargins(14, 5, 14, 5)
        info.setSpacing(12)
        self._info_label = QLabel()
        self._info_label.setWordWrap(True)
        info.addWidget(self._info_label, 1)
        self._notice = QLabel()
        self._notice.setObjectName("SheetNotice")
        self._notice.setWordWrap(True)
        self._notice.hide()
        info.addWidget(self._notice, 2)
        self._job_progress = QProgressBar()
        self._job_progress.setRange(0, 0)
        self._job_progress.setTextVisible(False)
        self._job_progress.setMaximumWidth(160)
        self._job_progress.setMaximumHeight(10)
        self._job_progress.hide()
        info.addWidget(self._job_progress)
        self._markdown_button = QToolButton()
        self._markdown_button.setText(_("Copy as Markdown Table"))
        self._markdown_button.setAccessibleName(_("Copy as Markdown Table"))
        self._markdown_button.clicked.connect(self.copy_as_markdown)
        info.addWidget(self._markdown_button)
        layout.addWidget(self._info)

        # "This file changed on disk." with the two ways out.
        self._changed_bar = QWidget()
        self._changed_bar.setObjectName("SheetChanged")
        changed = QHBoxLayout(self._changed_bar)
        changed.setContentsMargins(14, 5, 10, 5)
        changed.setSpacing(8)
        self._changed_label = QLabel(_("This file changed on disk."))
        self._changed_label.setWordWrap(True)
        changed.addWidget(self._changed_label, 1)
        self._reload_button = QPushButton(_("Reload"))
        self._reload_button.setAccessibleName(_("Reload"))
        self._reload_button.clicked.connect(self._on_reload_clicked)
        changed.addWidget(self._reload_button)
        self._keep_button = QPushButton(_("Keep My Version"))
        self._keep_button.setAccessibleName(_("Keep My Version"))
        self._keep_button.clicked.connect(self._on_keep_clicked)
        changed.addWidget(self._keep_button)
        # The same bar when the file is gone: Save As or Close.
        self._save_as_button = QPushButton(_("Save As\u2026"))
        self._save_as_button.setAccessibleName(_("Save As\u2026"))
        self._save_as_button.clicked.connect(self.save_as_requested)
        changed.addWidget(self._save_as_button)
        self._close_button = QPushButton(_("Close"))
        self._close_button.setAccessibleName(_("Close"))
        self._close_button.clicked.connect(self.close_requested)
        changed.addWidget(self._close_button)
        self._deleted = False
        self._changed_bar.hide()
        layout.addWidget(self._changed_bar)

        # The formula bar.
        self._formula_bar = QWidget()
        self._formula_bar.setObjectName("FormulaBar")
        formula = QHBoxLayout(self._formula_bar)
        formula.setContentsMargins(8, 4, 8, 4)
        formula.setSpacing(6)
        self.name_box = QLineEdit()
        self.name_box.setAccessibleName(_("Cell Reference"))
        self.name_box.setToolTip(_("Type a cell such as C12 and press Return to go there"))
        self.name_box.setFixedWidth(90)
        self.name_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_box.returnPressed.connect(self._on_name_entered)
        self.name_box.installEventFilter(self)
        formula.addWidget(self.name_box)
        self.formula_field = QLineEdit()
        self.formula_field.setAccessibleName(_("Cell Contents"))
        self.formula_field.returnPressed.connect(self._on_formula_entered)
        self.formula_field.installEventFilter(self)
        formula.addWidget(self.formula_field, 1)
        layout.addWidget(self._formula_bar)

        # The grid, or how far loading got, or why it failed.
        self._stack = QStackedWidget()
        self.model = SheetModel(self)
        self.grid = _Grid(self)
        self.grid.setAccessibleName(_("Spreadsheet"))
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(_Delegate(self))
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.grid.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked |
                                  QAbstractItemView.EditTrigger.EditKeyPressed)
        self.grid.setWordWrap(False)
        self.grid.setShowGrid(True)
        self.grid.setTabKeyNavigation(False)
        self.grid.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.grid.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setDefaultSectionSize(int(sheet_io.DEFAULT_WIDTH_PX))
        header.setMinimumSectionSize(8)
        header.sectionResized.connect(self._on_section_resized)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._column_menu)
        rows_header = self.grid.verticalHeader()
        rows_header.setDefaultSectionSize(int(sheet_io.DEFAULT_HEIGHT_PX + 2))
        rows_header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows_header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        rows_header.customContextMenuRequested.connect(self._row_menu)
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._cell_menu)
        self.grid.selectionModel().currentChanged.connect(self._on_current_changed)
        self.grid.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.grid.horizontalScrollBar().valueChanged.connect(self._on_horizontal_scroll)
        self.model.columnsInserted.connect(lambda *_a: self._apply_new_column_widths())
        self.model.rowsInserted.connect(self._on_rows_inserted)
        self.grid.installEventFilter(self)
        self._stack.addWidget(self.grid)

        self._loading = QWidget()
        loading = QVBoxLayout(self._loading)
        loading.addStretch(1)
        self._loading_label = QLabel(_("Opening “{name}”…").format(
            name=os.path.basename(self._file_path)))
        self._loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._loading_label.setWordWrap(True)
        loading.addWidget(self._loading_label)
        self._progress = QProgressBar()
        self._progress.setAccessibleName(_("Opening"))
        self._progress.setRange(0, 100)
        self._progress.setMaximumWidth(260)
        loading.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignHCenter)
        loading.addStretch(2)
        self._stack.addWidget(self._loading)

        self._failure = QLabel()
        self._failure.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._failure.setWordWrap(True)
        self._stack.addWidget(self._failure)

        # The sheet as it was when a large change began, shown while the
        # change runs: nothing reads the workbook meanwhile.
        self._picture = QLabel()
        self._picture.setObjectName("SheetPicture")
        self._picture.setAccessibleName(_("Spreadsheet"))
        self._picture.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._stack.addWidget(self._picture)
        layout.addWidget(self._stack, 1)

        # One tab per sheet, and "+" for a new one.
        self._sheet_strip = QWidget()
        self._sheet_strip.setObjectName("SheetStrip")
        strip = QHBoxLayout(self._sheet_strip)
        strip.setContentsMargins(6, 2, 6, 2)
        strip.setSpacing(4)
        self._add_button = QToolButton()
        self._add_button.setText("+")
        self._add_button.setAccessibleName(_("Add Sheet"))
        self._add_button.setToolTip(_("Add a sheet"))
        self._add_button.clicked.connect(lambda: self.add_sheet())
        strip.addWidget(self._add_button)
        self.sheet_bar = QTabBar()
        self.sheet_bar.setAccessibleName(_("Sheets"))
        self.sheet_bar.setDrawBase(False)
        self.sheet_bar.setExpanding(False)
        self.sheet_bar.setDocumentMode(True)
        self.sheet_bar.setUsesScrollButtons(True)
        self.sheet_bar.currentChanged.connect(self._on_sheet_chosen)
        self.sheet_bar.tabBarDoubleClicked.connect(self.start_rename)
        self.sheet_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sheet_bar.customContextMenuRequested.connect(self._sheet_menu)
        strip.addWidget(self.sheet_bar, 1)
        layout.addWidget(self._sheet_strip)
        self._rename_edit: Optional[QLineEdit] = None

        self.setFocusProxy(self.grid)

    # -- loading -----------------------------------------------------------

    def _load_now(self) -> None:
        try:
            book = sheet_io.load_workbook(self._file_path)
        except sheet_io.SheetFileError as error:
            self._loaded(None, str(error), self._file_path)
            return
        self._loaded(book, "", self._file_path)

    def _load_in_background(self) -> None:
        self._stack.setCurrentWidget(self._loading)
        self._formula_bar.setEnabled(False)
        path = self._file_path
        background.run(lambda progress: _read_workbook(path, progress),
                       lambda read: self._loaded(*read), receiver=self,
                       progress=lambda share: self._progress.setValue(int(share * 100)),
                       discard=_close_unwanted, daemon=True, name="sheet-loader")

    def is_loading(self) -> bool:
        return self.book is None and self.load_ok and not self._released

    def _loaded(self, book, error: str, path: Optional[str] = None) -> None:
        if self._released:
            if book is not None:
                book.close()
            return
        if path is not None and os.path.abspath(path) != self._file_path:
            # The file was renamed or moved while it was read (follow_move).
            if book is None:
                # Gone from where it was read: read it where it is now.
                self._load_in_background()
                return
            book.path = self._file_path
        self._formula_bar.setEnabled(True)
        if book is None:
            self.load_ok = False
            self.load_error = error or _("The spreadsheet could not be read.")
            self._failure.setText(self.load_error)
            self._stack.setCurrentWidget(self._failure)
            self._formula_bar.setEnabled(False)
            self.loaded.emit(False)
            return
        self.load_ok = True
        self.load_error = ""
        self.book = book
        self._stack.setCurrentWidget(self.grid)
        self._fill_sheet_bar(0)
        self._update_info()
        self._check_lock()
        self.loaded.emit(True)

    def _fill_sheet_bar(self, index: int) -> None:
        self.sheet_bar.blockSignals(True)
        while self.sheet_bar.count():
            self.sheet_bar.removeTab(0)
        for sheet in self.book.sheets:
            self.sheet_bar.addTab(sheet.name)
        index = max(0, min(index, len(self.book.sheets) - 1))
        self.sheet_bar.setCurrentIndex(index)
        self.sheet_bar.blockSignals(False)
        self._sheet_strip.setVisible(True)
        self._add_button.setVisible(not self.book.is_text_table)
        self._show_sheet(self.book.sheets[index])
        self._show_facts()

    def _show_facts(self) -> None:
        """The bar's facts: how many sheets, and how large the file is."""
        facts = []
        if self.book is not None and not self.book.is_text_table:
            count = len(self.book.sheets)
            facts.append(ngettext("{count} sheet", "{count} sheets", count).format(count=count))
        try:
            facts.append(media_files.format_size(os.path.getsize(self._file_path)))
        except OSError:
            pass
        self._bar.set_file(self._file_path, facts)

    def _show_sheet(self, sheet: sheet_io.Sheet, current: Tuple[int, int] = (0, 0)) -> None:
        self.sheet = sheet
        self.model.set_sheet(sheet)
        self._apply_layout()
        row, col = current
        self.model.ensure(row, col)
        self.grid.setCurrentIndex(self.model.index(row, col))
        self._sync_formula_bar()

    def _views(self) -> List[QTableView]:
        """The main grid and the frozen panes, which show the same cells."""
        return [self.grid] + list(self._panes.values())

    def _apply_layout(self) -> None:
        sheet = self.sheet
        self._width_guard = True
        try:
            digit = self._digit_width()
            for view in self._views():
                header = view.horizontalHeader()
                header.setDefaultSectionSize(_screen_width(sheet.default_width, digit))
                for col in range(self.model.columnCount()):
                    width = sheet.widths.get(col)
                    header.resizeSection(col, _screen_width(width if width else
                                                            sheet.default_width, digit))
                rows = view.verticalHeader()
                rows.setDefaultSectionSize(int(round(sheet.default_height + 2)))
                for row, height in sheet.heights.items():
                    if row < self.model.rowCount():
                        rows.resizeSection(row, max(int(round(height)), 8))
        finally:
            self._width_guard = False
        for view in self._views():
            view.clearSpans()
            if len(sheet.merges) <= _MAX_SPANS:
                for r1, c1, r2, c2 in sheet.merges:
                    if r2 > r1 or c2 > c1:
                        self.model.ensure(r2, c2)
                        view.setSpan(r1, c1, r2 - r1 + 1, c2 - c1 + 1)
        self._set_frozen(sheet.frozen)

    def _on_rows_inserted(self, _parent, first: int, last: int) -> None:
        if self.sheet is None:
            return
        for row, height in self.sheet.heights.items():
            if first <= row <= last:
                for view in self._views():
                    view.verticalHeader().resizeSection(row, max(int(round(height)), 8))

    def _digit_width(self) -> int:
        """The widest digit of the grid's font, plain or bold: Excel's
        "maximum digit width", the unit its column widths count in. A file
        made in Excel (Calibri 11, 7 pixels a digit) then holds as many
        digits in a column here as there."""
        self.grid.ensurePolished()
        font = QFont(self.grid.font())
        key = font.key()
        if key != self._digit_key:
            widest = 1
            for bold in (False, True):
                font.setBold(bold)
                metrics = QFontMetrics(font)
                widest = max([widest] + [metrics.horizontalAdvance(d) for d in "0123456789"])
            self._digit_key, self._digit = key, widest
        return self._digit

    def _show_width(self, sheet: sheet_io.Sheet, col: int) -> None:
        if sheet is not self.sheet:
            return
        self._width_guard = True
        try:
            for view in self._views():
                view.horizontalHeader().resizeSection(
                    col, _screen_width(sheet.width(col), self._digit_width()))
        finally:
            self._width_guard = False
        self._place_frozen()

    # -- frozen rows and columns -------------------------------------------

    def _frozen_counts(self) -> Tuple[int, int]:
        return self._frozen or (0, 0)

    def _frozen_extent(self, kind: str) -> int:
        """How far from the viewport's top (left) the frozen rows (columns)
        reach."""
        rows, cols = self._frozen_counts()
        if kind == "rows":
            return sum(self.grid.rowHeight(r) for r in range(rows))
        return sum(self.grid.columnWidth(c) for c in range(cols))

    def _make_pane(self, kind: str) -> QTableView:
        """A frozen pane: a table laid over the grid that shows the frozen
        rows ("rows"), columns ("cols") or both ("corner") and does not
        scroll with them."""
        pane = _Grid(self, parent=self.grid, pane=kind)
        pane.setAccessibleName({"rows": _("Frozen Rows"), "cols": _("Frozen Columns"),
                                "corner": _("Frozen Cells")}[kind])
        pane.setModel(self.model)
        pane.setSelectionModel(self.grid.selectionModel())
        pane.setItemDelegate(self.grid.itemDelegate())
        pane.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        pane.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pane.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pane.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        pane.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        pane.setSelectionMode(self.grid.selectionMode())
        pane.setEditTriggers(self.grid.editTriggers())
        pane.setWordWrap(False)
        pane.setFrameShape(QFrame.Shape.NoFrame)
        pane.setTabKeyNavigation(False)
        columns = pane.horizontalHeader()
        columns.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        columns.setMinimumSectionSize(8)
        columns.sectionResized.connect(self._on_pane_column_resized)
        columns.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        columns.customContextMenuRequested.connect(
            lambda pos, header=columns: self._column_menu(pos, header))
        rows = pane.verticalHeader()
        rows.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        rows.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        rows.customContextMenuRequested.connect(
            lambda pos, header=rows: self._row_menu(pos, header))
        pane.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pane.customContextMenuRequested.connect(lambda pos, view=pane: self._cell_menu(pos, view))
        if kind == "rows":
            self.grid.horizontalScrollBar().valueChanged.connect(
                pane.horizontalScrollBar().setValue)
        if kind == "cols":
            self.grid.verticalScrollBar().valueChanged.connect(pane.verticalScrollBar().setValue)
        self._panes[kind] = pane
        pane.hide()
        return pane

    def _set_frozen(self, frozen: Optional[Tuple[int, int]]) -> None:
        rows, cols = frozen or (0, 0)
        rows, cols = max(0, min(rows, 1000)), max(0, min(cols, 200))
        self._frozen = (rows, cols) if rows or cols else None
        wanted = {"rows": rows > 0, "cols": cols > 0, "corner": rows > 0 and cols > 0}
        for kind in ("cols", "rows", "corner"):
            if wanted[kind] and kind not in self._panes:
                pane = self._make_pane(kind)
                self._copy_sizes(pane)
            if kind in self._panes:
                self._panes[kind].setVisible(wanted[kind])
        self._place_frozen()

    def _copy_sizes(self, pane: QTableView) -> None:
        self._width_guard = True
        try:
            source, target = self.grid.horizontalHeader(), pane.horizontalHeader()
            target.setDefaultSectionSize(source.defaultSectionSize())
            for col in range(self.model.columnCount()):
                if target.sectionSize(col) != source.sectionSize(col):
                    target.resizeSection(col, source.sectionSize(col))
            source, target = self.grid.verticalHeader(), pane.verticalHeader()
            target.setDefaultSectionSize(source.defaultSectionSize())
            if self.sheet is not None:
                for row in self.sheet.heights:
                    if row < self.model.rowCount():
                        target.resizeSection(row, source.sectionSize(row))
        finally:
            self._width_guard = False
        if self.sheet is not None and len(self.sheet.merges) <= _MAX_SPANS:
            pane.clearSpans()
            for r1, c1, r2, c2 in self.sheet.merges:
                if r2 > r1 or c2 > c1:
                    pane.setSpan(r1, c1, r2 - r1 + 1, c2 - c1 + 1)

    def _place_frozen(self) -> None:
        """Lay the frozen panes over the grid: over its headers and the
        frozen rows (columns), up to the scroll bars."""
        if not getattr(self, "_panes", None):
            return
        if not self._frozen:
            for pane in self._panes.values():
                pane.hide()
            return
        grid = self.grid
        frame = grid.frameWidth()
        viewport = grid.viewport().geometry()
        header_width = grid.verticalHeader().width()
        header_height = grid.horizontalHeader().height()
        rows_height = self._frozen_extent("rows")
        cols_width = self._frozen_extent("cols")
        right = viewport.x() + viewport.width() - frame
        bottom = viewport.y() + viewport.height() - frame
        shapes = {
            "rows": QRect(frame, frame, right, header_height + rows_height),
            "cols": QRect(frame, frame, header_width + cols_width, bottom),
            "corner": QRect(frame, frame, header_width + cols_width,
                            header_height + rows_height),
        }
        for kind in ("cols", "rows", "corner"):
            pane = self._panes.get(kind)
            if pane is None or pane.isHidden():
                continue
            pane.verticalHeader().setFixedWidth(header_width)
            pane.horizontalHeader().setFixedHeight(header_height)
            pane.setGeometry(shapes[kind])
            pane.raise_()
            if kind == "rows":
                pane.horizontalScrollBar().setValue(grid.horizontalScrollBar().value())
            if kind == "cols":
                pane.verticalScrollBar().setValue(grid.verticalScrollBar().value())

    def _on_pane_column_resized(self, col: int, old: int, new: int) -> None:
        """A column divider dragged on a frozen pane's header (which covers
        the grid's): the grid's column follows, and with it the file."""
        if self._width_guard:
            return
        if self.grid.columnWidth(col) != new:
            self.grid.horizontalHeader().resizeSection(col, new)

    def _paint_borders(self, view: QTableView, area: QRect) -> None:
        """The cells' borders as the file draws them, over the grid lines."""
        if self.book is None or self.sheet is None:
            return
        rows = self.model.rowCount()
        cols = self.model.columnCount()
        first_row = view.rowAt(area.top())
        last_row = view.rowAt(area.bottom())
        first_col = view.columnAt(area.left())
        last_col = view.columnAt(area.right())
        if first_row < 0 or first_col < 0:
            return
        last_row = rows - 1 if last_row < 0 else last_row
        last_col = cols - 1 if last_col < 0 else last_col
        painter = None
        try:
            for row in range(first_row, min(last_row + 1, first_row + 400)):
                for col in range(first_col, min(last_col + 1, first_col + 200)):
                    look = self.look_at(row, col)
                    if not (look.left or look.right or look.top or look.bottom):
                        continue
                    if painter is None:
                        painter = QPainter(view.viewport())
                    rect = view.visualRect(self.model.index(row, col))
                    left, top = rect.left() - 1, rect.top() - 1
                    right, bottom = rect.right(), rect.bottom()
                    for border, line in ((look.left, (left, top, left, bottom)),
                                         (look.right, (right, top, right, bottom)),
                                         (look.top, (left, top, right, top)),
                                         (look.bottom, (left, bottom, right, bottom))):
                        if border is None:
                            continue
                        pen = QPen(self.border_color(border, look), border.width)
                        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
                        if border.dashed:
                            pen.setStyle(Qt.PenStyle.DashLine)
                        painter.setPen(pen)
                        painter.drawLine(*line)
        finally:
            if painter is not None:
                painter.end()

    # -- what cells show ---------------------------------------------------

    def display_text(self, cell: Optional[sheet_io.Cell]) -> str:
        if cell is None:
            return ""
        if cell.shown is not None and cell.source is not sheet_io.UNSET:
            return cell.shown
        if cell.state == sf.STATE_STALE:
            # Worked out by the spreadsheet app once it opens the file.
            return "\u2026"
        if self.book is not None and self.book.is_text_table:
            return "" if cell.value is None else str(cell.value)
        return sheet_io.format_value(cell.value, cell.number_format, self._style,
                                     self.book.date1904 if self.book else False)

    def edit_text_of(self, cell: Optional[sheet_io.Cell]) -> str:
        return edit_text(cell, text_table=bool(self.book and self.book.is_text_table),
                         style=self._style, locale=self._locale,
                         date1904=self.book.date1904 if self.book else False,
                         language=self._language)

    def alignment_of(self, cell: sheet_io.Cell, look: sheet_io.Look = sheet_io.PLAIN):
        value = cell.value
        vertical = Qt.AlignmentFlag.AlignVCenter
        if look.align:
            horizontal = {"left": Qt.AlignmentFlag.AlignLeft,
                          "center": Qt.AlignmentFlag.AlignHCenter,
                          "right": Qt.AlignmentFlag.AlignRight}[look.align]
            return int(horizontal | vertical)
        if isinstance(value, (bool, sf.CellError)):
            return int(Qt.AlignmentFlag.AlignHCenter | vertical)
        numeric = isinstance(value, (int, float, _dt.date, _dt.time, _dt.timedelta))
        if isinstance(value, str) and self.book is not None and self.book.is_text_table:
            numeric = _looks_numeric(value, self._style)
        if numeric:
            return int(Qt.AlignmentFlag.AlignRight | vertical)
        return int(Qt.AlignmentFlag.AlignLeft | vertical)

    def look_at(self, row: int, col: int) -> sheet_io.Look:
        if self.book is None or self.sheet is None:
            return sheet_io.PLAIN
        return self.book.look_at(self.sheet, row, col)

    def brush(self, color: str) -> QColor:
        found = self._colors.get(color)
        if found is None:
            found = self._colors[color] = QColor(color)
        return found

    def text_color(self, look: sheet_io.Look) -> Optional[QColor]:
        """The file's font colour where it can be read on the cell; on a
        filled cell with automatic text, black or white for the fill."""
        background = self.brush(look.fill) if look.fill else self._grid_background
        if look.color:
            color = self.brush(look.color)
            if _contrast(color, background) >= 2.0:
                return color
        if look.fill:
            return self.brush("#000000") if _luminance(background) > 0.4 else \
                self.brush("#FFFFFF")
        return None

    def border_color(self, border: sheet_io.Border, look: sheet_io.Look) -> QColor:
        color = self.brush(border.color or "#000000")
        background = self.brush(look.fill) if look.fill else self._grid_background
        if _contrast(color, background) < 1.6:
            # Black lines on the dark theme's grid: drawn in its text grey.
            return self.brush("#B0B0B0" if self.is_dark else "#404040")
        return color

    # -- the info line -----------------------------------------------------

    def _update_info(self) -> None:
        if self.book is None:
            return
        if self._job is not None:
            self._info_label.setText(self._job.label)
            self._notice.hide()
            return
        label = file_kinds.format_label(self._file_path)
        if self.read_only:
            self._info_label.setText(label)
        elif self.book.is_text_table:
            self._info_label.setText(_("{format} · Edits are saved into this file as text.")
                                     .format(format=label))
        else:
            self._info_label.setText(_("{format} · Edits are saved into this file.")
                                     .format(format=label))
        notices = []
        if self._lock_path:
            notices.append(lock_notice_text())
        if self.book.unsaveable:
            notices.append(_("This file contains parts MyEditor cannot save ({parts}). "
                             "Edits are saved as a copy.").format(
                parts=", ".join(self.book.unsaveable)))
        self._notice.setText("\n".join(notices))
        self._notice.setVisible(bool(notices))

    def _check_lock(self) -> None:
        """Read-only while another app has the file open."""
        if self.book is None:
            return
        lock = self.book.lock_owner()
        read_only = lock is not None or self._view_only or self.is_busy()
        if lock == self._lock_path and read_only == self.read_only:
            return
        self._lock_path = lock
        if lock and self._editor is not None:
            self.grid.closePersistentEditor(self.grid.currentIndex())
        self.read_only = read_only
        self.formula_field.setReadOnly(self.read_only)
        self._add_button.setEnabled(not self.read_only)
        self._update_info()

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.WindowActivate, QEvent.Type.Show):
            self._check_lock()
        return super().event(event)

    # -- large changes on a worker thread ----------------------------------

    def is_busy(self) -> bool:
        """Whether a large change runs on a worker thread, or has just
        ended and the call that began it has yet to return. The tab is
        read-only then, Undo waits, and the tab is not closed."""
        return self._job is not None or self._job_frames > 0

    def after_job(self, then: Callable[[], None]) -> None:
        """Do ``then`` once no large change runs (at once when none does):
        the window closes a tab so. It runs from the event loop, never
        inside the change or a dialog."""
        if not self.is_busy():
            then()
            return
        self._after_job.append(then)

    def _book_size(self) -> int:
        return sum(len(sheet.cells) for sheet in self.book.sheets) if self.book else 0

    def _run_job(self, label: str, work, *, size: int, done=None, wait_cursor: bool = False):
        """Run ``work(progress)`` and return what it returns (or raise what
        it raises); ``done(result)`` follows on this thread. Work on up to
        BACKGROUND_CELLS cells runs here and now (``wait_cursor``: with the
        wait cursor). Larger work runs on a worker thread while this waits
        in an event loop of its own, as a dialog does, so the window goes
        on responding: the tab shows a picture of the sheet and what it is
        doing, takes no edits and no Undo, and nothing but the worker
        touches the workbook. One job runs at a time: the next waits.

        Why a loop of its own, and not a continuation once the worker is
        done: the callers must have finished when they return. Qt's undo
        stack counts a step as done when its redo() or undo() returns
        (_StructureCommand, _CellsCommand), and the window counts a sheet
        as saved when save() returns (closing, quitting, Save All). Run
        later, the workbook would lag behind the stack's place and its
        clean state.

        Why it is safe: everything the person or the window can reach
        while the loop runs keeps away from the workbook until the job is
        over and the call that began it has returned (is_busy, which
        _job_frames holds true until then). The tab is read-only and shows
        a picture of the sheet instead of the grid; the sheet bar, name
        box, formula field and buttons are off; Undo and Redo refuse
        (_UndoStack); a reload, a move, closing the tab or releasing it
        wait for it (after_job, run from the event loop afterwards); and
        another job (a Save from the window's menu) waits in
        _wait_for_job before it starts. Other tabs and windows go on as
        usual: they never touch this workbook."""
        self._wait_for_job()
        if size <= BACKGROUND_CELLS:
            if wait_cursor:
                with _busy():
                    result = work(lambda _share: None)
            else:
                result = work(lambda _share: None)
            if done is not None:
                done(result)
            return result
        job = _Job(self, label, done)
        self._enter_job(job)
        self._job_frames += 1
        try:
            job.start(work)
            if not job.done:
                loop = QEventLoop()
                job.loops.append(loop)
                loop.exec()
        finally:
            self._job_frames -= 1
            if shiboken6.isValid(self) and not self.is_busy():
                self._now_idle()
        if job.error is not None:
            raise job.error
        return job.result

    def _wait_for_job(self) -> None:
        while self._job is not None:
            loop = QEventLoop()
            self._job.loops.append(loop)
            loop.exec()

    def _enter_job(self, job: _Job) -> None:
        was_idle = not self.is_busy()
        if was_idle:
            self._job_had_focus = self.grid.hasFocus()
        if self._stack.currentWidget() is self.grid:
            self._picture.setPixmap(self.grid.grab())
            self._stack.setCurrentWidget(self._picture)
        self._job = job
        for widget in (self.sheet_bar, self.name_box, self.formula_field, self._add_button,
                       self._markdown_button):
            widget.setEnabled(False)
        self._job_progress.setRange(0, 0)
        self._job_progress.setAccessibleName(job.label)
        self._job_progress.show()
        self._check_lock()
        self._update_info()
        if was_idle:
            self.busy_changed.emit(True)
            self._announce_history()

    def _show_job_progress(self, share: float) -> None:
        self._job_progress.setRange(0, 1000)
        self._job_progress.setValue(int(max(0.0, min(share, 1.0)) * 1000))

    def _leave_job(self, job: _Job) -> None:
        """The worker is done: the sheet is shown again and what the job
        was for follows (``done``). Edits wait until the call that began
        the job has returned (_now_idle)."""
        if self._job is not job:
            return
        self._job = None
        self._job_progress.hide()
        if self._stack.currentWidget() is self._picture:
            self._stack.setCurrentWidget(self.grid)
            self._picture.clear()
        self._update_info()
        if job.error is None and job.on_done is not None:
            job.on_done(job.result)

    def _now_idle(self) -> None:
        for widget in (self.sheet_bar, self.name_box, self.formula_field, self._markdown_button):
            widget.setEnabled(True)
        self._check_lock()
        self._add_button.setEnabled(not self.read_only)
        self._update_info()
        if self._job_had_focus and self._stack.currentWidget() is self.grid:
            self.grid.setFocus()
        self.busy_changed.emit(False)
        self._announce_history()
        if self._after_job:
            QTimer.singleShot(0, self, self._run_after_job)

    def _run_after_job(self) -> None:
        """What waited for the job (a reload, a move, closing the tab),
        from the event loop once nothing runs and no dialog is open."""
        if not self._after_job:
            return
        if self.is_busy() or QApplication.activeModalWidget() is not None:
            QTimer.singleShot(100, self, self._run_after_job)
            return
        waiting, self._after_job = self._after_job, []
        for then in waiting:
            then()

    def _announce_history(self) -> None:
        """Undo and Redo were offered or held back: the window asks again."""
        stack = self.undo_stack
        stack.canUndoChanged.emit(stack.canUndo())
        stack.canRedoChanged.emit(stack.canRedo())

    # -- the top bar -------------------------------------------------------

    def set_default_app(self, name: Optional[str]) -> None:
        """Name the app this computer opens the file with ("Open in
        Numbers"), or offer "Open With…" when there is none."""
        self._default_app = name
        self._bar.set_default_app(name)

    def set_reveal_label(self, text: str) -> None:
        """The words of the reveal button ("Show in Finder", "Show in
        Explorer"), from default_apps.reveal_label."""
        self._bar.set_reveal_label(text)

    # -- the formula bar ---------------------------------------------------

    def current_cell(self) -> Tuple[int, int]:
        index = self.grid.currentIndex()
        if not index.isValid():
            return 0, 0
        return index.row(), index.column()

    def _on_current_changed(self, current, _previous) -> None:
        if current.isValid():
            self.model.ensure(current.row() + 20, current.column() + 3)
        self._sync_formula_bar()

    def _on_selection_changed(self, *_args) -> None:
        self._sync_name_box()

    def _sync_name_box(self) -> None:
        block = self.selected_block()
        if block is None:
            return
        r1, c1, r2, c2 = block
        if (r1, c1) == (r2, c2) or self._is_one_merge(block):
            self.name_box.setText(sf.cell_name(*self.current_cell()))
        else:
            self.name_box.setText(f"{sf.cell_name(r1, c1)}:{sf.cell_name(r2, c2)}")

    def _is_one_merge(self, block) -> bool:
        return self.sheet is not None and tuple(block) in {tuple(m) for m in self.sheet.merges}

    def _sync_formula_bar(self) -> None:
        self._sync_name_box()
        if self.formula_field.hasFocus() and self.formula_field.isModified():
            return
        row, col = self.current_cell()
        self.formula_field.setText(self.edit_text_of(self.model.cell(row, col)))
        self.formula_field.setModified(False)

    def _mirror_editor(self, text: str) -> None:
        self.formula_field.setText(text)

    def _on_name_entered(self) -> None:
        text = self.name_box.text().strip()
        first, _colon, second = text.partition(":")
        start = sf.parse_cell_name(first)
        end = sf.parse_cell_name(second) if second else start
        if start is None or end is None:
            QApplication.beep()
            self._sync_name_box()
            return
        self.go_to(*start, end=end)
        self.grid.setFocus()

    def go_to(self, row: int, col: int, *, end: Optional[Tuple[int, int]] = None) -> None:
        """Make (row, col) the current cell (selecting up to ``end``)."""
        end = end or (row, col)
        self.model.ensure(max(row, end[0]) + 20, max(col, end[1]) + 3)
        index = self.model.index(row, col)
        self.grid.setCurrentIndex(index)
        if end != (row, col):
            selection = QItemSelection(self.model.index(min(row, end[0]), min(col, end[1])),
                                       self.model.index(max(row, end[0]), max(col, end[1])))
            self.grid.selectionModel().select(
                selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.grid.scrollTo(index)

    def _on_formula_entered(self) -> None:
        if self.read_only:
            return
        row, col = self.current_cell()
        if self.commit_text(row, col, self.formula_field.text()):
            self.formula_field.setModified(False)
            self._move(1, 0)
        self.grid.setFocus()

    # -- keys --------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress:
            if obj is self.formula_field and event.key() == Qt.Key.Key_Escape:
                self.formula_field.setModified(False)
                self._sync_formula_bar()
                self.grid.setFocus()
                return True
            if obj is self.name_box and event.key() == Qt.Key.Key_Escape:
                self._sync_name_box()
                self.grid.setFocus()
                return True
            if obj is self._rename_edit and event.key() == Qt.Key.Key_Escape:
                self._end_rename(commit=False)
                return True
        if event.type() == QEvent.Type.FocusIn and obj is self.grid:
            self._check_lock()
        return super().eventFilter(obj, event)

    def _editor_key(self, event) -> bool:
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & (Qt.KeyboardModifier.AltModifier |
                                    Qt.KeyboardModifier.ControlModifier):
                # Option-Return (Alt+Enter in Excel) is a line break in the cell.
                self._editor.insert("\n")
                return True
            self._finish_edit(move=(-1, 0) if shift else (1, 0))
            return True
        if key == Qt.Key.Key_Tab:
            self._finish_edit(move=(0, 1))
            return True
        if key == Qt.Key.Key_Backtab:
            self._finish_edit(move=(0, -1))
            return True
        if key == Qt.Key.Key_Escape:
            self._cancel_edit()
            return True
        arrows = {Qt.Key.Key_Up: (-1, 0), Qt.Key.Key_Down: (1, 0),
                  Qt.Key.Key_Left: (0, -1), Qt.Key.Key_Right: (0, 1)}
        if key in arrows and not shift:
            if self._edit_mode == "enter":
                # Typing replaced the cell: arrows confirm and move on, as
                # in Excel's Enter mode.
                self._finish_edit(move=arrows[key])
                return True
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                return True
        return False

    def _finish_edit(self, move: Tuple[int, int]) -> None:
        editor = self._editor
        if editor is None:
            return
        index = self.grid.currentIndex()
        self._commit_editor(editor, index, move=move)

    def _commit_editor(self, editor, index, move) -> None:
        if self._committing:
            return
        self._committing = True
        try:
            text = editor.text()
            (self._editor_view or self.grid).closeEditor(
                editor, QStyledItemDelegate.EndEditHint.NoHint)
            self._editor = None
            if index.isValid():
                self.commit_text(index.row(), index.column(), text)
        finally:
            self._committing = False
        self.grid.setFocus()
        if move:
            self._move(*move)
        self._sync_formula_bar()

    def _cancel_edit(self) -> None:
        editor = self._editor
        if editor is None:
            return
        self._committing = True
        try:
            (self._editor_view or self.grid).closeEditor(
                editor, QStyledItemDelegate.EndEditHint.RevertModelCache)
        finally:
            self._committing = False
        self._editor = None
        self.grid.setFocus()
        self._sync_formula_bar()

    def _move(self, rows: int, cols: int) -> None:
        row, col = self.current_cell()
        # A merged cell is left from its far side.
        span_rows = self.grid.rowSpan(row, col)
        span_cols = self.grid.columnSpan(row, col)
        if rows > 0:
            rows += span_rows - 1
        if cols > 0:
            cols += span_cols - 1
        row = max(0, min(row + rows, sf.MAX_ROWS - 1))
        col = max(0, min(col + cols, sf.MAX_COLS - 1))
        self.go_to(row, col)

    def start_editing(self, text: Optional[str] = None) -> bool:
        """Edit the current cell in place: with ``text`` replacing what it
        holds (as typing does), or with its content (F2)."""
        if self.read_only or self.book is None:
            return False
        self._pending_start = text
        self._edit_mode = "enter" if text is not None else "edit"
        index = self.grid.currentIndex()
        if not index.isValid():
            return False
        started = self._view_for(index.row(), index.column()).edit(
            index, QAbstractItemView.EditTrigger.EditKeyPressed, None)
        if not started:
            self._pending_start = None
        return started

    def _view_for(self, row: int, col: int) -> QTableView:
        """The view a cell is seen in: a frozen pane for frozen cells."""
        rows, cols = self._frozen_counts()
        kind = "corner" if row < rows and col < cols else "rows" if row < rows else \
            "cols" if col < cols else None
        pane = self._panes.get(kind) if kind else None
        return pane if pane is not None and not pane.isHidden() else self.grid

    def _grid_key(self, event) -> bool:
        """The grid's keys when no cell is being edited; True when handled."""
        key = event.key()
        mods = event.modifiers()
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        command = bool(mods & (Qt.KeyboardModifier.ControlModifier |
                               Qt.KeyboardModifier.MetaModifier))
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
            return True
        if event.matches(QKeySequence.StandardKey.Cut):
            self.cut()
            return True
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste()
            return True
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo_stack.undo()
            return True
        if event.matches(QKeySequence.StandardKey.Redo):
            self.undo_stack.redo()
            return True
        if event.matches(QKeySequence.StandardKey.SelectAll):
            self.grid.selectAll()
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._move(-1 if shift else 1, 0)
            return True
        if key == Qt.Key.Key_Tab:
            self._move(0, 1)
            return True
        if key == Qt.Key.Key_Backtab:
            self._move(0, -1)
            return True
        if key == Qt.Key.Key_F2:
            self.start_editing()
            return True
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not command:
            self.clear_selection()
            return True
        text = event.text()
        if text and text.isprintable() and not command and \
                not (mods & Qt.KeyboardModifier.AltModifier and sys.platform != "darwin"):
            if self.start_editing(text):
                return True
        return False

    def _on_horizontal_scroll(self, value: int) -> None:
        bar = self.grid.horizontalScrollBar()
        if value >= bar.maximum() - 40 and self.model.columnCount() < sf.MAX_COLS:
            self.model.ensure(0, self.model.columnCount() + _GROW_COLS - 1)

    def _apply_new_column_widths(self) -> None:
        if self.sheet is None:
            return
        self._width_guard = True
        try:
            digit = self._digit_width()
            for view in self._views():
                header = view.horizontalHeader()
                for col, width in self.sheet.widths.items():
                    shown = _screen_width(width, digit)
                    if col < self.model.columnCount() and header.sectionSize(col) != shown:
                        header.resizeSection(col, shown)
        finally:
            self._width_guard = False

    # -- editing -----------------------------------------------------------

    def _input_cell(self, row: int, col: int, text: str, *,
                    canonical: bool = False) -> Optional[sheet_io.Cell]:
        """The cell ``text`` typed into (row, col) makes; None to empty it.
        A ``canonical`` formula is in the files' spelling already (cells
        copied here), not the app's language."""
        sheet, book = self.sheet, self.book
        base = book.blank_cell(sheet, row, col)
        if book.is_text_table:
            if text == "":
                return None if base.extra is None else base.edited(value=None)
            return base.edited(value=text, formula=None)
        # Control characters (a vertical tab pasted from a slide) cannot be
        # kept in a workbook: dropped as Excel drops them on entry.
        text = _CONTROL_CHARACTERS.sub("", text)
        parsed = parse_input(text, style=self._style, current_format=base.number_format,
                             locale=self._locale,
                             language="en" if canonical else self._language)
        if parsed.value is None and parsed.formula is None:
            # Emptied: the look of the cell stays, as Delete in Excel keeps it.
            if base.style is None and base.extra is None and base.number_format == "General":
                return None
            return base.edited(value=None, formula=None, state="")
        cell = base.edited(value=parsed.value, formula=parsed.formula, state="")
        if parsed.number_format and parsed.number_format != base.number_format:
            cell = book.cell_with_format(sheet, cell, parsed.number_format)
        return cell

    def commit_text(self, row: int, col: int, text: str) -> bool:
        """Put what was typed into (row, col), as one undoable step."""
        if self.read_only or self.book is None:
            return False
        old = self.sheet.cells.get((row, col))
        if text == self.edit_text_of(old) and old is not None:
            return True
        if old is None and text == "":
            return True
        problem = self._input_problem(text)
        if problem:
            from alerts import inform
            inform(self, title=problem, is_dark=self.is_dark)
            return False
        new = self._input_cell(row, col, text)
        self.undo_stack.push(_CellsCommand(self, self.sheet, {(row, col): (old, new)},
                                           _("Typing")))
        return True

    def _input_problem(self, text: str, *, canonical: bool = False) -> Optional[str]:
        """Why ``text`` cannot go into a cell of this file, or None."""
        if len(text) > MAX_CELL_TEXT:
            return _("A cell can hold at most {count} characters.").format(
                count=MAX_CELL_TEXT)
        if self.book is not None and not self.book.is_text_table and text.startswith("="):
            formula = text if canonical else sf.delocalize_formula(text, self._language)
            spilling = sf.uses_spilling_function(formula)
            if spilling:
                return _("MyEditor cannot write {function} into the file: its result fills "
                         "several cells, which only Excel and LibreOffice record. Enter it "
                         "there.").format(function=spilling)
        return None

    def _apply_cells(self, sheet: sheet_io.Sheet, cells: Dict) -> None:
        if self.book is None:
            return
        book = self.book

        def work(_progress):
            changed = book.set_cells(sheet, cells)
            sheet.used_size()
            return changed

        self._run_job(_("Updating the sheet\u2026"), work, size=len(cells),
                      done=lambda changed: self._show_changed_cells(sheet, changed))

    def _show_changed_cells(self, sheet: sheet_io.Sheet, changed) -> None:
        if sheet is not self.sheet:
            self._select_sheet(sheet)
        self.model.cells_changed(changed)
        self.model.refit()
        self._sync_formula_bar()

    def _change_cells(self, sheet: sheet_io.Sheet, text: str, compute, *, size: int) -> bool:
        """One undoable step changing the cells ``compute`` works out (as
        {(row, col): (old, new)}, or a sentence saying why nothing can
        change). Many cells are worked out and changed on a worker thread.
        False when nothing changed."""
        changed = None
        if size <= BACKGROUND_CELLS:
            changes = compute()
        else:
            book = self.book

            def work(_progress):
                changes = compute()
                if not changes or isinstance(changes, str):
                    return changes, None
                changed = book.set_cells(sheet, {pos: new for pos, (_old, new) in changes.items()})
                sheet.used_size()
                return changes, changed

            changes, changed = self._run_job(_("Updating the sheet\u2026"), work, size=size)
        if isinstance(changes, str):
            from alerts import inform
            inform(self, title=changes, is_dark=self.is_dark)
            return False
        if not changes:
            return False
        if changed is not None:
            self._show_changed_cells(sheet, changed)
        self.undo_stack.push(_CellsCommand(self, sheet, changes, text,
                                           applied=changed is not None))
        return True

    def _select_sheet(self, sheet: sheet_io.Sheet) -> None:
        index = self.book.sheets.index(sheet)
        self.sheet_bar.blockSignals(True)
        self.sheet_bar.setCurrentIndex(index)
        self.sheet_bar.blockSignals(False)
        self._show_sheet(sheet, self.current_cell())

    def selected_block(self) -> Optional[Tuple[int, int, int, int]]:
        """The selected rectangle (r1, c1, r2, c2), or the current cell."""
        ranges = self.grid.selectionModel().selection()
        if ranges.isEmpty():
            index = self.grid.currentIndex()
            if not index.isValid():
                return None
            return index.row(), index.column(), index.row(), index.column()
        top = min(r.top() for r in ranges)
        left = min(r.left() for r in ranges)
        bottom = max(r.bottom() for r in ranges)
        right = max(r.right() for r in ranges)
        return top, left, bottom, right

    def _block_used(self, block) -> Tuple[int, int, int, int]:
        """A selection cut down to the cells that hold anything (Select
        All selects a million rows; copying them all helps nobody)."""
        r1, c1, r2, c2 = block
        rows, cols = self.sheet.used_size()
        return r1, c1, max(r1, min(r2, rows - 1)), max(c1, min(c2, cols - 1))

    def clear_selection(self) -> None:
        """Empty the selected cells (their look stays)."""
        if self.read_only or self.book is None:
            return
        block = self.selected_block()
        if block is None:
            return
        r1, c1, r2, c2 = self._block_used(block)
        sheet = self.sheet

        def compute():
            changes = {}
            for (r, c), cell in sheet.cells.items():
                if r1 <= r <= r2 and c1 <= c <= c2 and not cell.is_empty:
                    changes[(r, c)] = (cell, self._input_cell(r, c, ""))
            return changes

        self._change_cells(sheet, _("Clear"), compute,
                           size=min((r2 - r1 + 1) * (c2 - c1 + 1), len(sheet.cells)))

    def copy(self) -> None:
        if self.is_busy():
            QApplication.beep()
            return
        block = self.selected_block()
        if block is None or self.sheet is None:
            return
        r1, c1, r2, c2 = self._block_used(block)
        cells, path = self.sheet.cells, self._file_path

        def work(progress):
            texts, raw = [], []
            for r in range(r1, r2 + 1):
                texts.append([self.display_text(cells.get((r, c))) for c in range(c1, c2 + 1)])
                raw.append([self._raw_text(cells.get((r, c))) for c in range(c1, c2 + 1)])
                if (r - r1) % 5000 == 4999:
                    progress((r - r1) / (r2 - r1 + 1))
            payload = json.dumps({"origin": [r1, c1], "cells": raw, "file": path})
            return to_tsv(texts), payload.encode("utf-8")

        text, payload = self._run_job(_("Copying\u2026"), work,
                                      size=(r2 - r1 + 1) * (c2 - c1 + 1))
        from PySide6.QtCore import QMimeData
        data = QMimeData()
        data.setText(text)
        data.setData(_CELLS_MIME, payload)
        QGuiApplication.clipboard().setMimeData(data)

    def _raw_text(self, cell: Optional[sheet_io.Cell]) -> str:
        """A cell for the clipboard between MyEditor's tabs: a formula as
        files spell it, so a paste in any language reads it the same."""
        if cell is not None and cell.formula is not None:
            return cell.formula
        return self.edit_text_of(cell)

    def cut(self) -> None:
        """Copy, then empty the cells, as Numbers does."""
        if self.read_only:
            return
        self.copy()
        self.clear_selection()

    def paste(self) -> None:
        """Paste tab-separated text (or cells copied here, with their
        formulas moved along) into the selection. A selection that holds
        the copied block a whole number of times is filled with it."""
        if self.read_only or self.book is None:
            return
        mime = QGuiApplication.clipboard().mimeData()
        if mime is None:
            return
        origin = None
        if mime.hasFormat(_CELLS_MIME):
            try:
                payload = json.loads(bytes(mime.data(_CELLS_MIME)).decode("utf-8"))
                rows = payload["cells"]
                origin = tuple(payload["origin"])
            except (ValueError, KeyError, TypeError):
                rows = from_tsv(mime.text())
        elif mime.hasText():
            rows = from_tsv(mime.text())
        else:
            return
        height = len(rows)
        width = max((len(r) for r in rows), default=1)
        block = self.selected_block()
        if block is None:
            return
        r1, c1, r2, c2 = block
        times_down = (r2 - r1 + 1) // height if (r2 - r1 + 1) % height == 0 else 1
        times_right = (c2 - c1 + 1) // width if (c2 - c1 + 1) % width == 0 else 1
        sheet, text_table = self.sheet, self.book.is_text_table

        def compute():
            changes = {}
            for i in range(height * times_down):
                for j in range(width * times_right):
                    row_texts = rows[i % height]
                    text = row_texts[j % width] if j % width < len(row_texts) else ""
                    r, c = r1 + i, c1 + j
                    if r >= sf.MAX_ROWS or c >= sf.MAX_COLS:
                        continue
                    if origin is not None and text.startswith("=") and not text_table:
                        text = sf.shift_formula(text, r - (origin[0] + i % height),
                                                c - (origin[1] + j % width))
                    old = sheet.cells.get((r, c))
                    if old is None and text == "":
                        continue
                    problem = self._input_problem(text, canonical=origin is not None)
                    if problem:
                        # Nothing is pasted rather than part of it.
                        return problem
                    changes[(r, c)] = (old, self._input_cell(r, c, text,
                                                             canonical=origin is not None))
            return changes

        if not self._change_cells(sheet, _("Paste"), compute,
                                  size=height * times_down * width * times_right):
            return
        self.go_to(r1, c1, end=(r1 + height * times_down - 1, c1 + width * times_right - 1))

    def copy_as_markdown(self) -> None:
        """The sheet's used cells as a Markdown table on the clipboard, to
        set into a note."""
        if self.sheet is None:
            return
        rows, cols = self.sheet.used_size()
        if not rows:
            return
        lines = []
        for r in range(min(rows, 1000)):
            fields = [self.display_text(self.sheet.cells.get((r, c))).replace("|", "\\|")
                      .replace("\n", " ") for c in range(cols)]
            lines.append("| " + " | ".join(fields) + " |")
            if r == 0:
                aligns = []
                for c in range(cols):
                    below = self.sheet.cells.get((1, c))
                    numeric = below is not None and isinstance(below.value, (int, float)) and \
                        not isinstance(below.value, bool)
                    aligns.append(" ---: " if numeric else " --- ")
                lines.append("|" + "|".join(aligns) + "|")
        QGuiApplication.clipboard().setText("\n".join(lines) + "\n")

    # -- rows, columns, widths ---------------------------------------------

    def _structure(self, text: str, action, select: Optional[int] = None,
                   undo_select: Optional[int] = None) -> bool:
        if self.read_only or self.book is None:
            return False
        if self._editor is not None:
            self._finish_edit(move=(0, 0))
        command = _StructureCommand(self, text, action, select, undo_select)
        self.undo_stack.push(command)
        if command.error:
            from alerts import inform
            inform(self, title=command.error, is_dark=self.is_dark)
            return False
        return True

    def insert_rows(self, at: int, count: int = 1) -> bool:
        sheet = self.sheet
        return self._structure(ngettext("Insert Row", "Insert Rows", count),
                               lambda: self.book.insert(sheet, "row", at, count))

    def delete_rows(self, at: int, count: int = 1) -> bool:
        sheet = self.sheet
        return self._structure(ngettext("Delete Row", "Delete Rows", count),
                               lambda: self.book.delete(sheet, "row", at, count))

    def insert_columns(self, at: int, count: int = 1) -> bool:
        sheet = self.sheet
        return self._structure(ngettext("Insert Column", "Insert Columns", count),
                               lambda: self.book.insert(sheet, "col", at, count))

    def delete_columns(self, at: int, count: int = 1) -> bool:
        sheet = self.sheet
        return self._structure(ngettext("Delete Column", "Delete Columns", count),
                               lambda: self.book.delete(sheet, "col", at, count))

    def _on_section_resized(self, col: int, old: int, new: int) -> None:
        if not self._width_guard:
            self._width_guard = True
            try:
                for pane in self._panes.values():
                    if pane.columnWidth(col) != new:
                        pane.horizontalHeader().resizeSection(col, new)
            finally:
                self._width_guard = False
            self._place_frozen()
        if self._width_guard or self.sheet is None or self.read_only:
            return
        width = _file_width(new, self._digit_width())
        if self.book is not None and self.book.is_text_table:
            # A text table has no widths to save; the column is just wider
            # on screen.
            self.sheet.widths[col] = width
            return
        previous = self.sheet.widths.get(col)
        self.undo_stack.push(_WidthCommand(self, self.sheet, col, previous, width))

    def _structure_changed(self, select: Optional[int]) -> None:
        if self._editor is not None:
            self._cancel_edit()
        current = self.current_cell()
        if self.book is None:
            return
        sheets = self.book.sheets
        if select is None:
            index = sheets.index(self.sheet) if self.sheet in sheets else \
                min(self.sheet_bar.currentIndex(), len(sheets) - 1)
        else:
            index = max(0, min(select, len(sheets) - 1))
        self._fill_sheet_bar(index)
        if self.book.sheets[index] is self.sheet:
            self.model.ensure(*current)
            self.grid.setCurrentIndex(self.model.index(*current))
        self._sync_formula_bar()

    # -- sheets ------------------------------------------------------------

    def current_sheet_index(self) -> int:
        return self.sheet_bar.currentIndex()

    def add_sheet(self, name: Optional[str] = None) -> bool:
        """Add a sheet after the last one and show it."""
        if self.book is None or self.book.is_text_table:
            return False
        before = self.current_sheet_index()
        index = len(self.book.sheets)
        return self._structure(_("Add Sheet"), lambda: self.book.add_sheet(name, index),
                               select=index, undo_select=before)

    def duplicate_sheet(self, index: Optional[int] = None) -> bool:
        if self.book is None or self.book.is_text_table:
            return False
        index = self.current_sheet_index() if index is None else index
        sheet = self.book.sheets[index]
        return self._structure(_("Duplicate Sheet"), lambda: self.book.duplicate_sheet(sheet),
                               select=index + 1, undo_select=index)

    def rename_sheet(self, index: Optional[int] = None, name: Optional[str] = None) -> bool:
        """Rename a sheet; without ``name``, edit its tab's name in place."""
        if self.book is None or self.book.is_text_table:
            return False
        index = self.current_sheet_index() if index is None else index
        if name is None:
            self.start_rename(index)
            return True
        sheet = self.book.sheets[index]
        if name == sheet.name:
            return True
        problem = self.book.sheet_name_problem(name, sheet)
        if problem:
            from alerts import inform
            inform(self, title=problem)
            return False
        return self._structure(_("Rename Sheet"), lambda: self.book.rename_sheet(sheet, name),
                               select=index, undo_select=index)

    def delete_sheet(self, index: Optional[int] = None, *, confirm: bool = True) -> bool:
        """Delete a sheet after asking (Undo brings it back)."""
        if self.book is None or self.book.is_text_table or \
                not self._can_delete_sheet(self.current_sheet_index() if index is None
                                           else index):
            return False
        index = self.current_sheet_index() if index is None else index
        sheet = self.book.sheets[index]
        if confirm:
            from alerts import confirm_destructive
            if not confirm_destructive(
                    self, title=_("Delete the sheet “{name}”?").format(name=sheet.name),
                    message=_("Its cells are deleted. Formulas on other sheets that read "
                              "them show #REF!."),
                    action=_("Delete Sheet"), is_dark=self.is_dark):
                return False
        return self._structure(_("Delete Sheet"), lambda: self.book.delete_sheet(sheet),
                               select=max(0, index - 1), undo_select=index)

    def _can_delete_sheet(self, index: int) -> bool:
        """Whether the sheet at ``index`` may go: a workbook keeps at least
        one sheet that is not hidden."""
        sheets = self.book.sheets if self.book is not None else []
        if not 0 <= index < len(sheets) or len(sheets) <= 1:
            return False
        return sheets[index].hidden or any(not other.hidden for i, other in enumerate(sheets)
                                           if i != index)

    def _on_sheet_chosen(self, index: int) -> None:
        if self.book is None or index < 0 or index >= len(self.book.sheets):
            return
        if self._editor is not None:
            self._finish_edit(move=(0, 0))
        self._show_sheet(self.book.sheets[index])

    def start_rename(self, index: int) -> None:
        if self.book is None or self.read_only or self.book.is_text_table or index < 0:
            return
        self._end_rename(commit=False)
        rect = self.sheet_bar.tabRect(index)
        edit = QLineEdit(self.sheet_bar)
        edit.setAccessibleName(_("Sheet Name"))
        edit.setText(self.book.sheets[index].name)
        edit.selectAll()
        edit.setGeometry(rect.adjusted(2, 1, -2, -1).united(
            QRect(rect.left(), rect.top(), max(rect.width(), 120), rect.height())))
        edit.installEventFilter(self)
        edit.returnPressed.connect(lambda: self._end_rename(commit=True))
        edit.editingFinished.connect(lambda: self._end_rename(commit=True))
        edit.setProperty("sheet_index", index)
        self._rename_edit = edit
        edit.show()
        edit.setFocus()

    def _end_rename(self, commit: bool) -> None:
        edit = self._rename_edit
        if edit is None:
            return
        self._rename_edit = None
        index = edit.property("sheet_index")
        name = edit.text().strip()
        edit.hide()
        edit.deleteLater()
        if commit and name:
            self.rename_sheet(index, name)
        self.grid.setFocus()

    # -- menus -------------------------------------------------------------

    def _menu(self) -> QMenu:
        return QMenu(self)

    def _row_menu(self, pos: QPoint, header=None) -> None:
        if self.read_only or self.book is None:
            return
        header = header or self.grid.verticalHeader()
        row = header.logicalIndexAt(pos)
        if row < 0:
            return
        r1, _c1, r2, _c2 = self._header_block(row, axis="row")
        count = r2 - r1 + 1
        menu = self._menu()
        menu.addAction(_("Insert Row Above"), lambda: self.insert_rows(r1, 1))
        menu.addAction(_("Insert Row Below"), lambda: self.insert_rows(r2 + 1, 1))
        menu.addSeparator()
        menu.addAction(ngettext("Delete Row", "Delete Rows", count),
                       lambda: self.delete_rows(r1, count))
        menu.exec(header.mapToGlobal(pos))

    def _column_menu(self, pos: QPoint, header=None) -> None:
        if self.read_only or self.book is None:
            return
        header = header or self.grid.horizontalHeader()
        col = header.logicalIndexAt(pos)
        if col < 0:
            return
        _r1, c1, _r2, c2 = self._header_block(col, axis="col")
        count = c2 - c1 + 1
        menu = self._menu()
        menu.addAction(_("Insert Column Before"), lambda: self.insert_columns(c1, 1))
        menu.addAction(_("Insert Column After"), lambda: self.insert_columns(c2 + 1, 1))
        menu.addSeparator()
        menu.addAction(ngettext("Delete Column", "Delete Columns", count),
                       lambda: self.delete_columns(c1, count))
        menu.exec(header.mapToGlobal(pos))

    def _header_block(self, index: int, axis: str):
        """The selected rows (columns) when ``index`` is among them, else
        just ``index``."""
        block = self.selected_block()
        if block is not None:
            r1, c1, r2, c2 = block
            if axis == "row" and r1 <= index <= r2 and c1 == 0 and \
                    c2 >= self.model.columnCount() - 1:
                return block
            if axis == "col" and c1 <= index <= c2 and r1 == 0 and \
                    r2 >= self.model.rowCount() - 1:
                return block
        if axis == "row":
            return index, 0, index, 0
        return 0, index, 0, index

    def _cell_menu(self, pos: QPoint, view=None) -> None:
        if self.book is None:
            return
        menu = self._menu()
        editable = not self.read_only
        cut = menu.addAction(_("Cut"), self.cut)
        cut.setEnabled(editable)
        menu.addAction(_("Copy"), self.copy)
        paste = menu.addAction(_("Paste"), self.paste)
        paste.setEnabled(editable)
        menu.addSeparator()
        clear = menu.addAction(_("Clear"), self.clear_selection)
        clear.setEnabled(editable)
        menu.exec((view or self.grid).viewport().mapToGlobal(pos))

    def _sheet_menu(self, pos: QPoint) -> None:
        if self.book is None or self.book.is_text_table:
            return
        index = self.sheet_bar.tabAt(pos)
        if index < 0:
            return
        editable = not self.read_only
        menu = self._menu()
        rename = menu.addAction(_("Rename"), lambda: self.start_rename(index))
        rename.setEnabled(editable)
        duplicate = menu.addAction(_("Duplicate"), lambda: self.duplicate_sheet(index))
        duplicate.setEnabled(editable)
        menu.addSeparator()
        delete = menu.addAction(_("Delete…"), lambda: self.delete_sheet(index))
        delete.setEnabled(editable and self._can_delete_sheet(index))
        menu.exec(self.sheet_bar.mapToGlobal(pos))

    # -- saving ------------------------------------------------------------

    def _on_clean_changed(self, clean: bool) -> None:
        self.modification_changed.emit(not clean)

    def is_modified(self) -> bool:
        return not self.undo_stack.isClean()

    def save(self, *, overwrite_changes: bool = False) -> SaveResult:
        """Save into the file. See SaveResult for what comes back."""
        if self.book is None:
            return SaveResult(FAILED, self.load_error or _("The spreadsheet is not open yet."))
        if self._editor is not None:
            self._finish_edit(move=(0, 0))
        book, overwrite = self.book, overwrite_changes or self._keep_mine
        try:
            self._run_job(_("Saving\u2026"), lambda _progress: book.save(overwrite_changes=overwrite),
                          size=self._book_size())
        except sheet_io.FileInUseError as error:
            self._check_lock()
            return SaveResult(IN_USE, str(error), path=self._file_path)
        except sheet_io.FileDeletedError as error:
            self._show_deleted()
            return SaveResult(DELETED, str(error), path=self._file_path)
        except sheet_io.ChangedOnDiskError as error:
            return SaveResult(CHANGED_ON_DISK, str(error), path=self._file_path)
        except sheet_io.UnsaveablePartsError as error:
            return SaveResult(NEEDS_COPY, str(error), tuple(error.parts), self._file_path)
        except (sheet_io.SheetFileError, OSError) as error:
            _log.warning("%s could not be saved: %s", self._file_path, error)
            return SaveResult(FAILED, str(error), path=self._file_path)
        except Exception:     # a save must report, never take the window down
            _log.exception("%s could not be saved", self._file_path)
            return SaveResult(FAILED, _("The spreadsheet could not be saved. The file on the "
                                        "disk is as it was."), path=self._file_path)
        self._keep_mine = False
        self._changed_bar.hide()
        self.undo_stack.setClean()
        return SaveResult(SAVED, path=self._file_path, switched=True)

    def save_as(self, path: str) -> SaveResult:
        """Save into another file: of the same kind (the tab then shows
        it), as an Excel workbook (from .ods or a text table; the tab then
        shows it), or the sheet on screen as a text table (.csv, .tsv: an
        export, the tab stays with this file)."""
        if self.book is None:
            return SaveResult(FAILED, self.load_error or _("The spreadsheet is not open yet."))
        if self._editor is not None:
            self._finish_edit(move=(0, 0))
        path = os.path.abspath(path)
        target_kind = sheet_io.kind_of(path)
        if os.path.realpath(path) == os.path.realpath(self._file_path):
            return self.save()
        book, sheet = self.book, self.sheet
        try:
            self._run_job(_("Saving\u2026"), lambda _progress: book.save(path, sheet=sheet),
                          size=self._book_size())
        except sheet_io.FileInUseError as error:
            return SaveResult(IN_USE, str(error), path=path)
        except (sheet_io.SheetFileError, OSError) as error:
            _log.warning("%s could not be saved: %s", path, error)
            return SaveResult(FAILED, str(error), path=path)
        except Exception:     # a save must report, never take the window down
            _log.exception("%s could not be saved", path)
            return SaveResult(FAILED, _("The spreadsheet could not be saved."), path=path)
        if target_kind == self.book.kind:
            self.book.adopt_path(path)
            self._switch_to(path)
            self.undo_stack.setClean()
            return SaveResult(SAVED, path=path, switched=True)
        if target_kind in ("csv", "tsv"):
            return SaveResult(SAVED, path=path, switched=False)
        # Converted: the tab now edits the new file. Its steps go once no
        # Undo of a large change is still under way (they would be taken
        # from under it).
        self._switch_to(path)
        self.after_job(self.undo_stack.clear)
        self.reload()
        return SaveResult(SAVED, path=path, switched=True)

    def set_view_only(self) -> None:
        """Show the workbook and never edit it (a Quick Look of the file):
        read-only whatever another app does with the file."""
        self._view_only = True
        self.read_only = True
        self.formula_field.setReadOnly(True)
        self._add_button.setEnabled(False)
        # A quiet preview: nothing that edits is shown.
        self._formula_bar.hide()
        self._add_button.hide()
        self._update_info()

    def follow_move(self, path: str) -> None:
        """The file was renamed or moved (from the library, or from the
        tab's menu): go on with it there, unsaved changes kept."""
        path = os.path.abspath(path)
        if self.is_busy():
            # A save running now writes where it began; the move follows.
            self.after_job(lambda: self.follow_move(path))
            return
        if self.book is not None:
            self.book.path = path
        self._switch_to(path)

    def _switch_to(self, path: str) -> None:
        self._file_path = path
        self._show_facts()
        self._lock_path = None
        self._update_info()

    # -- the file on the disk ----------------------------------------------

    def schedule_reload(self) -> None:
        """The file changed on the disk (the window's watcher says so):
        read it again when nothing here is unsaved, else ask."""
        if self._reload_pending:
            return
        self._reload_pending = True
        QTimer.singleShot(400, self, self._do_scheduled_reload)

    def _do_scheduled_reload(self) -> None:
        self._reload_pending = False
        if self._released or self.book is None:
            return
        if self.is_busy():
            self.after_job(self.schedule_reload)
            return
        if not os.path.exists(self._file_path):
            self._show_deleted()
            return
        if self._deleted:
            # Back where it was (an Undo in the Finder): as if never gone.
            self._deleted = False
            self._show_changed_bar(False)
        self._check_lock()
        if not self.book.changed_on_disk():
            return
        if self.is_modified():
            self._show_changed_bar(True)
            return
        self.reload()

    def _show_changed_bar(self, show: bool) -> None:
        self._changed_label.setText(_("This file changed on disk."))
        for button, wanted in ((self._reload_button, True), (self._keep_button, True),
                               (self._save_as_button, False), (self._close_button, False)):
            button.setVisible(wanted)
        self._changed_bar.setVisible(show)

    def _show_deleted(self) -> None:
        """The file is gone (in the Trash, or deleted): say so, and offer
        what helps, saving it under a name or closing the tab. Nothing is
        written back by itself."""
        self._deleted = True
        self._changed_label.setText(_("This file was moved to the Trash or deleted."))
        for button, wanted in ((self._reload_button, False), (self._keep_button, False),
                               (self._save_as_button, True), (self._close_button, True)):
            button.setVisible(wanted)
        self._changed_bar.show()

    def is_deleted(self) -> bool:
        return self._deleted

    def reload(self) -> None:
        """Read the file again, keeping the sheet and cell on screen."""
        if self._released:
            return
        if self.is_busy():
            self.after_job(self.reload)
            return
        sheet_index = max(self.current_sheet_index(), 0)
        current = self.current_cell()
        try:
            book = sheet_io.load_workbook(self._file_path)
        except sheet_io.SheetFileError as error:
            _log.warning("%s could not be read again: %s", self._file_path, error)
            return
        if self.book is not None:
            self.book.close()
        self.book = book
        self.undo_stack.clear()
        self.undo_stack.setClean()
        self._changed_bar.hide()
        self._keep_mine = False
        self.load_ok = True
        self._stack.setCurrentWidget(self.grid)
        self._fill_sheet_bar(sheet_index)
        self.model.ensure(*current)
        self.grid.setCurrentIndex(self.model.index(*current))
        self._lock_path = None
        self._check_lock()
        self._update_info()

    def _on_reload_clicked(self) -> None:
        self.reload()

    def _on_keep_clicked(self) -> None:
        # The next save replaces the file on the disk with this version.
        self._keep_mine = True
        self._changed_bar.hide()

    def release(self) -> None:
        """Let go of the workbook as the tab closes."""
        self._released = True
        self._reload_pending = False
        if self.is_busy():
            book = self.book
            self.after_job(lambda: book.close() if book is not None else None)
            return
        if self.book is not None:
            self.book.close()

    def save_view_state(self) -> None:
        """Nothing to remember: a sheet opens on its first sheet, at A1."""

    def document_title(self) -> str:
        return os.path.splitext(os.path.basename(self._file_path))[0]

    # -- looks -------------------------------------------------------------

    def update_theme(self, is_dark: bool) -> None:
        self.is_dark = is_dark
        ring = theme.focus_ring_color(is_dark)
        if is_dark:
            colors = dict(bar="#252526", line="#3C3C3C", text="#D4D4D4", muted="#9A9A9A",
                          field="#1E1E1E", hover="#3C3C3C", grid="#1E1E1E", gridline="#333336",
                          head="#2A2A2A", selected="#264F78", notice="#FFD080",
                          changed="#3C3000", tab_on="#1E1E1E")
        else:
            colors = dict(bar="#F8F8F8", line="#E1E1E1", text="#333333", muted="#6E6E73",
                          field="#FFFFFF", hover="#E1E1E1", grid="#FFFFFF", gridline="#E3E3E6",
                          head="#F3F3F3", selected="#CCE4FF", notice="#7A5000",
                          changed="#FFF8DC", tab_on="#FFFFFF")
        css = f"""
            #FormulaBar {{ background: {colors['bar']};
                border-bottom: 1px solid {colors['line']}; }}
            #SheetInfo {{ background: {colors['grid']}; border-bottom: 1px solid {colors['line']}; }}
            #SheetPicture {{ background: {colors['grid']}; }}
            #SheetChanged {{ background: {colors['changed']};
                border-bottom: 1px solid {colors['line']}; }}
            #SheetStrip {{ background: {colors['bar']}; border-top: 1px solid {colors['line']}; }}
            QLabel {{ color: {colors['text']}; font-size: 12px; background: transparent; }}
            #SheetInfo QLabel {{ color: {colors['muted']}; }}
            #SheetNotice {{ color: {colors['notice']}; }}
            QLineEdit {{ background: {colors['field']}; color: {colors['text']};
                border: 1px solid {colors['line']}; border-radius: 4px; padding: 2px 4px;
                font-size: 12px; }}
            QLineEdit:focus {{ border: 2px solid {ring}; padding: 1px 3px; }}
            QToolButton {{ background: transparent; color: {colors['text']};
                border: 1px solid transparent; border-radius: 4px; padding: 3px 8px;
                font-size: 12px; }}
            QToolButton:hover {{ background: {colors['hover']}; }}
            QToolButton:focus {{ border: 2px solid {ring}; padding: 2px 7px; }}
            QTableView {{ background: {colors['grid']}; color: {colors['text']};
                gridline-color: {colors['gridline']}; border: none;
                selection-background-color: {colors['selected']};
                selection-color: {colors['text']}; font-size: 12px; }}
            QHeaderView::section {{ background: {colors['head']}; color: {colors['muted']};
                border: none; border-right: 1px solid {colors['line']};
                border-bottom: 1px solid {colors['line']}; padding: 0 4px; font-size: 11px; }}
            QTableCornerButton::section {{ background: {colors['head']}; border: none;
                border-right: 1px solid {colors['line']};
                border-bottom: 1px solid {colors['line']}; }}
            QTabBar::tab {{ background: transparent; color: {colors['muted']};
                padding: 3px 12px; margin: 2px 1px; border-radius: 5px; font-size: 12px; }}
            QTabBar::tab:selected {{ background: {colors['tab_on']}; color: {colors['text']}; }}
        """
        self.setStyleSheet(css)
        self._bar.update_theme(is_dark, self._file_path)
        self._grid_background = QColor(colors["grid"])
        self.grid.viewport().update()


def _luminance(color: QColor) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
    return 0.2126 * channel(color.redF()) + 0.7152 * channel(color.greenF()) + \
        0.0722 * channel(color.blueF())


def _contrast(one: QColor, other: QColor) -> float:
    """The contrast ratio of two colours (WCAG): 1 to 21."""
    a, b = _luminance(one), _luminance(other)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)
