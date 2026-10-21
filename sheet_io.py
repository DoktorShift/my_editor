# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spreadsheet files read and written without losing what is in them.

``load_workbook(path)`` reads an Excel workbook (.xlsx, .xlsm), an
OpenDocument spreadsheet (.ods) or a text table (.csv, .tsv) into a
``Workbook``: its sheets, each with its cells, column widths and frozen
panes. ``Workbook.save()`` writes it back into the same file.

What a person's file holds matters more than anything here, so every
format is edited in place rather than written anew:

- Excel: the workbook openpyxl loaded stays in memory and only its cells
  are rewritten, so styles, merged cells, number formats, names, data
  validation, conditional formatting and comments stay. openpyxl cannot
  write some parts (charts, pictures, pivot tables, ...); a workbook with
  any of them is never saved over (``unsaveable_parts``), only as a copy.
  After openpyxl has written the file, each formula gets its result back
  (so Quick Look and other apps show numbers) and Excel and LibreOffice
  are asked to calculate again when they open it.
- OpenDocument: only the edited sheets in content.xml change. Every
  other entry of the package stays byte for byte as it was, with its
  compression, and "mimetype" stays first and uncompressed. A run of
  repeated rows or cells is split only where something changed.
- Text tables: the delimiter, the encoding (with its byte order mark),
  the line endings and the quotes of every field stay as they were.
  Cells are text exactly as written; nothing is calculated.

A save never replaces a file another app has open (its lock file is
there) nor one that changed on the disk since it was read, unless the
caller says so. Every write goes through ``atomic_file.save_document``,
which keeps the file's permissions, extended attributes and links.

Rows and columns are counted from 0 everywhere in this module.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import csv
import dataclasses
import datetime as _dt
import io
import logging
import math
import os
import re
import warnings
import zipfile
from decimal import Decimal
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import atomic_file
import file_kinds
import regular_files
import sheet_formulas as sf
from i18n import _

_log = logging.getLogger(__name__)

# Beyond this many cells a file is too big to edit here.
MAX_CELLS = 4_000_000

# A column's width when the file names none, in pixels (Excel's 8.43
# characters; LibreOffice's 2.258 cm is within a pixel of it).
DEFAULT_WIDTH_PX = 64.0
DEFAULT_HEIGHT_PX = 20.0


class _Unset:
    """No backend value: the cell was edited, so the model's value is
    written."""

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET = _Unset()


@dataclasses.dataclass(frozen=True)
class Cell:
    """One cell. Never changed in place: an edit puts a new Cell there, so
    a copy of a sheet's cells is a snapshot for Undo.

    ``value`` is the constant, or a formula's last result. ``formula`` is
    the formula in Excel's syntax ("=SUM(A1:A3)"). ``source`` is what the
    file held as it was read (written back while the cell is unchanged),
    ``UNSET`` once it was edited. ``style`` and ``extra`` belong to the
    file format (an Excel style, the OpenDocument cell element and its
    comment) and are carried along unchanged. ``shown`` is the text the
    spreadsheet app showed, kept while the cell is unchanged. ``state`` is
    the formula engine's word on a formula (sheet_formulas.STATE_*)."""

    value: object = None
    formula: Optional[str] = None
    number_format: str = "General"
    style: object = None
    extra: object = None
    source: object = UNSET
    shown: Optional[str] = None
    state: str = ""
    # For an array formula (one formula filling a block): that block, as
    # "A1:B3" on the cell's own sheet. It moves with the cell.
    array: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        return self.formula is None and (self.value is None or self.value == "")

    def edited(self, **changes) -> "Cell":
        """This cell with ``changes`` and no longer as the file had it."""
        return dataclasses.replace(self, source=UNSET, shown=None, **changes)


class Sheet:
    """One sheet: its cells by (row, col), and how it looks."""

    _next_uid = 1

    def __init__(self, name: str, backend=None) -> None:
        self.uid = Sheet._next_uid
        Sheet._next_uid += 1
        self.name = name
        self.cells: Dict[Tuple[int, int], Cell] = {}
        self.widths: Dict[int, float] = {}          # column -> pixels
        self.changed_widths: Set[int] = set()
        self.heights: Dict[int, float] = {}         # row -> pixels
        self.default_width = DEFAULT_WIDTH_PX
        self.default_height = DEFAULT_HEIGHT_PX
        self.merges: List[Tuple[int, int, int, int]] = []
        # Whether ``merges`` holds every merged block of the file.
        self.merges_complete = True
        self.frozen: Optional[Tuple[int, int]] = None
        self.hidden = False
        self.backend = backend
        self.changed = False
        self._size: Optional[Tuple[int, int]] = None

    def __repr__(self) -> str:
        return f"Sheet({self.name!r})"

    def used_size(self) -> Tuple[int, int]:
        """(rows, columns) up to the last cell that holds or shows anything."""
        if self._size is None:
            rows = cols = 0
            for (r, c), cell in self.cells.items():
                if r >= rows:
                    rows = r + 1
                if c >= cols:
                    cols = c + 1
            self._size = (rows, cols)
        return self._size

    def touched(self, keys: Optional[Iterable[Tuple[int, int]]] = None) -> None:
        """The sheet changed: at the (row, col) ``keys`` only, when given.
        The used size then follows those cells instead of being counted
        again over every cell (typing into a sheet of 400 000 cells)."""
        self.changed = True
        if keys is None or self._size is None:
            self._size = None
            return
        rows, cols = self._size
        cells = self.cells
        for key in keys:
            r, c = key
            if key in cells:
                if r >= rows:
                    rows = r + 1
                if c >= cols:
                    cols = c + 1
            elif r >= rows - 1 or c >= cols - 1:
                # The last row or column may have emptied: counted again
                # when next asked.
                self._size = None
                return
        self._size = (rows, cols)

    def width(self, col: int) -> float:
        return self.widths.get(col, self.default_width)


# --------------------------------------------------------------------------- #
# Errors a save reports                                                       #
# --------------------------------------------------------------------------- #

class SheetFileError(Exception):
    """A spreadsheet that cannot be read or written; ``str()`` is a
    sentence for the person."""


class FileInUseError(SheetFileError):
    """Another app has the file open (its lock file is there)."""

    def __init__(self, lock_path: str) -> None:
        super().__init__(_("The file is open in another app."))
        self.lock_path = lock_path


class ChangedOnDiskError(SheetFileError):
    """The file changed on the disk since it was read."""

    def __init__(self) -> None:
        super().__init__(_("The file changed on the disk since it was opened."))


class FileDeletedError(SheetFileError):
    """The file was moved to the Trash or deleted since it was read."""

    def __init__(self) -> None:
        super().__init__(_("This file was moved to the Trash or deleted."))


class UnsaveablePartsError(SheetFileError):
    """The file holds parts that saving would drop."""

    def __init__(self, parts: List[str]) -> None:
        super().__init__(_("The file contains parts that MyEditor cannot save."))
        self.parts = list(parts)


# --------------------------------------------------------------------------- #
# Lock files and the disk                                                     #
# --------------------------------------------------------------------------- #

def _disk_stamp(path: str):
    """What says a file is still the same one: its modification time,
    size and identity on the disk; None when it is gone."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size, info.st_ino)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_stable(path: str) -> Tuple[bytes, tuple]:
    """A file's bytes, read once, with the stamp the file had before and
    after the read: when another app saved it meanwhile, it is read again,
    so the bytes and the stamp always belong to one version."""
    for _attempt in range(5):
        before = _disk_stamp(path)
        with open(path, "rb") as f:
            data = f.read()
        if before is not None and _disk_stamp(path) == before:
            return data, before
    raise SheetFileError(_("The file keeps changing while MyEditor reads it. Try again "
                           "once the other app has saved it."))


@dataclasses.dataclass(frozen=True)
class SaveFormat:
    """A format a workbook can be saved as: its extension, its name for a
    file dialog (with "{patterns}" for the extension's pattern), and
    whether it is an export of the sheet on screen (a text table), after
    which the workbook stays with its own file."""

    ext: str
    label: str
    export: bool


def save_formats(kind: str) -> List[SaveFormat]:
    """What a workbook of ``kind`` ("xlsx", "ods", ...) can be saved as, its
    own format first. The one list Workbook.save and the Save As dialog
    both follow: its own format, an Excel workbook from an OpenDocument
    file or a text table, and a text table of the sheet shown."""
    names = {
        "xlsx": _("Excel Workbook ({patterns})"),
        "xlsm": _("Excel Macro-Enabled Workbook ({patterns})"),
        "ods": _("OpenDocument Spreadsheet ({patterns})"),
        "csv": _("CSV, the Sheet Shown ({patterns})"),
        "tsv": _("TSV, the Sheet Shown ({patterns})"),
    }
    if kind not in names:
        return []
    own = SaveFormat("." + kind, names[kind], False)
    formats = [own]
    if kind in ("ods", "csv", "tsv"):
        formats.append(SaveFormat(".xlsx", names["xlsx"], False))
    for text in ("csv", "tsv"):
        if text != kind:
            formats.append(SaveFormat("." + text, names[text], True))
    return formats


def save_as_path(path: str, chosen_ext: str, suggested_ext: str, kind: str) -> str:
    """The file a Save As dialog's answer means: the name follows the
    format picked in the dialog unless the person typed another one this
    workbook can be saved as (a name still ending in the suggested
    extension follows the picked format; a name without a known one gets
    it)."""
    stem, ext = os.path.splitext(path)
    known = [f.ext for f in save_formats(kind)]
    ext = ext.lower()
    if ext not in known:
        return path + chosen_ext
    if ext == suggested_ext and chosen_ext != ext and chosen_ext in known:
        return stem + chosen_ext
    return path


def kind_of(path: str) -> str:
    """"xlsx", "xlsm", "ods", "csv" or "tsv" (by the name's extension)."""
    return os.path.splitext(path)[1].lower().lstrip(".")


# --------------------------------------------------------------------------- #
# Parts openpyxl cannot write                                                 #
# --------------------------------------------------------------------------- #

def unsaveable_parts(path: str, data: Optional[bytes] = None) -> List[str]:
    """What a save of the file at ``path`` would drop, as names a person
    reads ("Charts", "Custom views"); empty when nothing would be lost.

    For Excel workbooks this is an allow-list: only package parts and
    sheet and workbook settings that the openpyxl round trip is known to
    keep (tests/test_sheet_io.py proves each) count as safe; anything
    else in the file is named, so a save never drops what it does not
    know."""
    ext = os.path.splitext(path)[1].lower()
    if not regular_files.is_regular_file(path):
        return []                        # a pipe with a workbook's name is never opened
    try:
        archive = zipfile.ZipFile(io.BytesIO(data) if data is not None else path)
    except (OSError, zipfile.BadZipFile):
        return []
    with archive:
        names = archive.namelist()
        if ext == ".ods":
            found = []
            if any(n.startswith("META-INF/") and "signatures" in n for n in names):
                found.append(_("Digital signature"))
            return found
        if ext not in (".xlsx", ".xlsm"):
            return []
        return _ooxml_unsaveable(archive, names, macros=ext == ".xlsm")


# Package parts openpyxl reads and writes again (or that Excel makes anew
# from the rest: the calculation chain, the preview picture).
_KEPT_PARTS = re.compile(r"""(?x)
    \[Content_Types\]\.xml | _rels/\.rels
  | docProps/(?:app|core|custom)\.xml | docProps/thumbnail\.\w+
  | xl/workbook\.xml | xl/_rels/workbook\.xml\.rels | xl/styles\.xml | xl/sharedStrings\.xml
  | xl/theme/theme\d+\.xml | xl/calcChain\.xml
  | xl/worksheets/sheet\d+\.xml | xl/worksheets/_rels/sheet\d+\.xml\.rels
  | xl/comments\d*\.xml | xl/comments/comment\d+\.xml
  | xl/drawings/(?:vmlDrawing|commentsDrawing)\d+\.vml
  | xl/tables/table\d+\.xml
  | xl/externalLinks/externalLink\d+\.xml | xl/externalLinks/_rels/externalLink\d+\.xml\.rels
""")

# What a person calls the parts openpyxl drops, by where they live.
def _part_labels():
    return [
        ("xl/charts/", _("Charts")), ("xl/chartsheets/", _("Charts")),
        ("xl/media/", _("Pictures")), ("xl/diagrams/", _("SmartArt graphics")),
        ("xl/pivotTables/", _("Pivot tables")), ("xl/pivotCache/", _("Pivot tables")),
        ("xl/slicers/", _("Slicers")), ("xl/slicerCaches/", _("Slicers")),
        ("xl/timelines/", _("Timelines")), ("xl/timelineCaches/", _("Timelines")),
        ("xl/activeX/", _("ActiveX controls")), ("xl/ctrlProps/", _("Form controls")),
        ("xl/threadedComments/", _("Threaded comments")), ("xl/persons/", _("Threaded comments")),
        ("xl/richData/", _("Pictures in cells and linked data types")),
        ("xl/metadata.xml", _("Dynamic array formulas")),
        ("xl/model/", _("Data connections and the data model")),
        ("xl/connections.xml", _("Data connections and the data model")),
        ("xl/queryTables/", _("Data connections and the data model")),
        ("customXml/", _("Custom XML data")),
        ("customUI/", _("Custom ribbon and toolbars")),
        ("customUI14/", _("Custom ribbon and toolbars")),
        ("xl/macrosheets/", _("Excel 4 macro and dialog sheets")),
        ("xl/dialogsheets/", _("Excel 4 macro and dialog sheets")),
        ("_xmlsignatures/", _("Digital signature")),
        ("xl/vbaProjectSignature", _("Digital signature")),
        ("xl/embeddings/", _("Embedded objects")),
        ("xl/printerSettings/", _("Printer settings")),
        ("xl/webextensions/", _("Office add-ins")),
        ("xl/ink/", _("Ink drawings")),
        ("xl/revisions/", _("Change history of a shared workbook")),
        ("xl/userNames.xml", _("Change history of a shared workbook")),
        ("xl/volatileDependencies.xml", _("Real-time data links")),
        ("xl/vbaProject.bin", _("Macros")),
    ]


# Elements of a worksheet openpyxl keeps (its reader reads them and its
# writer writes them).
_KEPT_SHEET_ELEMENTS = frozenset({
    "sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData", "sheetCalcPr",
    "sheetProtection", "autoFilter", "mergeCells", "conditionalFormatting", "dataValidations",
    "hyperlinks", "printOptions", "pageMargins", "pageSetup", "headerFooter", "rowBreaks",
    "colBreaks", "drawing", "legacyDrawing", "tableParts", "scenarios",
    # Not written by openpyxl, but read and put back (_read_kept):
    "customSheetViews", "ignoredErrors"})

_KEPT_BOOK_ELEMENTS = frozenset({
    "fileVersion", "workbookPr", "workbookProtection", "bookViews", "sheets",
    "externalReferences", "definedNames", "calcPr", "oleSize", "fileRecoveryPr",
    "pivotCaches", "AlternateContent", "customWorkbookViews"})

# Workbook extensions that only describe Excel's own settings.
_HARMLESS_BOOK_EXTENSIONS = frozenset({
    "{140A7094-0E35-4892-8432-C4D2E57EDEB5}", "{B58B0392-4F1F-4190-BB64-5DF3571DCE5F}",
    "{79F54976-1DA5-4618-B147-4CDE4B953A38}"})


def _element_labels():
    return {
        "customSheetViews": _("Custom views"), "customWorkbookViews": _("Custom views"),
        "ignoredErrors": _("Ignored error checks"), "protectedRanges": _("Protected ranges"),
        "phoneticPr": _("Phonetic guides"), "dataConsolidate": _("Data consolidation"),
        "cellWatches": _("Watched cells"), "smartTags": _("Smart tags"),
        "smartTagPr": _("Smart tags"), "smartTagTypes": _("Smart tags"),
        "oleObjects": _("Embedded objects"), "controls": _("Form controls"),
        "webPublishItems": _("Web publishing settings"),
        "webPublishing": _("Web publishing settings"),
        "webPublishObjects": _("Web publishing settings"),
        "picture": _("Sheet background picture"),
        "legacyDrawingHF": _("Pictures in headers and footers"),
        "sortState": _("Sort settings"), "fileSharing": _("Protection against changes"),
        "functionGroups": _("Custom function groups"),
        "AlternateContent": _("Controls and embedded objects"),
    }


def _extension_labels():
    return {
        "{05C60535-1F16-4fd2-B633-F4F36F0B64E0}": _("Sparklines"),
        "{78C0D931-6437-407d-A8EE-F0AAD7539E65}":
            _("Conditional formatting with data bars or icon sets"),
        "{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}": _("Validation that refers to other sheets"),
        "{A8765BA9-456A-4dab-B4F3-ACF838C121DE}": _("Slicers"),
        "{3A4CF648-6AED-40f4-86FF-DC5316D8AED3}": _("Slicers"),
        "{BBE1A952-AA13-448e-AADC-164F8A28A991}": _("Slicers"),
        "{46BE6895-7355-4a93-B00E-2C351335B9C9}": _("Slicers"),
        "{7E03D99C-DC04-49d9-9315-930204A7B6E9}": _("Timelines"),
        "{D0CA8CA8-9F24-4464-BF8E-62219DCF47F9}": _("Timelines"),
        "{FCE2AD5D-F65C-4FA6-A056-5C36A1767C68}": _("Data connections and the data model"),
        "{FC87AEE6-9EDD-4A0A-B7FB-166176984837}": _("Protected ranges"),
    }


def _ooxml_unsaveable(archive: zipfile.ZipFile, names: List[str], *, macros: bool) -> List[str]:
    from lxml import etree
    found: List[str] = []

    def add(label: str) -> None:
        if label not in found:
            found.append(label)

    labels = _part_labels()
    for name in names:
        if name.endswith("/") or _KEPT_PARTS.fullmatch(name):
            continue
        if macros and (name == "xl/vbaProject.bin"):
            continue
        if _KEPT_BACK_PARTS.fullmatch(name):
            continue     # read here and put back on saving
        if re.fullmatch(r"xl/drawings/drawing\d*\.xml|xl/drawings/_rels/.*", name):
            continue     # named below by what they show
        for prefix, label in labels:
            if name.startswith(prefix):
                add(label)
                break
        else:
            add(_("Other parts of the workbook ({part})").format(part=name))
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    for name in names:
        if re.fullmatch(r"xl/drawings/drawing\d*\.xml", name):
            drawing = _xml_or_none(_read_quietly(archive, name))
            if drawing is None:
                add(_("Drawings"))
                continue
            shown = {etree.QName(e).localname for e in drawing.iter() if isinstance(e.tag, str)}
            if "pic" in shown:
                add(_("Pictures"))
            if shown & {"sp", "grpSp", "cxnSp"}:
                add(_("Shapes and text boxes"))
            if "graphicFrame" in shown and not any(n.startswith("xl/charts/") for n in names):
                add(_("SmartArt graphics"))
        elif re.fullmatch(r"xl/drawings/(?:vmlDrawing|commentsDrawing)\d+\.vml", name):
            # Comments live in VML; anything else there (buttons, check
            # boxes) is a form control openpyxl does not write again.
            vml = _xml_or_none(_read_quietly(archive, name), recover=True)
            if vml is None:
                add(_("Form controls"))
                continue
            kinds = {e.get("ObjectType") for e in vml.iter()
                     if isinstance(e.tag, str) and e.get("ObjectType")}
            if kinds - {"Note"}:
                add(_("Form controls"))
        elif re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
            for label in _unkept_elements(_read_quietly(archive, name), parser,
                                          _KEPT_SHEET_ELEMENTS, _KEPT_BACK_EXTENSIONS):
                add(label)
        elif name == "xl/workbook.xml":
            for label in _unkept_elements(_read_quietly(archive, name), parser,
                                          _KEPT_BOOK_ELEMENTS, _HARMLESS_BOOK_EXTENSIONS):
                add(label)
    return found


def _read_quietly(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except (KeyError, OSError, zipfile.BadZipFile, RuntimeError):
        return b""


def _sheet_head(xml: bytes):
    """A sheet's (or the workbook's) XML read with a real parser, its cells
    left out: the root element with every other element as it is and an
    empty sheetData. The cells are read as a stream and let go of row by
    row, so a large sheet costs no more memory than one row. Raises
    lxml's XMLSyntaxError for XML that is not well formed."""
    from lxml import etree
    events = etree.iterparse(io.BytesIO(xml), events=("start", "end"), huge_tree=True,
                             resolve_entities=False, no_network=True)
    depth = 0
    in_cells = False
    root = None
    for event, element in events:
        if event == "start":
            depth += 1
            if depth == 1:
                root = element
            elif depth == 2 and etree.QName(element).localname == "sheetData":
                in_cells = True
            continue
        if in_cells and depth == 3:
            # A row of cells, read: dropped at once.
            element.clear()
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
        elif in_cells and depth == 2:
            in_cells = False
            element.clear()
        depth -= 1
    return root


def _xml_or_none(xml: bytes, *, recover: bool = False):
    """``xml`` parsed, or None when it cannot be read at all (VML that
    Excel writes is not always well formed, hence ``recover``)."""
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True,
                             recover=recover)
    try:
        return etree.fromstring(xml, parser)
    except (etree.XMLSyntaxError, ValueError):
        return None


def _unkept_elements(xml: bytes, parser, kept, harmless_extensions) -> List[str]:
    """Names of what a sheet's or the workbook's XML holds beyond the
    elements openpyxl keeps. The cells (sheetData) are cut out first: they
    are kept, and reading them would take long on a large sheet."""
    from lxml import etree
    if not xml:
        return []
    try:
        root = _sheet_head(xml)
    except etree.XMLSyntaxError:
        return [_("Parts of the workbook MyEditor cannot read")]
    labels, extensions, found = _element_labels(), _extension_labels(), []
    for child in root:
        if not isinstance(child.tag, str):
            continue
        local = etree.QName(child).localname
        if local == "extLst":
            for ext in child:
                uri = ext.get("uri", "")
                if uri in harmless_extensions:
                    continue
                found.append(extensions.get(uri, _("Newer Excel features ({uri})").format(
                    uri=uri)))
            continue
        if local in kept:
            continue
        found.append(labels.get(local, _("Other workbook settings ({name})").format(name=local)))
    return found


# --------------------------------------------------------------------------- #
# Snapshots for Undo                                                          #
# --------------------------------------------------------------------------- #

@dataclasses.dataclass
class _SheetState:
    sheet: Sheet
    name: str
    cells: Dict
    widths: Dict
    changed_widths: Set
    heights: Dict
    merges: List
    frozen: object
    backend: object


@dataclasses.dataclass
class Snapshot:
    """How part of a workbook was before a change, to put it back."""

    order: List[Sheet]
    sheets: List[_SheetState]
    other_cells: Dict[Tuple[int, Tuple[int, int]], Optional[Cell]]
    book_state: object
    active: int = 0
    # After a row or column change: the formulas (where they were) whose
    # values it changed, to calculate again on Undo. None after other
    # changes, which index and calculate the whole workbook again.
    recalc: Optional[Set[tuple]] = None


# --------------------------------------------------------------------------- #
# The workbook                                                                #
# --------------------------------------------------------------------------- #

def _all_or_nothing(method):
    """A change to rows, columns or sheets happens completely or not at
    all: when anything in it fails, everything it touched (cells, the
    file's own structure, other sheets' formulas) is put back from the
    snapshot it took first, and a SheetFileError says so."""

    @functools.wraps(method)
    def run(self, *args, **kwargs):
        outer = self._pending is None
        if outer:
            self._pending = []
        try:
            return method(self, *args, **kwargs)
        except Exception as error:
            if not outer:
                raise
            for snapshot in reversed(self._pending):
                try:
                    self.restore(snapshot, recalculate=False)
                except Exception:     # the rollback itself failed: say so loudly
                    _log.exception("could not put the workbook back after a failed change")
                    raise SheetFileError(_("The change failed, and the workbook could not "
                                           "be put back. Close it without saving.")) from error
            if isinstance(error, SheetFileError):
                raise
            _log.exception("%s failed", method.__name__)
            raise SheetFileError(_("The change could not be made. The sheet is as it was."
                                   )) from error
        finally:
            if outer:
                self._pending = None

    return run


class Workbook:
    """A spreadsheet file in memory, ready to edit and save.

    Change cells with ``set_cells``, rows and columns with
    ``insert``/``delete``, sheets with ``add_sheet``, ``rename_sheet``,
    ``duplicate_sheet`` and ``delete_sheet``. Each structural change
    returns a ``Snapshot``; ``restore(snapshot)`` takes it back (Undo)."""

    def __init__(self, path: str, kind: str, backend: "_Backend") -> None:
        self.path = os.path.abspath(path)
        self.kind = kind
        self.backend = backend
        self.sheets: List[Sheet] = []
        self.date1904 = False
        self.unsaveable: List[str] = []
        # The file as it was read: its stamp, and the digest of its bytes
        # (set by load_workbook, from the very bytes it parsed).
        self._stamp = _disk_stamp(self.path)
        self._digest: Optional[str] = None
        self.engine = sf.Engine(self)
        # Formula cells whose text a structural change rewrote, to
        # calculate again.
        self._rewritten: Set[tuple] = set()
        # The snapshots taken by the change running now, to roll it back.
        self._pending: Optional[List[Snapshot]] = None

    # -- what the formula engine reads ------------------------------------

    def sheet_uids(self) -> List[int]:
        return [sheet.uid for sheet in self.sheets]

    def sheet_uid(self, name: str) -> Optional[int]:
        folded = name.casefold()
        for sheet in self.sheets:
            if sheet.name.casefold() == folded:
                return sheet.uid
        return None

    def sheet_name(self, uid: int) -> str:
        return self.by_uid(uid).name

    def by_uid(self, uid: int) -> Sheet:
        for sheet in self.sheets:
            if sheet.uid == uid:
                return sheet
        raise KeyError(uid)

    def cells_of(self, uid: int) -> Dict:
        for sheet in self.sheets:
            if sheet.uid == uid:
                return sheet.cells
        return {}

    def used_size(self, uid: int) -> Tuple[int, int]:
        return self.by_uid(uid).used_size()

    def defined_name(self, name: str, uid: int) -> Optional[str]:
        return self.backend.defined_name(self, name, uid)

    def store_result(self, uid: int, row: int, col: int, value, state: str) -> None:
        sheet = self.by_uid(uid)
        cell = sheet.cells.get((row, col))
        if cell is None:
            return
        if sf.same_value(cell.value, value) and cell.state == state:
            # The same result (to Excel's 15 digits): the file's stays.
            return
        if self._pending:
            # A change to rows, columns or sheets is running: remember the
            # result as it was, for Undo and for putting things back.
            first = self._pending[0]
            snapped = {state_.sheet.uid for state_ in first.sheets}
            if uid not in snapped:
                first.other_cells.setdefault((uid, (row, col)), cell)
        if self.backend.results_in_source:
            sheet.cells[(row, col)] = dataclasses.replace(
                cell, value=value, state=state, source=UNSET, shown=None)
        else:
            sheet.cells[(row, col)] = dataclasses.replace(cell, value=value, state=state,
                                                          shown=None)
        sheet.changed = True

    # -- the file ----------------------------------------------------------

    @property
    def is_text_table(self) -> bool:
        return self.kind in ("csv", "tsv")

    def sheet(self, name: str) -> Optional[Sheet]:
        uid = self.sheet_uid(name)
        return None if uid is None else self.by_uid(uid)

    def changed_on_disk(self) -> bool:
        """Whether the file on the disk is no longer the one read (or last
        saved) here. A file only touched (the same bytes) has not changed."""
        current = _disk_stamp(self.path)
        if current == self._stamp:
            return False
        if current is None or self._digest is None:
            return True
        try:
            with open(self.path, "rb") as f:
                same = _digest(f.read()) == self._digest
        except OSError:
            return True
        if same:
            self._stamp = current
        return not same

    def lock_owner(self) -> Optional[str]:
        """The lock file of another app that has this file open, or None."""
        return file_kinds.lock_owner_file(self.path)

    def save(self, path: Optional[str] = None, *, overwrite_changes: bool = False,
             sheet: Optional[Sheet] = None) -> str:
        """Write the workbook into its file, or into ``path``.

        Into its own file: raises FileInUseError while another app has it
        open, ChangedOnDiskError when it changed on the disk since it was
        read (unless ``overwrite_changes``), UnsaveablePartsError when the
        file holds parts a save would drop. Into another file: of the same
        kind, or converted (any workbook into .xlsx; ``sheet``, else the
        first sheet, into .csv or .tsv). Returns the path written; raises
        SheetFileError or OSError when nothing was written."""
        target = os.path.abspath(path or self.path)
        same = os.path.realpath(target) == os.path.realpath(self.path)
        lock = file_kinds.lock_owner_file(target)
        if lock:
            raise FileInUseError(lock)
        if same:
            if not os.path.exists(target):
                # Gone, not changed: a save would quietly bring back a file
                # the person put in the Trash. Saving it is a Save As.
                raise FileDeletedError()
            if not overwrite_changes and self.changed_on_disk():
                raise ChangedOnDiskError()
            if self.unsaveable:
                raise UnsaveablePartsError(self.unsaveable)
        kind = kind_of(target)
        if "." + kind not in [f.ext for f in save_formats(self.kind)]:
            raise SheetFileError(_("MyEditor cannot save this workbook in that format."))
        if kind == self.kind:
            data = self.backend.serialize(self)
        elif kind == "xlsx":
            data = _model_to_xlsx(self)
        else:
            data = _model_to_text_table(self, sheet or self.sheets[0], kind)
        atomic_file.save_document(target, data)
        if same:
            self._stamp, self._digest = _disk_stamp(self.path), _digest(data)
            for each in self.sheets:
                each.changed = False
        else:
            self._written = (target, _digest(data))
        return target

    def adopt_path(self, path: str) -> None:
        """Make ``path`` (just saved as a copy of the same kind) this
        workbook's file."""
        self.path = os.path.abspath(path)
        self._stamp = _disk_stamp(self.path)
        written = getattr(self, "_written", None)
        self._digest = written[1] if written and written[0] == self.path else None
        self.unsaveable = []
        for sheet in self.sheets:
            sheet.changed = False

    def close(self) -> None:
        """Let go of what the file format holds open (the copy of an Excel
        workbook's macros)."""
        self.backend.close()

    # -- cells -------------------------------------------------------------

    def set_cells(self, sheet: Sheet, cells: Dict[Tuple[int, int], Optional[Cell]]) -> Set[tuple]:
        """Put ``cells`` into ``sheet`` (None empties a cell) and calculate
        again what depends on them. Returns the (uid, row, col) of every
        cell whose value may have changed."""
        keys = []
        for (row, col), cell in cells.items():
            if cell is None:
                sheet.cells.pop((row, col), None)
            else:
                sheet.cells[(row, col)] = cell
            key = (sheet.uid, row, col)
            keys.append(key)
            if self.is_text_table:
                continue
            self.engine.register(key, cell.formula if cell is not None else None)
        sheet.touched(cells.keys())
        if self.is_text_table:
            return set(keys)
        recalculated = self.engine.recalculate(keys)
        return set(keys) | recalculated

    def number_format_at(self, sheet: Sheet, row: int, col: int) -> str:
        """The number format a value typed into (row, col) gets."""
        cell = sheet.cells.get((row, col))
        if cell is not None:
            return cell.number_format
        return self.backend.format_at(self, sheet, row, col)

    def cell_with_format(self, sheet: Sheet, cell: Cell, number_format: str) -> Cell:
        """``cell`` showing its value in ``number_format``."""
        if number_format == cell.number_format:
            return cell
        return self.backend.with_format(self, sheet, cell, number_format)

    def look_at(self, sheet: Sheet, row: int, col: int) -> "Look":
        """How (row, col) looks as the file says: its fill, font colour,
        borders, alignment and wrapping (an empty cell too, from its row's
        or column's style)."""
        cell = sheet.cells.get((row, col))
        return self.backend.look(self, sheet, row, col, cell)

    def blank_cell(self, sheet: Sheet, row: int, col: int) -> Cell:
        """What an empty (row, col) carries before something is typed into
        it: its style, so the typed value keeps the cell's look."""
        existing = sheet.cells.get((row, col))
        if existing is not None:
            return existing
        return self.backend.blank_cell(self, sheet, row, col)

    # -- snapshots ---------------------------------------------------------

    def snapshot(self, sheets: Iterable[Sheet]) -> Snapshot:
        states = []
        for sheet in sheets:
            states.append(_SheetState(
                sheet, sheet.name, dict(sheet.cells), dict(sheet.widths),
                set(sheet.changed_widths), dict(sheet.heights), list(sheet.merges),
                sheet.frozen, self.backend.snapshot_sheet(self, sheet)))
        snapshot = Snapshot(list(self.sheets), states, {}, self.backend.snapshot_book(self))
        if self._pending is not None:
            self._pending.append(snapshot)
        return snapshot

    def restore(self, snapshot: Snapshot, *, recalculate: bool = True) -> None:
        """Put back what ``snapshot`` holds and calculate again (not when
        a failed change is rolled back: the snapshot's results are the
        ones that belong to it)."""
        incremental = snapshot.recalc is not None and not self.is_text_table
        if incremental:
            # Forget the formulas as they are now; those put back are
            # indexed again below.
            for state in snapshot.sheets:
                for key, cell in state.sheet.cells.items():
                    if cell.formula:
                        self.engine.unregister((state.sheet.uid,) + key)
            for uid, key in snapshot.other_cells:
                self.engine.unregister((uid,) + key)
        self.backend.restore_book(self, snapshot.book_state, snapshot.order)
        self.sheets = list(snapshot.order)
        for state in snapshot.sheets:
            sheet = state.sheet
            sheet.name = state.name
            sheet.cells = dict(state.cells)
            sheet.widths = dict(state.widths)
            sheet.changed_widths = set(state.changed_widths)
            sheet.heights = dict(state.heights)
            sheet.merges = list(state.merges)
            sheet.frozen = state.frozen
            self.backend.restore_sheet(self, sheet, state.backend)
            sheet.touched()
        for (uid, key), cell in snapshot.other_cells.items():
            try:
                target = self.by_uid(uid)
            except KeyError:
                continue
            self._rewritten.add((uid,) + key)
            if cell is None:
                target.cells.pop(key, None)
            else:
                target.cells[key] = cell
            target.touched()
        if not incremental:
            self._recalculate_after_structure([state.sheet for state in snapshot.sheets],
                                              calculate=recalculate)
            return
        self._rewritten = set()
        engine = self.engine
        for state in snapshot.sheets:
            uid = state.sheet.uid
            for key, cell in state.sheet.cells.items():
                if cell.formula:
                    engine.register((uid,) + key, cell.formula)
        for uid, key in snapshot.other_cells:
            cell = self.cells_of(uid).get(key)
            engine.register((uid,) + key, cell.formula if cell is not None else None)
        if recalculate:
            engine.calculate_all(set(snapshot.recalc) & engine.formulas())

    def _recalculate_after_structure(self, sheets: Sequence[Sheet], *,
                                     calculate: bool = True) -> None:
        if self.is_text_table:
            return
        self.engine.rebuild()
        if not calculate:
            self._rewritten = set()
            return
        touched = set(self._rewritten)
        self._rewritten = set()
        for sheet in sheets:
            touched |= self.engine.readers_of_sheet(sheet.uid)
        touched &= self.engine.formulas()
        self.engine.calculate_all(touched)

    def _rewrite_formulas(self, snapshot: Snapshot, rewrite: Callable[[str, Sheet], str],
                          skip: Optional[Sheet] = None) -> None:
        """Run every formula through ``rewrite``, remembering in
        ``snapshot`` the cells of other sheets it changes."""
        snapped = {state.sheet.uid for state in snapshot.sheets}
        for sheet in self.sheets:
            if sheet is skip:
                continue
            for key, cell in list(sheet.cells.items()):
                if not cell.formula:
                    continue
                new = rewrite(cell.formula, sheet)
                if new == cell.formula:
                    continue
                if sheet.uid not in snapped:
                    snapshot.other_cells.setdefault((sheet.uid, key), cell)
                sheet.cells[key] = cell.edited(formula=new, value=cell.value)
                sheet.touched()
                self._rewritten.add((sheet.uid,) + key)
        self.backend.rewrite_names(self, rewrite)

    # -- rows and columns --------------------------------------------------

    @_all_or_nothing
    def insert(self, sheet: Sheet, axis: str, at: int, count: int = 1) -> Snapshot:
        """Insert ``count`` empty rows (``axis`` "row") or columns ("col")
        before ``at``. Formulas everywhere follow their cells."""
        return self._shift(sheet, axis, at, count)

    @_all_or_nothing
    def delete(self, sheet: Sheet, axis: str, at: int, count: int = 1) -> Snapshot:
        """Delete ``count`` rows or columns from ``at``. A formula that read
        a deleted cell shows #REF!."""
        return self._shift(sheet, axis, at, -count)

    def _shift(self, sheet: Sheet, axis: str, at: int, count: int) -> Snapshot:
        """Rows or columns inserted (``count`` > 0) or deleted. Only the
        formulas that refer past ``at`` are rewritten, and only those
        whose inputs changed are calculated again, so a sheet with tens of
        thousands of formulas stays quick."""
        snapshot = self.snapshot([sheet])
        row_axis = axis == "row"
        engine = self.engine
        uid = sheet.uid
        formulas = not self.is_text_table
        if formulas:
            changing, rewrite = engine.plan_shift(uid, axis, at, count)
        else:
            changing, rewrite = set(), set()

        def moved(index: int) -> Optional[int]:
            if count > 0:
                return index + count if index >= at else index
            if at <= index < at - count:
                return None
            return index + count if index >= at - count else index

        def moved_key(key: Tuple[int, int]) -> Optional[Tuple[int, int]]:
            r, c = key
            if row_axis:
                nr = moved(r)
                return None if nr is None or nr >= sf.MAX_ROWS else (nr, c)
            nc = moved(c)
            return None if nc is None or nc >= sf.MAX_COLS else (r, nc)

        cells = {}
        # Formulas that move or change are indexed again; the others (above
        # or left of the change, reading nothing past it) stay as they are.
        reindex: List[Tuple[int, int]] = []
        def block(text: str) -> Optional[str]:
            moved_text = sf.adjust_for_insert_delete("=" + text, home=sheet.name,
                                                     target=sheet.name, axis=axis, at=at,
                                                     count=count)[1:]
            return None if "#REF!" in moved_text else moved_text

        for key, cell in sheet.cells.items():
            new_key = moved_key(key)
            if formulas and cell.formula and (new_key != key or (uid,) + key in rewrite):
                engine.unregister((uid,) + key)
                if new_key is not None:
                    reindex.append(new_key)
            if new_key is not None:
                if cell.array:
                    cell = dataclasses.replace(cell, array=block(cell.array))
                moved_source = self.backend.moved_source(cell.source, block)
                if moved_source is not cell.source:
                    cell = dataclasses.replace(cell, source=moved_source)
                cells[new_key] = cell
        sheet.cells = cells
        if row_axis:
            sheet.heights = {moved(r): h for r, h in sheet.heights.items() if moved(r) is not None}
        else:
            sheet.widths = {moved(c): w for c, w in sheet.widths.items() if moved(c) is not None}
            sheet.changed_widths = {moved(c) for c in sheet.changed_widths
                                    if moved(c) is not None}
        merges = []
        for r1, c1, r2, c2 in sheet.merges:
            lo, hi = (r1, r2) if row_axis else (c1, c2)
            span = _shift_span(lo, hi, at, count)
            if span is None:
                continue
            lo, hi = span
            merges.append((lo, c1, hi, c2) if row_axis else (r1, lo, r2, hi))
        sheet.merges = merges
        if sheet.frozen:
            fr, fc = sheet.frozen
            if row_axis and at < fr:
                fr = max(at, fr + count) if count < 0 else fr + count
            if not row_axis and at < fc:
                fc = max(at, fc + count) if count < 0 else fc + count
            sheet.frozen = (fr, fc)
        self.backend.shift(self, sheet, axis, at, count)
        name = sheet.name

        def rewrite_text(formula: str, home: Sheet) -> str:
            return sf.adjust_for_insert_delete(formula, home=home.name, target=name,
                                               axis=axis, at=at, count=count)

        # The formulas that refer past the change, where they are now.
        touched: List[Tuple[int, Tuple[int, int]]] = []
        for key_uid, r, c in rewrite:
            if key_uid == uid:
                new_key = moved_key((r, c))
                if new_key is not None:
                    touched.append((uid, new_key))
            else:
                touched.append((key_uid, (r, c)))
        for key_uid, key in touched:
            target = sheet if key_uid == uid else self.by_uid(key_uid)
            cell = target.cells.get(key)
            if cell is None or not cell.formula:
                continue
            new = rewrite_text(cell.formula, target)
            if new == cell.formula:
                continue
            if key_uid != uid:
                snapshot.other_cells.setdefault((key_uid, key), cell)
                engine.unregister((key_uid,) + key)
            target.cells[key] = Cell(cell.value, new, cell.number_format, cell.style,
                                     cell.extra, UNSET, None, cell.state, cell.array)
            target.touched()
        self.backend.rewrite_names(self, rewrite_text)
        if formulas:
            for key in reindex:
                cell = sheet.cells.get(key)
                if cell is not None and cell.formula:
                    engine.register((uid,) + key, cell.formula)
            for key_uid, key in touched:
                if key_uid != uid:
                    cell = self.cells_of(key_uid).get(key)
                    engine.register((key_uid,) + key, cell.formula if cell else None)
            again = set()
            for key_uid, r, c in changing:
                if key_uid == uid:
                    new_key = moved_key((r, c))
                    if new_key is not None:
                        again.add((uid,) + new_key)
                else:
                    again.add((key_uid, r, c))
            engine.calculate_all(again & engine.formulas())
            snapshot.recalc = set(changing)
        sheet.touched()
        return snapshot

    def replace_cell(self, sheet: Sheet, key: Tuple[int, int], cell: Cell) -> None:
        """Put ``cell`` at ``key`` during a change to rows, columns or
        sheets, remembering the cell it replaces for Undo."""
        if self._pending:
            first = self._pending[0]
            if sheet.uid not in {state.sheet.uid for state in first.sheets}:
                first.other_cells.setdefault((sheet.uid, key), sheet.cells.get(key))
        sheet.cells[key] = cell
        sheet.touched()

    def set_width(self, sheet: Sheet, col: int, pixels: float) -> None:
        """Make column ``col`` ``pixels`` wide (saved into the file)."""
        sheet.widths[col] = max(4.0, float(pixels))
        sheet.changed_widths.add(col)
        sheet.changed = True

    # -- sheets ------------------------------------------------------------

    def unique_sheet_name(self, base: Optional[str] = None) -> str:
        """A name no sheet has: "Sheet 2", or ``base`` with a number."""
        if base is None:
            number = len(self.sheets) + 1
            while self.sheet_uid(_("Sheet {number}").format(number=number)) is not None:
                number += 1
            return _("Sheet {number}").format(number=number)
        if self.sheet_uid(base) is None:
            return base
        number = 2
        while self.sheet_uid(f"{base} {number}") is not None:
            number += 1
        return f"{base} {number}"

    def sheet_name_problem(self, name: str, sheet: Optional[Sheet] = None) -> Optional[str]:
        """Why ``name`` cannot name a sheet, or None."""
        if not name.strip():
            return _("A sheet needs a name.")
        if len(name) > 31:
            return _("A sheet name can have at most 31 characters.")
        if any(ch in name for ch in "[]:*?/\\") or name.startswith("'") or name.endswith("'"):
            return _("A sheet name cannot contain [ ] : * ? / \\ or begin or end with '.")
        other = self.sheet_uid(name)
        if other is not None and (sheet is None or other != sheet.uid):
            return _("Another sheet already has this name.")
        return None

    @_all_or_nothing
    def add_sheet(self, name: Optional[str] = None, index: Optional[int] = None) -> Snapshot:
        if self.is_text_table:
            raise SheetFileError(_("A text table has only one sheet."))
        snapshot = self.snapshot([])
        name = name or self.unique_sheet_name()
        index = len(self.sheets) if index is None else index
        sheet = Sheet(name)
        self.backend.add_sheet(self, sheet, index)
        self.sheets.insert(index, sheet)
        sheet.changed = True
        self._recalculate_after_structure([sheet])
        return snapshot

    @_all_or_nothing
    def duplicate_sheet(self, sheet: Sheet) -> Snapshot:
        if self.is_text_table:
            raise SheetFileError(_("A text table has only one sheet."))
        snapshot = self.snapshot([])
        name = self.unique_sheet_name(sheet.name)
        copy_ = Sheet(name)
        copy_.cells = dict(sheet.cells)
        copy_.widths = dict(sheet.widths)
        copy_.changed_widths = set(sheet.widths)
        copy_.heights = dict(sheet.heights)
        copy_.merges = list(sheet.merges)
        copy_.frozen = sheet.frozen
        copy_.default_width = sheet.default_width
        index = self.sheets.index(sheet) + 1
        self.backend.duplicate_sheet(self, sheet, copy_, index)
        self.sheets.insert(index, copy_)
        copy_.touched()
        self._recalculate_after_structure([copy_])
        return snapshot

    @_all_or_nothing
    def rename_sheet(self, sheet: Sheet, name: str) -> Snapshot:
        problem = self.sheet_name_problem(name, sheet)
        if problem:
            raise SheetFileError(problem)
        snapshot = self.snapshot([sheet])
        old = sheet.name
        sheet.name = name
        self.backend.rename_sheet(self, sheet, old, name)

        def rewrite(formula: str, home: Sheet) -> str:
            return sf.rename_sheet_in_formula(formula, old, name)
        self._rewrite_formulas(snapshot, rewrite)
        sheet.touched()
        self._recalculate_after_structure([sheet])
        return snapshot

    @_all_or_nothing
    def delete_sheet(self, sheet: Sheet) -> Snapshot:
        if len(self.sheets) <= 1:
            raise SheetFileError(_("A workbook needs at least one sheet."))
        if not sheet.hidden and not any(not other.hidden for other in self.sheets
                                        if other is not sheet):
            raise SheetFileError(_("A workbook needs at least one sheet that is not hidden."))
        snapshot = self.snapshot([sheet])
        name = sheet.name
        order = [each.name for each in self.sheets]
        self.backend.delete_sheet(self, sheet)
        self.sheets.remove(sheet)

        def rewrite(formula: str, home: Sheet) -> str:
            return sf.break_sheet_in_formula(formula, name, order)
        self._rewrite_formulas(snapshot, rewrite)
        self._recalculate_after_structure([sheet])
        return snapshot

    @_all_or_nothing
    def move_sheet(self, sheet: Sheet, index: int) -> Snapshot:
        snapshot = self.snapshot([])
        self.sheets.remove(sheet)
        self.sheets.insert(index, sheet)
        self.backend.reorder(self)
        return snapshot


def _shift_span(lo: int, hi: int, at: int, count: int) -> Optional[Tuple[int, int]]:
    """A span lo..hi after inserting (count > 0) or deleting rows at ``at``;
    None when all of it was deleted."""
    if count > 0:
        if lo >= at:
            lo += count
        if hi >= at and not (lo > at and hi < at):
            hi += count if hi >= at else 0
        return lo, hi
    gone_lo, gone_hi = at, at - count - 1
    if gone_lo <= lo and hi <= gone_hi:
        return None
    width = -count
    lo = lo - width if lo > gone_hi else (gone_lo if lo >= gone_lo else lo)
    hi = hi - width if hi > gone_hi else (gone_lo - 1 if hi >= gone_lo else hi)
    return lo, hi


# --------------------------------------------------------------------------- #
# Backends                                                                    #
# --------------------------------------------------------------------------- #

class _Backend:
    """What a file format adds to the model; the defaults do nothing."""

    results_in_source = False

    def defined_name(self, book, name, uid):
        return None

    def format_at(self, book, sheet, row, col) -> str:
        return "General"

    def named_formulas(self) -> Dict[str, str]:
        """The workbook's names and what they stand for ("=Data!$A$1:$A$9")."""
        return {}

    def _name_new_table_columns(self, book, sheet) -> None:
        pass

    def moved_source(self, source, block: Callable[[str], Optional[str]]):
        """A cell's file value after rows or columns moved (``block``
        moves a reference on the cell's sheet); the same object when
        nothing in it names a place."""
        return source

    def look(self, book, sheet, row, col, cell) -> "Look":
        return PLAIN

    def with_format(self, book, sheet, cell, number_format):
        return cell.edited(number_format=number_format, value=cell.value,
                           formula=cell.formula)

    def blank_cell(self, book, sheet, row, col) -> Cell:
        return Cell()

    def snapshot_sheet(self, book, sheet):
        return None

    def restore_sheet(self, book, sheet, state):
        pass

    def snapshot_book(self, book):
        return None

    def restore_book(self, book, state, order):
        pass

    def shift(self, book, sheet, axis, at, count):
        pass

    def rewrite_names(self, book, rewrite):
        pass

    def add_sheet(self, book, sheet, index):
        pass

    def duplicate_sheet(self, book, sheet, copy_, index):
        pass

    def rename_sheet(self, book, sheet, old, new):
        pass

    def delete_sheet(self, book, sheet):
        pass

    def reorder(self, book):
        pass

    def serialize(self, book) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        pass


def load_workbook(path: str, progress: Optional[Callable[[float], None]] = None) -> Workbook:
    """Read the spreadsheet at ``path``. Raises SheetFileError with a
    sentence for the person when it cannot be read."""
    kind = kind_of(path)
    if os.path.exists(path) and not regular_files.is_regular_file(path):
        # A pipe or a device with a spreadsheet's name: opening it would
        # wait, or read forever.
        raise SheetFileError(_("The file is damaged or is not a spreadsheet."))
    try:
        if kind not in ("xlsx", "xlsm", "ods", "csv", "tsv"):
            raise SheetFileError(_("MyEditor cannot edit this kind of spreadsheet."))
        # Read once; everything is parsed from these bytes, and the stamp
        # is the one they were read under.
        data, stamp = _read_stable(path)
        if kind in ("xlsx", "xlsm"):
            book = _load_xlsx(path, kind, progress, data)
        elif kind == "ods":
            book = _load_ods(path, progress, data)
        else:
            book = _load_text_table(path, kind, data)
        book._stamp, book._digest = stamp, _digest(data)
    except SheetFileError:
        raise
    except FileNotFoundError:
        raise SheetFileError(_("The file could not be found."))
    except PermissionError:
        raise SheetFileError(_("MyEditor is not allowed to read this file."))
    except (zipfile.BadZipFile, KeyError):
        raise SheetFileError(_("The file is damaged or is not a spreadsheet."))
    except MemoryError:
        raise SheetFileError(_("The spreadsheet is too large to open here."))
    except Exception as exc:     # a damaged file must give a message, not a crash
        _log.exception("%s could not be read", path)
        raise SheetFileError(_("The spreadsheet could not be read ({error}).").format(
            error=type(exc).__name__))
    book.engine.rebuild()
    missing = [(sheet.uid, r, c) for sheet in book.sheets
               for (r, c), cell in sheet.cells.items()
               if cell.formula and cell.value is None and cell.state != sf.STATE_APP]
    if missing:
        # Only the formulas the file holds no result for: the results it
        # holds were worked out by the spreadsheet app from these same
        # inputs.
        book.engine.calculate_all(missing, follow=False)
    for sheet in book.sheets:
        sheet.changed = False
    return book


# --------------------------------------------------------------------------- #
# Excel                                                                       #
# --------------------------------------------------------------------------- #

# A sheet keeps its column widths in Excel's own pixels: a width of N
# characters is N times the maximum digit width of Excel's Calibri 11
# (7 pixels) plus 5 of padding. Kept exact, so a width read is written
# back as it was; the grid scales it to the digits of the font it draws
# with (sheet_tab).
def _px_from_chars(width: float) -> float:
    return width * 7 + 5


def _chars_from_px(pixels: float) -> float:
    return round(max(pixels - 5, 0) / 7, 2)


class _XlsxBackend(_Backend):
    results_in_source = False

    def __init__(self, workbook, keep_vba: bool) -> None:
        self.wb = workbook
        self.keep_vba = keep_vba
        # What the file holds that openpyxl drops, put back on saving.
        self.kept: Optional[_Kept] = None
        self._looks: Optional[_XlsxLooks] = None
        self._line_styles: Dict[int, tuple] = {}

    def _name_new_table_columns(self, book, sheet) -> None:
        """A column inserted inside an Excel table gets a header, as in
        Excel ("Column3"): a table's header cells must name its columns."""
        from openpyxl.utils.cell import range_boundaries
        ws = sheet.backend
        for table in ws.tables.values():
            if table.headerRowCount == 0:
                continue
            try:
                min_col, min_row, max_col, _max_row = range_boundaries(table.ref)
            except (TypeError, ValueError):
                continue
            taken = set()
            for col in range(min_col - 1, max_col):
                cell = sheet.cells.get((min_row - 1, col))
                if cell is not None and cell.value not in (None, ""):
                    taken.add(str(cell.value).casefold())
            number = 1
            for col in range(min_col - 1, max_col):
                key = (min_row - 1, col)
                cell = sheet.cells.get(key)
                if cell is not None and cell.value not in (None, ""):
                    continue
                while f"column{number}" in taken:
                    number += 1
                taken.add(f"column{number}")
                base = cell or book.blank_cell(sheet, *key)
                book.replace_cell(sheet, key, base.edited(value=f"Column{number}"))

    def moved_source(self, source, block):
        from openpyxl.worksheet.formula import DataTableFormula
        if not isinstance(source, DataTableFormula):
            return source
        moved = copy.copy(source)
        for attribute in ("ref", "r1", "r2"):
            text = getattr(source, attribute, None)
            if text:
                setattr(moved, attribute, block(text) or text)
        return moved

    def look(self, book, sheet, row, col, cell):
        if self._looks is None:
            self._looks = _XlsxLooks(self.wb)
        if cell is not None:
            return self._looks.look(cell.style)
        style = self._line_style(sheet, row, col)
        return self._looks.look(style) if style is not None else PLAIN

    def _line_style(self, sheet, row, col):
        """The style a whole row or column gives its empty cells."""
        ws = sheet.backend
        found = self._line_styles.get(id(ws))
        if found is None or found[0] is not ws.row_dimensions or found[3] != (
                len(ws.row_dimensions), len(ws.column_dimensions)):
            rows = {key - 1: dim._style for key, dim in ws.row_dimensions.items()
                    if getattr(dim, "s", None) and getattr(dim, "customFormat", False)}
            cols = [((dim.min or 1) - 1, (dim.max or dim.min or 1) - 1, dim._style)
                    for dim in ws.column_dimensions.values()
                    if getattr(dim, "style_id", 0)]
            found = (ws.row_dimensions, rows, cols,
                     (len(ws.row_dimensions), len(ws.column_dimensions)))
            self._line_styles[id(ws)] = found
        _dims, rows, cols, _size = found
        if row in rows:
            return rows[row]
        for low, high, style in cols:
            if low <= col <= high:
                return style
        return None

    def close(self) -> None:
        archive = getattr(self.wb, "vba_archive", None)
        if archive is not None:
            try:
                archive.close()
            except (OSError, ValueError):
                pass

    # -- names -------------------------------------------------------------

    def defined_name(self, book, name, uid):
        sheet = book.by_uid(uid)
        ws = sheet.backend
        found = None
        try:
            found = ws.defined_names.get(name)
        except AttributeError:
            found = None
        if found is None:
            for key, value in self.wb.defined_names.items():
                if key.casefold() == name.casefold():
                    found = value
                    break
        if found is None or not found.attr_text:
            return None
        return "=" + found.attr_text

    def rewrite_names(self, book, rewrite):
        """Every formula and reference the workbook keeps outside its cells'
        own formulas, through ``rewrite`` (formula text, the sheet it is
        on): defined names, conditional-format rules, validation lists and
        limits, and the targets of links within the workbook."""
        holders = [self.wb.defined_names] + [ws.defined_names for ws in self.wb.worksheets
                                            if hasattr(ws, "defined_names")]
        home = book.sheets[0] if book.sheets else None
        for names in holders:
            for defined in names.values():
                if defined.attr_text and home is not None:
                    new = rewrite("=" + defined.attr_text, home)
                    defined.attr_text = new[1:]
        if self.kept is not None:
            _rewrite_kept(self.kept, book, rewrite)
        for sheet in book.sheets:
            ws = sheet.backend
            for cf_rules in ws.conditional_formatting._cf_rules.values():
                for rule in cf_rules:
                    if rule.formula:
                        rule.formula = [rewrite("=" + text, sheet)[1:] for text in rule.formula]
            for dv in ws.data_validations.dataValidation:
                for attribute in ("formula1", "formula2"):
                    text = getattr(dv, attribute)
                    if text:
                        bare = text[1:] if text.startswith("=") else text
                        new = rewrite("=" + bare, sheet)[1:]
                        if new != bare:
                            setattr(dv, attribute, ("=" if text.startswith("=") else "") + new)
            for key, cell in list(sheet.cells.items()):
                if not isinstance(cell.extra, tuple) or cell.extra[1] is None:
                    continue
                comment, link = cell.extra
                if not link.location:
                    continue
                new = rewrite("=" + link.location, sheet)[1:]
                if new != link.location:
                    moved = copy.copy(link)
                    moved.location = new
                    book.replace_cell(sheet, key, dataclasses.replace(cell, extra=(comment,
                                                                                   moved)))

    # -- formats and styles ------------------------------------------------

    def format_at(self, book, sheet, row, col):
        return "General"

    def with_format(self, book, sheet, cell, number_format):
        return cell.edited(number_format=number_format, value=cell.value, formula=cell.formula)

    def blank_cell(self, book, sheet, row, col):
        ws = sheet.backend
        from openpyxl.utils import get_column_letter
        letter = get_column_letter(col + 1)
        dimension = ws.column_dimensions.get(letter) if letter in ws.column_dimensions else None
        if dimension is not None and dimension.style_id:
            return Cell(style=copy.copy(dimension._style),
                        number_format=dimension.number_format or "General")
        row_dimension = ws.row_dimensions.get(row + 1) if (row + 1) in ws.row_dimensions else None
        if row_dimension is not None and row_dimension.s:
            return Cell(style=copy.copy(row_dimension._style),
                        number_format=row_dimension.number_format or "General")
        return Cell()

    # -- structure ---------------------------------------------------------

    def snapshot_sheet(self, book, sheet):
        ws = sheet.backend
        return _XlsxSheetMeta.capture(ws)

    def restore_sheet(self, book, sheet, state):
        state.restore(sheet.backend)
        self._line_styles.clear()

    def snapshot_book(self, book):
        names = {key: value.attr_text for key, value in self.wb.defined_names.items()}
        local = {ws.title: {k: v.attr_text for k, v in ws.defined_names.items()}
                 for ws in self.wb.worksheets if hasattr(ws, "defined_names")}
        rules = [(rule, list(rule.formula or ())) for ws in self.wb.worksheets
                 for cf_rules in ws.conditional_formatting._cf_rules.values()
                 for rule in cf_rules]
        validations = [(dv, dv.formula1, dv.formula2) for ws in self.wb.worksheets
                       for dv in ws.data_validations.dataValidation]
        return (list(self.wb._sheets), names, local, self.wb.active,
                {id(ws): ws.title for ws in self.wb._sheets}, rules, validations,
                self.kept.snapshot() if self.kept is not None else None)

    def restore_book(self, book, state, order):
        sheets, names, local, active, titles, rules, validations, kept = state
        if kept is not None and self.kept is not None:
            self.kept.restore(kept)
        self.wb._sheets = list(sheets)
        if active is not None and active in self.wb._sheets:
            self.wb.active = self.wb._sheets.index(active)
        for rule, formulas in rules:
            rule.formula = list(formulas)
        for dv, formula1, formula2 in validations:
            dv.formula1, dv.formula2 = formula1, formula2
        for ws in self.wb._sheets:
            if id(ws) in titles and ws.title != titles[id(ws)]:
                ws.title = titles[id(ws)]
        for key, text in names.items():
            if key in self.wb.defined_names:
                self.wb.defined_names[key].attr_text = text
        for ws in self.wb.worksheets:
            if hasattr(ws, "defined_names") and ws.title in local:
                for key, text in local[ws.title].items():
                    if key in ws.defined_names:
                        ws.defined_names[key].attr_text = text
        for sheet in order:
            sheet.changed = True

    def shift(self, book, sheet, axis, at, count):
        _shift_xlsx_meta(sheet.backend, sheet.name, axis, at, count)
        # Frozen panes as the model worked them out (a row inserted at the
        # first scrolling row leaves the frozen rows as they are).
        sheet.backend.freeze_panes = sf.cell_name(*sheet.frozen) if sheet.frozen else None
        self._line_styles.clear()
        if axis == "col":
            self._name_new_table_columns(book, sheet)

    def add_sheet(self, book, sheet, index):
        ws = self.wb.create_sheet(sheet.name, self._wb_index(book, index))
        sheet.backend = ws

    def _wb_index(self, book, index):
        """Where the model's sheet ``index`` goes among openpyxl's sheets
        (which also hold chart sheets)."""
        if index >= len(book.sheets):
            return len(self.wb._sheets)
        return self.wb._sheets.index(book.sheets[index].backend)

    def duplicate_sheet(self, book, sheet, copy_, index):
        ws = self.wb.copy_worksheet(sheet.backend)
        ws.title = copy_.name
        self.wb._sheets.remove(ws)
        self.wb._sheets.insert(self._wb_index(book, index), ws)
        copy_.backend = ws

    def rename_sheet(self, book, sheet, old, new):
        sheet.backend.title = new

    def delete_sheet(self, book, sheet):
        ws = sheet.backend
        active = self.wb.active
        self.wb._sheets.remove(ws)
        if active is ws or active is None or active not in self.wb._sheets:
            # The first sheet that is not hidden takes over; Excel refuses a
            # file whose active sheet is hidden.
            visible = [index for index, each in enumerate(self.wb._sheets)
                       if getattr(each, "sheet_state", "visible") == "visible"]
            self.wb.active = visible[0] if visible else 0
        for other in self.wb.worksheets:
            for view in other.views.sheetView:
                view.tabSelected = other is self.wb.active

    def reorder(self, book):
        # openpyxl keeps the active sheet as a position: the same sheet stays
        # active wherever it moves, so the file opens on it as before.
        active = self.wb.active
        others = [ws for ws in self.wb._sheets if all(ws is not s.backend for s in book.sheets)]
        self.wb._sheets = [sheet.backend for sheet in book.sheets] + others
        if active in self.wb._sheets:
            self.wb.active = self.wb._sheets.index(active)

    # -- writing -----------------------------------------------------------

    def serialize(self, book) -> bytes:
        from openpyxl.cell.cell import Cell as XCell, MergedCell
        for sheet in book.sheets:
            ws = sheet.backend
            if sheet.changed or sheet.changed_widths:
                _write_xlsx_cells(ws, sheet, XCell, MergedCell)
                _write_xlsx_widths(ws, sheet)
        _sync_xlsx_tables(book)
        try:
            from openpyxl.workbook.properties import CalcProperties
            if self.wb.calculation is None:
                self.wb.calculation = CalcProperties()
            self.wb.calculation.fullCalcOnLoad = True
        except Exception:     # an old openpyxl: the cached results still help
            _log.debug("could not ask for a calculation on load", exc_info=True)
        buffer = io.BytesIO()
        self.wb.save(buffer)
        parts = {sheet.backend.path.lstrip("/"): sheet for sheet in book.sheets}
        written = _write_cached_results(buffer.getvalue(), parts, book.date1904)
        if self.kept is not None:
            written = _put_back(written, book, self.kept)
        return written


def _xlsx_value(cell: Cell):
    """The value and openpyxl data type a model cell is written as."""
    from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
    if cell.formula is not None and cell.array:
        # An array formula, over the block it fills now.
        return ArrayFormula(cell.array, sf.to_file_formula(cell.formula)), "f"
    if cell.source is not UNSET and cell.source is not None:
        source = cell.source
        if isinstance(source, DataTableFormula):
            return source, "f"
        if cell.formula is not None and isinstance(source, str):
            return source, "f"
        if isinstance(source, str) or type(source).__name__ == "CellRichText":
            return source, "s"
    if cell.formula is not None:
        return sf.to_file_formula(cell.formula), "f"
    value = cell.value
    if value is None or value == "":
        return None, "n"
    if isinstance(value, bool):
        return value, "b"
    if isinstance(value, (int, float)):
        return value, "n"
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        return value, "d"
    if isinstance(value, sf.CellError):
        return value.code, "e"
    return _excel_escaped(str(value)), "s"


_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _excel_escaped(text: str) -> str:
    """Text with the control characters XML cannot hold written as Excel
    writes them (a vertical tab as "_x000B_"), so Excel reads them back."""
    if not _XML_ILLEGAL.search(text):
        return text
    return _XML_ILLEGAL.sub(lambda m: f"_x{ord(m.group(0)):04X}_", text)


def _shown_format(cell: Cell) -> str:
    """The number format a cell is written with: a date or time in a
    cell without a format gets one, or Excel would show its serial number."""
    if (not cell.number_format or cell.number_format == "General") and \
            isinstance(cell.value, (_dt.date, _dt.time)):
        return _default_date_code(cell.value)
    return cell.number_format


def _write_xlsx_cells(ws, sheet, XCell, MergedCell) -> None:
    cells = {}
    hyperlinks = []
    for (r, c), cell in sorted(sheet.cells.items()):
        if cell.extra == "merged":
            target = MergedCell(ws, row=r + 1, column=c + 1)
            if cell.style is not None:
                target._style = copy.copy(cell.style)
            cells[(r + 1, c + 1)] = target
            continue
        target = XCell(ws, row=r + 1, column=c + 1)
        if cell.style is not None:
            target._style = copy.copy(cell.style)
        value, data_type = _xlsx_value(cell)
        target._value = value
        target.data_type = data_type
        number_format = _shown_format(cell)
        if number_format and target.number_format != number_format:
            target.number_format = number_format
        if isinstance(cell.extra, tuple):
            comment, hyperlink = cell.extra
            if comment is not None:
                target.comment = comment
            if hyperlink is not None:
                link = copy.copy(hyperlink)
                link.ref = target.coordinate
                target._hyperlink = link
                hyperlinks.append(link)
        cells[(r + 1, c + 1)] = target
    ws._cells = cells
    ws._hyperlinks = []


def _write_xlsx_widths(ws, sheet) -> None:
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.dimensions import ColumnDimension
    for col in sorted(sheet.changed_widths):
        index = col + 1
        # Split a group of columns (min..max) that holds this one, so
        # only this column changes.
        for key, dim in list(ws.column_dimensions.items()):
            low, high = dim.min or 0, dim.max or 0
            if low and high and low <= index <= high and (low != index or high != index):
                del ws.column_dimensions[key]
                pieces = [(low, index - 1), (index, index), (index + 1, high)]
                for start, end in pieces:
                    if start > end:
                        continue
                    piece = copy.copy(dim)
                    piece.min, piece.max = start, end
                    piece.index = get_column_letter(start)
                    ws.column_dimensions[get_column_letter(start)] = piece
                break
        letter = get_column_letter(index)
        dim = ws.column_dimensions.get(letter) if letter in ws.column_dimensions else None
        if dim is None:
            dim = ColumnDimension(ws, index=letter)
            ws.column_dimensions[letter] = dim
        dim.min = dim.max = index
        dim.width = _chars_from_px(sheet.widths.get(col, sheet.default_width))


def _sync_xlsx_tables(book) -> None:
    """Keep each Excel table's column names equal to its header cells, as
    Excel requires (it repairs the file otherwise)."""
    from openpyxl.worksheet.table import TableColumn
    from openpyxl.utils.cell import range_boundaries
    for sheet in book.sheets:
        ws = sheet.backend
        for table in getattr(ws, "tables", {}).values():
            try:
                min_col, min_row, max_col, max_row = range_boundaries(table.ref)
            except (TypeError, ValueError):
                continue
            if not table.headerRowCount and table.headerRowCount is not None:
                continue
            columns = list(table.tableColumns)
            names, seen = [], set()
            for offset, col in enumerate(range(min_col, max_col + 1)):
                cell = sheet.cells.get((min_row - 1, col - 1))
                name = "" if cell is None or cell.value is None else sf.number_text(cell.value) \
                    if isinstance(cell.value, (int, float)) else str(cell.value)
                name = name or f"Column{offset + 1}"
                base, number = name, 2
                while name.casefold() in seen:
                    name, number = f"{base}{number}", number + 1
                seen.add(name.casefold())
                names.append(name)
            if len(columns) == len(names) and all(c.name == n for c, n in zip(columns, names)):
                continue
            rebuilt = []
            for offset, name in enumerate(names):
                if offset < len(columns):
                    column = columns[offset]
                    column.name = name
                else:
                    column = TableColumn(id=offset + 1, name=name)
                rebuilt.append(column)
            for offset, column in enumerate(rebuilt):
                column.id = offset + 1
            table.tableColumns = rebuilt
            if table.autoFilter is not None:
                table.autoFilter.ref = table.ref


class _XlsxSheetMeta:
    """What an openpyxl sheet keeps besides its cells, for Undo."""

    def __init__(self) -> None:
        self.parts = {}

    @classmethod
    def capture(cls, ws) -> "_XlsxSheetMeta":
        meta = cls()
        if not hasattr(ws, "merged_cells"):
            return meta
        meta.parts["merged"] = [str(r) for r in ws.merged_cells.ranges]
        meta.parts["rows"] = [(key, dim) for key, dim in ws.row_dimensions.items()]
        meta.parts["cols"] = [(key, dim, dim.min, dim.max, dim.index, dim.width)
                              for key, dim in ws.column_dimensions.items()]
        meta.parts["cf"] = [(cf, str(cf.sqref), list(rules))
                            for cf, rules in ws.conditional_formatting._cf_rules.items()]
        meta.parts["dv"] = [(dv, str(dv.sqref)) for dv in ws.data_validations.dataValidation]
        meta.parts["filter"] = ws.auto_filter.ref
        meta.parts["freeze"] = ws.freeze_panes
        meta.parts["print_area"] = ws._print_area
        meta.parts["print_rows"] = ws._print_rows
        meta.parts["print_cols"] = ws._print_cols
        meta.parts["tables"] = [(table, table.ref) for table in ws.tables.values()]
        meta.parts["table_names"] = dict(ws.tables)
        meta.parts["title"] = ws.title
        return meta

    def restore(self, ws) -> None:
        if not self.parts:
            return
        from openpyxl.worksheet.cell_range import MultiCellRange
        from openpyxl.formatting.formatting import ConditionalFormattingList
        ws.merged_cells = MultiCellRange(" ".join(self.parts["merged"]))
        ws.row_dimensions.clear()
        for key, dim in self.parts["rows"]:
            dim.index = key
            ws.row_dimensions[key] = dim
        ws.column_dimensions.clear()
        for key, dim, low, high, index, width in self.parts["cols"]:
            dim.min, dim.max, dim.index = low, high, index
            dim.width = width
            ws.column_dimensions[key] = dim
        rules = ConditionalFormattingList()
        for cf, sqref, cf_rules in self.parts["cf"]:
            cf.sqref = MultiCellRange(sqref)
            rules._cf_rules[cf] = cf_rules
            rules.max_priority = max([rules.max_priority] + [r.priority or 0 for r in cf_rules])
        ws.conditional_formatting = rules
        ws.data_validations.dataValidation = []
        for dv, sqref in self.parts["dv"]:
            dv.sqref = MultiCellRange(sqref)
            ws.data_validations.dataValidation.append(dv)
        ws.auto_filter.ref = self.parts["filter"]
        ws.freeze_panes = self.parts["freeze"]
        ws._print_area = self.parts["print_area"]
        ws._print_rows = self.parts["print_rows"]
        ws._print_cols = self.parts["print_cols"]
        ws.tables.clear()
        for name, table in self.parts["table_names"].items():
            dict.__setitem__(ws.tables, name, table)
        for table, ref in self.parts["tables"]:
            table.ref = ref
            if table.autoFilter is not None:
                table.autoFilter.ref = ref


def _shift_range_text(text: str, sheet_name: str, axis: str, at: int, count: int) -> Optional[str]:
    """A range ("A1:B3", "A1:B3 D4") after rows or columns moved; None when
    all of it was deleted."""
    if not text:
        return text
    kept = []
    for part in str(text).split():
        moved = sf.adjust_for_insert_delete("=" + part, home=sheet_name, target=sheet_name,
                                            axis=axis, at=at, count=count)[1:]
        if "#REF!" not in moved:
            kept.append(moved)
    return " ".join(kept) if kept else None


def _shift_xlsx_meta(ws, name: str, axis: str, at: int, count: int) -> None:
    """Move what an openpyxl sheet keeps besides its cells (merged cells,
    row heights, column widths, conditional formats, validation, filter,
    frozen panes, print titles, tables) along with inserted or deleted
    rows or columns."""
    from openpyxl.worksheet.cell_range import MultiCellRange
    from openpyxl.formatting.formatting import ConditionalFormattingList
    from openpyxl.utils import get_column_letter
    if not hasattr(ws, "merged_cells"):
        return
    merged = []
    for cell_range in ws.merged_cells.ranges:
        moved = _shift_range_text(str(cell_range), name, axis, at, count)
        if moved and ":" in moved:
            merged.append(moved)
    ws.merged_cells = MultiCellRange(" ".join(merged))

    def moved_index(index0: int) -> Optional[int]:
        if count > 0:
            return index0 + count if index0 >= at else index0
        if at <= index0 < at - count:
            return None
        return index0 + count if index0 >= at - count else index0

    if axis == "row":
        rows = {}
        for key, dim in list(ws.row_dimensions.items()):
            new = moved_index(key - 1)
            if new is None:
                continue
            dim.index = new + 1
            rows[new + 1] = dim
        ws.row_dimensions.clear()
        for key in sorted(rows):
            ws.row_dimensions[key] = rows[key]
    else:
        cols = {}
        for key, dim in list(ws.column_dimensions.items()):
            low, high = (dim.min or 1) - 1, (dim.max or dim.min or 1) - 1
            span = _shift_span(low, high, at, count)
            if span is None:
                continue
            dim.min, dim.max = span[0] + 1, span[1] + 1
            dim.index = get_column_letter(dim.min)
            cols[dim.index] = dim
        ws.column_dimensions.clear()
        for key, dim in cols.items():
            ws.column_dimensions[key] = dim
    rules = ConditionalFormattingList()
    for cf, cf_rules in ws.conditional_formatting._cf_rules.items():
        moved = _shift_range_text(str(cf.sqref), name, axis, at, count)
        if not moved:
            continue
        cf.sqref = MultiCellRange(moved)
        rules._cf_rules[cf] = cf_rules
    rules.max_priority = ws.conditional_formatting.max_priority
    ws.conditional_formatting = rules
    kept = []
    for dv in ws.data_validations.dataValidation:
        moved = _shift_range_text(str(dv.sqref), name, axis, at, count)
        if moved:
            dv.sqref = MultiCellRange(moved)
            kept.append(dv)
    ws.data_validations.dataValidation = kept
    if ws.auto_filter.ref:
        ws.auto_filter.ref = _shift_range_text(ws.auto_filter.ref, name, axis, at, count)
    for table in list(ws.tables.values()):
        moved = _shift_range_text(table.ref, name, axis, at, count)
        if moved:
            table.ref = moved
            if table.autoFilter is not None:
                table.autoFilter.ref = moved
        else:
            del ws.tables[table.name]
    # Rows and columns printed on every page ("1:1", "A:B"): openpyxl keeps
    # them as ranges of its own, written and read as text.
    if ws.print_title_rows and axis == "row":
        moved = _shift_range_text(ws.print_title_rows, name, axis, at, count)
        ws._print_rows = None
        ws.print_title_rows = moved
    if ws.print_title_cols and axis == "col":
        moved = _shift_range_text(ws.print_title_cols, name, axis, at, count)
        ws._print_cols = None
        ws.print_title_cols = moved
    try:
        area = ws._print_area
        if area:
            text = ",".join(str(r.coord) for r in area)
            moved = _shift_range_text(text.replace(",", " "), name, axis, at, count)
            ws.print_area = moved.split() if moved else None
    except Exception:     # print areas are a convenience; never fail a structural edit
        _log.debug("print area not moved", exc_info=True)


def _load_xlsx(path: str, kind: str, progress, data: bytes) -> Workbook:
    import openpyxl
    from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
    keep_vba = kind == "xlsm"
    if data[:8].startswith(b"\xd0\xcf\x11\xe0"):
        raise SheetFileError(_("The workbook is locked with a password or uses Excel's old "
                               "format. Open it in the spreadsheet app."))
    with warnings.catch_warnings():
        # openpyxl warns about the extensions it drops; unsaveable_parts
        # names them to the person instead.
        warnings.simplefilter("ignore", UserWarning)
        wb = openpyxl.load_workbook(io.BytesIO(data), keep_vba=keep_vba, keep_links=True,
                                    rich_text=True)
    book = Workbook(path, kind, _XlsxBackend(wb, keep_vba))
    book.backend.kept = _read_kept(data, wb)
    book.date1904 = bool(getattr(wb, "epoch", None) and wb.epoch.year == 1904)
    book.engine.date1904 = book.date1904
    book.unsaveable = unsaveable_parts(path, data)
    formulas: Dict[str, List[Tuple[int, int]]] = {}
    total = 0
    worksheets = wb.worksheets
    for index, ws in enumerate(worksheets):
        sheet = Sheet(ws.title, ws)
        sheet.hidden = ws.sheet_state != "visible"
        for (row, col), xcell in ws._cells.items():
            total += 1
            if total > MAX_CELLS:
                raise SheetFileError(_("The spreadsheet is too large to open here."))
            r, c = row - 1, col - 1
            style = copy.copy(xcell._style) if xcell.has_style else None
            number_format = xcell.number_format if xcell.has_style else "General"
            if type(xcell).__name__ == "MergedCell":
                sheet.cells[(r, c)] = Cell(style=style, extra="merged",
                                           number_format=number_format, source=None)
                continue
            raw = xcell._value
            extra = None
            if xcell._comment is not None or xcell._hyperlink is not None:
                extra = (xcell._comment, xcell._hyperlink)
            formula = None
            state = ""
            value = raw
            array = None
            if xcell.data_type == "f":
                if isinstance(raw, ArrayFormula):
                    formula = raw.text if raw.text and raw.text.startswith("=") else \
                        "=" + (raw.text or "")
                    array = raw.ref
                    state = sf.STATE_APP
                elif isinstance(raw, DataTableFormula):
                    formula = "=TABLE()"
                    state = sf.STATE_APP
                else:
                    formula = raw
                    if sf.needs_app(formula):
                        state = sf.STATE_APP
                value = None
                formulas.setdefault(ws.title, []).append((r, c))
            elif xcell.data_type == "e":
                value = sf.ERROR_CODES.get(str(raw), sf.CellError(str(raw)))
            elif type(raw).__name__ == "CellRichText":
                value = str(raw)
            if raw is None and style is None and extra is None:
                continue
            sheet.cells[(r, c)] = Cell(value=value, formula=formula,
                                       number_format=number_format or "General",
                                       style=style, extra=extra, source=raw, state=state,
                                       array=array)
        for key, dim in ws.column_dimensions.items():
            if dim.width and dim.customWidth is not False:
                low = (dim.min or 1) - 1
                high = (dim.max or dim.min or 1) - 1
                for col in range(low, min(high, low + 1024) + 1):
                    sheet.widths[col] = _px_from_chars(dim.width)
        default = ws.sheet_format.defaultColWidth
        if default:
            sheet.default_width = _px_from_chars(float(default))
        elif ws.sheet_format.baseColWidth:
            sheet.default_width = _px_from_chars(float(ws.sheet_format.baseColWidth) + 0.71)
        for key, dim in ws.row_dimensions.items():
            if dim.ht:
                sheet.heights[key - 1] = float(dim.ht) * 96 / 72
        if ws.sheet_format.defaultRowHeight:
            sheet.default_height = float(ws.sheet_format.defaultRowHeight) * 96 / 72
        for merged in ws.merged_cells.ranges:
            sheet.merges.append((merged.min_row - 1, merged.min_col - 1,
                                 merged.max_row - 1, merged.max_col - 1))
        if ws.freeze_panes:
            spot = sf.parse_cell_name(ws.freeze_panes)
            if spot and spot != (0, 0):
                sheet.frozen = spot
        book.sheets.append(sheet)
        if progress:
            progress(0.6 * (index + 1) / max(len(worksheets), 1))
    if formulas:
        _read_cached_results(data, book, formulas)
    if progress:
        progress(0.9)
    return book


def _read_cached_results(data: bytes, book: Workbook, formulas) -> None:
    """The results the spreadsheet app stored with each formula."""
    import openpyxl
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            cached = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True,
                                            keep_links=False)
    except Exception:     # the results are a help; without them we calculate
        _log.debug("cached results unreadable", exc_info=True)
        return
    try:
        for title, positions in formulas.items():
            if title not in cached.sheetnames:
                continue
            sheet = book.sheet(title)
            ws = cached[title]
            wanted = set(positions)
            top = max(r for r, _c in positions) + 1
            for row in ws.iter_rows(min_row=1, max_row=top):
                for xcell in row:
                    if not hasattr(xcell, "row") or xcell.row is None:
                        continue
                    key = (xcell.row - 1, xcell.column - 1)
                    if key not in wanted:
                        continue
                    value = xcell.value
                    if xcell.data_type == "e":
                        value = sf.ERROR_CODES.get(str(value), sf.CellError(str(value)))
                    cell = sheet.cells.get(key)
                    if cell is not None and value is not None:
                        sheet.cells[key] = dataclasses.replace(cell, value=value)
    finally:
        cached.close()


_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _write_cached_results(package: bytes, parts: Dict[str, Sheet], date1904: bool) -> bytes:
    """``package`` (an .xlsx openpyxl wrote) with each formula's result in
    its <v>, so other apps and previews show it before calculating."""
    from lxml import etree
    source = zipfile.ZipFile(io.BytesIO(package))
    out = io.BytesIO()
    with source, zipfile.ZipFile(out, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            sheet = parts.get(info.filename)
            if sheet is not None and any(cell.formula for cell in sheet.cells.values()):
                data = _fill_results(data, sheet, date1904, etree)
            target.writestr(info, data, compress_type=info.compress_type)
    return out.getvalue()


def _fill_results(data: bytes, sheet: Sheet, date1904: bool, etree) -> bytes:
    parser = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(data, parser)
    ns = {"m": _SHEET_NS}
    for element in root.iterfind(".//m:sheetData/m:row/m:c", ns):
        formula = element.find("m:f", ns)
        if formula is None:
            continue
        spot = sf.parse_cell_name(element.get("r", ""))
        if spot is None:
            continue
        cell = sheet.cells.get(spot)
        value_element = element.find("m:v", ns)
        value = None if cell is None else cell.value
        if cell is not None and cell.state == sf.STATE_CIRCULAR:
            value = None
        if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
            value = sf.to_serial(value, date1904)
        if value is None or (isinstance(value, sf.CellError) and value.code not in
                             sf.ERROR_CODES):
            if value_element is not None:
                element.remove(value_element)
            element.attrib.pop("t", None)
            continue
        if value_element is None:
            value_element = etree.SubElement(element, f"{{{_SHEET_NS}}}v")
        if isinstance(value, bool):
            element.set("t", "b")
            value_element.text = "1" if value else "0"
        elif isinstance(value, (int, float)):
            element.attrib.pop("t", None)
            value_element.text = repr(float(value)) if isinstance(value, float) else str(value)
            if value_element.text.endswith(".0"):
                value_element.text = value_element.text[:-2]
        elif isinstance(value, sf.CellError):
            element.set("t", "e")
            value_element.text = value.code
        else:
            element.set("t", "str")
            value_element.text = str(value)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _model_to_xlsx(book: Workbook) -> bytes:
    """A new Excel workbook holding the model's sheets (an .ods or a text
    table saved as .xlsx): values, formulas, number formats and widths."""
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.utils.datetime import CALENDAR_MAC_1904
    from openpyxl.workbook.defined_name import DefinedName
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    if book.date1904:
        # Results were worked out counting days from 1904: the new
        # workbook counts the same way, so every date stays the same day.
        wb.epoch = CALENDAR_MAC_1904
    parts = {}
    for sheet in book.sheets:
        ws = wb.create_sheet(sheet.name[:31])
        for (r, c), cell in sorted(sheet.cells.items()):
            if cell.is_empty:
                continue
            target = ws.cell(row=r + 1, column=c + 1)
            if cell.formula is not None:
                target._value = sf.to_file_formula(cell.formula)
                target.data_type = "f"
            elif book.is_text_table:
                # Plain numbers become numbers; anything else ("007",
                # "12,5", "=A1") stays the text it was.
                text = str(cell.value)
                if re.fullmatch(r"-?(0|[1-9]\d{0,14})(\.\d+)?", text):
                    target.value = sf._clean(float(text))
                else:
                    target._value = text
                    target.data_type = "s"
            else:
                value, data_type = _xlsx_value(dataclasses.replace(cell, source=UNSET))
                target._value = value
                target.data_type = data_type
            number_format = _shown_format(cell)
            if number_format and number_format != "General":
                target.number_format = number_format
        for col, width in sheet.widths.items():
            ws.column_dimensions[get_column_letter(col + 1)].width = _chars_from_px(width)
        if sheet.frozen:
            ws.freeze_panes = sf.cell_name(*sheet.frozen)
        parts[sheet] = ws
    for name, text in book.backend.named_formulas().items():
        # The names formulas use (an OpenDocument file's named ranges and
        # expressions), so none of them becomes #NAME? in Excel.
        wb.defined_names[name] = DefinedName(name, attr_text=sf.to_file_formula(text)[1:])
    if wb.calculation is not None:
        wb.calculation.fullCalcOnLoad = True
    buffer = io.BytesIO()
    wb.save(buffer)
    by_part = {ws.path.lstrip("/"): sheet for sheet, ws in parts.items()}
    return _write_cached_results(buffer.getvalue(), by_part, book.date1904)


# --------------------------------------------------------------------------- #
# Text tables                                                                 #
# --------------------------------------------------------------------------- #

class _TextBackend(_Backend):
    def __init__(self, encoding: str, bom: bytes, newline: str, delimiter: str,
                 final_newline: bool, quote_all: bool) -> None:
        self.encoding = encoding
        self.bom = bom
        self.newline = newline
        self.delimiter = delimiter
        self.final_newline = final_newline
        self.quote_all = quote_all

    def snapshot_sheet(self, book, sheet):
        return list(sheet.backend)

    def restore_sheet(self, book, sheet, state):
        sheet.backend = list(state)

    def shift(self, book, sheet, axis, at, count):
        lengths = sheet.backend
        if axis == "row":
            if count > 0:
                # New lines have as many fields as their neighbour, so the
                # table stays rectangular for the apps that read it.
                near = lengths[at] if at < len(lengths) else (lengths[-1] if lengths else 0)
                lengths[at:at] = [near] * count
            else:
                del lengths[at:at - count]
        else:
            for i, length in enumerate(lengths):
                if length > at:
                    lengths[i] = max(at, length + count) if count < 0 else length + count

    def serialize(self, book) -> bytes:
        sheet = book.sheets[0]
        return _text_table_bytes(sheet, self.delimiter, self.newline, self.encoding, self.bom,
                                 self.final_newline, self.quote_all, sheet.backend)


def _needs_quotes(text: str, delimiter: str) -> bool:
    return any(ch in text for ch in (delimiter, '"', "\n", "\r"))


def _text_table_bytes(sheet: Sheet, delimiter: str, newline: str, encoding: str, bom: bytes,
                      final_newline: bool, quote_all: bool, lengths: Sequence[int],
                      as_text: Optional[Callable[[Cell], str]] = None) -> bytes:
    rows, cols = sheet.used_size()
    count = max(rows, len(lengths))
    by_row: Dict[int, Dict[int, Cell]] = {}
    for (r, c), cell in sheet.cells.items():
        by_row.setdefault(r, {})[c] = cell
    # A line past the file's last one is as wide as the widest line, so
    # the table stays rectangular.
    usual = max(lengths, default=0)
    lines = []
    for r in range(count):
        row_cells = by_row.get(r, {})
        width = max([lengths[r] if r < len(lengths) else usual] +
                    [c + 1 for c, cell in row_cells.items() if not cell.is_empty or
                     (isinstance(cell.extra, bool) and cell.extra)])
        fields = []
        for c in range(width):
            cell = row_cells.get(c)
            if cell is None:
                text, quoted = "", False
            else:
                text = as_text(cell) if as_text else ("" if cell.value is None else
                                                      str(cell.value))
                quoted = bool(cell.extra) if isinstance(cell.extra, bool) else False
            if quoted or (quote_all and text) or _needs_quotes(text, delimiter):
                text = '"' + text.replace('"', '""') + '"'
            fields.append(text)
        lines.append(delimiter.join(fields))
    text = newline.join(lines)
    if final_newline and lines:
        text += newline
    try:
        data = text.encode(encoding)
    except UnicodeEncodeError:
        raise SheetFileError(_("Some characters cannot be written in this file's text "
                               "encoding ({encoding}).").format(encoding=encoding))
    return bom + data


def _parse_text_table(text: str, delimiter: str) -> Tuple[List[List[Tuple[str, bool]]], bool]:
    """Rows of (field, was quoted), and whether the text ends with a line
    break. Quotes, doubled quotes and line breaks inside quotes as RFC 4180
    has them; a stray quote inside a field is kept as text."""
    rows: List[List[Tuple[str, bool]]] = []
    row: List[Tuple[str, bool]] = []
    field: List[str] = []
    quoted = False
    in_quotes = False
    i, n = 0, len(text)
    started = False
    while i < n:
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    field.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            field.append(ch)
            i += 1
            continue
        if ch == '"' and not field and not quoted:
            in_quotes = True
            quoted = True
            started = True
            i += 1
            continue
        if ch == delimiter:
            row.append(("".join(field), quoted))
            field, quoted, started = [], False, True
            i += 1
            continue
        if ch in "\r\n":
            if started or field or row:
                row.append(("".join(field), quoted))
            rows.append(row)
            row, field, quoted, started = [], [], False, False
            if ch == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
            i += 1
            continue
        field.append(ch)
        started = True
        i += 1
    final_newline = n > 0 and text[-1] in "\r\n" and not in_quotes
    if started or field or row:
        row.append(("".join(field), quoted))
        rows.append(row)
    return rows, final_newline


def _sniff_delimiter(text: str, kind: str) -> str:
    if kind == "tsv":
        return "\t"
    sample = "\n".join(text.splitlines()[:50])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        pass
    counts = {d: sample.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _decode(data: bytes) -> Tuple[str, str, bytes]:
    """Text, its encoding and its byte order mark."""
    for bom, encoding in ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"),
                          (b"\xfe\xff", "utf-16-be")):
        if data.startswith(bom):
            return data[len(bom):].decode(encoding), encoding, bom
    try:
        return data.decode("utf-8"), "utf-8", b""
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252", b""
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1", b""


def _load_text_table(path: str, kind: str, data: bytes) -> Workbook:
    text, encoding, bom = _decode(data)
    delimiter = _sniff_delimiter(text, kind)
    newline = atomic_file.newline_of(text) if any(ch in text for ch in "\r\n") else "\n"
    rows, final_newline = _parse_text_table(text, delimiter)
    fields = [field for row in rows for field in row if field[0]]
    quote_all = bool(fields) and all(quoted for _text, quoted in fields)
    backend = _TextBackend(encoding, bom, newline, delimiter, final_newline, quote_all)
    book = Workbook(path, kind, backend)
    name = os.path.splitext(os.path.basename(path))[0]
    sheet = Sheet(name, [len(row) for row in rows])
    total = 0
    for r, row in enumerate(rows):
        for c, (value, quoted) in enumerate(row):
            if value == "" and not quoted:
                continue
            total += 1
            if total > MAX_CELLS:
                raise SheetFileError(_("The spreadsheet is too large to open here."))
            sheet.cells[(r, c)] = Cell(value=value, extra=quoted, source=value)
    book.sheets.append(sheet)
    return book


def _model_to_text_table(book: Workbook, sheet: Sheet, kind: str) -> bytes:
    delimiter = "\t" if kind == "tsv" else ","
    if book.is_text_table:
        backend = book.backend
        lengths = sheet.backend
        if kind == book.kind:
            delimiter = backend.delimiter
        return _text_table_bytes(sheet, delimiter, backend.newline, backend.encoding,
                                 backend.bom, backend.final_newline, backend.quote_all, lengths)

    def as_text(cell: Cell) -> str:
        if cell.shown is not None:
            return cell.shown
        return format_value(cell.value, cell.number_format, NumberStyle(), book.date1904)
    # As Excel exports: every line as wide as the sheet's used part.
    rows, cols = sheet.used_size()
    return _text_table_bytes(sheet, delimiter, "\r\n", "utf-8", b"\xef\xbb\xbf", True, False,
                             [cols] * rows, as_text)


# --------------------------------------------------------------------------- #
# Showing values: number formats                                              #
# --------------------------------------------------------------------------- #

@dataclasses.dataclass(frozen=True)
class NumberStyle:
    """How numbers are written where they are shown: the separators and
    the month names of the app's language."""

    decimal: str = "."
    group: str = ","
    months: Tuple[str, ...] = ("January", "February", "March", "April", "May", "June", "July",
                               "August", "September", "October", "November", "December")
    days: Tuple[str, ...] = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
                             "Saturday", "Sunday")
    true_text: str = "TRUE"
    false_text: str = "FALSE"


_BUILTIN_DATE = {"mm-dd-yy", "d-mmm-yy", "d-mmm", "mmm-yy", "m/d/yy h:mm", "mm:ss", "[h]:mm:ss",
                 "mmss.0", "h:mm", "h:mm:ss", "h:mm AM/PM", "h:mm:ss AM/PM"}


def _sections(code: str) -> List[str]:
    parts, current, quoted, escaped = [], "", False, False
    for ch in code:
        if escaped:
            current += ch
            escaped = False
            continue
        if ch == "\\":
            current += ch
            escaped = True
            continue
        if ch == '"':
            quoted = not quoted
        if ch == ";" and not quoted:
            parts.append(current)
            current = ""
            continue
        current += ch
    parts.append(current)
    return parts


def _strip_codes(section: str) -> Tuple[str, bool]:
    """A section without [Red], [$-407] and the like; and whether it asks
    for red."""
    red = bool(re.search(r"\[(?i:red)\]", section))
    section = re.sub(r"\[(?:[A-Za-z]+|>=?-?[\d.]+|<=?-?[\d.]+|=-?[\d.]+)\]", "", section)
    section = re.sub(r"\[\$([^\]-]*)(?:-[0-9A-Fa-f]+)?\]", lambda m: f'"{m.group(1)}"', section)
    return section, red


def _date_letters(code: str) -> str:
    """A format's first section without quoted text, escapes and codes
    such as [Red] or [$-407]: what is left to find date letters in."""
    section = _sections(code)[0]
    section = re.sub(r'"[^"]*"|\\.|_.|\*.', "", section)
    section = re.sub(r"\[(?![hms]+\])[^\]]*\]", "", section, flags=re.I)
    return re.sub(r"(?i)(s)\.0+", r"\1", section)


def is_date_format(code: str) -> bool:
    """Whether a number format shows a date or a time."""
    if not code or code in ("General", "@"):
        return False
    if code in _BUILTIN_DATE:
        return True
    plain = _date_letters(code)
    if re.search(r"[0#?]", plain) or "General" in plain:
        return False
    return bool(re.search(r"(?i)[ydhms]|am/pm|a/p", plain))


def number_format_kind(code: str) -> str:
    """"general", "number", "percent", "currency", "date", "time" or "text"."""
    if not code or code == "General":
        return "general"
    if code == "@":
        return "text"
    if is_date_format(code):
        plain = _date_letters(code).lower()
        if re.search(r"[dy]", plain):
            return "date"
        return "time" if re.search(r"[hs]", plain) else "date"
    section = _sections(code)[0]
    if "%" in re.sub(r'"[^"]*"', "", section):
        return "percent"
    if re.search(r'[€$£¥]|\[\$|"[^"]*[A-Z€$£¥][^"]*"', section):
        return "currency"
    return "number"


def format_value(value, code: str, style: NumberStyle = NumberStyle(),
                 date1904: bool = False) -> str:
    """``value`` as a spreadsheet shows it in number format ``code``."""
    if value is None:
        return ""
    if isinstance(value, sf.CellError):
        return value.code
    if isinstance(value, bool):
        return style.true_text if value else style.false_text
    if isinstance(value, str):
        sections = _sections(code or "General")
        if len(sections) >= 4 and "@" in sections[3]:
            return _literal(sections[3]).replace("@", value)
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time, _dt.timedelta)):
        number = sf.to_serial(value, date1904)
        if not code or code == "General":
            code = _default_date_code(value)
        value = number
    if not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
        return "#NUM!"
    code = code or "General"
    if code == "General" or code == "@":
        return _general(value, style)
    sections = _sections(code)
    if value < 0 and len(sections) >= 2:
        section, negative = sections[1], True
        value = -value
    elif value == 0 and len(sections) >= 3:
        section, negative = sections[2], False
    else:
        section, negative = sections[0], False
    section, _red = _strip_codes(section)
    if section.strip() in ("General", ""):
        text = _general(abs(value) if negative else value, style)
        return text
    if is_date_format(section):
        try:
            return _format_date(value, section, style, date1904)
        except (OverflowError, ValueError):
            return "#" * 6
    text = _format_number(value, section, style)
    return text


def _default_date_code(value) -> str:
    if isinstance(value, _dt.datetime):
        return "yyyy-mm-dd hh:mm" if (value.hour or value.minute or value.second) \
            else "yyyy-mm-dd"
    if isinstance(value, _dt.date):
        return "yyyy-mm-dd"
    return "hh:mm:ss"


def _general(value: float, style: NumberStyle) -> str:
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer() and
                                  abs(value) < 1e11):
        text = str(int(value))
        return text if len(text) <= 11 else _scientific(value, style)
    magnitude = abs(value)
    if magnitude >= 1e11 or magnitude < 1e-9:
        return _scientific(value, style)
    digits = 10 - max(0, int(math.floor(math.log10(magnitude))) + 1) if magnitude >= 1 else 9
    text = f"{value:.{max(digits, 0)}f}".rstrip("0").rstrip(".")
    if text in ("-0", ""):
        text = "0"
    return text.replace(".", style.decimal)


def _scientific(value: float, style: NumberStyle) -> str:
    text = f"{value:.5E}"
    mantissa, exponent = text.split("E")
    mantissa = mantissa.rstrip("0").rstrip(".")
    sign = exponent[0]
    return f"{mantissa.replace('.', style.decimal)}E{sign}{int(exponent[1:]):02d}"


def _literal(section: str) -> str:
    out, i = [], 0
    while i < len(section):
        ch = section[i]
        if ch == '"':
            end = section.find('"', i + 1)
            end = len(section) if end < 0 else end
            out.append(section[i + 1:end])
            i = end + 1
        elif ch == "\\" and i + 1 < len(section):
            out.append(section[i + 1])
            i += 2
        elif ch in "_":
            out.append(" ")
            i += 2
        elif ch == "*":
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _format_number(value: float, section: str, style: NumberStyle) -> str:
    """A number in a section such as "#,##0.00", "0%", "0.00E+00" or
    "#,##0.00 \"€\"" (text and literals around the digits kept)."""
    # Split into literal text and the number pattern.
    pieces: List[List[str]] = []

    def add(kind: str, text: str) -> None:
        if pieces and pieces[-1][0] == kind:
            pieces[-1][1] += text
        else:
            pieces.append([kind, text])

    i = 0
    while i < len(section):
        ch = section[i]
        in_number = bool(pieces) and pieces[-1][0] == "number"
        if ch == '"':
            end = section.find('"', i + 1)
            end = len(section) if end < 0 else end
            add("text", section[i + 1:end])
            i = end + 1
        elif ch == "\\" and i + 1 < len(section):
            add("text", section[i + 1])
            i += 2
        elif ch == "_" and i + 1 < len(section):
            add("text", " ")
            i += 2
        elif ch == "*" and i + 1 < len(section):
            i += 2
        elif ch in "0#?.,%" or (ch in "Ee" and in_number) or \
                (ch in "+-" and in_number and pieces[-1][1].upper().endswith("E")):
            add("number", ch)
            i += 1
        else:
            add("text", ch)
            i += 1
    patterns = [p for kind, p in pieces if kind == "number"]
    pattern = "".join(patterns)
    percent = pattern.count("%")
    shown = value * (100 ** percent)
    pattern_digits = pattern.replace("%", "")
    if "E" in pattern_digits.upper():
        mantissa_pattern = re.split(r"[Ee]", pattern_digits)[0]
        decimals = len(mantissa_pattern.split(".")[1]) if "." in mantissa_pattern else 0
        text = f"{abs(shown):.{decimals}E}"
        mantissa, exponent = text.split("E")
        number_text = mantissa.replace(".", style.decimal) + "E" + exponent[0] + \
            exponent[1:].rjust(2, "0")
    else:
        integer_pattern, _dot, fraction_pattern = pattern_digits.partition(".")
        # Trailing commas scale by thousands.
        scale = len(integer_pattern) - len(integer_pattern.rstrip(","))
        integer_pattern = integer_pattern.rstrip(",")
        shown /= 1000 ** scale
        grouping = "," in integer_pattern
        decimals = len(fraction_pattern.replace(",", ""))
        required = fraction_pattern.count("0")
        rounded = Decimal(repr(abs(shown))).quantize(Decimal(1).scaleb(-decimals),
                                                     rounding="ROUND_HALF_UP")
        text = f"{rounded:f}"
        whole, _d, fraction = text.partition(".")
        min_int = integer_pattern.replace(",", "").count("0")
        whole = whole.lstrip("0")
        if len(whole) < min_int:
            whole = whole.rjust(min_int, "0")
        if grouping and whole:
            groups = []
            while len(whole) > 3:
                groups.insert(0, whole[-3:])
                whole = whole[:-3]
            groups.insert(0, whole)
            whole = style.group.join(groups)
        if decimals:
            fraction = fraction.ljust(decimals, "0")[:decimals]
            keep = len(fraction.rstrip("0"))
            fraction = fraction[:max(keep, required)]
        else:
            fraction = ""
        number_text = whole + (style.decimal + fraction if fraction else "")
        if not number_text:
            number_text = "0" if min_int or not fraction_pattern else ""
    negative = shown < 0 or (value < 0)
    out = []
    used = False
    for kind, piece in pieces:
        if kind == "text":
            out.append(piece)
        elif not used:
            out.append(number_text + "%" * piece.count("%"))
            used = True
        else:
            out.append("%" * piece.count("%"))
    text = "".join(out)
    if negative and not text.startswith("-") and float(Decimal(repr(abs(value)))) != 0:
        text = "-" + text
    return text


_DATE_TOKEN = re.compile(r'"[^"]*"|\\.|yyyy|yy|mmmmm|mmmm|mmm|mm|m|dddd|ddd|dd|d|hh|h|ss|s|'
                         r'AM/PM|am/pm|A/P|a/p|\.0+|\[h+\]|\[m+\]|\[s+\]|.', re.I)


def _format_date(serial: float, section: str, style: NumberStyle, date1904: bool) -> str:
    moment = sf.from_serial(serial, date1904)
    tokens = _DATE_TOKEN.findall(section)
    twelve = any(t.upper() in ("AM/PM", "A/P") for t in tokens)
    out = []
    for i, token in enumerate(tokens):
        lower = token.lower()
        if token.startswith('"'):
            out.append(token[1:-1])
        elif token.startswith("\\"):
            out.append(token[1:])
        elif lower == "yyyy":
            out.append(f"{moment.year:04d}")
        elif lower == "yy":
            out.append(f"{moment.year % 100:02d}")
        elif lower in ("m", "mm"):
            # m after h or before s is minutes.
            before = [t.lower() for t in tokens[:i] if t.strip() and t.lower()[0] in "hdys"]
            after = [t.lower() for t in tokens[i + 1:] if t.strip() and t.lower()[0] in "hdys"]
            minute = (before and before[-1].startswith("h")) or \
                (after and after[0].startswith("s"))
            number = moment.minute if minute else moment.month
            out.append(f"{number:02d}" if lower == "mm" else str(number))
        elif lower == "mmm":
            out.append(style.months[moment.month - 1][:3])
        elif lower == "mmmm":
            out.append(style.months[moment.month - 1])
        elif lower == "mmmmm":
            out.append(style.months[moment.month - 1][:1])
        elif lower == "d":
            out.append(str(moment.day))
        elif lower == "dd":
            out.append(f"{moment.day:02d}")
        elif lower == "ddd":
            out.append(style.days[moment.weekday()][:3])
        elif lower == "dddd":
            out.append(style.days[moment.weekday()])
        elif lower in ("h", "hh"):
            hour = moment.hour
            if twelve:
                hour = hour % 12 or 12
            out.append(f"{hour:02d}" if lower == "hh" else str(hour))
        elif lower in ("s", "ss"):
            out.append(f"{moment.second:02d}" if lower == "ss" else str(moment.second))
        elif lower.startswith("[h"):
            out.append(str(int(serial * 24)))
        elif lower.startswith("[m"):
            out.append(str(int(serial * 1440)))
        elif lower.startswith("[s"):
            out.append(str(int(round(serial * 86400))))
        elif lower == "am/pm":
            out.append("AM" if moment.hour < 12 else "PM")
        elif lower == "a/p":
            out.append("A" if moment.hour < 12 else "P")
        elif lower.startswith(".0"):
            out.append(style.decimal + "0" * (len(lower) - 1))
        else:
            out.append(token)
    return "".join(out)


# --------------------------------------------------------------------------- #
# OpenDocument                                                                #
# --------------------------------------------------------------------------- #

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "number": "urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "calcext": "urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0",
    "config": "urn:oasis:names:tc:opendocument:xmlns:config:1.0",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
}


def _q(prefix: str, name: str) -> str:
    return f"{{{_NS[prefix]}}}{name}"


T_TABLE = _q("table", "table")
T_ROW = _q("table", "table-row")
T_CELL = _q("table", "table-cell")
T_COVERED = _q("table", "covered-table-cell")
T_COL = _q("table", "table-column")
A_ROWS_REP = _q("table", "number-rows-repeated")
A_COLS_REP = _q("table", "number-columns-repeated")
A_STYLE = _q("table", "style-name")
A_FORMULA = _q("table", "formula")
A_VTYPE = _q("office", "value-type")
A_CALC_VTYPE = _q("calcext", "value-type")
A_VALUE = _q("office", "value")
A_DATE = _q("office", "date-value")
A_TIME = _q("office", "time-value")
A_BOOL = _q("office", "boolean-value")
A_STRING = _q("office", "string-value")
A_CURRENCY = _q("office", "currency")
A_DEFAULT_STYLE = _q("table", "default-cell-style-name")
TEXT_P = _q("text", "p")
_ROW_GROUPS = {_q("table", n) for n in ("table-rows", "table-header-rows", "table-row-group")}
_COL_GROUPS = {_q("table", n) for n in ("table-columns", "table-header-columns",
                                        "table-column-group")}
_VALUE_ATTRS = (A_VTYPE, A_CALC_VTYPE, A_VALUE, A_DATE, A_TIME, A_BOOL, A_STRING, A_CURRENCY,
                A_FORMULA)
_SPAN_ATTRS = (_q("table", "number-columns-spanned"), _q("table", "number-rows-spanned"))
_SOFT_BREAK = _q("text", "soft-page-break")


class _Run:
    """Identical rows, ``count`` of them: the row element (attributes
    only), its cells as (element, repeat) pairs, and the row groups that
    hold it. Never changed: a change makes a new run."""

    __slots__ = ("elem", "count", "cells", "path")

    def __init__(self, elem, count: int, cells: Tuple, path: Tuple) -> None:
        self.elem, self.count, self.cells, self.path = elem, count, cells, path

    def with_(self, **changes) -> "_Run":
        values = {"elem": self.elem, "count": self.count, "cells": self.cells,
                  "path": self.path}
        values.update(changes)
        return _Run(**values)


class _OdsTable:
    """A sheet of content.xml as runs of rows and columns."""

    def __init__(self, element) -> None:
        self.element = element
        self.rows: List[_Run] = []
        self.columns: List[Tuple[object, int, Tuple]] = []

    def copy(self) -> "_OdsTable":
        other = _OdsTable(self.element)
        other.rows = list(self.rows)
        other.columns = list(self.columns)
        return other


def _repeat(elem, attr) -> int:
    try:
        return max(1, int(elem.get(attr, "1")))
    except ValueError:
        return 1


def _has_content(elem) -> bool:
    if elem.get(A_VTYPE) or elem.get(A_FORMULA) or elem.get(A_CALC_VTYPE):
        return True
    for child in elem:
        if child.tag == TEXT_P and (child.text or len(child)):
            return True
    return False


def _paragraph_text(p) -> str:
    out = [p.text or ""]
    for child in p:
        tag = child.tag
        if tag == _q("text", "s"):
            out.append(" " * _repeat(child, _q("text", "c")))
        elif tag == _q("text", "tab"):
            out.append("\t")
        elif tag == _q("text", "line-break"):
            out.append("\n")
        elif tag in (_q("office", "annotation"),):
            pass
        else:
            out.append(_paragraph_text(child))
        out.append(child.tail or "")
    return "".join(out)


def _cell_text(elem) -> str:
    return "\n".join(_paragraph_text(p) for p in elem if p.tag == TEXT_P)


_DURATION = re.compile(r"^-?P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?$")


def _ods_value(elem):
    vtype = elem.get(A_VTYPE)
    calc_type = elem.get(A_CALC_VTYPE)
    if calc_type == "error":
        code = _cell_text(elem).strip()
        return sf.ERROR_CODES.get(code, sf.CellError(code or "#VALUE!"))
    if vtype in ("float", "percentage", "currency"):
        try:
            return sf._clean(float(elem.get(A_VALUE, "0")))
        except ValueError:
            return _cell_text(elem)
    if vtype == "date":
        text = elem.get(A_DATE, "")
        try:
            if "T" in text:
                return _dt.datetime.fromisoformat(text[:26])
            return _dt.date.fromisoformat(text[:10])
        except ValueError:
            return _cell_text(elem)
    if vtype == "time":
        match = _DURATION.match(elem.get(A_TIME, ""))
        if not match:
            return _cell_text(elem)
        days, hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
        if total < 86400:
            whole = int(total)
            return _dt.time(whole // 3600, whole % 3600 // 60, whole % 60,
                            int(round((total - whole) * 1e6)) % 1000000)
        return _dt.timedelta(seconds=total)
    if vtype == "boolean":
        return elem.get(A_BOOL, "false").lower() == "true"
    if vtype == "string":
        if elem.get(A_STRING) is not None:
            return elem.get(A_STRING)
        return _cell_text(elem)
    if vtype is None:
        text = _cell_text(elem)
        return text if text else None
    return _cell_text(elem)


class _OdsStyles:
    """Cell styles and their number formats, from content.xml and
    styles.xml."""

    def __init__(self, content_root, styles_root) -> None:
        self.cell_styles: Dict[str, object] = {}
        self.data_styles: Dict[str, object] = {}
        self.column_styles: Dict[str, object] = {}
        self.automatic = content_root.find(_q("office", "automatic-styles"))
        if self.automatic is None:
            self.automatic = content_root.makeelement(_q("office", "automatic-styles"), {})
            body = content_root.find(_q("office", "body"))
            content_root.insert(list(content_root).index(body) if body is not None
                                else len(content_root), self.automatic)
        for root in (styles_root, content_root):
            if root is None:
                continue
            for holder in root:
                if holder.tag not in (_q("office", "styles"), _q("office", "automatic-styles")):
                    continue
                for style in holder:
                    name = style.get(_q("style", "name"))
                    if name is None:
                        continue
                    if style.tag == _q("style", "style"):
                        family = style.get(_q("style", "family"))
                        if family == "table-cell":
                            self.cell_styles[name] = style
                        elif family == "table-column":
                            self.column_styles[name] = style
                    elif style.tag.startswith("{" + _NS["number"] + "}"):
                        self.data_styles[name] = style
        self._formats: Dict[str, str] = {}
        self._counter = 0

    def column_width_px(self, name: Optional[str]) -> Optional[float]:
        style = self.column_styles.get(name or "")
        if style is None:
            return None
        props = style.find(_q("style", "table-column-properties"))
        if props is None:
            return None
        return _length_px(props.get(_q("style", "column-width")))

    def format_of(self, style_name: Optional[str]) -> str:
        """The Excel-like number format code of a cell style."""
        if not style_name:
            return "General"
        cached = self._formats.get(style_name)
        if cached is not None:
            return cached
        code = "General"
        name, seen = style_name, set()
        while name and name not in seen:
            seen.add(name)
            style = self.cell_styles.get(name)
            if style is None:
                break
            data = style.get(_q("style", "data-style-name"))
            if data:
                code = _data_style_code(self.data_styles.get(data))
                break
            name = style.get(_q("style", "parent-style-name"))
        self._formats[style_name] = code
        return code

    def style_with_format(self, base: Optional[str], code: str) -> str:
        """A cell style like ``base`` showing numbers in ``code``, made in
        content.xml's automatic styles."""
        self._counter += 1
        while f"ceME{self._counter}" in self.cell_styles:
            self._counter += 1
        data_name = f"NME{self._counter}"
        while data_name in self.data_styles:
            self._counter += 1
            data_name = f"NME{self._counter}"
        data = _data_style_element(self.automatic, data_name, code)
        self.automatic.append(data)
        self.data_styles[data_name] = data
        base_style = self.cell_styles.get(base or "")
        name = f"ceME{self._counter}"
        if base_style is not None and base_style.getparent() is self.automatic:
            style = copy.deepcopy(base_style)
        else:
            style = self.automatic.makeelement(_q("style", "style"), {})
            style.set(_q("style", "family"), "table-cell")
            style.set(_q("style", "parent-style-name"),
                      base if base_style is not None else "Default")
        style.set(_q("style", "name"), name)
        style.set(_q("style", "data-style-name"), data_name)
        self.automatic.append(style)
        self.cell_styles[name] = style
        self._formats[name] = code
        return name


def _length_px(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = re.match(r"^([\d.]+)\s*(cm|mm|in|pt|pc|px)?$", text.strip())
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or "pt"
    per_inch = {"cm": 2.54, "mm": 25.4, "in": 1.0, "pt": 72.0, "pc": 6.0, "px": 96.0}[unit]
    return number / per_inch * 96


def _data_style_code(style) -> str:
    """An OpenDocument number style as an Excel number format code."""
    if style is None:
        return "General"
    tag = style.tag.split("}")[1]
    parts = []
    for child in style:
        name = child.tag.split("}")[1]
        long = child.get(_q("number", "style")) == "long"
        if name == "number":
            decimals = int(child.get(_q("number", "decimal-places"), "0") or 0)
            grouping = child.get(_q("number", "grouping")) == "true"
            min_int = int(child.get(_q("number", "min-integer-digits"), "1") or 1)
            integer = ("#,##" if grouping else "") + "0" * max(min_int, 1)
            if grouping and min_int > 1:
                integer = "#," + "0" * min_int
            parts.append(integer + ("." + "0" * decimals if decimals else ""))
        elif name == "scientific-number":
            decimals = int(child.get(_q("number", "decimal-places"), "0") or 0)
            parts.append("0" + ("." + "0" * decimals if decimals else "") + "E+00")
        elif name == "fraction":
            parts.append("# ?/?")
        elif name == "text":
            text = child.text or ""
            if text == "%":
                parts.append("%")
            elif text:
                parts.append('"' + text.replace('"', "") + '"')
        elif name == "currency-symbol":
            parts.append('"' + (child.text or "") + '"')
        elif name == "year":
            parts.append("yyyy" if long else "yy")
        elif name == "month":
            textual = child.get(_q("number", "textual")) == "true"
            parts.append(("mmmm" if long else "mmm") if textual else ("mm" if long else "m"))
        elif name == "day":
            parts.append("dd" if long else "d")
        elif name == "day-of-week":
            parts.append("dddd" if long else "ddd")
        elif name == "hours":
            parts.append("hh" if long else "h")
        elif name == "minutes":
            parts.append("mm" if long else "m")
        elif name == "seconds":
            parts.append("ss" if long else "s")
        elif name == "am-pm":
            parts.append("AM/PM")
        elif name == "boolean":
            return "General"
        elif name == "text-content":
            parts.append("@")
    code = "".join(parts).replace("\"\"", "")
    if not code:
        return "General"
    if tag == "percentage-style" and "%" not in code:
        code += "%"
    return code


def _data_style_element(parent, name: str, code: str):
    """A number style element (number:number-style, percentage, currency
    or date) for the format codes MyEditor sets itself."""
    kind = number_format_kind(code)
    number = _NS["number"]

    def element(tag, attrib=None, text=None):
        item = parent.makeelement(f"{{{number}}}{tag}", attrib or {})
        if text is not None:
            item.text = text
        return item

    if kind == "date" or kind == "time":
        root = element("date-style" if kind == "date" else "time-style",
                       {f"{{{_NS['style']}}}name": name})
        for token in _DATE_TOKEN.findall(code):
            lower = token.lower()
            long_attr = {f"{{{number}}}style": "long"}
            if lower == "yyyy":
                root.append(element("year", long_attr))
            elif lower == "yy":
                root.append(element("year"))
            elif lower in ("mm", "m"):
                if kind == "time":
                    root.append(element("minutes", long_attr if lower == "mm" else None))
                else:
                    root.append(element("month", long_attr if lower == "mm" else None))
            elif lower in ("dd", "d"):
                root.append(element("day", long_attr if lower == "dd" else None))
            elif lower in ("hh", "h"):
                root.append(element("hours", long_attr if lower == "hh" else None))
            elif lower in ("ss", "s"):
                root.append(element("seconds", long_attr if lower == "ss" else None))
            else:
                root.append(element("text", text=token.strip('"')))
        return root
    decimals = 0
    match = re.search(r"0\.(0+)", code)
    if match:
        decimals = len(match.group(1))
    attrs = {f"{{{number}}}decimal-places": str(decimals),
             f"{{{number}}}min-decimal-places": str(decimals),
             f"{{{number}}}min-integer-digits": "1"}
    if "#,##" in code:
        attrs[f"{{{number}}}grouping"] = "true"
    if kind == "percent":
        root = element("percentage-style", {f"{{{_NS['style']}}}name": name})
        root.append(element("number", attrs))
        root.append(element("text", text="%"))
        return root
    if kind == "currency":
        symbol = re.search(r'"([^"]*)"', code)
        symbol_text = (symbol.group(1) if symbol else "€").strip()
        root = element("currency-style", {f"{{{_NS['style']}}}name": name})
        before = code.strip().startswith('"')
        if before:
            root.append(element("currency-symbol", text=symbol_text))
            root.append(element("number", attrs))
        else:
            root.append(element("number", attrs))
            root.append(element("text", text=" "))
            root.append(element("currency-symbol", text=symbol_text))
        return root
    root = element("number-style", {f"{{{_NS['style']}}}name": name})
    root.append(element("number", attrs))
    return root


_CURRENCY_CODES = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY", "CHF": "CHF", "kr": "SEK",
                   "zł": "PLN", "₹": "INR", "₽": "RUB", "Fr.": "CHF"}


class _OdsBackend(_Backend):
    results_in_source = True

    def __init__(self, path: str, infos: List[zipfile.ZipInfo], entries: Dict[str, bytes],
                 tree, styles: _OdsStyles, spreadsheet) -> None:
        self.infos = infos
        self.entries = entries
        self.tree = tree
        self.styles = styles
        self.spreadsheet = spreadsheet
        self.has_calcext = "calcext" in (tree.getroot().nsmap or {})
        self.names: Dict[str, str] = {}
        self.date1904 = False
        self.column_style_cache: Dict[Tuple[Optional[str], float], str] = {}
        self._looks: Optional[_OdsLooks] = None
        self._run_index: Dict[int, tuple] = {}
        # Attributes changed outside the sheets' runs, as (element, name,
        # value before), for Undo.
        self.journal: List[Tuple[object, Optional[str], str]] = []
        # Parts of the package other than content.xml that changed (charts,
        # settings), parsed when first needed.
        self.object_trees: Dict[str, object] = {}
        self.settings = None
        self.modified_entries: Set[str] = set()

    # -- names and formats -------------------------------------------------

    def named_formulas(self) -> Dict[str, str]:
        return dict(self.names)

    def defined_name(self, book, name, uid):
        for key, text in self.names.items():
            if key.casefold() == name.casefold():
                return text
        return None

    def format_at(self, book, sheet, row, col):
        return self.styles.format_of(self._style_at(sheet.backend, row, col))

    def look(self, book, sheet, row, col, cell):
        if self._looks is None:
            self._looks = _OdsLooks(self.styles)
        if cell is not None and cell.style:
            return self._looks.look(cell.style)
        return self._looks.look(self._fast_style_at(sheet.backend, row, col))

    def _fast_style_at(self, table: "_OdsTable", row: int, col: int) -> Optional[str]:
        """``_style_at`` through an index of where each run starts, made
        again when the runs change; the grid asks for every cell on
        screen."""
        import bisect
        index = self._run_index.get(id(table))
        if index is None or index[0] is not table.rows or index[1] is not table.columns:
            row_starts, start = [], 0
            for run in table.rows:
                row_starts.append(start)
                start += run.count
            col_starts, start = [], 0
            for _elem, count, _path in table.columns:
                col_starts.append(start)
                start += count
            index = (table.rows, table.columns, row_starts, col_starts, {})
            self._run_index[id(table)] = index
        rows, columns, row_starts, col_starts, cell_starts = index
        position = bisect.bisect_right(row_starts, row) - 1
        if 0 <= position < len(rows):
            run = rows[position]
            if position + 1 < len(row_starts) or row < row_starts[position] + run.count:
                starts = cell_starts.get(position)
                if starts is None:
                    starts, start = [], 0
                    for _elem, count in run.cells:
                        starts.append(start)
                        start += count
                    cell_starts[position] = starts
                at = bisect.bisect_right(starts, col) - 1
                if 0 <= at < len(run.cells) and col < starts[at] + run.cells[at][1]:
                    style = run.cells[at][0].get(A_STYLE)
                    if style:
                        return style
                if run.elem.get(A_DEFAULT_STYLE):
                    return run.elem.get(A_DEFAULT_STYLE)
        at = bisect.bisect_right(col_starts, col) - 1
        if 0 <= at < len(columns) and col < col_starts[at] + columns[at][1]:
            return columns[at][0].get(A_DEFAULT_STYLE)
        return None

    def _style_at(self, table: _OdsTable, row: int, col: int) -> Optional[str]:
        elem = _cell_at(table, row, col)
        if elem is not None and elem.get(A_STYLE):
            return elem.get(A_STYLE)
        start = 0
        for run in table.rows:
            if start <= row < start + run.count:
                if run.elem.get(A_DEFAULT_STYLE):
                    return run.elem.get(A_DEFAULT_STYLE)
                break
            start += run.count
        start = 0
        for col_elem, count, _path in table.columns:
            if start <= col < start + count:
                return col_elem.get(A_DEFAULT_STYLE)
            start += count
        return None

    def blank_cell(self, book, sheet, row, col):
        style = self._style_at(sheet.backend, row, col)
        return Cell(style=style, number_format=self.styles.format_of(style))

    def with_format(self, book, sheet, cell, number_format):
        style = self.styles.style_with_format(cell.style, number_format)
        return cell.edited(style=style, number_format=number_format, value=cell.value,
                           formula=cell.formula)

    # -- structure ---------------------------------------------------------

    def snapshot_sheet(self, book, sheet):
        return (sheet.backend.copy(), sheet.backend.element.get(_q("table", "name")))

    def restore_sheet(self, book, sheet, state):
        table, name = state
        sheet.backend = table.copy()
        if name is not None:
            sheet.backend.element.set(_q("table", "name"), name)

    def snapshot_book(self, book):
        return [child for child in self.spreadsheet], dict(self.names), len(self.journal)

    def restore_book(self, book, state, order):
        children, names, journal_length = state
        # Attributes changed since (addresses, sheet names), back.
        while len(self.journal) > journal_length:
            element, key, value = self.journal.pop()
            if key is None:
                element.text = value
            else:
                element.set(key, value)
        for child in list(self.spreadsheet):
            self.spreadsheet.remove(child)
        for child in children:
            self.spreadsheet.append(child)
        self.names = names
        for sheet in order:
            sheet.changed = True

    def shift(self, book, sheet, axis, at, count):
        table = sheet.backend
        if axis == "row":
            _ods_shift_rows(table, at, count)
        else:
            _ods_shift_columns(table, at, count)
        # Merged blocks grew, shrank or lost their first row: the cells
        # say so again (the anchor's spans, the covered cells).
        if sheet.merges_complete:
            _ods_apply_merges(table, sheet.merges)
        name = sheet.name

        def moved(excel: str, home: Optional[str]) -> str:
            return sf.adjust_for_insert_delete("=" + excel, home=home or "", target=name,
                                               axis=axis, at=at, count=count)[1:]
        self._rewrite_references(moved)

    # Attributes that hold cell addresses or range lists, in content.xml
    # and in charts (Object N/content.xml).
    _ADDRESS_ATTRIBUTES = frozenset({
        "cell-range-address", "base-cell-address", "target-range-address", "cell-address",
        "data-source-range-address", "source-cell-range-addresses",
        "notify-on-update-of-ranges", "print-ranges", "values-cell-range-address",
        "label-cell-address", "range-address"})
    # Attributes that hold formulas: validation and conditional-format
    # conditions, named expressions.
    _FORMULA_ATTRIBUTES = frozenset({"condition", "expression", "value"})

    def _reference_trees(self):
        """content.xml and every chart's content, parsed when first asked."""
        yield "content.xml", self.tree
        from lxml import etree
        for info in self.infos:
            name = info.filename
            if not re.fullmatch(r"(?:[^/]+/)+content\.xml", name) or name == "content.xml":
                continue
            tree = self.object_trees.get(name)
            if tree is None:
                parser = etree.XMLParser(huge_tree=True, resolve_entities=False,
                                         no_network=True)
                try:
                    tree = etree.ElementTree(etree.fromstring(self.entries[name], parser))
                except etree.XMLSyntaxError:
                    continue
                self.object_trees[name] = tree
            yield name, tree

    def _rewrite_references(self, change: Callable[[str, Optional[str]], str]) -> None:
        """Pass every address and formula the document keeps outside its
        cells (named ranges and expressions, validation, conditional
        formats, database and print ranges, charts) through ``change``,
        which takes one reference in Excel's spelling and the sheet it is
        on (for references that name none) and returns it as it is now.
        Every change is journaled for Undo."""
        for entry, tree in self._reference_trees():
            names = " or ".join(f"local-name()='{n}'" for n in
                                sorted(self._ADDRESS_ATTRIBUTES | self._FORMULA_ATTRIBUTES))
            for attribute in tree.xpath(f"//@*[{names}]"):
                element = attribute.getparent()
                key = attribute.attrname
                local = key.rsplit("}", 1)[-1]
                value = str(attribute)
                if element.tag in (T_CELL, T_COVERED) and local not in ("cell-range-address",):
                    continue
                if local == "value" and not element.tag.endswith("}condition"):
                    continue
                home = self._home_sheet(element)
                if local in self._ADDRESS_ATTRIBUTES:
                    new = sf.rewrite_openformula_addresses(
                        value, lambda excel: change(excel, home))
                else:
                    new = sf.rewrite_openformula_references(
                        value, lambda excel: change(excel, home))
                if new != value:
                    self.journal.append((element, key, value))
                    element.set(key, new)
                    self.modified_entries.add(entry)

    def _home_sheet(self, element) -> Optional[str]:
        """The sheet a reference without a sheet name means: the one of the
        element's base cell, else the table that holds it."""
        for key in (_q("calcext", "base-cell-address"), _q("table", "base-cell-address")):
            base = element.get(key)
            if base:
                sheet = sf.split_openformula_addresses(base)[0].rpartition(".")[0].lstrip("$")
                if sheet.startswith("'"):
                    sheet = sheet[1:-1].replace("''", "'")
                if sheet:
                    return sheet
        parent = element
        while parent is not None:
            if parent.tag == T_TABLE:
                return parent.get(_q("table", "name"))
            parent = parent.getparent()
        return None

    def rewrite_names(self, book, rewrite):
        home = book.sheets[0] if book.sheets else None
        if home is None:
            return
        for key, text in list(self.names.items()):
            self.names[key] = rewrite(text, home)

    def _new_table(self, name: str, template=None):
        tag = T_TABLE
        attrib = {_q("table", "name"): name}
        if template is not None and template.get(A_STYLE):
            attrib[A_STYLE] = template.get(A_STYLE)
        element = self.spreadsheet.makeelement(tag, attrib)
        element.append(element.makeelement(T_COL, {}))
        row = element.makeelement(T_ROW, {})
        row.append(row.makeelement(T_CELL, {}))
        element.append(row)
        return element

    def _tables(self):
        return [child for child in self.spreadsheet if child.tag == T_TABLE]

    def add_sheet(self, book, sheet, index):
        tables = self._tables()
        element = self._new_table(sheet.name, tables[0] if tables else None)
        if index < len(tables):
            self.spreadsheet.insert(list(self.spreadsheet).index(tables[index]), element)
        elif tables:
            self.spreadsheet.insert(list(self.spreadsheet).index(tables[-1]) + 1, element)
        else:
            self.spreadsheet.append(element)
        sheet.backend = _read_ods_table(element)[0]

    def duplicate_sheet(self, book, sheet, copy_, index):
        source = sheet.backend
        element = copy.deepcopy(source.element)
        element.set(_q("table", "name"), copy_.name)
        source_index = list(self.spreadsheet).index(source.element)
        self.spreadsheet.insert(source_index + 1, element)
        table = _OdsTable(element)
        table.rows = list(source.rows)
        table.columns = list(source.columns)
        copy_.backend = table

    def rename_sheet(self, book, sheet, old, new):
        self.journal.append((sheet.backend.element, _q("table", "name"), old))
        sheet.backend.element.set(_q("table", "name"), new)
        self._rewrite_references(
            lambda excel, home: sf.rename_sheet_in_formula("=" + excel, old, new)[1:])
        self._rename_in_settings(old, new)

    def _rename_in_settings(self, old: str, new: Optional[str]) -> None:
        """settings.xml keeps each sheet's view (frozen panes, the cursor)
        under the sheet's name, and the sheet on screen: they follow a
        renamed sheet (or, for a deleted one, the active sheet becomes
        another)."""
        tree = self._settings_tree()
        if tree is None:
            return
        config = _NS["config"]
        for entry in tree.iter(f"{{{config}}}config-item-map-entry"):
            if entry.get(f"{{{config}}}name") == old and new is not None:
                self.journal.append((entry, f"{{{config}}}name", old))
                entry.set(f"{{{config}}}name", new)
                self.modified_entries.add("settings.xml")
        for item in tree.iter(f"{{{config}}}config-item"):
            if item.get(f"{{{config}}}name") == "ActiveTable" and item.text == old:
                self.journal.append((item, None, old))
                item.text = new if new is not None else self._first_table_name(old)
                self.modified_entries.add("settings.xml")

    def _first_table_name(self, other_than: str) -> str:
        for table in self._tables():
            name = table.get(_q("table", "name"))
            if name != other_than:
                return name
        return ""

    def _settings_tree(self):
        if "settings.xml" not in self.entries:
            return None
        if self.settings is None:
            from lxml import etree
            try:
                self.settings = etree.ElementTree(etree.fromstring(
                    self.entries["settings.xml"],
                    etree.XMLParser(resolve_entities=False, no_network=True)))
            except etree.XMLSyntaxError:
                return None
        return self.settings

    def delete_sheet(self, book, sheet):
        self.spreadsheet.remove(sheet.backend.element)
        name = sheet.name
        order = [each.name for each in book.sheets]
        self._rewrite_references(
            lambda excel, home: sf.break_sheet_in_formula("=" + excel, name, order)[1:])
        self._rename_in_settings(name, None)

    def reorder(self, book):
        tables = self._tables()
        if not tables:
            return
        first = list(self.spreadsheet).index(tables[0])
        for element in tables:
            self.spreadsheet.remove(element)
        for offset, sheet in enumerate(book.sheets):
            self.spreadsheet.insert(first + offset, sheet.backend.element)

    # -- writing -----------------------------------------------------------

    def serialize(self, book) -> bytes:
        from lxml import etree
        for sheet in book.sheets:
            if sheet.changed or sheet.changed_widths:
                _write_ods_table(self, sheet, etree)
        self._write_frozen(book)
        content = etree.tostring(self.tree, xml_declaration=True, encoding="UTF-8")
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            for info in self.infos:
                name = info.filename
                if name == "content.xml":
                    data = content
                elif name in self.modified_entries and name in self.object_trees:
                    data = etree.tostring(self.object_trees[name], xml_declaration=True,
                                          encoding="UTF-8")
                elif name == "settings.xml" and name in self.modified_entries:
                    data = etree.tostring(self.settings, xml_declaration=True, encoding="UTF-8")
                else:
                    data = self.entries[name]
                archive.writestr(info, data, compress_type=info.compress_type)
        return out.getvalue()

    def _write_frozen(self, book) -> None:
        """Frozen rows and columns that a change moved, into settings.xml."""
        tree = None
        config = _NS["config"]
        for sheet in book.sheets:
            if sheet.frozen == getattr(sheet, "loaded_frozen", sheet.frozen):
                continue
            tree = tree or self._settings_tree()
            if tree is None:
                return
            rows, cols = sheet.frozen or (0, 0)
            for entry in tree.iter(f"{{{config}}}config-item-map-entry"):
                if entry.get(f"{{{config}}}name") != sheet.name:
                    continue
                for item in entry:
                    name = item.get(f"{{{config}}}name")
                    if name == "VerticalSplitPosition" and rows:
                        item.text = str(rows)
                    elif name == "HorizontalSplitPosition" and cols:
                        item.text = str(cols)
                self.modified_entries.add("settings.xml")
            sheet.loaded_frozen = sheet.frozen


def _cell_at(table: _OdsTable, row: int, col: int):
    start = 0
    for run in table.rows:
        if start <= row < start + run.count:
            c0 = 0
            for elem, count in run.cells:
                if c0 <= col < c0 + count:
                    return elem
                c0 += count
            return None
        start += run.count
    return None


def _blank_like(elem):
    """An empty cell with ``elem``'s look: no value, no text, no comment,
    no merge."""
    if elem is None:
        return None
    tag = T_CELL
    attrib = {k: v for k, v in elem.attrib.items()
              if k not in _VALUE_ATTRS and k not in _SPAN_ATTRS and k != A_COLS_REP
              and k != _q("table", "content-validation-name")}
    new = elem.makeelement(tag, attrib)
    return new


def _split_runs(runs: List, at: int, count_of, with_count) -> int:
    """Make a run start at ``at``; the index of that run (len(runs) when
    ``at`` is past the end)."""
    start = 0
    for index, run in enumerate(runs):
        n = count_of(run)
        if start == at:
            return index
        if start < at < start + n:
            first = with_count(run, at - start)
            second = with_count(run, start + n - at)
            runs[index:index + 1] = [first, second]
            return index + 1
        start += n
    return len(runs)


def _total(runs, count_of) -> int:
    return sum(count_of(run) for run in runs)


def _row_count(run):
    return run.count


def _row_with(run, n):
    return run.with_(count=n)


def _cell_count(pair):
    return pair[1]


def _cell_with(pair, n):
    return (pair[0], n)


def _blank_row(run: _Run) -> _Run:
    cells = tuple((_blank_like(elem), n) for elem, n in run.cells)
    return run.with_(cells=cells, count=1)


# What a new row or column takes from its neighbour: its look, never its
# visibility (a hidden or collapsed neighbour) or anything else.
_LINE_LOOK = (A_STYLE, A_DEFAULT_STYLE)


def _common_path(one: Tuple, other: Tuple) -> Tuple:
    """The row (column) groups two neighbours share: a line inserted
    between them belongs to those, so inserting inside a group extends it
    and inserting right after one (or after the header rows) does not."""
    shared = []
    for a, b in zip(one, other):
        if a is not b:
            break
        shared.append(a)
    return tuple(shared)


def _new_line(template, parent) -> object:
    return parent.makeelement(template.tag, {k: v for k, v in template.attrib.items()
                                             if k in _LINE_LOOK})


def _ods_shift_rows(table: _OdsTable, at: int, count: int) -> None:
    rows = list(table.rows)
    total = _total(rows, _row_count)
    if count > 0:
        if at >= total:
            return
        index = _split_runs(rows, at, _row_count, _row_with)
        # New rows look like the row above them (Excel's "format same as
        # above"), or the first row when inserted at the top; they belong
        # to the groups both neighbours belong to.
        above = rows[index - 1] if index > 0 else None
        below = rows[index] if index < len(rows) else None
        template = above or below
        path = _common_path(above.path, below.path) if above and below else ()
        new = _blank_row(template).with_(count=count, path=path,
                                         elem=_new_line(template.elem, table.element))
        rows.insert(index, new)
        # Keep the sheet's row count: the empty rows at the end give way.
        if total and _total(rows, _row_count) > max(total, sf.MAX_ROWS):
            last = rows[-1]
            excess = _total(rows, _row_count) - max(total, sf.MAX_ROWS)
            if last.count > excess and not any(_has_content(e) for e, _n in last.cells):
                rows[-1] = last.with_(count=last.count - excess)
    else:
        first = _split_runs(rows, at, _row_count, _row_with)
        end = _split_runs(rows, at - count, _row_count, _row_with)
        del rows[first:end]
    table.rows = rows


def _shift_cells(cells: Tuple, at: int, count: int, limit: int) -> Tuple:
    pairs = list(cells)
    total = _total(pairs, _cell_count)
    if count > 0:
        if at >= total:
            return cells
        index = _split_runs(pairs, at, _cell_count, _cell_with)
        template = pairs[index - 1][0] if index > 0 else (pairs[index][0] if pairs else None)
        if template is None:
            return cells
        pairs.insert(index, (_blank_like(template), count))
        over = _total(pairs, _cell_count) - max(total, limit)
        if total and over > 0:
            elem, n = pairs[-1]
            if n > over and not _has_content(elem):
                pairs[-1] = (elem, n - over)
    else:
        first = _split_runs(pairs, at, _cell_count, _cell_with)
        end = _split_runs(pairs, at - count, _cell_count, _cell_with)
        del pairs[first:end]
    return tuple(pairs)


def _ods_shift_columns(table: _OdsTable, at: int, count: int) -> None:
    limit = max(_total(table.columns, lambda c: c[1]), 1024)
    table.rows = [run.with_(cells=_shift_cells(run.cells, at, count, limit))
                  for run in table.rows]
    columns = list(table.columns)

    def col_with(entry, n):
        return (entry[0], n, entry[2])

    total = _total(columns, lambda c: c[1])
    if count > 0:
        if at <= total:
            index = _split_runs(columns, at, lambda c: c[1], col_with)
            before = columns[index - 1] if index > 0 else None
            after = columns[index] if index < len(columns) else None
            template = before or after
            if template is not None:
                path = _common_path(before[2], after[2]) if before and after else ()
                columns.insert(index, (_new_line(template[0], table.element), count, path))
    else:
        first = _split_runs(columns, at, lambda c: c[1], col_with)
        end = _split_runs(columns, at - count, lambda c: c[1], col_with)
        del columns[first:end]
    table.columns = columns


_SPANS = (_q("table", "number-columns-spanned"), _q("table", "number-rows-spanned"))
_MAX_MERGES = 200_000


def _cell_like(elem, tag: str, spans: Tuple[int, int] = (1, 1)):
    """``elem`` as a plain cell, an anchor of a merge (``spans`` rows and
    columns) or a covered cell, keeping all else it holds."""
    attrib = {k: v for k, v in elem.attrib.items() if k not in _SPANS and k != A_COLS_REP}
    new = elem.makeelement(tag, attrib)
    rows, cols = spans
    if tag == T_CELL and cols > 1:
        new.set(_SPANS[0], str(cols))
    if tag == T_CELL and rows > 1:
        new.set(_SPANS[1], str(rows))
    for child in elem:
        new.append(copy.deepcopy(child))
    return new


def _merge_state(elem) -> Tuple[str, Tuple[int, int]]:
    return elem.tag, (int(elem.get(_SPANS[1], "1") or 1), int(elem.get(_SPANS[0], "1") or 1))


def _ods_apply_merges(table: "_OdsTable", merges) -> None:
    """Make the runs' anchors and covered cells match ``merges`` (blocks
    as (r1, c1, r2, c2)): every block's first cell spans it, its other
    cells are covered, and no other cell spans or is covered."""
    wanted: Dict[Tuple[int, int], Tuple[str, Tuple[int, int]]] = {}
    for r1, c1, r2, c2 in merges:
        if r2 == r1 and c2 == c1:
            continue
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                wanted[(r, c)] = (T_COVERED, (1, 1))
        wanted[(r1, c1)] = (T_CELL, (r2 - r1 + 1, c2 - c1 + 1))
    changes: Dict[Tuple[int, int], Tuple[str, Tuple[int, int]]] = {}
    start = 0
    for run in table.rows:
        col = 0
        for elem, n in run.cells:
            state = _merge_state(elem)
            if state != (T_CELL, (1, 1)):
                for r in range(start, start + min(run.count, 10000)):
                    for c in range(col, col + min(n, 10000)):
                        if (r, c) not in wanted:
                            changes[(r, c)] = (T_CELL, (1, 1))
            col += n
        start += run.count
    held = _elements_at(table, wanted.keys())
    for position, state in wanted.items():
        elem = held.get(position)
        if elem is None or _merge_state(elem) != state:
            changes[position] = state
    if not changes:
        return
    rows_wanted: Dict[int, Dict[int, Tuple[str, Tuple[int, int]]]] = {}
    for (r, c), state in changes.items():
        rows_wanted.setdefault(r, {})[c] = state
    table.rows = _map_runs(table, rows_wanted)


def _map_runs(table: "_OdsTable", rows_wanted) -> List["_Run"]:
    """The runs with the cells at the given places made anew (as a plain,
    anchor or covered cell), splitting only the runs that hold them."""
    out: List[_Run] = []
    start = 0
    pending = sorted(rows_wanted)
    for run in table.rows:
        end = start + run.count
        inside = [r for r in pending if start <= r < end]
        if not inside:
            out.append(run)
            start = end
            continue
        cursor = start
        for r in inside:
            if r > cursor:
                out.append(run.with_(count=r - cursor))
            out.append(run.with_(count=1, cells=_map_cells(run.cells, rows_wanted[r],
                                                           table.element)))
            cursor = r + 1
        if cursor < end:
            out.append(run.with_(count=end - cursor))
        pending = [r for r in pending if r >= end]
        start = end
    return out


def _map_cells(cells: Tuple, wanted: Dict[int, Tuple[str, Tuple[int, int]]], parent) -> Tuple:
    out = []
    start = 0
    pending = sorted(wanted)
    for elem, n in cells:
        end = start + n
        inside = [c for c in pending if start <= c < end]
        if not inside:
            out.append((elem, n))
            start = end
            continue
        cursor = start
        for c in inside:
            if c > cursor:
                out.append((elem, c - cursor))
            tag, spans = wanted[c]
            out.append((_cell_like(elem, tag, spans), 1))
            cursor = c + 1
        if cursor < end:
            out.append((elem, end - cursor))
        pending = [c for c in pending if c >= end]
        start = end
    for c in pending:
        if c > start:
            out.append((parent.makeelement(T_CELL, {}), c - start))
        tag, spans = wanted[c]
        out.append((_cell_like(parent.makeelement(T_CELL, {}), tag, spans), 1))
        start = c + 1
    return tuple(out)


def _read_ods_table(element) -> Tuple[_OdsTable, List[Tuple[int, int, object]]]:
    """The runs of a table element, and its cells that hold something as
    (row, col, element)."""
    table = _OdsTable(element)
    found: List[Tuple[int, int, object]] = []

    def walk_columns(parent, path):
        for child in parent:
            if child.tag == T_COL:
                table.columns.append((child, _repeat(child, A_COLS_REP), path))
            elif child.tag in _COL_GROUPS:
                walk_columns(child, path + (child,))

    row_index = [0]

    def walk_rows(parent, path):
        for child in parent:
            if child.tag == T_ROW:
                count = _repeat(child, A_ROWS_REP)
                cells = []
                col = 0
                content = []
                for cell in child:
                    if cell.tag not in (T_CELL, T_COVERED):
                        continue
                    n = _repeat(cell, A_COLS_REP)
                    cells.append((cell, n))
                    if _has_content(cell):
                        content.append((col, n, cell))
                    col += n
                if content:
                    if count * sum(n for _c, n, _e in content) > MAX_CELLS:
                        raise SheetFileError(_("The spreadsheet is too large to open here."))
                    for r in range(row_index[0], row_index[0] + count):
                        for start, n, cell in content:
                            for c in range(start, start + n):
                                found.append((r, c, cell))
                table.rows.append(_Run(child, count, tuple(cells), path))
                row_index[0] += count
            elif child.tag in _ROW_GROUPS:
                walk_rows(child, path + (child,))

    walk_columns(element, ())
    walk_rows(element, ())
    return table, found


def _load_ods(path: str, progress, data: bytes) -> Workbook:
    from lxml import etree
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        entries = {info.filename: archive.read(info.filename) for info in infos}
    mimetype = entries.get("mimetype", b"").strip()
    if mimetype and mimetype != b"application/vnd.oasis.opendocument.spreadsheet":
        raise SheetFileError(_("The file is not an OpenDocument spreadsheet."))
    manifest = entries.get("META-INF/manifest.xml", b"")
    if b"encryption-data" in manifest:
        raise SheetFileError(_("The spreadsheet is locked with a password. Open it in the "
                               "spreadsheet app."))
    parser = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True,
                             remove_blank_text=False)
    tree = etree.ElementTree(etree.fromstring(entries["content.xml"], parser))
    root = tree.getroot()
    styles_root = None
    if "styles.xml" in entries:
        styles_root = etree.fromstring(entries["styles.xml"], parser)
    styles = _OdsStyles(root, styles_root)
    body = root.find(_q("office", "body"))
    spreadsheet = body.find(_q("office", "spreadsheet")) if body is not None else None
    if spreadsheet is None:
        raise SheetFileError(_("The file is not an OpenDocument spreadsheet."))
    backend = _OdsBackend(path, infos, entries, tree, styles, spreadsheet)
    book = Workbook(path, "ods", backend)
    book.unsaveable = unsaveable_parts(path, data)
    null_date = spreadsheet.find(f"{_q('table', 'calculation-settings')}/{_q('table', 'null-date')}")
    if null_date is not None and null_date.get(_q("table", "date-value"), "").startswith("1904"):
        book.date1904 = True
        book.engine.date1904 = True
        backend.date1904 = True
    _read_ods_names(spreadsheet, backend)
    tables = [child for child in spreadsheet if child.tag == T_TABLE]
    total = 0
    for index, element in enumerate(tables):
        table, found = _read_ods_table(element)
        sheet = Sheet(element.get(_q("table", "name"), f"Sheet{index + 1}"), table)
        total += len(found)
        if total > MAX_CELLS:
            raise SheetFileError(_("The spreadsheet is too large to open here."))
        for r, c, elem in found:
            formula = elem.get(A_FORMULA)
            excel = sf.from_openformula(formula) if formula else None
            state = ""
            if excel is not None and sf.needs_app(excel):
                state = sf.STATE_APP
            array = None
            matrix_cols = elem.get(_q("table", "number-matrix-columns-spanned"))
            matrix_rows = elem.get(_q("table", "number-matrix-rows-spanned"))
            if excel is not None and (matrix_cols or matrix_rows):
                # A matrix (array) formula filling a block.
                width, height = int(matrix_cols or 1), int(matrix_rows or 1)
                array = f"{sf.cell_name(r, c)}:{sf.cell_name(r + height - 1, c + width - 1)}"
                state = sf.STATE_APP
            style = elem.get(A_STYLE)
            sheet.cells[(r, c)] = Cell(
                value=_ods_value(elem), formula=excel,
                number_format=styles.format_of(style or backend._style_at(table, r, c)),
                style=style, extra=elem, source=elem, shown=_cell_text(elem), state=state,
                array=array)
        _read_ods_layout(sheet, table, styles)
        book.sheets.append(sheet)
        if progress:
            progress(0.9 * (index + 1) / max(len(tables), 1))
    _read_ods_frozen(entries.get("settings.xml"), book, etree)
    for sheet in book.sheets:
        sheet.loaded_frozen = sheet.frozen
    return book


def _read_ods_names(spreadsheet, backend) -> None:
    holder = spreadsheet.find(_q("table", "named-expressions"))
    if holder is None:
        return
    for item in holder:
        name = item.get(_q("table", "name"))
        if not name:
            continue
        if item.tag == _q("table", "named-range"):
            address = item.get(_q("table", "cell-range-address"), "")
            backend.names[name] = sf.from_openformula("of:=[" + address + "]")
        elif item.tag == _q("table", "named-expression"):
            expression = item.get(_q("table", "expression"), "")
            backend.names[name] = sf.from_openformula(expression)


def _read_ods_layout(sheet: Sheet, table: _OdsTable, styles: _OdsStyles) -> None:
    col = 0
    for elem, count, _path in table.columns:
        width = styles.column_width_px(elem.get(A_STYLE))
        if width:
            for c in range(col, min(col + count, col + 1024)):
                sheet.widths[c] = width
        col += count
        if col > 16384:
            break
    row = 0
    for run in table.rows:
        c = 0
        for elem, n in run.cells:
            spanned_cols = int(elem.get(_SPAN_ATTRS[0], "1") or 1)
            spanned_rows = int(elem.get(_SPAN_ATTRS[1], "1") or 1)
            if elem.tag == T_CELL and (spanned_cols > 1 or spanned_rows > 1):
                if len(sheet.merges) + run.count * n > _MAX_MERGES:
                    # Too many to keep track of: they stay as the file has
                    # them when rows or columns change.
                    sheet.merges_complete = False
                    c += n
                    continue
                for r in range(row, row + run.count):
                    for cc in range(c, c + n):
                        sheet.merges.append((r, cc, r + spanned_rows - 1, cc + spanned_cols - 1))
            c += n
        row += run.count


def _read_ods_frozen(settings: Optional[bytes], book: Workbook, etree) -> None:
    if not settings:
        return
    try:
        root = etree.fromstring(settings, etree.XMLParser(resolve_entities=False,
                                                          no_network=True))
    except etree.XMLSyntaxError:
        return
    config = _NS["config"]
    for entry in root.iter(f"{{{config}}}config-item-map-entry"):
        name = entry.get(f"{{{config}}}name")
        sheet = book.sheet(name) if name else None
        if sheet is None:
            continue
        items = {item.get(f"{{{config}}}name"): (item.text or "")
                 for item in entry if item.tag == f"{{{config}}}config-item"}
        rows = cols = 0
        if items.get("VerticalSplitMode") == "2":
            rows = int(items.get("VerticalSplitPosition", "0") or 0)
        if items.get("HorizontalSplitMode") == "2":
            cols = int(items.get("HorizontalSplitPosition", "0") or 0)
        if rows or cols:
            sheet.frozen = (rows, cols)


# -- writing a sheet into content.xml ----------------------------------------

def _ods_cell_element(backend: _OdsBackend, cell: Optional[Cell], base, date1904: bool):
    """The element a model cell is written as, keeping ``base``'s style,
    comment and anything else that is not its value."""
    if cell is not None and cell.source is not UNSET and cell.source is not None:
        # Unchanged content; whether it is merged comes from the runs (a
        # merge the cell belongs to may have grown or shrunk).
        if base is None or base is cell.source or \
                _merge_state(base) == _merge_state(cell.source):
            return cell.source
        return _cell_like(cell.source, *_merge_state(base))
    if base is None:
        base = backend.spreadsheet.makeelement(T_CELL, {})
    attrib = {k: v for k, v in base.attrib.items()
              if k not in _VALUE_ATTRS and k != A_COLS_REP}
    keep = [child for child in base if child.tag != TEXT_P]
    if cell is not None and isinstance(cell.extra, type(base)) and cell.extra is not base:
        keep = [child for child in cell.extra if child.tag != TEXT_P]
    element = base.makeelement(base.tag if base.tag in (T_CELL, T_COVERED) else T_CELL, attrib)
    for child in keep:
        element.append(copy.deepcopy(child))
    if cell is None:
        return element
    if cell.style:
        element.set(A_STYLE, cell.style)
    if cell.is_empty:
        return element
    value = cell.value
    if cell.formula is not None:
        element.set(A_FORMULA, sf.to_openformula(cell.formula))
    kind = number_format_kind(cell.number_format)
    shown = format_value(value, cell.number_format, NumberStyle(), date1904)

    def typed(vtype: str) -> None:
        element.set(A_VTYPE, vtype)
        if backend.has_calcext:
            element.set(A_CALC_VTYPE, vtype)

    if isinstance(value, sf.CellError) or (cell.formula is not None and value is None):
        if isinstance(value, sf.CellError) and value.code in sf.ERROR_CODES:
            element.set(A_VTYPE, "string")
            element.set(A_STRING, "")
            if backend.has_calcext:
                element.set(A_CALC_VTYPE, "error")
            shown = value.code
        elif value is None:
            return element
        else:
            return element
    elif isinstance(value, bool):
        typed("boolean")
        element.set(A_BOOL, "true" if value else "false")
        shown = "TRUE" if value else "FALSE"
    elif isinstance(value, (int, float)) or isinstance(value, (_dt.date, _dt.time,
                                                               _dt.timedelta)):
        number = value if isinstance(value, (int, float)) else sf.to_serial(value, date1904)
        if kind == "date" or isinstance(value, _dt.date):
            moment = sf.from_serial(number, date1904) if not isinstance(value, _dt.date) \
                else value
            typed("date")
            if isinstance(moment, _dt.datetime) and (moment.hour or moment.minute or
                                                     moment.second):
                element.set(A_DATE, moment.strftime("%Y-%m-%dT%H:%M:%S"))
            else:
                element.set(A_DATE, moment.strftime("%Y-%m-%d"))
        elif kind == "time" or isinstance(value, (_dt.time, _dt.timedelta)):
            typed("time")
            seconds = round(number * 86400)
            element.set(A_TIME, f"PT{seconds // 3600:02d}H{seconds % 3600 // 60:02d}M"
                                f"{seconds % 60:02d}S")
        elif kind == "percent":
            typed("percentage")
            element.set(A_VALUE, sf.number_text(number))
        elif kind == "currency":
            typed("currency")
            element.set(A_VALUE, sf.number_text(number))
            symbol = re.search(r'"([^"]*)"', cell.number_format)
            code = _CURRENCY_CODES.get((symbol.group(1) if symbol else "").strip())
            if code:
                element.set(A_CURRENCY, code)
        else:
            typed("float")
            element.set(A_VALUE, sf.number_text(number))
    else:
        typed("string")
        shown = str(value)
    for line in shown.split("\n"):
        p = element.makeelement(TEXT_P, {})
        _set_paragraph_text(p, line)
        element.append(p)
    return element


def _set_paragraph_text(p, text: str) -> None:
    """Text into a text:p the way OpenDocument writes runs of spaces and
    tabs (text:s, text:tab)."""
    parts = re.split(r"( {2,}|\t)", text)
    last = None
    for part in parts:
        if not part:
            continue
        if part == "\t":
            last = p.makeelement(_q("text", "tab"), {})
            p.append(last)
        elif part.startswith("  "):
            if last is None:
                p.text = (p.text or "") + " "
            else:
                last.tail = (last.tail or "") + " "
            last = p.makeelement(_q("text", "s"), {_q("text", "c"): str(len(part) - 1)}) \
                if len(part) > 2 else p.makeelement(_q("text", "s"), {})
            p.append(last)
        else:
            if last is None:
                p.text = (p.text or "") + part
            else:
                last.tail = (last.tail or "") + part
    if text.startswith(" ") and len(p) and p.text == " ":
        pass


def _write_ods_table(backend: _OdsBackend, sheet: Sheet, etree) -> None:
    """Rewrite the rows and columns of one table element from its runs and
    the model's cells, leaving everything else in the element alone."""
    table: _OdsTable = sheet.backend
    rows = list(table.rows)
    # Cells whose element changes: every cell whose element is not the one
    # the runs hold at its place (edited, recalculated, or put back by
    # Undo after a save), and content the model no longer has.
    writes: Dict[int, Dict[int, Optional[Cell]]] = {}
    held = _elements_at(table, sheet.cells.keys())
    for (r, c), cell in sheet.cells.items():
        if cell.source is UNSET or cell.source is None or held.get((r, c)) is not cell.source:
            writes.setdefault(r, {})[c] = cell
    row_start = 0
    for run in rows:
        col = 0
        for elem, n in run.cells:
            if _has_content(elem):
                for r in range(row_start, row_start + run.count):
                    for c in range(col, col + n):
                        if (r, c) not in sheet.cells:
                            writes.setdefault(r, {})[c] = None
            col += n
        row_start += run.count
    if writes:
        rows = _apply_writes(backend, rows, writes, sheet, table)
    columns = _apply_widths(backend, table, sheet)
    element = table.element
    first_index = None
    for index, child in enumerate(list(element)):
        if child.tag in (T_COL, T_ROW, _SOFT_BREAK) or child.tag in _ROW_GROUPS or \
                child.tag in _COL_GROUPS:
            if first_index is None:
                first_index = index
            element.remove(child)
    if first_index is None:
        first_index = len(element)
    new_children = _serialize_columns(element, columns) + _serialize_rows(element, rows)
    for offset, child in enumerate(new_children):
        element.insert(first_index + offset, child)
    # The runs and the model stay as they are: each save writes the
    # file's content from them again, so Undo after a save stays true.


def _elements_at(table: _OdsTable, positions: Iterable[Tuple[int, int]]) -> Dict:
    """The element the runs hold at each of ``positions``, in one pass."""
    by_row: Dict[int, List[int]] = {}
    for r, c in positions:
        by_row.setdefault(r, []).append(c)
    found = {}
    wanted = sorted(by_row)
    i = 0
    start = 0
    for run in table.rows:
        end = start + run.count
        while i < len(wanted) and wanted[i] < end:
            r = wanted[i]
            if r >= start:
                cols = sorted(by_row[r])
                j = 0
                c0 = 0
                for elem, n in run.cells:
                    while j < len(cols) and cols[j] < c0 + n:
                        if cols[j] >= c0:
                            found[(r, cols[j])] = elem
                        j += 1
                    c0 += n
            i += 1
        start = end
        if i >= len(wanted):
            break
    return found


def _apply_writes(backend, rows: List[_Run], writes, sheet: Sheet, table: _OdsTable):
    wanted_rows = sorted(writes)
    out: List[_Run] = []
    start = 0
    pending = list(wanted_rows)
    for run in rows:
        end = start + run.count
        inside = [r for r in pending if start <= r < end]
        if not inside:
            out.append(run)
            start = end
            continue
        cursor = start
        for r in inside:
            if r > cursor:
                out.append(run.with_(count=r - cursor))
            out.append(run.with_(count=1, cells=_write_row_cells(backend, run.cells, writes[r],
                                                                 sheet)))
            cursor = r + 1
        if cursor < end:
            out.append(run.with_(count=end - cursor))
        pending = [r for r in pending if r >= end]
        start = end
    for r in pending:
        if r > start:
            out.append(_Run(table.element.makeelement(T_ROW, {}), r - start, (), ()))
        out.append(_Run(table.element.makeelement(T_ROW, {}), 1,
                        _write_row_cells(backend, (), writes[r], sheet), ()))
        start = r + 1
    return out


def _write_row_cells(backend, cells: Tuple, row_writes: Dict[int, Optional[Cell]],
                     sheet: Sheet) -> Tuple:
    out = []
    start = 0
    pending = sorted(row_writes)
    book_date1904 = backend.date1904
    for elem, n in cells:
        end = start + n
        inside = [c for c in pending if start <= c < end]
        if not inside:
            out.append((elem, n))
            start = end
            continue
        cursor = start
        for c in inside:
            if c > cursor:
                out.append((elem, c - cursor))
            out.append((_ods_cell_element(backend, row_writes[c], elem, book_date1904), 1))
            cursor = c + 1
        if cursor < end:
            out.append((elem, end - cursor))
        pending = [c for c in pending if c >= end]
        start = end
    for c in pending:
        if c > start:
            out.append((backend.spreadsheet.makeelement(T_CELL, {}), c - start))
        out.append((_ods_cell_element(backend, row_writes[c], None, book_date1904), 1))
        start = c + 1
    return tuple(out)


def _apply_widths(backend: _OdsBackend, table: _OdsTable, sheet: Sheet):
    columns = list(table.columns)

    def col_with(entry, n):
        return (entry[0], n, entry[2])

    for col in sorted(sheet.changed_widths):
        total = _total(columns, lambda c: c[1])
        if col >= total:
            template = columns[-1] if columns else (table.element.makeelement(T_COL, {}), 1, ())
            if col > total:
                columns.append((template[0], col - total, template[2]))
            columns.append((template[0], 1, template[2]))
        index = _split_runs(columns, col, lambda c: c[1], col_with)
        _split_runs(columns, col + 1, lambda c: c[1], col_with)
        elem, n, path = columns[index]
        style_name = _column_style(backend, elem.get(A_STYLE), sheet.width(col))
        new = elem.makeelement(T_COL, dict(elem.attrib))
        new.set(A_STYLE, style_name)
        columns[index] = (new, n, path)
    return columns


def _column_style(backend: _OdsBackend, base: Optional[str], pixels: float) -> str:
    """A column style like ``base`` but ``pixels`` wide; made once for
    each width, however often the file is saved."""
    cache = backend.column_style_cache
    key = (base, round(pixels, 2))
    if key in cache:
        return cache[key]
    styles = backend.styles
    number = 1
    while f"coME{number}" in styles.column_styles:
        number += 1
    name = f"coME{number}"
    original = styles.column_styles.get(base or "")
    if original is not None:
        style = copy.deepcopy(original)
    else:
        style = styles.automatic.makeelement(_q("style", "style"), {
            _q("style", "family"): "table-column"})
    style.set(_q("style", "name"), name)
    props = style.find(_q("style", "table-column-properties"))
    if props is None:
        props = style.makeelement(_q("style", "table-column-properties"), {})
        style.append(props)
    props.set(_q("style", "column-width"), f"{pixels / 96 * 2.54:.3f}cm")
    props.set(_q("style", "use-optimal-column-width"), "false")
    styles.automatic.append(style)
    styles.column_styles[name] = style
    cache[key] = name
    return name


def _nest(parent, items: List[Tuple[object, Tuple]], make):
    """Elements in their groups again: ``items`` are (element, path)
    pairs, consecutive ones with the same group share one group element."""
    out = []
    stack: List[Tuple[object, object]] = []    # (original group, new group)
    for element, path in items:
        depth = 0
        while depth < len(stack) and depth < len(path) and stack[depth][0] is path[depth]:
            depth += 1
        del stack[depth:]
        for group in path[depth:]:
            new_group = parent.makeelement(group.tag, dict(group.attrib))
            if stack:
                stack[-1][1].append(new_group)
            else:
                out.append(new_group)
            stack.append((group, new_group))
        if stack:
            stack[-1][1].append(element)
        else:
            out.append(element)
    return out


def _serialize_columns(parent, columns) -> List:
    items = []
    for elem, count, path in columns:
        new = parent.makeelement(T_COL, {k: v for k, v in elem.attrib.items()
                                         if k != A_COLS_REP})
        if count > 1:
            new.set(A_COLS_REP, str(count))
        items.append((new, path))
    return _nest(parent, items, None)


def _serialize_rows(parent, rows: List[_Run]) -> List:
    items = []
    for run in rows:
        new = parent.makeelement(run.elem.tag, {k: v for k, v in run.elem.attrib.items()
                                                if k != A_ROWS_REP})
        if run.count > 1:
            new.set(A_ROWS_REP, str(run.count))
        for elem, n in _merge_cells(run.cells):
            cell = copy.deepcopy(elem)
            cell.tail = None
            if n > 1:
                cell.set(A_COLS_REP, str(n))
            else:
                cell.attrib.pop(A_COLS_REP, None)
            new.append(cell)
        if not len(new):
            new.append(parent.makeelement(T_CELL, {}))
        items.append((new, run.path))
    return _nest(parent, items, None)


def _merge_cells(cells: Tuple) -> List[Tuple[object, int]]:
    """Neighbouring pieces of one split run joined again."""
    out: List[Tuple[object, int]] = []
    for elem, n in cells:
        if out and out[-1][0] is elem:
            out[-1] = (elem, out[-1][1] + n)
        else:
            out.append((elem, n))
    return out


# --------------------------------------------------------------------------- #
# How cells look: fill, font colour, borders, alignment, wrap                 #
# --------------------------------------------------------------------------- #

@dataclasses.dataclass(frozen=True)
class Border:
    """One side of a cell's border: its width on screen in pixels (1 thin,
    2 medium, 3 thick or double), its colour ("#RRGGBB", None for the
    automatic black) and whether it is dashed or dotted."""

    width: int
    color: Optional[str] = None
    dashed: bool = False


@dataclasses.dataclass(frozen=True)
class Look:
    """What a cell's style shows: colours as "#RRGGBB" (None where the file
    leaves them automatic), ``align`` "left", "center" or "right" (None:
    numbers right, text left), and the borders by side."""

    fill: Optional[str] = None
    color: Optional[str] = None
    bold: bool = False
    italic: bool = False
    align: Optional[str] = None
    wrap: bool = False
    left: Optional[Border] = None
    right: Optional[Border] = None
    top: Optional[Border] = None
    bottom: Optional[Border] = None


PLAIN = Look()

_ALIGN = {"left": "left", "start": "left", "justify": "left", "fill": "left",
          "center": "center", "centerContinuous": "center", "distributed": "center",
          "right": "right", "end": "right"}

# Excel's border styles by how thick they are drawn.
_BORDER_WIDTH = {"hair": 1, "thin": 1, "dotted": 1, "dashed": 1, "dashDot": 1, "dashDotDot": 1,
                 "medium": 2, "mediumDashed": 2, "mediumDashDot": 2, "mediumDashDotDot": 2,
                 "slantDashDot": 2, "thick": 3, "double": 3}

# The Office theme's colours, for a workbook that carries no theme.
_OFFICE_THEME = ["FFFFFF", "000000", "E7E6E6", "44546A", "4472C4", "ED7D31", "A5A5A5",
                 "FFC000", "5B9BD5", "70AD47", "0563C1", "954F72"]


def _theme_colors(loaded_theme) -> List[str]:
    """A workbook's theme colours in Excel's order (light 1, dark 1,
    light 2, dark 2, six accents, link, followed link)."""
    if not loaded_theme:
        return list(_OFFICE_THEME)
    try:
        from lxml import etree
        root = etree.fromstring(loaded_theme, etree.XMLParser(resolve_entities=False,
                                                              no_network=True))
    except Exception:     # a damaged theme: the Office colours are a fair guess
        return list(_OFFICE_THEME)
    drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
    scheme = root.find(f".//{{{drawing}}}clrScheme")
    if scheme is None:
        return list(_OFFICE_THEME)
    found = {}
    for child in scheme:
        name = etree.QName(child).localname
        for color in child:
            value = color.get("val") if etree.QName(color).localname == "srgbClr" else \
                color.get("lastClr")
            if value:
                found[name] = value
    order = ["lt1", "dk1", "lt2", "dk2", "accent1", "accent2", "accent3", "accent4",
             "accent5", "accent6", "hlink", "folHlink"]
    return [found.get(name, default) for name, default in zip(order, _OFFICE_THEME)]


def _tinted(hex_rgb: str, tint: float) -> str:
    """A theme colour made lighter (tint > 0) or darker (< 0), as Excel
    does it, on the colour's lightness."""
    import colorsys
    r, g, b = (int(hex_rgb[i:i + 2], 16) / 255 for i in (0, 2, 4))
    h, lightness, s = colorsys.rgb_to_hls(r, g, b)
    if tint < 0:
        lightness = lightness * (1 + tint)
    else:
        lightness = lightness * (1 - tint) + tint
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, lightness)), s)
    return "".join(f"{round(v * 255):02X}" for v in (r, g, b))


class _XlsxLooks:
    """Looks of an Excel workbook's styles, worked out once per style."""

    def __init__(self, wb) -> None:
        self.wb = wb
        self.theme = _theme_colors(getattr(wb, "loaded_theme", None))
        from openpyxl.styles.colors import COLOR_INDEX
        self.indexed = list(getattr(wb, "_colors", None) or COLOR_INDEX)
        self.cache: Dict[tuple, Look] = {}

    def color(self, color) -> Optional[str]:
        if color is None:
            return None
        try:
            kind = color.type
            if kind == "rgb":
                value = str(color.rgb)
                if len(value) >= 6 and re.fullmatch(r"[0-9A-Fa-f]+", value):
                    return "#" + value[-6:].upper()
                return None
            if kind == "indexed":
                index = int(color.indexed)
                if 0 <= index < len(self.indexed):
                    return "#" + str(self.indexed[index])[-6:].upper()
                return None     # 64, 65: the system's own colours
            if kind == "theme":
                index = int(color.theme)
                if 0 <= index < len(self.theme):
                    base = self.theme[index]
                    tint = float(color.tint or 0)
                    return "#" + (_tinted(base, tint) if tint else base.upper())
        except (AttributeError, TypeError, ValueError):
            return None
        return None

    def look(self, style) -> Look:
        if style is None:
            return PLAIN
        key = (style.fontId, style.fillId, style.borderId, style.alignmentId)
        found = self.cache.get(key)
        if found is not None:
            return found
        wb = self.wb
        values = {}
        try:
            font = wb._fonts[style.fontId]
            values["bold"] = bool(font.b)
            values["italic"] = bool(font.i)
            values["color"] = self.color(font.color)
        except (IndexError, AttributeError):
            pass
        try:
            fill = wb._fills[style.fillId]
            pattern = getattr(fill, "patternType", None)
            if pattern and pattern != "none":
                values["fill"] = self.color(fill.fgColor if pattern == "solid" else
                                            fill.fgColor) or None
            elif getattr(fill, "stop", None):
                values["fill"] = self.color(fill.stop[0].color)
        except (IndexError, AttributeError):
            pass
        try:
            border = wb._borders[style.borderId]
            for side in ("left", "right", "top", "bottom"):
                edge = getattr(border, side, None)
                kind = getattr(edge, "style", None) if edge is not None else None
                if kind and kind in _BORDER_WIDTH:
                    values[side] = Border(_BORDER_WIDTH[kind], self.color(edge.color),
                                          "dash" in kind.lower() or kind == "dotted")
        except (IndexError, AttributeError):
            pass
        try:
            alignment = wb._alignments[style.alignmentId]
            values["align"] = _ALIGN.get(alignment.horizontal or "")
            values["wrap"] = bool(alignment.wrap_text)
        except (IndexError, AttributeError):
            pass
        found = Look(**values)
        self.cache[key] = found
        return found


_ODS_BORDER = re.compile(r"(?P<width>[\d.]+)(?P<unit>pt|cm|mm|in|px)\s+(?P<style>\w+)"
                         r"(?:\s+(?P<color>#[0-9A-Fa-f]{6}))?")


def _ods_border(text: Optional[str]) -> Optional[Border]:
    if not text or text.strip() in ("none", "hidden"):
        return None
    match = _ODS_BORDER.search(text)
    if not match:
        return None
    points = (_length_px(match.group("width") + match.group("unit")) or 0) * 72 / 96
    style = match.group("style")
    width = 3 if style == "double" or points > 1.8 else (2 if points > 0.8 else 1)
    color = match.group("color")
    return Border(width, color.upper() if color else None, style in ("dashed", "dotted"))


class _OdsLooks:
    """Looks of an OpenDocument spreadsheet's cell styles."""

    def __init__(self, styles: "_OdsStyles") -> None:
        self.styles = styles
        self.cache: Dict[Optional[str], Look] = {}

    def look(self, name: Optional[str]) -> Look:
        if not name:
            return PLAIN
        found = self.cache.get(name)
        if found is not None:
            return found
        chain, seen, current = [], set(), name
        while current and current not in seen:
            seen.add(current)
            element = self.styles.cell_styles.get(current)
            if element is None:
                break
            chain.append(element)
            current = element.get(_q("style", "parent-style-name"))
        values: Dict[str, object] = {}
        for element in reversed(chain):     # the style's own settings win
            cell = element.find(_q("style", "table-cell-properties"))
            if cell is not None:
                background = cell.get(_q("fo", "background-color"))
                if background:
                    values["fill"] = None if background == "transparent" else background.upper()
                every = cell.get(_q("fo", "border"))
                if every is not None:
                    for side in ("left", "right", "top", "bottom"):
                        values[side] = _ods_border(every)
                for side in ("left", "right", "top", "bottom"):
                    one = cell.get(_q("fo", f"border-{side}"))
                    if one is not None:
                        values[side] = _ods_border(one)
                wrap = cell.get(_q("fo", "wrap-option"))
                if wrap:
                    values["wrap"] = wrap == "wrap"
            paragraph = element.find(_q("style", "paragraph-properties"))
            if paragraph is not None and paragraph.get(_q("fo", "text-align")):
                values["align"] = _ALIGN.get(paragraph.get(_q("fo", "text-align")))
            text = element.find(_q("style", "text-properties"))
            if text is not None:
                color = text.get(_q("fo", "color"))
                if color:
                    values["color"] = color.upper()
                weight = text.get(_q("fo", "font-weight"))
                if weight:
                    values["bold"] = weight == "bold" or (weight.isdigit() and int(weight) >= 600)
                slant = text.get(_q("fo", "font-style"))
                if slant:
                    values["italic"] = slant == "italic"
        found = Look(**values)
        self.cache[name] = found
        return found


# --------------------------------------------------------------------------- #
# Excel: what openpyxl drops, kept and put back                               #
# --------------------------------------------------------------------------- #
#
# Excel writes several things into ordinary workbooks that openpyxl reads
# past and does not write again: printer settings, custom views, the
# error checks someone told Excel to ignore, newer conditional formats,
# validation and sparklines (in a sheet's extLst), threaded comments and
# the people who wrote them, Office add-ins. They are read from the file
# here, moved along with every change to rows, columns and sheets (their
# addresses and formulas go through the same rewriting as the cells'),
# and put back into the package openpyxl wrote, in the order the schema
# asks for, with their relationships and content types. What is put back
# untouched is byte for byte what the file held.

_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_REL_PRINTER = _R_NS + "/printerSettings"
_REL_THREADED = "http://schemas.microsoft.com/office/2017/10/relationships/threadedComment"
_REL_PERSON = "http://schemas.microsoft.com/office/2017/10/relationships/person"
_REL_TASKPANES = "http://schemas.microsoft.com/office/2011/relationships/webextensiontaskpanes"

# CT_Worksheet's sequence: an element put back goes before the first one
# that comes later in it.
_SHEET_ORDER = (
    "sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData", "sheetCalcPr",
    "sheetProtection", "protectedRanges", "scenarios", "autoFilter", "sortState",
    "dataConsolidate", "customSheetViews", "mergeCells", "phoneticPr", "conditionalFormatting",
    "dataValidations", "hyperlinks", "printOptions", "pageMargins", "pageSetup", "headerFooter",
    "rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors", "smartTags",
    "drawing", "legacyDrawing", "legacyDrawingHF", "drawingHF", "picture", "oleObjects",
    "controls", "webPublishItems", "tableParts", "extLst")
# CT_Workbook's sequence.
_BOOK_ORDER = (
    "fileVersion", "fileSharing", "workbookPr", "workbookProtection", "bookViews", "sheets",
    "functionGroups", "externalReferences", "definedNames", "calcPr", "oleSize",
    "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes", "webPublishing",
    "fileRecoveryPr", "webPublishObjects", "extLst")

# Sheet elements and extensions kept this way. The header and footer
# replace openpyxl's own: it drops one it cannot parse (LibreOffice's and
# Numbers' font and colour codes), and they name no cells.
_KEPT_BACK_ELEMENTS = ("customSheetViews", "ignoredErrors", "headerFooter")
_X14_CONDITIONAL = "{78C0D931-6437-407d-A8EE-F0AAD7539E65}"
_X14_VALIDATION = "{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}"
_SPARKLINES = "{05C60535-1F16-4fd2-B633-F4F36F0B64E0}"
_KEPT_BACK_EXTENSIONS = frozenset({_X14_CONDITIONAL, _X14_VALIDATION, _SPARKLINES})
_KEPT_BACK_PARTS = re.compile(r"xl/printerSettings/printerSettings\d+\.bin|"
                              r"xl/threadedComments/threadedComment\d+\.xml|"
                              r"xl/persons/person\d*\.xml|xl/webextensions/.+")


def _local(element) -> str:
    from lxml import etree
    return etree.QName(element).localname


def _part_path(base: str, target: str) -> str:
    """A relationship target as a package part name."""
    if target.startswith("/"):
        return target.lstrip("/")
    folder = base.rsplit("/", 1)[0] if "/" in base else ""
    parts = [p for p in (folder + "/" + target).split("/") if p]
    out: List[str] = []
    for part in parts:
        if part == "..":
            if out:
                out.pop()
        elif part != ".":
            out.append(part)
    return "/".join(out)


def _rels_name(part: str) -> str:
    folder, _slash, name = part.rpartition("/")
    return f"{folder}/_rels/{name}.rels" if folder else f"_rels/{name}.rels"


def _relationships(entries: Dict[str, bytes], part: str, parser) -> Dict[str, Tuple[str, str]]:
    """A part's relationships: id -> (type, target part)."""
    from lxml import etree
    data = entries.get(_rels_name(part))
    if not data:
        return {}
    root = etree.fromstring(data, parser)
    found = {}
    for rel in root:
        if rel.get("TargetMode") == "External":
            continue
        found[rel.get("Id")] = (rel.get("Type"), _part_path(part, rel.get("Target", "")))
    return found


@dataclasses.dataclass
class _KeptSheet:
    """What one sheet keeps that openpyxl drops."""

    part: str
    elements: Dict[str, object]               # customSheetViews, ignoredErrors
    extensions: List[object]                  # the kept <ext> of the sheet's extLst
    rule_extensions: Dict[int, object]        # cfRule priority -> its <extLst>
    page_setup: Optional[str]                 # printer settings part of pageSetup
    rels: Dict[str, Tuple[str, str]]          # the sheet's relationships, as read
    threaded: Optional[Tuple[str, object]]    # threaded comments part and tree


@dataclasses.dataclass
class _Kept:
    """What a workbook keeps that openpyxl drops."""

    entries: Dict[str, bytes]
    content_types: object
    sheets: Dict[int, _KeptSheet]             # id(openpyxl worksheet) -> kept
    sheet_ids: Dict[str, object]              # original sheetId -> worksheet
    custom_views: Optional[object]            # <customWorkbookViews>
    persons: Optional[str]                    # the persons part
    root_rels: List[object]                   # root relationships to put back

    def snapshot(self):
        return ({key: (dict((k, copy.deepcopy(v)) for k, v in kept.elements.items()),
                       [copy.deepcopy(e) for e in kept.extensions],
                       copy.deepcopy(kept.threaded))
                 for key, kept in self.sheets.items()},
                copy.deepcopy(self.custom_views))

    def restore(self, state) -> None:
        sheets, views = state
        for key, (elements, extensions, threaded) in sheets.items():
            kept = self.sheets.get(key)
            if kept is not None:
                kept.elements, kept.extensions, kept.threaded = elements, extensions, threaded
        self.custom_views = views


def _read_kept(data: bytes, wb) -> Optional[_Kept]:
    """What the workbook in ``data`` holds that openpyxl will not write,
    and that is put back on saving."""
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()
                   if not name.endswith("/") and not name.startswith("xl/worksheets/sheet")
                   and not name.startswith("xl/media/")}
        book_rels = _relationships(entries, "xl/workbook.xml", parser)
        book = etree.fromstring(entries["xl/workbook.xml"], parser)
        by_title = {ws.title: ws for ws in wb.worksheets}
        kept = _Kept(entries, etree.fromstring(entries["[Content_Types].xml"], parser), {}, {},
                     None, None, [])
        for element in book:
            if _local(element) == "customWorkbookViews":
                kept.custom_views = element
            elif _local(element) == "sheets":
                for sheet in element:
                    ws = by_title.get(sheet.get("name"))
                    relation = book_rels.get(sheet.get(f"{{{_R_NS}}}id"))
                    if ws is None or relation is None:
                        continue
                    kept.sheet_ids[sheet.get("sheetId")] = ws
                    try:
                        xml = archive.read(relation[1])
                    except KeyError:
                        continue
                    kept.sheets[id(ws)] = _read_kept_sheet(relation[1], xml, entries, parser)
        for relation in book_rels.values():
            if relation[0] == _REL_PERSON:
                kept.persons = relation[1]
        root_rels = etree.fromstring(entries["_rels/.rels"], parser)
        kept.root_rels = [rel for rel in root_rels if rel.get("Type") == _REL_TASKPANES]
    return kept


def _read_kept_sheet(part: str, xml: bytes, entries, parser) -> _KeptSheet:
    from lxml import etree
    root = _sheet_head(xml)
    rels = _relationships(entries, part, parser)
    kept = _KeptSheet(part, {}, [], {}, None, rels, None)
    for child in root:
        if not isinstance(child.tag, str):
            continue
        name = _local(child)
        if name in _KEPT_BACK_ELEMENTS:
            kept.elements[name] = child
        elif name == "extLst":
            kept.extensions = [ext for ext in child if ext.get("uri") in _KEPT_BACK_EXTENSIONS]
        elif name == "pageSetup":
            relation = rels.get(child.get(f"{{{_R_NS}}}id") or "")
            if relation and relation[0] == _REL_PRINTER:
                kept.page_setup = relation[1]
        elif name == "conditionalFormatting":
            for rule in child:
                extension = next((e for e in rule if isinstance(e.tag, str)
                                  and _local(e) == "extLst"), None)
                if extension is not None and rule.get("priority"):
                    kept.rule_extensions[int(rule.get("priority"))] = extension
    for relation_type, target in rels.values():
        if relation_type == _REL_THREADED and target in entries:
            kept.threaded = (target, etree.fromstring(entries[target], parser))
    return kept


# -- moving with the cells ----------------------------------------------------

_XM_NS = "http://schemas.microsoft.com/office/excel/2006/main"
_ADDRESS_ATTRIBUTES = ("sqref", "ref", "topLeftCell", "activeCell")


def _rewrite_kept(kept: _Kept, book, rewrite) -> None:
    """Pass the addresses and formulas of everything kept through
    ``rewrite`` (formula text, the sheet it is on), as the cells' own
    formulas are; an address whose cells were all deleted goes, and so
    does the rule, list, sparkline or comment it belonged to."""
    sheets = {id(sheet.backend): sheet for sheet in book.sheets}
    for key, kept_sheet in kept.sheets.items():
        sheet = sheets.get(key)
        if sheet is None:
            continue

        def address(text: str) -> Optional[str]:
            moved = []
            for part in text.split():
                new = rewrite("=" + part, sheet)[1:]
                if "#REF!" not in new:
                    moved.append(new)
            return " ".join(moved) if moved else None

        for element in list(kept_sheet.elements.values()):
            _rewrite_addresses_in(element, address)
        if "ignoredErrors" in kept_sheet.elements and \
                not len(kept_sheet.elements["ignoredErrors"]):
            del kept_sheet.elements["ignoredErrors"]
        for extension in kept_sheet.extensions:
            for holder in list(extension.iter(f"{{{_XM_NS}}}sqref")):
                new = address(holder.text or "")
                if new is None:
                    _remove_rule_of(holder)
                else:
                    holder.text = new
            for formula in extension.iter(f"{{{_XM_NS}}}f"):
                if formula.text:
                    formula.text = rewrite("=" + formula.text, sheet)[1:]
        if kept_sheet.threaded is not None:
            part, tree = kept_sheet.threaded
            for comment in list(tree):
                if comment.get("ref"):
                    new = address(comment.get("ref"))
                    if new is None:
                        tree.remove(comment)
                    else:
                        comment.set("ref", new)


def _rewrite_addresses_in(element, address) -> None:
    for item in list(element.iter()):
        if not isinstance(item.tag, str):
            continue
        for name in _ADDRESS_ATTRIBUTES:
            value = item.get(name)
            if value is None:
                continue
            new = address(value)
            if new is not None:
                item.set(name, new)
            elif _local(item) == "ignoredError":
                item.getparent().remove(item)
                break
            elif name in ("topLeftCell", "activeCell"):
                item.set(name, "A1")
            else:
                item.set(name, "A1")


def _remove_rule_of(holder) -> None:
    """Remove what an x14 address belongs to: a sparkline, a validation, a
    conditional format."""
    item = holder.getparent()
    while item is not None and _local(item) not in ("sparkline", "dataValidation",
                                                    "conditionalFormatting"):
        item = item.getparent()
    if item is not None and item.getparent() is not None:
        item.getparent().remove(item)


# -- putting back --------------------------------------------------------------

def _insert_in_order(root, element, order) -> None:
    name = _local(element)
    rank = order.index(name)
    for index, child in enumerate(root):
        if isinstance(child.tag, str) and _local(child) in order and \
                order.index(_local(child)) > rank:
            root.insert(index, element)
            return
    root.append(element)


class _Package:
    """The package openpyxl wrote, changed part by part."""

    def __init__(self, data: bytes, parser) -> None:
        from lxml import etree
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.infos = archive.infolist()
            self.parts = {info.filename: archive.read(info.filename) for info in self.infos}
        self.parser = parser
        self.types = etree.fromstring(self.parts["[Content_Types].xml"], parser)
        self.trees: Dict[str, object] = {}
        self.added: List[str] = []

    def tree(self, name: str):
        from lxml import etree
        if name not in self.trees:
            data = self.parts.get(name)
            if data is None:
                root = etree.Element(f"{{{_PKG_REL_NS}}}Relationships", nsmap={None: _PKG_REL_NS})
            else:
                root = etree.fromstring(data, self.parser)
            self.trees[name] = root
        return self.trees[name]

    def free_name(self, wanted: str) -> str:
        if wanted not in self.parts:
            return wanted
        stem, dot, ext = wanted.rpartition(".")
        base = re.sub(r"\d+$", "", stem)
        number = 1
        while f"{base}{number}.{ext}" in self.parts:
            number += 1
        return f"{base}{number}.{ext}"

    def add_part(self, name: str, data: bytes, original_types) -> None:
        self.parts[name] = data
        self.added.append(name)
        self._type_for(name, original_types)

    def _type_for(self, name: str, original_types) -> None:
        for override in self.types:
            if override.get("PartName") == "/" + name:
                return
        for override in original_types:
            if _local(override) == "Override" and \
                    override.get("PartName", "").lstrip("/") in (name, self._source_of(name)):
                self.types.append(self.types.makeelement(
                    f"{{{_CT_NS}}}Override", {"PartName": "/" + name,
                                              "ContentType": override.get("ContentType")}))
                return
        extension = name.rpartition(".")[2].lower()
        if any(d.get("Extension", "").lower() == extension for d in self.types
               if _local(d) == "Default"):
            return
        for default in original_types:
            if _local(default) == "Default" and default.get("Extension", "").lower() == extension:
                self.types.insert(0, copy.deepcopy(default))
                return

    def _source_of(self, name: str) -> str:
        return getattr(self, "_sources", {}).get(name, name)

    def relate(self, part: str, relation_type: str, target: str) -> str:
        """A new relationship from ``part`` to ``target``; its id."""
        rels = self.tree(_rels_name(part))
        taken = {rel.get("Id") for rel in rels}
        number = 1
        while f"rIdKept{number}" in taken:
            number += 1
        rel_id = f"rIdKept{number}"
        folder = part.rsplit("/", 1)[0] if "/" in part else ""
        relative = target[len(folder) + 1:] if folder and target.startswith(folder + "/") \
            else "/" + target
        rels.append(rels.makeelement(f"{{{_PKG_REL_NS}}}Relationship",
                                     {"Id": rel_id, "Type": relation_type, "Target": relative}))
        return rel_id

    def bytes(self) -> bytes:
        from lxml import etree
        for name, tree in self.trees.items():
            self.parts[name] = etree.tostring(tree, xml_declaration=True, encoding="UTF-8",
                                              standalone=True)
        self.parts["[Content_Types].xml"] = etree.tostring(
            self.types, xml_declaration=True, encoding="UTF-8", standalone=True)
        out = io.BytesIO()
        known = {info.filename for info in self.infos}
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            for info in self.infos:
                archive.writestr(info, self.parts[info.filename],
                                 compress_type=info.compress_type)
            for name in self.parts:
                if name not in known:
                    archive.writestr(name, self.parts[name])
        return out.getvalue()


def _put_back(data: bytes, book, kept: _Kept) -> bytes:
    """``data`` (what openpyxl wrote) with everything ``kept`` put back."""
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    package = _Package(data, parser)
    package._sources = {}
    copied: Dict[str, str] = {}

    def copy_part(original: str) -> str:
        """The original part copied into the package once; its new name."""
        if original not in copied:
            name = package.free_name(original)
            package._sources[name] = original
            package.add_part(name, kept.entries[original], kept.content_types)
            copied[original] = name
        return copied[original]

    for sheet in book.sheets:
        ws = sheet.backend
        kept_sheet = kept.sheets.get(id(ws))
        if kept_sheet is None:
            continue
        part = ws.path.lstrip("/")
        root = package.tree(part)
        for name, element in kept_sheet.elements.items():
            for written in [c for c in root if isinstance(c.tag, str) and _local(c) == name]:
                root.remove(written)
            element = copy.deepcopy(element)
            _relink(element, kept_sheet, package, part, copy_part, kept.entries)
            _insert_in_order(root, element, _SHEET_ORDER)
        if kept_sheet.extensions:
            extension_list = next((child for child in root if isinstance(child.tag, str)
                                   and _local(child) == "extLst"), None)
            if extension_list is None:
                extension_list = root.makeelement(f"{{{_SHEET_NS}}}extLst", {})
                _insert_in_order(root, extension_list, _SHEET_ORDER)
            for extension in kept_sheet.extensions:
                extension_list.append(copy.deepcopy(extension))
        if kept_sheet.rule_extensions:
            _put_back_rule_extensions(root, kept_sheet)
        if kept_sheet.page_setup and kept_sheet.page_setup in kept.entries:
            setup = next((child for child in root if isinstance(child.tag, str)
                          and _local(child) == "pageSetup"), None)
            if setup is not None:
                setup.set(f"{{{_R_NS}}}id",
                          package.relate(part, _REL_PRINTER, copy_part(kept_sheet.page_setup)))
        if kept_sheet.threaded is not None and len(kept_sheet.threaded[1]):
            original, tree = kept_sheet.threaded
            name = package.free_name(original)
            package._sources[name] = original
            package.add_part(name, etree.tostring(tree, xml_declaration=True, encoding="UTF-8",
                                                  standalone=True), kept.content_types)
            package.relate(part, _REL_THREADED, name)
    _put_back_book(package, book, kept, copy_part)
    return package.bytes()


def _relink(element, kept_sheet: _KeptSheet, package, part: str, copy_part,
            entries: Dict[str, bytes]) -> None:
    """Relationships an element points to (a custom view's printer
    settings) made again in the new package."""
    key = f"{{{_R_NS}}}id"
    for item in element.iter():
        if not isinstance(item.tag, str) or item.get(key) is None:
            continue
        relation = kept_sheet.rels.get(item.get(key))
        if relation is None or relation[1] not in entries:
            del item.attrib[key]
            continue
        item.set(key, package.relate(part, relation[0], copy_part(relation[1])))


def _put_back_rule_extensions(root, kept_sheet: _KeptSheet) -> None:
    """The links of conditional formats to their newer (x14) parts, which
    openpyxl drops from each rule: matched by the rule's priority, which
    openpyxl keeps. A rule that cannot be matched stops the save rather
    than leaving a conditional format drawn twice."""
    found = set()
    for formatting in root:
        if not isinstance(formatting.tag, str) or _local(formatting) != "conditionalFormatting":
            continue
        for rule in formatting:
            if not isinstance(rule.tag, str) or not rule.get("priority"):
                continue
            extension = kept_sheet.rule_extensions.get(int(rule.get("priority")))
            if extension is not None:
                rule.append(copy.deepcopy(extension))
                found.add(int(rule.get("priority")))
    linked = {priority for priority, ext in kept_sheet.rule_extensions.items()}
    if linked - found and _rules_still_there(kept_sheet, linked - found):
        raise UnsaveablePartsError([_("Conditional formatting with data bars or icon sets")])


def _rules_still_there(kept_sheet: _KeptSheet, priorities) -> bool:
    """Whether the newer parts of these rules are still kept (a rule whose
    cells were all deleted went with them)."""
    ids = set()
    for priority in priorities:
        extension = kept_sheet.rule_extensions[priority]
        ids |= {node.text for node in extension.iter() if _local(node) == "id" and node.text}
    for extension in kept_sheet.extensions:
        for node in extension.iter():
            if isinstance(node.tag, str) and _local(node) == "id" and node.text in ids:
                return True
    return False


def _put_back_book(package: _Package, book, kept: _Kept, copy_part) -> None:
    from lxml import etree
    workbook = package.tree("xl/workbook.xml")
    if kept.custom_views is not None and len(kept.custom_views):
        views = copy.deepcopy(kept.custom_views)
        new_ids = {}
        sheets_element = next(c for c in workbook if isinstance(c.tag, str)
                              and _local(c) == "sheets")
        title_ids = {s.get("name"): s.get("sheetId") for s in sheets_element}
        for old, ws in kept.sheet_ids.items():
            if ws in book.backend.wb._sheets and ws.title in title_ids:
                new_ids[old] = title_ids[ws.title]
        first = next(iter(title_ids.values()), "1")
        for view in views:
            if view.get("activeSheetId") is not None:
                view.set("activeSheetId", new_ids.get(view.get("activeSheetId"), first))
        _insert_in_order(workbook, views, _BOOK_ORDER)
    if kept.persons and kept.persons in kept.entries:
        package.relate("xl/workbook.xml", _REL_PERSON, copy_part(kept.persons))
    if kept.root_rels:
        root = package.tree("_rels/.rels")
        for relation in kept.root_rels:
            target = _part_path("", relation.get("Target", ""))
            if target not in kept.entries:
                continue
            for name in kept.entries:
                if name.startswith("xl/webextensions/"):
                    copy_part(name)
            added = etree.fromstring(etree.tostring(relation))
            taken = {rel.get("Id") for rel in root}
            if added.get("Id") in taken:
                number = 1
                while f"rIdKept{number}" in taken:
                    number += 1
                added.set("Id", f"rIdKept{number}")
            root.append(added)
