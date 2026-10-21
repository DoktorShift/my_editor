# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Formulas in spreadsheets: read, calculate, move along with their cells.

A formula is kept as the text Excel writes ("=SUM(A1:A3)"), in every
format: an OpenDocument file's formulas ("of:=SUM([.A1:.A3])") are
translated on the way in and out (``from_openformula``, ``to_openformula``).
Every change to a formula's text (references moved by an inserted row, a
renamed sheet, a pasted copy) is made on its tokens, so the rest of the
text stays exactly as the person or the spreadsheet app wrote it.

``Engine`` calculates. It knows which cells each formula reads, so an edit
recalculates only the formulas that depend on the edited cell, in an
order where every formula's inputs are ready. A formula that reads itself
(directly or through others) shows ``CIRCULAR``, and the calculation
never loops. A function MyEditor does not know is not an error: the cell
keeps the result the spreadsheet app stored in the file, marked as
calculated by that app (``STATE_APP``).

Values follow Excel's rules: numbers are floats (a whole number may be an
int), text is str, TRUE and FALSE are bool, an empty cell is None, an
error is a ``CellError``. Dates are serial numbers as in Excel (day 1 is
1900-01-01); a date or time stored in a cell (a datetime) is read as its
serial number.
"""

from __future__ import annotations

import datetime as _dt
import logging
import math
import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal, InvalidOperation
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

import formula_names

_log = logging.getLogger(__name__)

MAX_ROWS = 1048576
MAX_COLS = 16384


# --------------------------------------------------------------------------- #
# Errors                                                                      #
# --------------------------------------------------------------------------- #

class CellError:
    """An error value such as #DIV/0!. Compared by its code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code

    def __eq__(self, other) -> bool:
        return isinstance(other, CellError) and other.code == self.code

    def __hash__(self) -> int:
        return hash(("CellError", self.code))

    def __repr__(self) -> str:
        return f"CellError({self.code!r})"

    def __str__(self) -> str:
        return self.code


NULL = CellError("#NULL!")
DIV0 = CellError("#DIV/0!")
VALUE = CellError("#VALUE!")
REF = CellError("#REF!")
NAME = CellError("#NAME?")
NUM = CellError("#NUM!")
NA = CellError("#N/A")
# A formula that reads its own result. Excel and LibreOffice have no error
# code for it that other apps read back, so it is never written to a file.
CIRCULAR = CellError("#CIRC!")

ERROR_CODES = {e.code: e for e in (NULL, DIV0, VALUE, REF, NAME, NUM, NA)}
ERROR_CODES["#GETTING_DATA"] = CellError("#GETTING_DATA")

# What the engine says about a formula cell besides its value.
STATE_OK = ""
STATE_APP = "app"            # calculated by the spreadsheet app; value kept
STATE_CIRCULAR = "circular"
# Calculated by the spreadsheet app, and its inputs changed here: the
# result is not known until the app calculates it, so none is shown or
# written (Excel and LibreOffice calculate a formula without a result).
STATE_STALE = "stale"


class NeedsApp(Exception):
    """The formula uses something MyEditor does not calculate (a function
    it does not know, an array constant, another workbook)."""


class _Raise(Exception):
    """An error value raised to leave a deep calculation at once."""

    def __init__(self, error: CellError) -> None:
        super().__init__(error.code)
        self.error = error


# --------------------------------------------------------------------------- #
# Cell addresses                                                              #
# --------------------------------------------------------------------------- #

def column_letters(col: int) -> str:
    """0 -> "A", 25 -> "Z", 26 -> "AA"."""
    col += 1
    letters = ""
    while col:
        col, rest = divmod(col - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def column_index(letters: str) -> int:
    """"A" -> 0, "AA" -> 26."""
    col = 0
    for ch in letters.upper():
        col = col * 26 + (ord(ch) - 64)
    return col - 1


def cell_name(row: int, col: int) -> str:
    """(0, 0) -> "A1"."""
    return f"{column_letters(col)}{row + 1}"


_CELL_NAME = re.compile(r"^\s*\$?([A-Za-z]{1,3})\$?(\d{1,7})\s*$")


def parse_cell_name(text: str) -> Optional[Tuple[int, int]]:
    """"C12" (or "$C$12") -> (11, 2); None when it is not a cell."""
    match = _CELL_NAME.match(text)
    if not match:
        return None
    col, row = column_index(match.group(1)), int(match.group(2)) - 1
    if not (0 <= row < MAX_ROWS and 0 <= col < MAX_COLS):
        return None
    return row, col


def quote_sheet_name(name: str) -> str:
    """A sheet name as a reference writes it: quoted when it is not a
    plain word, or when it could be read as something else (a cell such
    as "Q4", a reference in Excel's R1C1 style such as "R1C1", TRUE)."""
    if re.fullmatch(_PLAIN_NAME, name) and \
            not re.fullmatch(r"[A-Za-z]{1,3}\d+", name) and \
            not re.fullmatch(r"(?i)R\d*C?\d*|C\d*", name) and \
            name.upper() not in ("TRUE", "FALSE"):
        return name
    return "'" + name.replace("'", "''") + "'"


def quote_sheet_range(first: str, last: str) -> str:
    """The prefix of a reference to sheets ``first`` to ``last`` (a 3D
    reference): "Jan:Dec", or "'Jan 24:Dec 24'" when a name needs quotes."""
    if quote_sheet_name(first) == first and quote_sheet_name(last) == last:
        return f"{first}:{last}"
    return "'" + f"{first}:{last}".replace("'", "''") + "'"


# --------------------------------------------------------------------------- #
# Tokens                                                                      #
# --------------------------------------------------------------------------- #

_PLAIN_NAME = "[A-Za-z_\u00C0-\uFFFF][A-Za-z0-9_.\u00C0-\uFFFF]*"
_QUOTED = r"'(?:[^']|'')+'"
_SHEET = rf"(?:{_QUOTED}|{_PLAIN_NAME})!"
_CELL = r"\$?[A-Za-z]{1,3}\$?\d+"
_COLS = r"\$?[A-Za-z]{1,3}:\$?[A-Za-z]{1,3}"
_ROWS = r"\$?\d+:\$?\d+"
_REF_BODY = rf"(?:{_CELL}(?::{_CELL})?|{_COLS}|{_ROWS}|\#REF!)"
# A reference into another workbook: "[1]Sheet1!A1", "[Book.xlsx]Data!A1",
# "'C:\x\[Book.xlsx]Data'!A1", "[1]!Name". Never moved by anything here.
_EXTERNAL = (rf"(?:\[[^\]\[]+\](?:{_PLAIN_NAME})?|'(?:[^'\[]|'')*\[(?:[^']|'')*')"
             rf"!(?:{_REF_BODY}|{_PLAIN_NAME})")
# A reference to the same place on several sheets: "Jan:Dec!A1",
# "'Jan 24:Dec 24'!A1".
_REF_3D = (rf"(?:{_PLAIN_NAME}:{_PLAIN_NAME}|'(?:[^':]|'')+:(?:[^']|'')+')!{_REF_BODY}")
# A table's structured reference: "Table1[Amount]", "Table1[[#This Row],
# [Price]]", "[@Price]" (an apostrophe escapes a bracket in a column name).
_STRUCTURED = (rf"(?:{_PLAIN_NAME})?\[(?:[^\[\]']|'.|\[(?:[^\[\]']|'.)*\])*\]")

_TOKEN = re.compile(rf"""
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<ext>{_EXTERNAL})(?![A-Za-z0-9_.(])
  | (?P<ref3d>{_REF_3D})(?![A-Za-z0-9_.(])
  | (?P<ref>(?:{_SHEET})?{_REF_BODY})(?![A-Za-z0-9_.(\[])
  | (?P<struct>{_STRUCTURED})
  | (?P<err>\#(?:DIV/0!|N/A|NAME\?|NULL!|NUM!|REF!|VALUE!|GETTING_DATA))
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<func>{_PLAIN_NAME}(?=\s*\())
  | (?P<name>[A-Za-z_\\\u00C0-\uFFFF][A-Za-z0-9_.?\\\u00C0-\uFFFF]*)
  | (?P<op><>|<=|>=|[-+*/^&=<>%~])
  | (?P<lp>\()
  | (?P<rp>\))
  | (?P<sep>[,;])
  | (?P<lb>\{{)
  | (?P<rb>\}})
  | (?P<at>@)
  | (?P<colon>:)
  | (?P<bar>\|)
""", re.VERBOSE)

# What a run of spaces between two references is: Excel's intersection
# operator ("A1:C3 B1:B5"), which OpenFormula writes as "!".
_OPERAND_END = {"ref", "ref3d", "ext", "struct", "rp", "name"}
_OPERAND_START = {"ref", "ref3d", "ext", "struct", "lp", "name"}


class Token:
    __slots__ = ("kind", "text", "start", "end", "ref")

    def __init__(self, kind: str, text: str, start: int, end: int, ref=None) -> None:
        self.kind, self.text, self.start, self.end = kind, text, start, end
        self.ref = ref

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.text!r})"


class FormulaSyntaxError(ValueError):
    """The formula cannot be read."""


def tokenize(formula: str) -> List[Token]:
    """The tokens of a formula's text, without its leading "=". Raises
    FormulaSyntaxError for text that is no formula."""
    text = formula[1:] if formula.startswith("=") else formula
    offset = len(formula) - len(text)
    tokens: List[Token] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise FormulaSyntaxError(f"unexpected {text[pos]!r} at {pos}")
        kind = match.lastgroup
        if kind == "ws":
            following = _TOKEN.match(text, match.end())
            if tokens and tokens[-1].kind in _OPERAND_END and following is not None and \
                    following.lastgroup in _OPERAND_START:
                tokens.append(Token("isect", match.group(kind), match.start() + offset,
                                    match.end() + offset))
        else:
            body = match.group(kind)
            ref = None
            if kind == "ref":
                ref = parse_ref(body)
                if not _in_bounds(ref):
                    # "LOG10" without "(", "ABC1234567": a name, not a cell.
                    kind, ref = "name", None
            tokens.append(Token(kind, body, match.start() + offset, match.end() + offset, ref))
        pos = match.end()
    return tokens


class Scan:
    """What a formula's text refers to, found once per text: its
    references as (start, end, sheet prefix as written, Ref), the names it
    uses, whether it uses TODAY() or NOW(), and whether it holds what the
    engine cannot follow (an array constant, another workbook)."""

    __slots__ = ("refs", "names", "volatile", "opaque")

    def __init__(self, refs, names, volatile, opaque) -> None:
        self.refs, self.names, self.volatile, self.opaque = refs, names, volatile, opaque


_SCANS: Dict[str, Scan] = {}


def scan(formula: str) -> Scan:
    """The Scan of a formula's text (kept, as many cells share texts and
    a structural change reads every formula)."""
    found = _SCANS.get(formula)
    if found is not None:
        return found
    try:
        tokens = tokenize(formula)
    except FormulaSyntaxError:
        found = Scan((), (), False, True)
    else:
        refs, names, volatile, opaque = [], [], False, False
        for token in tokens:
            kind = token.kind
            if kind == "ref":
                refs.append((token.start, token.end, _sheet_prefix(token.text), token.ref))
            elif kind == "name":
                if token.text.upper() not in ("TRUE", "FALSE"):
                    names.append(token.text)
            elif kind == "func":
                if _bare_name(token.text) in _VOLATILE:
                    volatile = True
            elif kind in ("lb", "ext", "colon"):
                opaque = True
        found = Scan(tuple(refs), tuple(names), volatile, opaque)
    _remember_scan(formula, found)
    return found


def _remember_scan(formula: str, found: Scan) -> None:
    if len(_SCANS) > 400000:
        _SCANS.clear()
    _SCANS[formula] = found


def _bare_name(name: str) -> str:
    upper = name.upper()
    for prefix in ("_XLFN._XLWS.", "_XLFN.", "_XLWS.", "COM.MICROSOFT."):
        if upper.startswith(prefix):
            return upper[len(prefix):]
    return upper


def _split_sheet(text: str) -> Tuple[Optional[str], str]:
    """("Sheet 2", "A1") from "'Sheet 2'!A1"; (None, "A1") without a sheet."""
    if "!" not in text or text.endswith("#REF!") and text.count("!") == 1:
        return None, text
    if text.startswith("'"):
        end = 1
        while True:
            end = text.index("'", end)
            if end + 1 < len(text) and text[end + 1] == "'":
                end += 2
                continue
            break
        return text[1:end].replace("''", "'"), text[end + 2:]
    sheet, _bang, body = text.partition("!")
    return sheet, body


_PART = re.compile(r"(\$?)([A-Za-z]{1,3})?(\$?)(\d+)?$")


def _parse_part(part: str):
    """A1 parts: ("$", "A", "$", "1") -> (col, col_abs, row, row_abs);
    a missing column or row is None."""
    match = _PART.match(part)
    if not match:
        return None
    dollar1, letters, dollar2, digits = match.groups()
    col = column_index(letters) if letters else None
    row = int(digits) - 1 if digits else None
    if letters is None:
        # "$5": the dollar belongs to the row.
        return None, False, row, bool(dollar1 or dollar2)
    return col, bool(dollar1), row, bool(dollar2)


class Ref:
    """A parsed reference: one cell, a block, whole columns or whole rows.
    Rows and columns are 0-based; ``None`` for the open side of whole
    columns (rows) or rows (columns)."""

    __slots__ = ("sheet", "r1", "c1", "r2", "c2", "ra1", "ca1", "ra2", "ca2", "kind", "error")

    def __init__(self, sheet, r1, c1, r2, c2, ra1=False, ca1=False, ra2=False, ca2=False,
                 kind="cell", error=False):
        self.sheet, self.r1, self.c1, self.r2, self.c2 = sheet, r1, c1, r2, c2
        self.ra1, self.ca1, self.ra2, self.ca2 = ra1, ca1, ra2, ca2
        self.kind, self.error = kind, error

    def copy(self) -> "Ref":
        return Ref(self.sheet, self.r1, self.c1, self.r2, self.c2, self.ra1, self.ca1,
                   self.ra2, self.ca2, self.kind, self.error)

    @property
    def is_cell(self) -> bool:
        return self.kind == "cell" and self.r1 == self.r2 and self.c1 == self.c2


def parse_ref(text: str) -> Optional[Ref]:
    """A reference token's text as a Ref; None when it is none."""
    sheet, body = _split_sheet(text)
    if body == "#REF!":
        return Ref(sheet, 0, 0, 0, 0, error=True, kind="error")
    first, colon, second = body.partition(":")
    a = _parse_part(first)
    b = _parse_part(second) if colon else a
    if a is None or b is None:
        return None
    c1, ca1, r1, ra1 = a
    c2, ca2, r2, ra2 = b
    if c1 is not None and r1 is not None and c2 is not None and r2 is not None:
        kind = "cell" if not colon else "area"
        ref = Ref(sheet, min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2), kind=kind)
        # Keep the dollars with the side they were written on.
        if r1 <= r2:
            ref.ra1, ref.ra2 = ra1, ra2
        else:
            ref.ra1, ref.ra2 = ra2, ra1
        if c1 <= c2:
            ref.ca1, ref.ca2 = ca1, ca2
        else:
            ref.ca1, ref.ca2 = ca2, ca1
        if ref.kind == "area" and ref.r1 == ref.r2 and ref.c1 == ref.c2:
            ref.kind = "area"
        return ref
    if colon and c1 is not None and c2 is not None and r1 is None and r2 is None:
        return Ref(sheet, None, min(c1, c2), None, max(c1, c2), False, ca1, False, ca2, kind="cols")
    if colon and r1 is not None and r2 is not None and c1 is None and c2 is None:
        return Ref(sheet, min(r1, r2), None, max(r1, r2), None, ra1, False, ra2, False, kind="rows")
    return None


def _valid_ref(text: str) -> bool:
    return _in_bounds(parse_ref(text))


def _in_bounds(ref: Optional[Ref]) -> bool:
    if ref is None:
        return False
    if ref.error:
        return True
    for row in (ref.r1, ref.r2):
        if row is not None and not 0 <= row < MAX_ROWS:
            return False
    for col in (ref.c1, ref.c2):
        if col is not None and not 0 <= col < MAX_COLS:
            return False
    return True


def format_ref(ref: Ref, sheet_text: Optional[str] = None) -> str:
    """A Ref written as A1 text, with its sheet prefix as given."""
    prefix = f"{sheet_text}!" if sheet_text else ""
    if ref.error:
        return prefix + "#REF!"

    def cell(r, c, ra, ca):
        return f"{'$' if ca else ''}{column_letters(c)}{'$' if ra else ''}{r + 1}"

    if ref.kind == "cols":
        return (f"{prefix}{'$' if ref.ca1 else ''}{column_letters(ref.c1)}:"
                f"{'$' if ref.ca2 else ''}{column_letters(ref.c2)}")
    if ref.kind == "rows":
        return f"{prefix}{'$' if ref.ra1 else ''}{ref.r1 + 1}:{'$' if ref.ra2 else ''}{ref.r2 + 1}"
    first = cell(ref.r1, ref.c1, ref.ra1, ref.ca1)
    if ref.kind == "cell":
        return prefix + first
    return f"{prefix}{first}:{cell(ref.r2, ref.c2, ref.ra2, ref.ca2)}"


def _sheet_prefix(text: str) -> Optional[str]:
    """The sheet prefix of a reference token as written, without "!"."""
    if "!" not in text:
        return None
    if text.startswith("'"):
        end = 1
        while True:
            end = text.index("'", end)
            if end + 1 < len(text) and text[end + 1] == "'":
                end += 2
                continue
            return text[:end + 1]
    head, _bang, body = text.partition("!")
    if not body and head == "#REF":
        return None
    return head


# --------------------------------------------------------------------------- #
# Parsing                                                                     #
# --------------------------------------------------------------------------- #

# Nodes are tuples: ("num", x) ("str", s) ("bool", b) ("err", e)
# ("ref", Ref) ("fn", NAME, args) ("neg", x) ("pos", x) ("pct", x)
# ("op", sym, a, b) ("name", ident) ("missing",) ("app",)

_COMPARE = {"=", "<>", "<", ">", "<=", ">="}


class _Parser:
    def __init__(self, tokens: List[Token]) -> None:
        self.tokens = [t for t in tokens if t.kind != "at"]
        self.pos = 0

    def peek(self) -> Optional[Token]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> Token:
        token = self.peek()
        if token is None:
            raise FormulaSyntaxError("the formula ends too soon")
        self.pos += 1
        return token

    def parse(self):
        if not self.tokens:
            raise FormulaSyntaxError("empty formula")
        node = self.comparison()
        if self.peek() is not None:
            raise FormulaSyntaxError(f"unexpected {self.peek().text!r}")
        return node

    def _binary(self, operators, below):
        node = below()
        while True:
            token = self.peek()
            if token is None or token.kind != "op" or token.text not in operators:
                return node
            self.take()
            node = ("op", token.text, node, below())

    def comparison(self):
        return self._binary(_COMPARE, self.concat)

    def concat(self):
        return self._binary({"&"}, self.additive)

    def additive(self):
        return self._binary({"+", "-"}, self.multiplicative)

    def multiplicative(self):
        return self._binary({"*", "/"}, self.power)

    def power(self):
        return self._binary({"^"}, self.percent)

    def percent(self):
        node = self.unary()
        while (token := self.peek()) is not None and token.kind == "op" and token.text == "%":
            self.take()
            node = ("pct", node)
        return node

    def unary(self):
        token = self.peek()
        if token is not None and token.kind == "op" and token.text in "+-":
            self.take()
            inner = self.unary()
            return ("neg", inner) if token.text == "-" else ("pos", inner)
        return self.primary()

    def primary(self):
        token = self.take()
        kind = token.kind
        if kind == "num":
            return ("num", float(token.text))
        if kind == "str":
            return ("str", token.text[1:-1].replace('""', '"'))
        if kind == "err":
            return ("err", ERROR_CODES.get(token.text, VALUE))
        if kind == "ref":
            node = ("ref", parse_ref(token.text))
            # "A1:B2" arrives as one token; "A1:INDEX(...)" needs the operator.
            if (nxt := self.peek()) is not None and nxt.kind == "colon":
                raise NeedsApp("range operator")
            return node
        if kind in ("ext", "ref3d", "struct", "isect"):
            # Another workbook, several sheets, a table, an intersection:
            # calculated by the spreadsheet app.
            raise NeedsApp(kind)
        if kind == "lb":
            raise NeedsApp("array constant")
        if kind == "lp":
            node = self.comparison()
            if self.take().kind != "rp":
                raise FormulaSyntaxError("missing )")
            return node
        if kind == "func":
            name = token.text.upper()
            for prefix in ("_XLFN._XLWS.", "_XLFN.", "_XLWS.", "COM.MICROSOFT."):
                if name.startswith(prefix):
                    name = name[len(prefix):]
            if self.take().kind != "lp":
                raise FormulaSyntaxError("missing (")
            args = []
            if (nxt := self.peek()) is not None and nxt.kind == "rp":
                self.take()
                return ("fn", name, tuple(args))
            while True:
                nxt = self.peek()
                if nxt is not None and nxt.kind in ("sep", "rp"):
                    args.append(("missing",))
                else:
                    args.append(self.comparison())
                end = self.take()
                if end.kind == "rp":
                    return ("fn", name, tuple(args))
                if end.kind != "sep":
                    raise FormulaSyntaxError(f"unexpected {end.text!r}")
        if kind == "name":
            upper = token.text.upper()
            if upper in ("TRUE", "FALSE"):
                return ("bool", upper == "TRUE")
            return ("name", token.text)
        raise FormulaSyntaxError(f"unexpected {token.text!r}")


_PARSED: Dict[str, object] = {}


def parse(formula: str):
    """The tree of a formula's text ("=1+A1"). Raises FormulaSyntaxError,
    or NeedsApp for what only the spreadsheet app calculates. Trees are
    shared between cells with the same text, so they are never changed."""
    tree = _PARSED.get(formula)
    if tree is None:
        try:
            tree = _Parser(tokenize(formula)).parse()
        except NeedsApp:
            tree = ("app",)
        if len(_PARSED) > 200000:
            _PARSED.clear()
        _PARSED[formula] = tree
    return tree


def is_formula(text) -> bool:
    return isinstance(text, str) and len(text) > 1 and text.startswith("=")


# --------------------------------------------------------------------------- #
# Rewriting references                                                        #
# --------------------------------------------------------------------------- #

def _rewrite_refs(formula: str, change) -> str:
    """``formula`` with each reference replaced by what ``change`` returns
    for it (given its Ref and its sheet prefix as written): a new Ref
    (written with the same prefix), text, or None to keep it. Text that is
    not a formula comes back as it was. The new text's scan is known at
    once, so a structural change reads each formula only once."""
    found = scan(formula)
    if not found.refs:
        return formula
    pieces, last = [], 0
    new_refs, shift, exact = [], 0, True
    for start, end, prefix, ref in found.refs:
        replacement = change(ref, prefix)
        if replacement is None:
            new_refs.append((start + shift, end + shift, prefix, ref))
            continue
        if isinstance(replacement, Ref):
            new_ref = replacement
            replacement = format_ref(new_ref, prefix)
            new_prefix = prefix
        else:
            new_ref = parse_ref(replacement)
            new_prefix = _sheet_prefix(replacement)
            if new_ref is None:
                exact = False
        if replacement == formula[start:end]:
            new_refs.append((start + shift, end + shift, prefix, ref))
            continue
        pieces.append(formula[last:start])
        pieces.append(replacement)
        last = end
        new_start = start + shift
        shift += len(replacement) - (end - start)
        new_refs.append((new_start, end + shift, new_prefix, new_ref))
    if not pieces:
        return formula
    pieces.append(formula[last:])
    text = "".join(pieces)
    if exact and text not in _SCANS:
        _remember_scan(text, Scan(tuple(new_refs), found.names, found.volatile, found.opaque))
    return text


def _same_sheet(prefix: Optional[str], ref: Ref, home: str, target: str) -> bool:
    name = ref.sheet if prefix is not None else home
    return name is not None and name.casefold() == target.casefold()


def adjust_for_insert_delete(formula: str, *, home: str, target: str, axis: str,
                             at: int, count: int) -> str:
    """``formula`` (in sheet ``home``) after ``count`` rows (``axis`` "row")
    or columns ("col") were inserted (``count`` > 0) or deleted (< 0) at
    ``at`` in sheet ``target``. References past the change move; a
    reference to a deleted cell becomes #REF!; a block that spans the
    change grows or shrinks, as in Excel."""

    def change(ref: Ref, prefix: Optional[str]) -> Optional[str]:
        if ref.error or not _same_sheet(prefix, ref, home, target):
            return None
        new = ref.copy()
        if axis == "row":
            if ref.kind == "cols":
                return None
            lo, hi = ref.r1, ref.r2
        else:
            if ref.kind == "rows":
                return None
            lo, hi = ref.c1, ref.c2
        limit = MAX_ROWS if axis == "row" else MAX_COLS
        if count > 0:
            if lo >= at:
                lo += count
            if hi >= at:
                hi += count
            if hi >= limit:
                if lo >= limit:
                    new.error = True
                hi = limit - 1
        else:
            gone_lo, gone_hi = at, at - count - 1
            if gone_lo <= lo and hi <= gone_hi:
                new.error = True
            else:
                width = -count
                if lo > gone_hi:
                    lo -= width
                elif lo >= gone_lo:
                    lo = gone_lo
                if hi > gone_hi:
                    hi -= width
                elif hi >= gone_lo:
                    hi = gone_lo - 1
        if new.error:
            return new
        if axis == "row":
            new.r1, new.r2 = lo, hi
        else:
            new.c1, new.c2 = lo, hi
        return new

    return _rewrite_refs(formula, change)


def shift_formula(formula: str, rows: int, cols: int) -> str:
    """``formula`` copied ``rows`` down and ``cols`` right: relative parts
    of its references move, absolute ones ($A$1) stay; one that would leave
    the sheet becomes #REF!, as when pasting in Excel."""
    if not rows and not cols:
        return formula

    def change(ref: Ref, _prefix: Optional[str]) -> Optional[str]:
        if ref.error:
            return None
        new = ref.copy()
        if ref.kind != "cols":
            if not ref.ra1:
                new.r1 += rows
            if not ref.ra2:
                new.r2 += rows
        if ref.kind != "rows":
            if not ref.ca1:
                new.c1 += cols
            if not ref.ca2:
                new.c2 += cols
        for value, limit in ((new.r1, MAX_ROWS), (new.r2, MAX_ROWS),
                             (new.c1, MAX_COLS), (new.c2, MAX_COLS)):
            if value is not None and not 0 <= value < limit:
                new.error = True
        if not new.error:
            if new.r1 is not None and new.r1 > new.r2:
                new.r1, new.r2, new.ra1, new.ra2 = new.r2, new.r1, new.ra2, new.ra1
            if new.c1 is not None and new.c1 > new.c2:
                new.c1, new.c2, new.ca1, new.ca2 = new.c2, new.c1, new.ca2, new.ca1
        return new

    return _rewrite_refs(formula, change)


def _rewrite_tokens(formula: str, change) -> str:
    """``formula`` with each token replaced by what ``change`` returns for
    it (None keeps it). For changes that are rare enough to read the
    formula again (renaming or deleting a sheet)."""
    try:
        tokens = tokenize(formula)
    except FormulaSyntaxError:
        return formula
    out, last = [], 0
    for token in tokens:
        replacement = change(token)
        if replacement is None or replacement == token.text:
            continue
        out.append(formula[last:token.start])
        out.append(replacement)
        last = token.end
    if not out:
        return formula
    out.append(formula[last:])
    return "".join(out)


def _split_3d(text: str) -> Tuple[str, str, str]:
    """"'Jan 24:Dec 24'!A1" -> ("Jan 24", "Dec 24", "A1")."""
    prefix, _bang, body = text.rpartition("!")
    if prefix.startswith("'"):
        prefix = prefix[1:-1].replace("''", "'")
    first, _colon, last = prefix.partition(":")
    return first, last, body


def rename_sheet_in_formula(formula: str, old: str, new: str) -> str:
    """``formula`` with references to sheet ``old`` naming ``new``: plain
    ones, and either end of a reference to several sheets (Jan:Dec!A1).
    References into other workbooks name their own sheets and stay."""
    folded = old.casefold()

    def change(token: Token) -> Optional[str]:
        if token.kind == "ref" and token.ref is not None:
            prefix = _sheet_prefix(token.text)
            if prefix is None or token.ref.sheet is None or token.ref.sheet.casefold() != folded:
                return None
            return format_ref(token.ref.copy(), quote_sheet_name(new))
        if token.kind == "ref3d":
            first, last, body = _split_3d(token.text)
            if folded not in (first.casefold(), last.casefold()):
                return None
            first = new if first.casefold() == folded else first
            last = new if last.casefold() == folded else last
            return f"{quote_sheet_range(first, last)}!{body}"
        return None

    return _rewrite_tokens(formula, change)


def break_sheet_in_formula(formula: str, gone: str,
                           order: Optional[Sequence[str]] = None) -> str:
    """``formula`` with references to the deleted sheet ``gone`` as #REF!.
    A reference to several sheets that ``gone`` ends loses only that end,
    as in Excel: it now ends at the next sheet inside it (``order`` are
    the sheet names as they were, in their order)."""
    folded = gone.casefold()

    def change(token: Token) -> Optional[str]:
        if token.kind == "ref" and token.ref is not None:
            prefix = _sheet_prefix(token.text)
            if prefix is None or token.ref.sheet is None or token.ref.sheet.casefold() != folded:
                return None
            return "#REF!"
        if token.kind == "ref3d":
            first, last, body = _split_3d(token.text)
            if folded not in (first.casefold(), last.casefold()):
                return None
            names = [name.casefold() for name in (order or ())]
            try:
                lo, hi = names.index(first.casefold()), names.index(last.casefold())
            except ValueError:
                return "#REF!"
            if lo > hi:
                lo, hi = hi, lo
            inside = [order[k] for k in range(lo, hi + 1) if names[k] != folded]
            if not inside:
                return "#REF!"
            if len(inside) == 1:
                return format_ref(parse_ref(body), quote_sheet_name(inside[0]))
            return f"{quote_sheet_range(inside[0], inside[-1])}!{body}"
        return None

    return _rewrite_tokens(formula, change)


def references(formula: str) -> List[Ref]:
    """Every reference in ``formula``, as written."""
    found: List[Ref] = []

    def change(ref: Ref, prefix: Optional[str]) -> Optional[str]:
        if prefix is None:
            ref = ref.copy()
            ref.sheet = None
        found.append(ref)
        return None

    _rewrite_refs(formula, change)
    return found


# --------------------------------------------------------------------------- #
# Formulas in the app's language                                              #
# --------------------------------------------------------------------------- #

def _known_name(name: str) -> str:
    """A function name without Excel's "_xlfn." prefix when it is one
    MyEditor puts back on saving (formula_names.FUTURE_FUNCTIONS)."""
    upper = name.upper()
    for prefix in ("_XLFN._XLWS.", "_XLFN.", "_XLWS."):
        if upper.startswith(prefix) and upper[len(prefix):] in formula_names.FUTURE_FUNCTIONS:
            return name[len(prefix):]
    return name


def localize_formula(formula: str, language: str) -> str:
    """A formula as the app's language writes it: "=SUM(A1,1.5)" is
    "=SUMME(A1;1,5)" in German. Text in quotes, references and sheet
    names stay as they are; a formula with an array constant ({...}) is
    shown as the file has it."""
    if not is_formula(formula):
        return formula
    try:
        tokens = tokenize(formula)
    except FormulaSyntaxError:
        return formula
    names = formula_names.FUNCTION_NAMES.get(language, {})
    syntax = formula_names.SYNTAX.get(language)
    booleans = formula_names.BOOLEANS.get(language)
    if syntax is not None and any(token.kind == "lb" for token in tokens):
        return formula
    out, last = [], 0
    for token in tokens:
        replacement = None
        if token.kind == "func":
            name = _known_name(token.text)
            replacement = names.get(name.upper(), name)
        elif token.kind == "name" and booleans and token.text.upper() in ("TRUE", "FALSE"):
            replacement = booleans[0] if token.text.upper() == "TRUE" else booleans[1]
        elif token.kind == "sep" and syntax is not None:
            replacement = syntax["separator"]
        elif token.kind == "num" and syntax is not None:
            replacement = token.text.replace(".", syntax["decimal"])
        if replacement is None or replacement == token.text:
            continue
        out.append(formula[last:token.start])
        out.append(replacement)
        last = token.end
    out.append(formula[last:])
    return "".join(out)


_LOCAL_WORD = re.compile(r"[^\W\d]\w*(?:\.\w+)*")


def delocalize_formula(text: str, language: str) -> str:
    """A formula as a person typed it in the app's language, written as
    files keep it: "=SUMME(A1;1,5)" is "=SUM(A1,1.5)". English names
    and the English comma are understood in every language."""
    if not is_formula(text):
        return text
    names = {local.upper(): canonical for canonical, local in
             formula_names.FUNCTION_NAMES.get(language, {}).items()}
    names.update({k.upper(): v for k, v in formula_names.ALIASES.get(language, {}).items()})
    syntax = formula_names.SYNTAX.get(language)
    booleans = formula_names.BOOLEANS.get(language)
    separator = syntax["separator"] if syntax else ","
    decimal = syntax["decimal"] if syntax else "."
    if syntax is not None and "{" in text:
        # An array constant: its separators differ again; left as typed.
        return text
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if decimal != "." and ch == decimal and i + 1 < n and text[i + 1].isdigit() and \
                not (out and out[-1] and (out[-1][-1].isalnum() or out[-1][-1] in "_$.)\"]")):
            # ",5": a number written without its leading zero.
            end = i + 1
            while end < n and text[end].isdigit():
                end += 1
            out.append("." + text[i + 1:end])
            i = end
            continue
        if ch == "[":
            # A reference into a table or another workbook, nested
            # brackets and all: as typed.
            end = _matching_bracket(text, i)
            out.append(text[i:end + 1])
            i = end + 1
            continue
        if ch in "\"'":
            close = ch
            end = i + 1
            while end < n:
                if text[end] == close:
                    if close != "]" and end + 1 < n and text[end + 1] == close:
                        end += 2
                        continue
                    break
                end += 1
            out.append(text[i:end + 1])
            i = end + 1
            continue
        if ch.isdigit() and not (out and out[-1] and (out[-1][-1].isalnum() or
                                                       out[-1][-1] in "_$.")):
            end = i
            while end < n and text[end].isdigit():
                end += 1
            if decimal != "." and end + 1 < n and text[end] == decimal and \
                    text[end + 1].isdigit():
                end += 1
                while end < n and text[end].isdigit():
                    end += 1
            if end < n and text[end] in "eE" and end + 1 < n and \
                    (text[end + 1].isdigit() or text[end + 1] in "+-"):
                end += 2
                while end < n and text[end].isdigit():
                    end += 1
            out.append(text[i:end].replace(decimal, ".") if decimal != "." else text[i:end])
            i = end
            continue
        match = _LOCAL_WORD.match(text, i) if (ch.isalpha() or ch == "_") else None
        if match and not (out and out[-1] and (out[-1][-1].isalnum() or out[-1][-1] in "$")):
            word = match.group(0)
            end = match.end()
            after = end
            while after < n and text[after] == " ":
                after += 1
            if after < n and text[after] == "(":
                out.append(names.get(word.upper(), word))
            elif end < n and text[end] == "!":
                out.append(word)
            elif booleans and word.upper() in booleans:
                out.append("TRUE" if word.upper() == booleans[0] else "FALSE")
            else:
                out.append(word)
            i = end
            continue
        if ch == separator and separator != ",":
            out.append(",")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def to_file_formula(formula: str) -> str:
    """``formula`` as Excel needs it in a file: newer functions carry the
    "_xlfn." prefix ("=_xlfn.CONCAT(A1:A3)")."""
    if not is_formula(formula):
        return formula
    try:
        tokens = tokenize(formula)
    except FormulaSyntaxError:
        return formula
    if any(token.kind == "op" and token.text == "~" for token in tokens):
        # OpenFormula's union of references is Excel's comma inside
        # parentheses: "A1:A2~C1" is "(A1:A2,C1)".
        return to_file_formula(_parenthesize_unions(formula, tokens))
    out, last = [], 0
    for token in tokens:
        if token.kind == "func" and token.text.upper() in formula_names.FUTURE_FUNCTIONS:
            name = token.text.upper()
            out.append(formula[last:token.start])
            out.append(formula_names.FUTURE_FUNCTIONS[name] + name)
            last = token.end
    out.append(formula[last:])
    return "".join(out)


def _parenthesize_unions(formula: str, tokens: List[Token]) -> str:
    """``formula`` with each chain of references joined by "~" written as
    Excel's union: in parentheses, joined by commas."""
    starts, ends = set(), set()
    i = 0
    while i < len(tokens):
        if tokens[i].kind == "op" and tokens[i].text == "~" and i > 0:
            first = i - 1
            last = i + 1
            while last + 2 < len(tokens) and tokens[last + 1].kind == "op" and \
                    tokens[last + 1].text == "~":
                last += 2
            starts.add(tokens[first].start)
            ends.add(tokens[last].end)
            i = last + 1
        else:
            i += 1
    unions = {t.start for t in tokens if t.kind == "op" and t.text == "~"}
    out = []
    for position, ch in enumerate(formula):
        if position in ends:
            out.append(")")
        if position in starts:
            out.append("(")
        out.append("," if position in unions else ch)
    if len(formula) in ends:
        out.append(")")
    return "".join(out)


def uses_spilling_function(formula: str) -> Optional[str]:
    """The name of a function in ``formula`` whose result spills over
    several cells (SORT, FILTER, ...), or None."""
    try:
        tokens = tokenize(formula)
    except FormulaSyntaxError:
        return None
    for token in tokens:
        if token.kind == "func" and _bare_name(token.text) in formula_names.SPILLING_FUNCTIONS:
            return _bare_name(token.text)
    return None


# --------------------------------------------------------------------------- #
# OpenFormula (OpenDocument spreadsheets)                                     #
# --------------------------------------------------------------------------- #

# Functions LibreOffice writes with a prefix in OpenDocument files.
_OF_PREFIXED = {"CONCAT", "TEXTJOIN", "IFS", "SWITCH", "MAXIFS", "MINIFS", "XLOOKUP",
                "XMATCH", "IFNA", "FILTER", "SORT", "UNIQUE", "SEQUENCE", "LET"}
_OF_PREFIXED.discard("IFNA")    # IFNA is plain OpenFormula


def to_openformula(formula: str) -> str:
    """Excel text "=SUM(A1:A3,Sheet2!B1)" as OpenFormula
    "of:=SUM([.A1:.A3];[$Sheet2.B1])"."""
    try:
        tokens = tokenize(formula)
    except FormulaSyntaxError:
        return "of:" + formula
    out, last = [], 0
    body_start = 1 if formula.startswith("=") else 0
    out.append("of:=")
    last = body_start
    in_array = False
    for i, token in enumerate(tokens):
        out.append(formula[last:token.start])
        last = token.end
        if token.kind == "lb":
            in_array = True
            out.append(token.text)
        elif token.kind == "rb":
            in_array = False
            out.append(token.text)
        elif token.kind == "ref":
            out.append(_ref_to_of(token.text))
        elif token.kind == "ref3d":
            out.append(_ref3d_to_of(token.text))
        elif token.kind == "ext":
            out.append(_external_to_of(token.text))
        elif token.kind == "isect":
            out.append("!")
        elif token.kind == "sep":
            # In an array constant OpenFormula separates columns with ";"
            # and rows with "|"; Excel with "," and ";".
            out.append(("|" if token.text == ";" else ";") if in_array else ";")
        elif token.kind == "func":
            name = token.text
            upper = name.upper()
            for prefix in ("_XLFN._XLWS.", "_XLFN.", "_XLWS."):
                if upper.startswith(prefix):
                    upper = upper[len(prefix):]
                    name = name[len(prefix):]
            if upper in _OF_PREFIXED:
                name = "COM.MICROSOFT." + upper
            out.append(name)
        elif token.kind == "name" and token.text.upper() in ("TRUE", "FALSE"):
            out.append(token.text.upper() + "()")
        else:
            out.append(token.text)
    out.append(formula[last:])
    return "".join(out)


def _of_sheet(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'" \
        if not re.fullmatch("[A-Za-z0-9_\u00C0-\uFFFF]+", sheet) else sheet


def _ref3d_to_of(text: str) -> str:
    """"Jan:Dec!A1:B2" -> "[$Jan.A1:$Dec.B2]"."""
    prefix, _bang, body = text.rpartition("!")
    if prefix.startswith("'"):
        prefix = prefix[1:-1].replace("''", "'")
    first, _colon, last = prefix.partition(":")
    start, _c, end = body.partition(":")
    return f"[${_of_sheet(first)}.{start}:${_of_sheet(last)}.{end or start}]"


def _external_to_of(text: str) -> str:
    """"'file:///C:/x/[Book.ods]Data'!A1" -> "['file:///C:/x/Book.ods'#$Data.A1]".
    Excel's numbered form ("[1]Sheet!A1") has no OpenFormula spelling and
    stays as it is."""
    match = re.fullmatch(r"'((?:[^'\[]|'')*)\[((?:[^'\]]|'')*)\]((?:[^']|'')*)'!(.+)", text)
    if not match:
        return text
    folder, book, sheet, body = (g.replace("''", "'") for g in match.groups())
    start, _c, end = body.partition(":")
    target = f"${_of_sheet(sheet)}.{start}" + (f":.{end}" if end else "")
    return "['" + (folder + book).replace("'", "''") + "'#" + target + "]"


def _ref_to_of(text: str) -> str:
    sheet, body = _split_sheet(text)
    prefix = _sheet_prefix(text)
    if sheet is not None and prefix is not None:
        sheet_part = "$" + (prefix if prefix.startswith("'") else
                            ("'" + sheet.replace("'", "''") + "'"
                             if not re.fullmatch(r"[A-Za-z0-9_À-￿]+", sheet) else sheet))
    else:
        sheet_part = ""
    if body == "#REF!":
        # OpenFormula's own spelling of a reference that is gone.
        return f"[{sheet_part}.#REF!]" if sheet_part else "[#REF!]"
    first, colon, second = body.partition(":")
    if colon:
        return f"[{sheet_part}.{first}:.{second}]"
    return f"[{sheet_part}.{first}]"


_OF_REF = re.compile(r"\[([^\]]*)\]")


def from_openformula(formula: str) -> str:
    """OpenFormula text ("of:=SUM([.A1:.A3])") as Excel text
    ("=SUM(A1:A3)"). Text written in Excel's own syntax ("msoxl:=...")
    comes back without its namespace."""
    text = formula
    if text.startswith("msoxl:"):
        return text[len("msoxl:"):]
    if text.startswith("of:"):
        text = text[3:]
    elif re.match(r"^[a-z]+:=", text):
        text = text.split(":", 1)[1]
    if not text.startswith("="):
        text = "=" + text
    out = []
    i = 0
    in_array = False
    while i < len(text):
        ch = text[i]
        if ch == '"':
            end = i + 1
            while end < len(text):
                if text[end] == '"':
                    if end + 1 < len(text) and text[end + 1] == '"':
                        end += 2
                        continue
                    break
                end += 1
            out.append(text[i:end + 1])
            i = end + 1
        elif ch == "[" and out and out[-1] and (out[-1][-1].isalnum() or out[-1][-1] in "_."):
            # A table's structured reference ("Table1[Amount]"): as it is.
            end = _matching_bracket(text, i)
            out.append(text[i:end + 1])
            i = end + 1
        elif ch == "[":
            end = i + 1
            in_quote = False
            while end < len(text) and (in_quote or text[end] != "]"):
                if text[end] == "'":
                    in_quote = not in_quote
                end += 1
            out.append(_of_ref_to_excel(text[i + 1:end]))
            i = end + 1
        elif ch == "{":
            in_array = True
            out.append(ch)
            i += 1
        elif ch == "}":
            in_array = False
            out.append(ch)
            i += 1
        elif ch == ";":
            out.append(",")
            i += 1
        elif ch == "|" and in_array:
            out.append(";")
            i += 1
        elif ch == "!":
            # Intersection of two references: a space in Excel's spelling.
            out.append(" ")
            i += 1
        else:
            match = re.match(r"[A-Za-z_][A-Za-z0-9_.]*", text[i:])
            if match:
                word = match.group(0)
                after = text[i + len(word):]
                upper = word.upper()
                if upper.startswith("COM.MICROSOFT."):
                    word = word[len("COM.MICROSOFT."):]
                if upper in ("TRUE", "FALSE") and after.startswith("()"):
                    out.append(upper)
                    i += len(match.group(0)) + 2
                    continue
                out.append(word)
                i += len(match.group(0))
            else:
                out.append(ch)
                i += 1
    return "".join(out)


def _matching_bracket(text: str, start: int) -> int:
    """Where the bracket opened at ``start`` closes (an apostrophe escapes
    a bracket, as in a table's column names)."""
    depth, i = 0, start
    while i < len(text):
        ch = text[i]
        if ch == "'":
            i += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text) - 1


def _of_ref_to_excel(inner: str) -> str:
    """"$Sheet2.A1:.B2" -> "Sheet2!A1:B2"; ".A1" -> "A1"; "$Jan.A1:$Dec.A1"
    -> "Jan:Dec!A1"; another file's "'file:///x/B.ods'#$Data.A1" ->
    "'file:///x/[B.ods]Data'!A1"."""
    external = re.fullmatch(r"'((?:[^']|'')*)'#(.*)", inner.strip())
    if external:
        url = external.group(1).replace("''", "'")
        folder, _slash, book = url.rpartition("/")
        local = _of_ref_to_excel(external.group(2))
        sheet_part, bang, body = local.rpartition("!")
        if not bang:
            return local
        sheet = sheet_part[1:-1].replace("''", "'") if sheet_part.startswith("'") else sheet_part
        return "'" + (f"{folder}/" if _slash else "") .replace("'", "''") + \
            "[" + book.replace("'", "''") + "]" + sheet.replace("'", "''") + "'!" + body
    parts = _split_of_range(inner)
    sheets = [_split_of_cell(part)[0] for part in parts]
    if len(parts) == 2 and sheets[0] and sheets[1] and \
            sheets[0].casefold() != sheets[1].casefold() and "#REF!" not in inner:
        first, last = _split_of_cell(parts[0]), _split_of_cell(parts[1])
        body = first[1] if first[1] == last[1] else f"{first[1]}:{last[1]}"
        return f"{quote_sheet_range(sheets[0], sheets[1])}!{body}"
    sheet = None
    cells = []
    for part in parts:
        part_sheet, cell = _split_of_cell(part)
        if part_sheet is not None and sheet is None:
            sheet = part_sheet
        cells.append(cell)
    body = ":".join(cells)
    if "#REF!" in body:
        body = "#REF!"
    if sheet is None:
        return body
    if sheet == "#REF!":
        return "#REF!"
    return f"{quote_sheet_name(sheet)}!{body}"


def _split_of_range(inner: str) -> List[str]:
    parts, current, in_quote = [], "", False
    for ch in inner:
        if ch == "'":
            in_quote = not in_quote
        if ch == ":" and not in_quote:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    return parts


def _split_of_cell(part: str) -> Tuple[Optional[str], str]:
    """"$'My Sheet'.$A$1" -> ("My Sheet", "$A$1"); ".A1" -> (None, "A1")."""
    part = part.strip()
    if part.startswith("$"):
        part = part[1:]
    if part.startswith("'"):
        end = 1
        while True:
            end = part.index("'", end)
            if end + 1 < len(part) and part[end + 1] == "'":
                end += 2
                continue
            break
        sheet = part[1:end].replace("''", "'")
        rest = part[end + 1:]
        return sheet, rest[1:] if rest.startswith(".") else rest
    head, dot, tail = part.rpartition(".")
    if not dot:
        return None, part
    return (head or None), tail


# --------------------------------------------------------------------------- #
# References inside OpenDocument attributes                                   #
# --------------------------------------------------------------------------- #

def _excel_ref_to_of_inner(excel: str) -> str:
    """One reference in Excel's spelling as OpenDocument writes it inside
    brackets or in an address attribute ("Data!A1:B2" -> "$Data.A1:.B2")."""
    if excel == "#REF!":
        return "#REF!"
    try:
        tokens = tokenize("=" + excel)
    except FormulaSyntaxError:
        return excel
    if len(tokens) != 1:
        return excel
    token = tokens[0]
    if token.kind == "ref":
        written = _ref_to_of(token.text)
    elif token.kind == "ref3d":
        written = _ref3d_to_of(token.text)
    elif token.kind == "ext":
        written = _external_to_of(token.text)
    else:
        return excel
    return written[1:-1] if written.startswith("[") and written.endswith("]") else written


def rewrite_openformula_references(text: str, change: Callable[[str], str]) -> str:
    """OpenFormula text (a formula, a validation or conditional-format
    condition) with each bracketed reference passed through ``change``,
    which takes and returns one reference in Excel's spelling. Text in
    quotes and table references stay as they are."""
    out, i, n, changed = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if ch == '"':
            end = i + 1
            while end < n:
                if text[end] == '"':
                    if end + 1 < n and text[end + 1] == '"':
                        end += 2
                        continue
                    break
                end += 1
            out.append(text[i:end + 1])
            i = end + 1
            continue
        if ch == "[":
            end = _matching_bracket(text, i) if (out and out[-1] and (
                out[-1][-1].isalnum() or out[-1][-1] in "_.")) else _reference_end(text, i)
            inner = text[i + 1:end]
            if out and out[-1] and (out[-1][-1].isalnum() or out[-1][-1] in "_."):
                out.append(text[i:end + 1])
            else:
                excel = _of_ref_to_excel(inner)
                new = change(excel)
                if new != excel:
                    out.append("[" + _excel_ref_to_of_inner(new) + "]")
                    changed = True
                else:
                    out.append(text[i:end + 1])
            i = end + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out) if changed else text


def _reference_end(text: str, start: int) -> int:
    """Where an OpenFormula reference opened at ``start`` closes (a quoted
    sheet name may hold a bracket)."""
    end, in_quote = start + 1, False
    while end < len(text) and (in_quote or text[end] != "]"):
        if text[end] == "'":
            in_quote = not in_quote
        end += 1
    return end


def split_openformula_addresses(value: str) -> List[str]:
    """An OpenDocument range list ("$S.A1:.B2 $'My Sheet'.C3") as its
    addresses: split at spaces outside quoted sheet names."""
    parts, current, in_quote = [], [], False
    for ch in value:
        if ch == "'":
            in_quote = not in_quote
        if ch == " " and not in_quote:
            if current:
                parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def rewrite_openformula_addresses(value: str, change: Callable[[str], str]) -> str:
    """An OpenDocument address or range list with each address passed
    through ``change`` (one reference in Excel's spelling in and out)."""
    parts = split_openformula_addresses(value)
    moved, changed = [], False
    for address in parts:
        excel = _of_ref_to_excel(address)
        new = change(excel)
        if new != excel:
            moved.append(_excel_ref_to_of_inner(new))
            changed = True
        else:
            moved.append(address)
    return " ".join(moved) if changed else value


# --------------------------------------------------------------------------- #
# Values                                                                      #
# --------------------------------------------------------------------------- #

_EPOCH_1900 = _dt.datetime(1899, 12, 30)
_EPOCH_1904 = _dt.datetime(1904, 1, 1)


def to_serial(value, date1904: bool = False) -> float:
    """A datetime, date or time as Excel's serial number."""
    if isinstance(value, _dt.datetime):
        moment = value.replace(tzinfo=None)
    elif isinstance(value, _dt.date):
        moment = _dt.datetime(value.year, value.month, value.day)
    elif isinstance(value, _dt.time):
        return (value.hour * 3600 + value.minute * 60 + value.second
                + value.microsecond / 1e6) / 86400
    elif isinstance(value, _dt.timedelta):
        return value.total_seconds() / 86400
    else:
        raise TypeError(value)
    epoch = _EPOCH_1904 if date1904 else _EPOCH_1900
    delta = moment - epoch
    serial = delta.days + delta.seconds / 86400 + delta.microseconds / 86400e6
    if not date1904 and serial < 61:
        # Excel counts a 29 February 1900 that never was.
        serial -= 1
    return serial if serial % 1 else int(serial)


def from_serial(serial: float, date1904: bool = False) -> _dt.datetime:
    """Excel's serial number as a datetime."""
    epoch = _EPOCH_1904 if date1904 else _EPOCH_1900
    if not date1904 and serial < 61:
        serial += 1
    days = math.floor(serial)
    seconds = round((serial - days) * 86400)
    return epoch + _dt.timedelta(days=days, seconds=seconds)


def number_text(value: float) -> str:
    """A number as text the way a formula turns it into text (=1/3&""):
    up to 15 significant digits, no trailing zeros."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"
    if math.isinf(value) or math.isnan(value):
        return "#NUM!"
    text = f"{value:.15g}"
    if "e" in text:
        mantissa, exponent = text.split("e")
        exp = int(exponent)
        if -20 < exp < 21:
            text = f"{Decimal(text):f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return text
        return f"{mantissa}E{'+' if exp >= 0 else '-'}{abs(exp):02d}"
    return text


def _clean(number: float):
    """Whole numbers as int, so =2*3 shows 6, not 6.0."""
    if isinstance(number, float) and number.is_integer() and abs(number) < 1e15:
        return int(number)
    return number


_NUMBER_TEXT = re.compile(r"^\s*([+-]?)(\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?\s*(%?)\s*$")


def text_to_number(text: str) -> Optional[float]:
    """Text that reads as a number ("3", " 1.5e3 ", "50%", "1,000"), or None."""
    cleaned = text.replace(",", "") if re.fullmatch(r"\s*[+-]?\d{1,3}(,\d{3})+(\.\d*)?\s*", text) \
        else text
    match = _NUMBER_TEXT.match(cleaned)
    if not match:
        return None
    try:
        number = float(cleaned.strip().rstrip("%").strip())
    except ValueError:
        return None
    if match.group(4):
        number /= 100
    return number


class Area:
    """A block of cells as a formula sees it: ``rows`` x ``cols`` values,
    read when asked for."""

    __slots__ = ("engine", "uid", "r1", "c1", "rows", "cols")

    def __init__(self, engine: "Engine", uid, r1: int, c1: int, rows: int, cols: int) -> None:
        self.engine, self.uid, self.r1, self.c1 = engine, uid, r1, c1
        self.rows, self.cols = max(rows, 0), max(cols, 0)

    def get(self, i: int, j: int):
        return self.engine.value(self.uid, self.r1 + i, self.c1 + j)

    def values(self) -> Iterator:
        """Every value, row by row; empty cells as None."""
        engine, uid = self.engine, self.uid
        cells = engine.book.cells_of(uid)
        for r in range(self.r1, self.r1 + self.rows):
            for c in range(self.c1, self.c1 + self.cols):
                if (r, c) in cells:
                    yield engine.value(uid, r, c)
                else:
                    yield None

    def present(self) -> Iterator:
        """The values of the cells that hold something (blanks skipped),
        cheap for whole columns over a sparse sheet."""
        engine, uid = self.engine, self.uid
        cells = engine.book.cells_of(uid)
        size = self.rows * self.cols
        if size > len(cells) * 2:
            keys = sorted(k for k in cells
                          if self.r1 <= k[0] < self.r1 + self.rows
                          and self.c1 <= k[1] < self.c1 + self.cols)
            for r, c in keys:
                yield engine.value(uid, r, c)
        else:
            for value in self.values():
                if value is not None:
                    yield value

    def column(self, j: int) -> List:
        return [self.get(i, j) for i in range(self.rows)]

    def row(self, i: int) -> List:
        return [self.get(i, j) for j in range(self.cols)]


# --------------------------------------------------------------------------- #
# Calculation                                                                 #
# --------------------------------------------------------------------------- #

class _Context:
    __slots__ = ("engine", "uid", "row", "col", "sheet_name")

    def __init__(self, engine, uid, row, col, sheet_name):
        self.engine, self.uid, self.row, self.col = engine, uid, row, col
        self.sheet_name = sheet_name


def _ensure(value):
    """Raise an error value; return anything else."""
    if isinstance(value, CellError):
        raise _Raise(value)
    return value


def _scalar(value, ctx: _Context):
    """A value as one value: a block gives the cell in the formula's own
    row or column (Excel's implicit intersection), or #VALUE!."""
    if isinstance(value, Area):
        if value.rows == 1 and value.cols == 1:
            return value.get(0, 0)
        if value.cols == 1 and value.r1 <= ctx.row < value.r1 + value.rows:
            return value.get(ctx.row - value.r1, 0)
        if value.rows == 1 and value.c1 <= ctx.col < value.c1 + value.cols:
            return value.get(0, ctx.col - value.c1)
        return VALUE
    return value


def _number(value, ctx: _Context) -> float:
    value = _ensure(_scalar(value, ctx))
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        return to_serial(value, ctx.engine.date1904)
    if isinstance(value, str):
        number = text_to_number(value)
        if number is None:
            number = _date_text_to_serial(value, ctx.engine.date1904)
        if number is None:
            raise _Raise(VALUE)
        return number
    raise _Raise(VALUE)


_DATE_TEXT = re.compile(r"^\s*(?:(\d{4})-(\d{1,2})-(\d{1,2}))?(?:[ T]?(\d{1,2}):(\d{2})"
                        r"(?::(\d{2}))?)?\s*$")


def _date_text_to_serial(text: str, date1904: bool) -> Optional[float]:
    """A date or time written as text ("2026-03-04", "2026-03-04 14:30",
    "14:30") as its serial number, as Excel reads such text in arithmetic
    and in date functions; None for other text."""
    match = _DATE_TEXT.match(text)
    if not match or not text.strip():
        return None
    year, month, day, hour, minute, second = match.groups()
    if year is None and hour is None:
        return None
    try:
        clock = _dt.time(int(hour or 0), int(minute or 0), int(second or 0))
        if year is None:
            return to_serial(clock)
        moment = _dt.datetime.combine(_dt.date(int(year), int(month), int(day)), clock)
    except ValueError:
        return None
    return to_serial(moment, date1904)


def _text(value, ctx: _Context) -> str:
    value = _ensure(_scalar(value, ctx))
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return number_text(value)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        return number_text(to_serial(value, ctx.engine.date1904))
    return str(value)


def _bool(value, ctx: _Context) -> bool:
    value = _ensure(_scalar(value, ctx))
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        if value.upper() == "TRUE":
            return True
        if value.upper() == "FALSE":
            return False
        raise _Raise(VALUE)
    return bool(_number(value, ctx))


def _kind_rank(value) -> int:
    if isinstance(value, bool):
        return 2
    if isinstance(value, str):
        return 1
    return 0


def round15(number):
    """A number to Excel's precision of 15 significant digits, the way
    Excel compares and shows numbers (=0.1+0.2=0.3 is TRUE)."""
    if isinstance(number, float) and math.isfinite(number) and number != 0:
        return float(f"{number:.15g}")
    return number


def same_value(a, b) -> bool:
    """Whether two cell values are the same as Excel sees them."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round15(a) == round15(b)
    return type(a) is type(b) and a == b


def _compare(a, b) -> int:
    """Excel's order: numbers < text < logical values; text ignores case;
    an empty cell is 0, "" or FALSE, whichever the other side is."""
    if a is None:
        a = "" if isinstance(b, str) else (False if isinstance(b, bool) else 0)
    if b is None:
        b = "" if isinstance(a, str) else (False if isinstance(a, bool) else 0)
    ka, kb = _kind_rank(a), _kind_rank(b)
    if ka != kb:
        return -1 if ka < kb else 1
    if ka == 1:
        a, b = a.casefold(), b.casefold()
    elif ka == 0:
        a, b = round15(a), round15(b)
    return (a > b) - (a < b)


def _normal(value, ctx):
    """A single value ready to compare: dates as serial numbers."""
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        return to_serial(value, ctx.engine.date1904)
    return value


def _flatten(args, ctx: _Context):
    """Numbers from arguments as SUM reads them: in a block, only
    numbers count (text, logical values and blanks are skipped); typed
    directly, logical values and numeric text count too. Errors raise."""
    for arg in args:
        if isinstance(arg, Area):
            for value in arg.present():
                if isinstance(value, CellError):
                    raise _Raise(value)
                if isinstance(value, bool) or isinstance(value, str):
                    continue
                if isinstance(value, (int, float)):
                    yield value
                elif isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
                    yield to_serial(value, ctx.engine.date1904)
        else:
            if arg is None:
                continue
            yield _number(arg, ctx)


def _round_decimal(number: float, digits: int, mode) -> float:
    try:
        quantum = Decimal(1).scaleb(-digits)
        result = Decimal(repr(number)).quantize(quantum, rounding=mode)
    except InvalidOperation:
        raise _Raise(NUM)
    return _clean(float(result))


_CRITERION = re.compile(r"^(<=|>=|<>|<|>|=)?(.*)$", re.S)


def _wildcard(pattern: str):
    """Excel's wildcards (* ? and ~ to escape) as a regular expression."""
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "~" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.I | re.S)


def _criterion(criterion, ctx: _Context) -> Callable[[object], bool]:
    """COUNTIF's criterion: 5, ">5", "<>x", "a*", "" as a test of a value."""
    criterion = _ensure(_scalar(criterion, ctx))
    if criterion is None:
        criterion = ""
    if isinstance(criterion, bool) or isinstance(criterion, (int, float)):
        target = criterion

        def test(value):
            value = _normal(value, ctx)
            if isinstance(target, bool):
                return isinstance(value, bool) and value == target
            if isinstance(value, str):
                number = text_to_number(value)
                return number is not None and number == target
            return isinstance(value, (int, float)) and not isinstance(value, bool) \
                and value == target
        return test
    text = str(criterion)
    if isinstance(criterion, (_dt.datetime, _dt.date)):
        text = number_text(to_serial(criterion, ctx.engine.date1904))
    operator, operand = _CRITERION.match(text).groups()
    operator = operator or "="
    number = text_to_number(operand) if operand.strip() else None
    upper = operand.upper()
    boolean = True if upper == "TRUE" else False if upper == "FALSE" else None
    error = ERROR_CODES.get(upper)

    def test(value):
        value = _normal(value, ctx)
        if error is not None:
            same = value == error
            return same if operator == "=" else (not same if operator == "<>" else False)
        if isinstance(value, CellError):
            return False
        if operand == "" and operator in ("=", "<>"):
            empty = value is None or value == ""
            return empty if operator == "=" else not empty
        if boolean is not None:
            if not isinstance(value, bool):
                return operator == "<>"
            result = _compare(value, boolean)
        elif number is not None:
            if isinstance(value, bool) or value is None:
                return operator == "<>"
            if isinstance(value, str):
                value_number = text_to_number(value)
                if value_number is None or operator not in ("=", "<>"):
                    return operator == "<>"
                value = value_number
            result = (value > number) - (value < number)
        else:
            if operator in ("=", "<>"):
                if value is None:
                    matches = False
                elif not isinstance(value, str):
                    matches = False
                else:
                    matches = bool(_wildcard(operand).match(value))
                return matches if operator == "=" else not matches
            if not isinstance(value, str):
                return False
            result = _compare(value, operand)
        return {"=": result == 0, "<>": result != 0, "<": result < 0, ">": result > 0,
                "<=": result <= 0, ">=": result >= 0}[operator]
    return test


def _as_area(value, ctx: _Context) -> Area:
    if isinstance(value, Area):
        return value
    raise _Raise(VALUE)


def _lookup_equal(target, value) -> bool:
    if isinstance(target, str) and isinstance(value, str):
        if any(ch in target for ch in "*?~"):
            return bool(_wildcard(target).match(value))
        return target.casefold() == value.casefold()
    if value is None:
        return False
    return _kind_rank(target) == _kind_rank(value) and _compare(target, value) == 0


def _match_position(target, values: Sequence, mode: int) -> Optional[int]:
    """MATCH's position (0-based) of ``target`` in ``values``."""
    if mode == 0:
        for i, value in enumerate(values):
            if _lookup_equal(target, value):
                return i
        return None
    found = None
    for i, value in enumerate(values):
        if value is None or isinstance(value, CellError) or _kind_rank(value) != _kind_rank(target):
            continue
        order = _compare(value, target)
        if mode > 0:
            if order <= 0:
                found = i
            else:
                break
        else:
            if order >= 0:
                found = i
            else:
                break
    return found


# Functions are called with their argument trees and the context, so IF
# and IFERROR calculate only the branch they need.

_FUNCTIONS: Dict[str, Callable] = {}
_VOLATILE = {"TODAY", "NOW", "RAND", "RANDBETWEEN"}


def _function(*names, lazy=False):
    def register(fn):
        for name in names:
            _FUNCTIONS[name] = (fn, lazy)
        return fn
    return register


def _arity(args, low: int, high: Optional[int] = None) -> None:
    if len(args) < low or (high is not None and len(args) > high):
        raise _Raise(VALUE)


@_function("SUM")
def _sum(args, ctx):
    return _clean(math.fsum(_flatten(args, ctx)))


@_function("PRODUCT")
def _product(args, ctx):
    result = 1.0
    seen = False
    for number in _flatten(args, ctx):
        result *= number
        seen = True
    return _clean(result) if seen else 0


@_function("AVERAGE")
def _average(args, ctx):
    numbers = list(_flatten(args, ctx))
    if not numbers:
        return DIV0
    return _clean(math.fsum(numbers) / len(numbers))


@_function("MIN")
def _min(args, ctx):
    numbers = list(_flatten(args, ctx))
    return _clean(min(numbers)) if numbers else 0


@_function("MAX")
def _max(args, ctx):
    numbers = list(_flatten(args, ctx))
    return _clean(max(numbers)) if numbers else 0


@_function("COUNT")
def _count(args, ctx):
    count = 0
    for arg in args:
        if isinstance(arg, Area):
            for value in arg.present():
                if isinstance(value, (int, float, _dt.date, _dt.time, _dt.timedelta)) and \
                        not isinstance(value, bool):
                    count += 1
        elif isinstance(arg, (int, float)):
            count += 1
        elif isinstance(arg, str) and text_to_number(arg) is not None:
            count += 1
    return count


@_function("COUNTA")
def _counta(args, ctx):
    count = 0
    for arg in args:
        if isinstance(arg, Area):
            count += sum(1 for value in arg.present() if value is not None)
        elif arg is not None:
            count += 1
    return count


@_function("COUNTBLANK")
def _countblank(args, ctx):
    _arity(args, 1, 1)
    area = _as_area(args[0], ctx)
    return sum(1 for value in area.values() if value is None or value == "")


def _paired(args, ctx, low: int):
    _arity(args, low, 3)
    area = _as_area(args[0], ctx)
    test = _criterion(args[1], ctx)
    target = area
    if len(args) == 3 and args[2] is not None:
        given = _as_area(args[2], ctx)
        # Excel takes the sum range's top left cell and the criteria
        # range's shape.
        target = Area(given.engine, given.uid, given.r1, given.c1, area.rows, area.cols)
    return area, test, target


@_function("COUNTIF")
def _countif(args, ctx):
    _arity(args, 2, 2)
    area = _as_area(args[0], ctx)
    test = _criterion(args[1], ctx)
    return sum(1 for value in area.values() if test(value))


@_function("SUMIF")
def _sumif(args, ctx):
    area, test, target = _paired(args, ctx, 2)
    total = []
    for i in range(area.rows):
        for j in range(area.cols):
            if test(area.get(i, j)):
                value = target.get(i, j)
                if isinstance(value, CellError):
                    return value
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    total.append(value)
                elif isinstance(value, (_dt.date, _dt.time)):
                    total.append(to_serial(value, ctx.engine.date1904))
    return _clean(math.fsum(total))


@_function("AVERAGEIF")
def _averageif(args, ctx):
    area, test, target = _paired(args, ctx, 2)
    numbers = []
    for i in range(area.rows):
        for j in range(area.cols):
            if test(area.get(i, j)):
                value = target.get(i, j)
                if isinstance(value, CellError):
                    return value
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numbers.append(value)
    if not numbers:
        return DIV0
    return _clean(math.fsum(numbers) / len(numbers))


@_function("IF", lazy=True)
def _if(args, ctx):
    _arity(args, 1, 3)
    condition = _bool(ctx.engine._eval(args[0], ctx), ctx)
    if condition:
        return ctx.engine._eval(args[1], ctx) if len(args) > 1 else True
    if len(args) > 2:
        return ctx.engine._eval(args[2], ctx)
    return False


@_function("IFERROR", lazy=True)
def _iferror(args, ctx):
    _arity(args, 2, 2)
    try:
        value = _scalar(ctx.engine._eval(args[0], ctx), ctx) \
            if not _is_ref_node(args[0]) else ctx.engine._eval(args[0], ctx)
    except _Raise as raised:
        value = raised.error
    if isinstance(value, CellError):
        return ctx.engine._eval(args[1], ctx)
    return value


def _is_ref_node(node) -> bool:
    return node[0] == "ref" and node[1] is not None and not node[1].is_cell


def _logicals(args, ctx):
    found = []
    for arg in args:
        if isinstance(arg, Area):
            for value in arg.present():
                if isinstance(value, CellError):
                    raise _Raise(value)
                if isinstance(value, bool):
                    found.append(value)
                elif isinstance(value, (int, float)):
                    found.append(value != 0)
        elif arg is None:
            continue
        else:
            found.append(_bool(arg, ctx))
    if not found:
        raise _Raise(VALUE)
    return found


@_function("AND")
def _and(args, ctx):
    return all(_logicals(args, ctx))


@_function("OR")
def _or(args, ctx):
    return any(_logicals(args, ctx))


@_function("NOT")
def _not(args, ctx):
    _arity(args, 1, 1)
    return not _bool(args[0], ctx)


def _make_round(mode):
    def fn(args, ctx):
        _arity(args, 1, 2)
        number = _number(args[0], ctx)
        digits = int(_number(args[1], ctx)) if len(args) > 1 else 0
        return _round_decimal(number, digits, mode)
    return fn


_FUNCTIONS["ROUND"] = (_make_round(ROUND_HALF_UP), False)
_FUNCTIONS["ROUNDUP"] = (_make_round(ROUND_UP), False)
_FUNCTIONS["ROUNDDOWN"] = (_make_round(ROUND_DOWN), False)


@_function("INT")
def _int(args, ctx):
    _arity(args, 1, 1)
    return int(math.floor(_number(args[0], ctx)))


@_function("ABS")
def _abs(args, ctx):
    _arity(args, 1, 1)
    return _clean(abs(_number(args[0], ctx)))


@_function("MOD")
def _mod(args, ctx):
    _arity(args, 2, 2)
    number, divisor = _number(args[0], ctx), _number(args[1], ctx)
    if divisor == 0:
        return DIV0
    return _clean(number - divisor * math.floor(number / divisor))


def _power_of(base: float, exponent: float):
    if base == 0 and exponent == 0:
        return NUM
    if base == 0 and exponent < 0:
        return DIV0
    try:
        result = math.pow(base, exponent)
    except (OverflowError, ValueError):
        return NUM
    if isinstance(result, complex) or math.isinf(result) or math.isnan(result):
        return NUM
    return _clean(result)


@_function("POWER")
def _power(args, ctx):
    _arity(args, 2, 2)
    return _power_of(_number(args[0], ctx), _number(args[1], ctx))


@_function("SQRT")
def _sqrt(args, ctx):
    _arity(args, 1, 1)
    number = _number(args[0], ctx)
    if number < 0:
        return NUM
    return _clean(math.sqrt(number))


def _texts(args, ctx):
    for arg in args:
        if isinstance(arg, Area):
            for value in arg.values():
                if isinstance(value, CellError):
                    raise _Raise(value)
                yield _text(value, ctx)
        else:
            yield _text(arg, ctx)


@_function("CONCAT")
def _concat(args, ctx):
    return "".join(_texts(args, ctx))


@_function("CONCATENATE")
def _concatenate(args, ctx):
    return "".join(_text(arg, ctx) for arg in args)


@_function("TEXTJOIN")
def _textjoin(args, ctx):
    _arity(args, 3)
    delimiter = _text(args[0], ctx)
    ignore_empty = _bool(args[1], ctx)
    parts = [part for part in _texts(args[2:], ctx) if part != "" or not ignore_empty]
    return delimiter.join(parts)


@_function("LEN")
def _len(args, ctx):
    _arity(args, 1, 1)
    return len(_text(args[0], ctx))


def _left(args, ctx):
    _arity(args, 1, 2)
    text = _text(args[0], ctx)
    count = int(_number(args[1], ctx)) if len(args) > 1 and args[1] is not None else 1
    if count < 0:
        return VALUE
    return text[:count]


def _right(args, ctx):
    _arity(args, 1, 2)
    text = _text(args[0], ctx)
    count = int(_number(args[1], ctx)) if len(args) > 1 and args[1] is not None else 1
    if count < 0:
        return VALUE
    return text[len(text) - count:] if count else ""


_FUNCTIONS["LEFT"] = (_left, False)
_FUNCTIONS["RIGHT"] = (_right, False)


@_function("MID")
def _mid(args, ctx):
    _arity(args, 3, 3)
    text = _text(args[0], ctx)
    start, count = int(_number(args[1], ctx)), int(_number(args[2], ctx))
    if start < 1 or count < 0:
        return VALUE
    return text[start - 1:start - 1 + count]


@_function("UPPER")
def _upper(args, ctx):
    _arity(args, 1, 1)
    return _text(args[0], ctx).upper()


@_function("LOWER")
def _lower(args, ctx):
    _arity(args, 1, 1)
    return _text(args[0], ctx).lower()


@_function("TRIM")
def _trim(args, ctx):
    _arity(args, 1, 1)
    return re.sub(" +", " ", _text(args[0], ctx).strip(" "))


@_function("SUBSTITUTE")
def _substitute(args, ctx):
    _arity(args, 3, 4)
    text, old, new = _text(args[0], ctx), _text(args[1], ctx), _text(args[2], ctx)
    if not old:
        return text
    if len(args) == 4:
        instance = int(_number(args[3], ctx))
        if instance < 1:
            return VALUE
        index = -1
        for _n in range(instance):
            index = text.find(old, index + 1)
            if index < 0:
                return text
        return text[:index] + new + text[index + len(old):]
    return text.replace(old, new)


@_function("VALUE")
def _value(args, ctx):
    _arity(args, 1, 1)
    value = _ensure(_scalar(args[0], ctx))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if value is None:
        return 0
    text = _text(value, ctx)
    number = text_to_number(text)
    if number is None:
        try:
            moment = _dt.datetime.fromisoformat(text.strip())
        except ValueError:
            return VALUE
        return to_serial(moment, ctx.engine.date1904)
    return _clean(number)


@_function("TODAY")
def _today(args, ctx):
    return to_serial(_dt.date.today(), ctx.engine.date1904)


@_function("NOW")
def _now(args, ctx):
    return to_serial(_dt.datetime.now().replace(microsecond=0), ctx.engine.date1904)


@_function("DATE")
def _date(args, ctx):
    _arity(args, 3, 3)
    year, month, day = (int(math.floor(_number(arg, ctx))) for arg in args)
    if 0 <= year < 1900:
        year += 1900
    if not 0 <= year < 10000:
        return NUM
    month -= 1
    year += month // 12
    month = month % 12 + 1
    try:
        base = _dt.datetime(year, month, 1)
    except ValueError:
        return NUM
    serial = to_serial(base, ctx.engine.date1904) + day - 1
    if serial < 0:
        return NUM
    return serial


def _date_part(args, ctx):
    _arity(args, 1, 1)
    serial = _number(args[0], ctx)
    if serial < 0:
        raise _Raise(NUM)
    if not ctx.engine.date1904 and int(serial) == 60:
        return None   # the 29 February 1900 Excel counts
    return from_serial(serial, ctx.engine.date1904)


@_function("YEAR")
def _year(args, ctx):
    moment = _date_part(args, ctx)
    return 1900 if moment is None else moment.year


@_function("MONTH")
def _month(args, ctx):
    moment = _date_part(args, ctx)
    return 2 if moment is None else moment.month


@_function("DAY")
def _day(args, ctx):
    moment = _date_part(args, ctx)
    return 29 if moment is None else moment.day


def _lookup(args, ctx, *, vertical: bool):
    _arity(args, 3, 4)
    target = _normal(_ensure(_scalar(args[0], ctx)), ctx)
    table = _as_area(args[1], ctx)
    index = int(_number(args[2], ctx))
    approximate = _bool(args[3], ctx) if len(args) > 3 and args[3] is not None else True
    if index < 1:
        return VALUE
    if index > (table.cols if vertical else table.rows):
        return REF
    keys = table.column(0) if vertical else table.row(0)
    keys = [_normal(key, ctx) for key in keys]
    position = _match_position(target, keys, 1 if approximate else 0)
    if position is None:
        return NA
    return table.get(position, index - 1) if vertical else table.get(index - 1, position)


@_function("VLOOKUP")
def _vlookup(args, ctx):
    return _lookup(args, ctx, vertical=True)


@_function("HLOOKUP")
def _hlookup(args, ctx):
    return _lookup(args, ctx, vertical=False)


@_function("INDEX")
def _index(args, ctx):
    _arity(args, 2, 4)
    area = _as_area(args[0], ctx)
    row = int(_number(args[1], ctx)) if args[1] is not None else 0
    col = int(_number(args[2], ctx)) if len(args) > 2 and args[2] is not None else None
    if col is None:
        if area.rows == 1 and area.cols > 1:
            row, col = 1, row
        else:
            col = 1 if area.cols == 1 else 0
    if row < 0 or col < 0 or row > area.rows or col > area.cols:
        return REF
    if row == 0 and col == 0:
        return area
    if row == 0:
        return Area(area.engine, area.uid, area.r1, area.c1 + col - 1, area.rows, 1)
    if col == 0:
        return Area(area.engine, area.uid, area.r1 + row - 1, area.c1, 1, area.cols)
    return Area(area.engine, area.uid, area.r1 + row - 1, area.c1 + col - 1, 1, 1)


@_function("MATCH")
def _match(args, ctx):
    _arity(args, 2, 3)
    target = _normal(_ensure(_scalar(args[0], ctx)), ctx)
    area = _as_area(args[1], ctx)
    mode = int(_number(args[2], ctx)) if len(args) > 2 and args[2] is not None else 1
    if area.rows != 1 and area.cols != 1:
        return NA
    values = [_normal(v, ctx) for v in (area.column(0) if area.cols == 1 else area.row(0))]
    position = _match_position(target, values, max(-1, min(1, mode)))
    return NA if position is None else position + 1


KNOWN_FUNCTIONS = frozenset(_FUNCTIONS)
# Functions that count an error typed into them instead of returning it.
_COUNTING = {"COUNT", "COUNTA"}


def _arith(op: str, a: float, b: float):
    if op == "+":
        return _clean(a + b)
    if op == "-":
        return _clean(a - b)
    if op == "*":
        result = a * b
        if math.isinf(result):
            return NUM
        return _clean(result)
    if op == "/":
        if b == 0:
            return DIV0
        return _clean(a / b)
    return _power_of(a, b)


class Engine:
    """Calculates a workbook's formulas and keeps track of what depends on
    what.

    ``book`` is what the engine reads and writes through:

    - ``sheet_uid(name)``: the id of the sheet called ``name`` (any case),
      or None
    - ``sheet_name(uid)``
    - ``cells_of(uid)``: that sheet's {(row, col): cell} mapping, each cell
      with ``value``, ``formula`` and ``state``
    - ``store_result(uid, row, col, value, state)``: a formula's new result
    - ``defined_name(name, uid)``: the formula text a defined name stands
      for (e.g. "=Sheet1!$A$1:$A$3"), or None
    - ``date1904``: whether the workbook counts days from 1904
    """

    def __init__(self, book) -> None:
        self.book = book
        self.date1904 = bool(getattr(book, "date1904", False))
        self._cell_deps: Dict[tuple, Set[tuple]] = {}
        self._col_areas: Dict[object, Dict[int, List[tuple]]] = {}
        self._wide_areas: Dict[object, List[tuple]] = {}
        self._registered: Dict[tuple, Tuple[list, list]] = {}
        self._volatile: Set[tuple] = set()
        # Formulas whose references the engine cannot see (array
        # constants, other workbooks): a structural change rewrites them
        # always.
        self._opaque: Set[tuple] = set()
        self._dirty: Set[tuple] = set()
        self._evaluating: Set[tuple] = set()
        self._no_cache: Set[tuple] = set()

    # -- dependencies ------------------------------------------------------

    def rebuild(self) -> None:
        """Index every formula of the workbook again."""
        self._cell_deps.clear()
        self._col_areas.clear()
        self._wide_areas.clear()
        self._registered.clear()
        self._volatile.clear()
        self._opaque.clear()
        for uid in self.book.sheet_uids():
            for (row, col), cell in list(self.book.cells_of(uid).items()):
                if cell.formula:
                    self.register((uid, row, col), cell.formula)

    def register(self, key: tuple, formula: Optional[str]) -> None:
        """Index (or, with None, forget) the formula of cell ``key``."""
        self.unregister(key)
        if not formula:
            return
        found = scan(formula)
        cells, areas = [], []
        uid = key[0]
        self._collect_scan(found, uid, cells, areas, depth=0)
        for target in cells:
            self._cell_deps.setdefault(target, set()).add(key)
        for area in areas:
            area_uid, r1, c1, r2, c2 = area
            if c2 - c1 <= 64:
                columns = self._col_areas.setdefault(area_uid, {})
                for c in range(c1, c2 + 1):
                    columns.setdefault(c, {})[(r1, r2, key)] = None
            else:
                self._wide_areas.setdefault(area_uid, {})[(r1, c1, r2, c2, key)] = None
        self._registered[key] = (cells, areas)
        if found.volatile:
            self._volatile.add(key)
        if found.opaque:
            self._opaque.add(key)

    def unregister(self, key: tuple) -> None:
        found = self._registered.pop(key, None)
        self._volatile.discard(key)
        self._opaque.discard(key)
        if not found:
            return
        cells, areas = found
        for target in cells:
            dependents = self._cell_deps.get(target)
            if dependents is not None:
                dependents.discard(key)
                if not dependents:
                    del self._cell_deps[target]
        for area_uid, r1, c1, r2, c2 in areas:
            if c2 - c1 <= 64:
                columns = self._col_areas.get(area_uid, {})
                for c in range(c1, c2 + 1):
                    entries = columns.get(c)
                    if entries:
                        entries.pop((r1, r2, key), None)
            else:
                self._wide_areas.get(area_uid, {}).pop((r1, c1, r2, c2, key), None)

    def _collect_scan(self, found: Scan, uid, cells, areas, depth) -> None:
        for _start, _end, prefix, ref in found.refs:
            if ref is None or ref.error:
                continue
            target = uid if prefix is None else self.book.sheet_uid(ref.sheet)
            if target is None:
                continue
            if ref.is_cell:
                cells.append((target, ref.r1, ref.c1))
            else:
                r1 = 0 if ref.r1 is None else ref.r1
                r2 = MAX_ROWS - 1 if ref.r2 is None else ref.r2
                c1 = 0 if ref.c1 is None else ref.c1
                c2 = MAX_COLS - 1 if ref.c2 is None else ref.c2
                areas.append((target, r1, c1, r2, c2))
        if depth < 8:
            for name in found.names:
                text = self.book.defined_name(name, uid)
                if text:
                    self._collect_scan(scan(text), uid, cells, areas, depth + 1)

    def plan_shift(self, uid, axis: str, at: int, count: int) -> Tuple[Set[tuple], Set[tuple]]:
        """Before rows (``axis`` "row") or columns of sheet ``uid`` are
        inserted (``count`` > 0) or deleted at ``at``: the formulas whose
        value may change (a block grows over new cells, or loses or reads
        deleted ones), and those whose text must change (they refer to a
        place at or past ``at``, or cannot be read here)."""
        changing: Set[tuple] = set()
        rewrite: Set[tuple] = set(self._opaque)
        row_axis = axis == "row"
        band_lo, band_hi = at, at - count - 1
        for key, (cells, areas) in self._registered.items():
            for target, r, c in cells:
                if target != uid:
                    continue
                index = r if row_axis else c
                if index >= at:
                    rewrite.add(key)
                    if count < 0 and index <= band_hi:
                        changing.add(key)
            for target, r1, c1, r2, c2 in areas:
                if target != uid:
                    continue
                lo, hi = (r1, r2) if row_axis else (c1, c2)
                if hi >= at:
                    rewrite.add(key)
                if count > 0:
                    if lo < at <= hi:
                        changing.add(key)
                elif lo <= band_hi and hi >= band_lo:
                    changing.add(key)
        return changing, rewrite

    def dependents(self, key: tuple) -> Set[tuple]:
        """The formulas that read cell ``key`` directly."""
        found = set(self._cell_deps.get(key, ()))
        uid, row, col = key
        for r1, r2, formula in self._col_areas.get(uid, {}).get(col, ()):
            if r1 <= row <= r2:
                found.add(formula)
        for r1, c1, r2, c2, formula in self._wide_areas.get(uid, ()):
            if r1 <= row <= r2 and c1 <= col <= c2:
                found.add(formula)
        return found

    def readers_of_sheet(self, uid) -> Set[tuple]:
        """Every formula that reads anything on sheet ``uid``."""
        found = set()
        for key, (cells, areas) in self._registered.items():
            if any(cell[0] == uid for cell in cells) or any(area[0] == uid for area in areas):
                found.add(key)
        return found

    def formulas(self) -> Set[tuple]:
        return set(self._registered)

    # -- calculating -------------------------------------------------------

    def recalculate(self, changed: Iterable[tuple], *, include_changed: bool = True) -> Set[tuple]:
        """Calculate again what depends on the cells in ``changed`` (and
        those cells themselves when they hold formulas), plus TODAY() and
        NOW(). Returns the formula cells it calculated."""
        dirty: Set[tuple] = set()
        queue = list(changed)
        if include_changed:
            dirty.update(key for key in queue if key in self._registered)
        seen = set(queue)
        while queue:
            key = queue.pop()
            for dependent in self.dependents(key):
                if dependent not in seen:
                    seen.add(dependent)
                    dirty.add(dependent)
                    queue.append(dependent)
        for key in self._volatile:
            if key not in dirty:
                dirty.add(key)
                for dependent in self._closure(key):
                    dirty.add(dependent)
        self._run(dirty)
        return dirty

    def _closure(self, key: tuple) -> Set[tuple]:
        found, queue = set(), [key]
        while queue:
            for dependent in self.dependents(queue.pop()):
                if dependent not in found:
                    found.add(dependent)
                    queue.append(dependent)
        return found

    def calculate_all(self, only: Optional[Iterable[tuple]] = None, *,
                      follow: bool = True) -> Set[tuple]:
        """Calculate every formula (or those in ``only`` and, with
        ``follow``, what depends on them)."""
        keys = set(self._registered) if only is None else set(only)
        if only is not None and follow:
            for key in list(keys):
                keys |= self._closure(key)
        self._run(keys)
        return keys

    def _run(self, dirty: Set[tuple]) -> None:
        if not dirty:
            return
        self._dirty = set(dirty)
        order, circular = self._order(self._dirty)
        for key in circular:
            self._dirty.discard(key)
            self.book.store_result(*key, CIRCULAR, STATE_CIRCULAR)
        for key in order:
            if key in self._dirty:
                self._evaluate_cell(key)
        self._dirty = set()

    def _precedents_in(self, key: tuple, dirty: Set[tuple]) -> List[tuple]:
        cells, areas = self._registered.get(key, ((), ()))
        found = [cell for cell in cells if cell in dirty]
        for area_uid, r1, c1, r2, c2 in areas:
            size = (r2 - r1 + 1) * (c2 - c1 + 1)
            if size <= len(dirty):
                for r in range(r1, r2 + 1):
                    for c in range(c1, c2 + 1):
                        if (area_uid, r, c) in dirty:
                            found.append((area_uid, r, c))
            else:
                found.extend(other for other in dirty
                             if other[0] == area_uid and r1 <= other[1] <= r2
                             and c1 <= other[2] <= c2)
        return found

    def _order(self, dirty: Set[tuple]) -> Tuple[List[tuple], Set[tuple]]:
        """The dirty formulas with every one after those it reads, and the
        ones that read themselves (Tarjan's strongly connected parts,
        without recursion so a long chain cannot overflow the stack)."""
        index: Dict[tuple, int] = {}
        low: Dict[tuple, int] = {}
        on_stack: Set[tuple] = set()
        stack: List[tuple] = []
        order: List[tuple] = []
        circular: Set[tuple] = set()
        counter = 0
        edges: Dict[tuple, List[tuple]] = {}
        for start in dirty:
            if start in index:
                continue
            work = [(start, 0)]
            while work:
                node, i = work.pop()
                if i == 0:
                    index[node] = low[node] = counter
                    counter += 1
                    stack.append(node)
                    on_stack.add(node)
                    edges[node] = self._precedents_in(node, dirty)
                successors = edges[node]
                if i < len(successors):
                    work.append((node, i + 1))
                    nxt = successors[i]
                    if nxt not in index:
                        work.append((nxt, 0))
                    elif nxt in on_stack:
                        low[node] = min(low[node], index[nxt])
                    continue
                if low[node] == index[node]:
                    component = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    if len(component) > 1 or node in edges[node]:
                        circular.update(component)
                    else:
                        order.append(node)
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
        return order, circular

    def _evaluate_cell(self, key: tuple) -> None:
        uid, row, col = key
        cell = self.book.cells_of(uid).get((row, col))
        self._dirty.discard(key)
        if cell is None or not cell.formula:
            return
        if getattr(cell, "array", None):
            # An array formula fills several cells; only the app calculates
            # it, and its inputs just changed.
            self.book.store_result(uid, row, col, None, STATE_STALE)
            return
        self._evaluating.add(key)
        try:
            value, state = self.evaluate(cell.formula, uid, row, col)
        finally:
            self._evaluating.discard(key)
        if state == STATE_APP:
            # Asked to calculate again because an input changed: the result
            # the app stored no longer holds, and only the app knows the new
            # one.
            value, state = None, STATE_STALE
        self.book.store_result(uid, row, col, value, state)

    def evaluate(self, formula: str, uid, row: int, col: int):
        """``formula``'s value as if it were in cell (row, col) of sheet
        ``uid``, and its state."""
        tree = parse_safely(formula)
        if tree[0] == "app":
            return None, STATE_APP
        ctx = _Context(self, uid, row, col, None)
        try:
            value = self._eval(tree, ctx)
            if isinstance(value, Area):
                value = _scalar(value, ctx)
            if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
                value = to_serial(value, self.date1904)
            if value is None:
                value = 0
            if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
                value = NUM
            return value, STATE_OK
        except _Raise as raised:
            return raised.error, STATE_OK
        except NeedsApp:
            return None, STATE_APP
        except RecursionError:
            return CIRCULAR, STATE_CIRCULAR
        except Exception:     # a formula must never take the sheet down
            _log.exception("formula %r could not be calculated", formula)
            return None, STATE_APP

    def value(self, uid, row: int, col: int):
        """A cell's value for a formula: a formula not calculated yet is
        calculated first."""
        key = (uid, row, col)
        if key in self._dirty:
            if key in self._evaluating:
                raise _Raise(CIRCULAR)
            self._evaluate_cell(key)
        cell = self.book.cells_of(uid).get((row, col))
        if cell is None:
            return None
        if cell.formula and key in self._evaluating:
            raise _Raise(CIRCULAR)
        if cell.state == STATE_STALE:
            # What this formula reads is not known yet: neither is its result.
            raise NeedsApp("stale input")
        value = cell.value
        if value is None and cell.formula:
            return 0
        return value

    def _sheet_uid(self, ref: Ref, ctx: _Context):
        if ref.sheet is None:
            return ctx.uid
        uid = self.book.sheet_uid(ref.sheet)
        if uid is None:
            raise _Raise(REF)
        return uid

    def _area(self, ref: Ref, ctx: _Context):
        uid = self._sheet_uid(ref, ctx)
        if ref.kind in ("cols", "rows"):
            rows, cols = self.book.used_size(uid)
            if ref.kind == "cols":
                return Area(self, uid, 0, ref.c1, rows, ref.c2 - ref.c1 + 1)
            return Area(self, uid, ref.r1, 0, ref.r2 - ref.r1 + 1, cols)
        return Area(self, uid, ref.r1, ref.c1, ref.r2 - ref.r1 + 1, ref.c2 - ref.c1 + 1)

    def _eval(self, node, ctx: _Context):
        kind = node[0]
        if kind == "num":
            return _clean(node[1])
        if kind == "str":
            return node[1]
        if kind == "bool":
            return node[1]
        if kind == "err":
            return node[1]
        if kind == "missing":
            return None
        if kind == "ref":
            ref = node[1]
            if ref is None or ref.error:
                return REF
            if ref.is_cell:
                value = self.value(self._sheet_uid(ref, ctx), ref.r1, ref.c1)
                return value
            return self._area(ref, ctx)
        if kind == "neg":
            value = self._eval(node[1], ctx)
            return _clean(-_number(value, ctx))
        if kind == "pos":
            return self._eval(node[1], ctx)
        if kind == "pct":
            return _number(self._eval(node[1], ctx), ctx) / 100
        if kind == "op":
            op = node[1]
            left = _ensure(_scalar(self._eval(node[2], ctx), ctx))
            right = _ensure(_scalar(self._eval(node[3], ctx), ctx))
            if op == "&":
                return _text(left, ctx) + _text(right, ctx)
            if op in _COMPARE:
                order = _compare(_normal(left, ctx), _normal(right, ctx))
                return {"=": order == 0, "<>": order != 0, "<": order < 0, ">": order > 0,
                        "<=": order <= 0, ">=": order >= 0}[op]
            return _arith(op, _number(left, ctx), _number(right, ctx))
        if kind == "fn":
            entry = _FUNCTIONS.get(node[1])
            if entry is None:
                raise NeedsApp(node[1])
            fn, lazy = entry
            if lazy:
                return fn(node[2], ctx)
            args = []
            for arg in node[2]:
                value = self._eval(arg, ctx)
                args.append(value)
            if node[1] not in _COUNTING:
                for value in args:
                    if isinstance(value, CellError):
                        return value
            return fn(args, ctx)
        if kind == "name":
            text = self.book.defined_name(node[1], ctx.uid)
            if not text:
                raise NeedsApp(node[1])
            return self._eval(parse_safely(text), ctx)
        if kind == "app":
            raise NeedsApp("app")
        raise NeedsApp(kind)


def parse_safely(formula: str):
    """``parse``, with text that cannot be read as an error value."""
    try:
        return parse(formula)
    except FormulaSyntaxError:
        return ("err", NAME)
    except RecursionError:
        return ("app",)


def needs_app(formula: str) -> bool:
    """Whether only the spreadsheet app calculates ``formula``: it uses a
    function MyEditor does not know, an array constant or another
    workbook, or cannot be read here."""
    tree = parse_safely(formula)

    def walk(node) -> bool:
        kind = node[0]
        if kind == "app":
            return True
        if kind == "fn":
            return node[1] not in _FUNCTIONS or any(walk(arg) for arg in node[2])
        if kind in ("neg", "pos", "pct"):
            return walk(node[1])
        if kind == "op":
            return walk(node[2]) or walk(node[3])
        return False

    return tree == ("err", NAME) or walk(tree)


def formula_error(formula: str) -> Optional[str]:
    """Why a formula the person typed cannot be read, or None."""
    try:
        _Parser(tokenize(formula)).parse()
    except FormulaSyntaxError as error:
        return str(error)
    except NeedsApp:
        return None
    return None
