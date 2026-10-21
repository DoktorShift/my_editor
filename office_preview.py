# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What MyEditor can show of an office file without the app that made it.

Three things, each from the file alone:

- ``package_thumbnail``: the preview picture an office app stores inside
  its file (OpenDocument ``Thumbnails/thumbnail.png``, Office's
  ``docProps/thumbnail.jpeg`` when "Save preview picture" was on, the
  ``preview.jpg`` of Numbers, Pages and Keynote). The file may be a zip
  or, for the Apple apps, a package folder.
- ``document_html``: a reading view of a Word or Writer document (.docx,
  .odt). pandoc makes it when it is on this computer; without it a
  plain reading of the document's XML gives the headings, paragraphs,
  lists, tables and pictures, without the page layout.
- ``import_as_markdown``: the same document as a new note next to it,
  its pictures in the note's ``<stem>_media`` folder, the original left
  as it is and nothing ever replaced.

Every file here comes from somebody else, so all of it is treated as
untrusted: only a regular file is opened (a pipe or a device with an
office file's name would block the reader or never end), a zip with
more parts or more unpacked bytes than an office file has is refused
from its end record before its parts are listed, sizes are capped
before anything is read (a zip states sizes that can lie, so reads are
capped too), XML is parsed without entities, DTDs or network access,
a paragraph's links are written in one pass over its text, pictures go
through ``image_safety`` (no SVG, no picture past the pixel budget),
pandoc runs with a time limit and extracts pictures only when it is
3.1.6 or later (older ones write wherever the document says). Reading
takes time, so the viewers do it on a worker thread.
"""

from __future__ import annotations

import base64
import html
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from typing import Optional

from PySide6.QtGui import QImage

import image_safety
import regular_files
import rmd_toolchain
from atomic_file import free_path, save_document, save_text_document
from i18n import _

_log = logging.getLogger(__name__)

# The documents the reading view and Import as Note take, by the pandoc
# reader that reads them.
_PANDOC_FORMATS = {".docx": "docx", ".odt": "odt"}

# A preview picture is a few hundred KB; anything past this is not one.
THUMBNAIL_BYTES_LIMIT = 8 * 1024 * 1024
# The document's own XML (word/document.xml, content.xml), uncompressed.
XML_BYTES_LIMIT = 64 * 1024 * 1024
# One picture in a document, and all of them together.
PICTURE_BYTES_LIMIT = 16 * 1024 * 1024
PICTURES_TOTAL_LIMIT = 96 * 1024 * 1024
# The HTML handed to the reading view.
HTML_CHARS_LIMIT = 16 * 1024 * 1024
# The most parts a zip may have, the bytes its list of parts may take,
# and the bytes all its parts may hold unpacked: an office file has a
# few hundred parts, a large one a few thousand.
ZIP_ENTRY_LIMIT = 10_000
ZIP_DIRECTORY_LIMIT = 2 * 1024 * 1024
ZIP_TOTAL_LIMIT = 512 * 1024 * 1024
_EOCD_SIZE = 22
# How long a reading view may take, pandoc or not, and an import.
READING_SECONDS = 20
IMPORT_SECONDS = 60

# Where an office app keeps its preview picture, in the order tried.
_THUMBNAIL_NAMES = (
    "Thumbnails/thumbnail.png",          # OpenDocument (.odt .ods .odp)
    "docProps/thumbnail.jpeg",           # Office Open XML (.docx .xlsx .pptx)
    "docProps/thumbnail.jpg",
    "docProps/thumbnail.png",
    "preview.jpg",                       # Numbers, Pages, Keynote (2013 on)
    "QuickLook/Preview.jpg",             # older iWork files
    "QuickLook/Preview.png",
    "preview-web.jpg",
    "QuickLook/Thumbnail.jpg",
    "QuickLook/Thumbnail.png",
)

_OOXML_THUMBNAIL_TYPE = ("http://schemas.openxmlformats.org/package/2006/"
                         "relationships/metadata/thumbnail")


class DocumentUnreadable(Exception):
    """The document could not be read; the message is a sentence for people."""


# --------------------------------------------------------------------------- #
# Reading parts of a zip or a package folder, capped                         #
# --------------------------------------------------------------------------- #

def _read_capped(stream, limit: int) -> Optional[bytes]:
    """At most ``limit`` bytes of ``stream``, or None when there are more.
    A zip's stated size can lie, so the read itself is what is capped."""
    data = stream.read(limit + 1)
    return None if len(data) > limit else data


class ZipTooLarge(zipfile.BadZipFile):
    """A zip with more parts, or more bytes in them, than an office file
    has; refused before its parts are listed."""


class _OwnedZip(zipfile.ZipFile):
    """A zip read from a stream it owns: closing the zip closes the file."""

    def __init__(self, stream):
        self._owned_stream = stream
        try:
            super().__init__(stream)
        except BaseException:
            stream.close()
            raise

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._owned_stream.close()


def _zip_directory(stream) -> tuple[int, int]:
    """(parts, bytes of the central directory) as the zip's end record
    states them (the Zip64 record when the plain one overflows), read
    from the end of the file without listing a single part. Raises
    BadZipFile when there is no such record."""
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    tail_size = min(size, _EOCD_SIZE + 0xFFFF)
    stream.seek(size - tail_size)
    tail = stream.read(tail_size)
    at = tail.rfind(b"PK\x05\x06")
    if at < 0 or tail_size - at < _EOCD_SIZE:
        raise zipfile.BadZipFile("no end of central directory record")
    fields = struct.unpack("<4s4H2LH", tail[at:at + _EOCD_SIZE])
    entries, directory_size, directory_offset = fields[4], fields[5], fields[6]
    if entries == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        record_at = size - tail_size + at
        if record_at < 20:
            raise zipfile.BadZipFile("no Zip64 locator")
        stream.seek(record_at - 20)
        locator = stream.read(20)
        if len(locator) != 20 or locator[:4] != b"PK\x06\x07":
            raise zipfile.BadZipFile("no Zip64 locator")
        offset = struct.unpack("<Q", locator[8:16])[0]
        if offset > size - 56:
            raise zipfile.BadZipFile("a Zip64 record outside the file")
        stream.seek(offset)
        record = stream.read(56)
        if len(record) != 56 or record[:4] != b"PK\x06\x06":
            raise zipfile.BadZipFile("no Zip64 end record")
        entries, directory_size = struct.unpack("<QQ", record[32:48])
    return entries, directory_size


def _open_zip(path: str) -> zipfile.ZipFile:
    """The office file at ``path`` opened as a zip, when it is a regular
    file (never a pipe or a device) whose parts an office file could
    have: at most ZIP_ENTRY_LIMIT of them, ZIP_TOTAL_LIMIT bytes
    together. The count is read from the zip's end record before
    anything is listed, so a zip of a million empty parts costs nothing.
    Raises OSError, BadZipFile or ZipTooLarge."""
    stream = regular_files.open_regular_file(path)
    try:
        entries, directory_size = _zip_directory(stream)
        if entries > ZIP_ENTRY_LIMIT or directory_size > ZIP_DIRECTORY_LIMIT:
            raise ZipTooLarge(f"{entries} parts in {directory_size} bytes")
        stream.seek(0)
    except BaseException:
        stream.close()
        raise
    archive = _OwnedZip(stream)
    infos = archive.infolist()
    if len(infos) > ZIP_ENTRY_LIMIT or sum(i.file_size for i in infos) > ZIP_TOTAL_LIMIT:
        archive.close()
        raise ZipTooLarge("too many bytes")
    return archive


def _zip_index(archive: zipfile.ZipFile) -> dict:
    """{lower-case part name: its ZipInfo}, made once per zip (the first
    of two names that differ only in case wins)."""
    index = getattr(archive, "_myeditor_index", None)
    if index is None:
        index = {}
        for info in archive.infolist():
            index.setdefault(info.filename.lower(), info)
        archive._myeditor_index = index
    return index


def _zip_member(archive: zipfile.ZipFile, name: str, limit: int) -> Optional[bytes]:
    """The bytes of ``name`` (found case-insensitively), or None when it is
    missing, larger than ``limit`` or unreadable."""
    info = _zip_index(archive).get(name.lower())
    if info is None or info.is_dir() or info.file_size > limit:
        return None
    try:
        with archive.open(info) as stream:
            return _read_capped(stream, limit)
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, EOFError,
            NotImplementedError):
        return None


def _package_member(folder: str, name: str, limit: int) -> Optional[bytes]:
    """The bytes of ``name`` inside a package folder, never outside it
    (a symlink in the package does not lead out), and only a regular
    file (a pipe or a device with the name is never opened)."""
    root = os.path.realpath(folder)
    path = os.path.realpath(os.path.join(folder, *name.split("/")))
    try:
        if os.path.commonpath([root, path]) != root:
            return None
    except ValueError:
        return None
    return regular_files.read_regular_file(path, limit)


def _ooxml_thumbnail_name(archive: zipfile.ZipFile) -> Optional[str]:
    """The thumbnail the package's relationships name, when they name one."""
    data = _zip_member(archive, "_rels/.rels", 1024 * 1024)
    if not data:
        return None
    try:
        root = _parse_xml(data)
    except DocumentUnreadable:
        return None
    for rel in root:
        if rel.get("Type") == _OOXML_THUMBNAIL_TYPE and rel.get("TargetMode") != "External":
            target = (rel.get("Target") or "").lstrip("/")
            return target or None
    return None


def package_thumbnail(path: str) -> Optional[QImage]:
    """The preview picture stored in the office file at ``path``, or None
    when it has none (or none this app may decode: a picture past the size
    or pixel budget, an SVG, a Windows metafile), or when the file is no
    zip an office app would write (see _open_zip). Reads the disk: the
    viewer and the file list call it on a worker thread."""
    try:
        if os.path.isdir(path):
            for name in _THUMBNAIL_NAMES:
                image = _decode(_package_member(path, name, THUMBNAIL_BYTES_LIMIT))
                if image is not None:
                    return image
            # A package keeps the zip it was saved as inside it sometimes.
            return None
        with _open_zip(path) as archive:
            declared = _ooxml_thumbnail_name(archive)
            names = ((declared,) if declared else ()) + _THUMBNAIL_NAMES
            for name in names:
                image = _decode(_zip_member(archive, name, THUMBNAIL_BYTES_LIMIT))
                if image is not None:
                    return image
    except ZipTooLarge as error:
        _log.info("Not looking into %s, a zip of more than an office file has: %s",
                  path, error)
    except zipfile.BadZipFile:
        pass                                  # an old .doc, a .key of 2009: no zip
    except (OSError, RuntimeError, ValueError, EOFError):
        _log.info("No preview picture in %s", path, exc_info=True)
    return None


def _decode(data: Optional[bytes]) -> Optional[QImage]:
    if not data:
        return None
    return image_safety.decode_image_bytes(data)


# --------------------------------------------------------------------------- #
# XML                                                                         #
# --------------------------------------------------------------------------- #

def _parse_xml(data: bytes):
    """An lxml element tree from untrusted bytes: no entities resolved, no
    DTD loaded, nothing fetched, no giant trees."""
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             dtd_validation=False, huge_tree=False, remove_comments=True,
                             remove_pis=True)
    try:
        return etree.fromstring(data, parser)
    except (etree.XMLSyntaxError, ValueError) as error:
        raise DocumentUnreadable(_("The document’s text could not be read.")) from error


# --------------------------------------------------------------------------- #
# The document as blocks, without pandoc                                      #
# --------------------------------------------------------------------------- #

@dataclass
class _Block:
    """One piece of a document read without pandoc: ``kind`` is "h" (with
    ``level``), "p", "li", "table" (``rows`` of cell texts) or "img"
    (``picture`` names the part in the zip). A paragraph also keeps its
    text in ``pieces``, in order: ``(words, web address or "")``, so its
    links are written around exactly the words they cover."""

    kind: str
    text: str = ""
    level: int = 0
    rows: Optional[list] = None
    picture: str = ""
    pieces: Optional[list] = None


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_V = "urn:schemas-microsoft-com:vml"
_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_XLINK = "http://www.w3.org/1999/xlink"


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


class _Deadline:
    """The time a reading may take; ``expired`` says when to stop."""

    def __init__(self, seconds: float):
        self._end = time.monotonic() + seconds
        self.hit = False

    def expired(self) -> bool:
        if time.monotonic() > self._end:
            self.hit = True
        return self.hit


def _docx_blocks(archive: zipfile.ZipFile, deadline: _Deadline) -> tuple[list, dict]:
    """A .docx as blocks, and the pictures it names (relationship id to
    the part's name in the zip)."""
    data = _zip_member(archive, "word/document.xml", XML_BYTES_LIMIT)
    if data is None:
        raise DocumentUnreadable(_("This file is not a Word document MyEditor can read."))
    root = _parse_xml(data)
    rels = _relationships(archive, "word/_rels/document.xml.rels", base="word/")
    headings = _docx_heading_styles(archive)
    body = root.find(_q(_W, "body"))
    blocks: list = []
    if body is not None:
        _docx_walk(body, blocks, rels, headings, deadline)
    return blocks, rels


def _relationships(archive, name: str, base: str) -> dict:
    """{relationship id: zip part, or ("url", address) for a web link}."""
    data = _zip_member(archive, name, 4 * 1024 * 1024)
    if not data:
        return {}
    try:
        root = _parse_xml(data)
    except DocumentUnreadable:
        return {}
    found = {}
    for rel in root:
        rid, target = rel.get("Id"), rel.get("Target") or ""
        if not rid:
            continue
        if rel.get("TargetMode") == "External":
            found[rid] = ("url", target)
        else:
            found[rid] = os.path.normpath(base + target).replace("\\", "/") \
                if not target.startswith("/") else target.lstrip("/")
    return found


def _docx_heading_styles(archive) -> dict:
    """{style id: heading level} from word/styles.xml. Style ids are in the
    language Word was in ("berschrift1" in German), their names are not
    ("heading 1"), and a style may also carry its outline level."""
    data = _zip_member(archive, "word/styles.xml", 8 * 1024 * 1024)
    if not data:
        return {}
    try:
        root = _parse_xml(data)
    except DocumentUnreadable:
        return {}
    levels = {}
    for style in root.iter(_q(_W, "style")):
        sid = style.get(_q(_W, "styleId"))
        name_el = style.find(_q(_W, "name"))
        name = (name_el.get(_q(_W, "val")) if name_el is not None else "") or ""
        level = None
        match = re.fullmatch(r"heading\s*(\d)", name.strip().lower())
        if match:
            level = int(match.group(1))
        elif name.strip().lower() == "title":
            level = 1
        outline = style.find(f"{_q(_W, 'pPr')}/{_q(_W, 'outlineLvl')}")
        if level is None and outline is not None:
            try:
                level = int(outline.get(_q(_W, "val"))) + 1
            except (TypeError, ValueError):
                level = None
        if sid and level and 1 <= level <= 6:
            levels[sid] = level
    return levels


def _docx_walk(parent, blocks: list, rels: dict, headings: dict, deadline: _Deadline) -> None:
    for child in parent:
        if deadline.expired():
            return
        tag = child.tag
        if tag == _q(_W, "p"):
            _docx_paragraph(child, blocks, rels, headings)
        elif tag == _q(_W, "tbl"):
            blocks.append(_Block("table", rows=_docx_table(child)))
        elif tag in (_q(_W, "sdt"), _q(_W, "customXml"), _q(_W, "sdtContent")):
            content = child.find(_q(_W, "sdtContent"))
            _docx_walk(content if content is not None else child, blocks, rels, headings,
                       deadline)


def _web_address(href: str) -> str:
    """``href`` when a click on it may go to the browser or the mail app,
    else ""."""
    return href if href.lower().startswith(("http://", "https://", "mailto:")) else ""


def _add_piece(pieces: list, words: str, href: str) -> None:
    """Add ``words`` to ``pieces``, joined to the last one when it has the
    same address."""
    if not words:
        return
    if pieces and pieces[-1][1] == href:
        pieces[-1] = (pieces[-1][0] + words, href)
    else:
        pieces.append((words, href))


def _docx_pieces(element, pieces: list, rels: Optional[dict], href: str = "") -> None:
    """The text in ``element``, in order, into ``pieces``: tabs and breaks
    kept, deleted text and field codes left out (they are other tags),
    the words of a web link with its address."""
    for node in element:
        tag = node.tag
        if tag == _q(_W, "t"):
            _add_piece(pieces, node.text or "", href)
        elif tag == _q(_W, "tab"):
            _add_piece(pieces, "\t", href)
        elif tag in (_q(_W, "br"), _q(_W, "cr")):
            _add_piece(pieces, "\n", href)
        elif tag == _q(_W, "hyperlink"):
            target = (rels or {}).get(node.get(_q(_R, "id")) or "")
            inner = _web_address(target[1]) if isinstance(target, tuple) else ""
            _docx_pieces(node, pieces, rels, inner or href)
        elif isinstance(tag, str):
            _docx_pieces(node, pieces, rels, href)


def _docx_text(element, pieces: Optional[list] = None, rels: Optional[dict] = None) -> str:
    """The text of a paragraph or run (see _docx_pieces); its pieces go
    into ``pieces`` when it is given."""
    found: list = []
    _docx_pieces(element, found, rels)
    if pieces is not None:
        pieces.extend(found)
    return "".join(words for words, _href in found)


def _docx_paragraph(paragraph, blocks: list, rels: dict, headings: dict) -> None:
    props = paragraph.find(_q(_W, "pPr"))
    level = 0
    listed = False
    if props is not None:
        style = props.find(_q(_W, "pStyle"))
        if style is not None:
            level = headings.get(style.get(_q(_W, "val")) or "", 0)
        outline = props.find(_q(_W, "outlineLvl"))
        if outline is not None and not level:
            try:
                level = min(6, int(outline.get(_q(_W, "val"))) + 1)
            except (TypeError, ValueError):
                level = 0
        listed = props.find(_q(_W, "numPr")) is not None
    pieces: list = []
    text = _docx_text(paragraph, pieces, rels)
    if text.strip():
        if level:
            blocks.append(_Block("h", text=text.strip(), level=level))
        else:
            blocks.append(_Block("li" if listed else "p", text=text, pieces=pieces))
    for blip in paragraph.iter(_q(_A, "blip"), _q(_V, "imagedata")):
        rid = blip.get(_q(_R, "embed")) or blip.get(_q(_R, "id"))
        target = rels.get(rid or "")
        if isinstance(target, str):
            blocks.append(_Block("img", picture=target))


def _docx_table(table) -> list:
    rows = []
    for row in table.iter(_q(_W, "tr")):
        cells = []
        for cell in row.findall(_q(_W, "tc")):
            texts = [_docx_text(p).strip() for p in cell.iter(_q(_W, "p"))]
            cells.append(" ".join(t for t in texts if t))
        rows.append(cells)
    return rows


def _odt_blocks(archive: zipfile.ZipFile, deadline: _Deadline) -> tuple[list, dict]:
    data = _zip_member(archive, "content.xml", XML_BYTES_LIMIT)
    if data is None:
        raise DocumentUnreadable(_("This file is not an OpenDocument text MyEditor can read."))
    root = _parse_xml(data)
    body = root.find(f"{_q(_OFFICE, 'body')}/{_q(_OFFICE, 'text')}")
    blocks: list = []
    if body is not None:
        _odt_walk(body, blocks, deadline, in_list=False)
    return blocks, {}


def _odt_pieces(element, pieces: list, href: str = "") -> None:
    """Text of an OpenDocument paragraph, in order, into ``pieces``: spans
    and links followed (a link's words with its address), ``text:s`` as
    its count of spaces, tabs and line breaks kept, notes' bodies and
    frames left out."""
    _add_piece(pieces, element.text or "", href)
    for child in element:
        tag = child.tag
        if tag == _q(_TEXT, "s"):
            try:
                count = max(1, min(int(child.get(_q(_TEXT, "c")) or 1), 1000))
            except ValueError:
                count = 1
            _add_piece(pieces, " " * count, href)
        elif tag == _q(_TEXT, "tab"):
            _add_piece(pieces, "\t", href)
        elif tag == _q(_TEXT, "line-break"):
            _add_piece(pieces, "\n", href)
        elif tag in (_q(_TEXT, "note"), _q(_DRAW, "frame")):
            pass
        elif isinstance(tag, str):
            inner = href
            if tag == _q(_TEXT, "a"):
                inner = _web_address(child.get(_q(_XLINK, "href")) or "") or href
            _odt_pieces(child, pieces, inner)
        _add_piece(pieces, child.tail or "", href)


def _odt_text(element, pieces: Optional[list] = None) -> str:
    """The text of an OpenDocument paragraph (see _odt_pieces); its pieces
    go into ``pieces`` when it is given."""
    found: list = []
    _odt_pieces(element, found)
    if pieces is not None:
        pieces.extend(found)
    return "".join(words for words, _href in found)


def _odt_pictures(element, blocks: list) -> None:
    for image in element.iter(_q(_DRAW, "image")):
        href = image.get(_q(_XLINK, "href")) or ""
        if href and "://" not in href:
            blocks.append(_Block("img", picture=href.lstrip("./")))


def _odt_walk(parent, blocks: list, deadline: _Deadline, *, in_list: bool) -> None:
    for child in parent:
        if deadline.expired():
            return
        tag = child.tag
        if tag == _q(_TEXT, "h"):
            try:
                level = int(child.get(_q(_TEXT, "outline-level")) or 1)
            except ValueError:
                level = 1
            text = _odt_text(child).strip()
            if text:
                blocks.append(_Block("h", text=text, level=max(1, min(level, 6))))
            _odt_pictures(child, blocks)
        elif tag == _q(_TEXT, "p"):
            pieces: list = []
            text = _odt_text(child, pieces)
            if text.strip():
                blocks.append(_Block("li" if in_list else "p", text=text, pieces=pieces))
            _odt_pictures(child, blocks)
        elif tag == _q(_TEXT, "list"):
            for item in child:
                _odt_walk(item, blocks, deadline, in_list=True)
        elif tag == _q(_TABLE, "table"):
            blocks.append(_Block("table", rows=_odt_table(child)))
        elif tag in (_q(_TEXT, "section"), _q(_TEXT, "list-item"), _q(_TEXT, "list-header")):
            _odt_walk(child, blocks, deadline, in_list=in_list)


def _odt_table(table) -> list:
    rows = []
    for row in table.iter(_q(_TABLE, "table-row")):
        cells = []
        for cell in row:
            if cell.tag != _q(_TABLE, "table-cell"):
                continue
            texts = [_odt_text(p).strip() for p in cell.iter(_q(_TEXT, "p"), _q(_TEXT, "h"))]
            text = " ".join(t for t in texts if t)
            try:
                repeat = int(cell.get(_q(_TABLE, "number-columns-repeated")) or 1)
            except ValueError:
                repeat = 1
            # A sheet-like table repeats its empty last cell thousands of times.
            cells.extend([text] * (min(repeat, 64) if text else min(repeat, 1)))
        while cells and not cells[-1]:
            cells.pop()
        rows.append(cells)
    while rows and not any(rows[-1]):
        rows.pop()
    return rows


def _read_blocks(path: str, deadline: _Deadline) -> tuple[list, zipfile.ZipFile]:
    """The blocks of the .docx or .odt at ``path`` and its open zip (the
    caller closes it), for pictures."""
    ext = os.path.splitext(path)[1].lower()
    try:
        archive = _open_zip(path)
    except ZipTooLarge as error:
        raise DocumentUnreadable(_("This document is too large for the reading view.")) \
            from error
    except (OSError, zipfile.BadZipFile, ValueError) as error:
        raise DocumentUnreadable(_("The document could not be opened.")) from error
    try:
        if ext == ".odt":
            blocks, _rels = _odt_blocks(archive, deadline)
        else:
            blocks, _rels = _docx_blocks(archive, deadline)
    except DocumentUnreadable:
        archive.close()
        raise
    except Exception as error:  # noqa: BLE001, any parser failure reads as one sentence
        archive.close()
        _log.warning("Could not read %s", path, exc_info=True)
        raise DocumentUnreadable(_("The document’s text could not be read.")) from error
    return blocks, archive


class _Pictures:
    """The pictures of one document, read capped and checked: only those
    ``image_safety`` would decode, each and all together within budget."""

    def __init__(self, archive: zipfile.ZipFile):
        self._archive = archive
        self._spent = 0

    def read(self, name: str) -> Optional[tuple[bytes, str]]:
        """(bytes, extension) of the picture part ``name``, or None."""
        room = min(PICTURE_BYTES_LIMIT, PICTURES_TOTAL_LIMIT - self._spent)
        if room <= 0:
            return None
        data = _zip_member(self._archive, name, room)
        if not data:
            return None
        mime = image_safety.sniff_image_mime(data)
        if mime not in image_safety.DECODABLE_MIMES:
            return None
        self._spent += len(data)
        return data, image_safety.sniff_image_ext(data) or ".png"


# --------------------------------------------------------------------------- #
# Blocks to HTML and to Markdown                                              #
# --------------------------------------------------------------------------- #

def _html_words(text: str) -> str:
    return html.escape(text).replace("\n", "<br>").replace("\t", "&emsp;")


def _html_text(text: str, pieces: Optional[list] = None) -> str:
    """A paragraph's HTML, made in one pass over its pieces: each link
    around exactly the words it covers (``text`` alone when there are no
    pieces)."""
    if not pieces:
        return _html_words(text)
    out = []
    for words, href in pieces:
        if href:
            out.append(f'<a href="{html.escape(href, quote=True)}">{_html_words(words)}</a>')
        else:
            out.append(_html_words(words))
    return "".join(out)


def _blocks_html(blocks: list, pictures: _Pictures, media_dir: Optional[str],
                 truncated: bool) -> str:
    out = []
    in_list = False
    picture_number = 0
    for block in blocks:
        if block.kind != "li" and in_list:
            out.append("</ul>")
            in_list = False
        if block.kind == "h":
            out.append(f"<h{block.level}>{html.escape(block.text)}</h{block.level}>")
        elif block.kind == "p":
            out.append(f"<p>{_html_text(block.text, block.pieces)}</p>")
        elif block.kind == "li":
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_html_text(block.text, block.pieces)}</li>")
        elif block.kind == "table" and block.rows:
            rows = []
            for index, row in enumerate(block.rows):
                cell = "th" if index == 0 else "td"
                rows.append("<tr>" + "".join(f"<{cell}>{html.escape(text)}</{cell}>"
                                             for text in row) + "</tr>")
            out.append('<table cellspacing="0" cellpadding="6">' + "".join(rows) + "</table>")
        elif block.kind == "img":
            found = pictures.read(block.picture)
            if found is None:
                continue
            data, ext = found
            picture_number += 1
            if media_dir:
                target = os.path.join(media_dir, f"picture-{picture_number}{ext}")
                with open(target, "wb") as stream:
                    stream.write(data)
                source = target
            else:
                mime = image_safety.sniff_image_mime(data)
                source = f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
            out.append(f'<p><img src="{html.escape(source, quote=True)}"></p>')
    if in_list:
        out.append("</ul>")
    if truncated:
        out.append("<p><i>{}</i></p>".format(html.escape(
            _("The rest of this document is not shown here. "
              "Open it in its app to read all of it."))))
    if not any(block.kind != "img" for block in blocks) and picture_number == 0:
        out.append("<p><i>{}</i></p>".format(html.escape(_("This document has no text."))))
    return "\n".join(out)


_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>|])")
_MD_LINE_START = re.compile(r"^(\s*)([#>+-]|\d+[.)])(?=\s)", re.MULTILINE)


def _md_text(text: str) -> str:
    text = _MD_SPECIAL.sub(r"\\\1", text.replace("\t", " "))
    text = _MD_LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text)
    # A line break inside a paragraph, the CommonMark way.
    return text.replace("\n", "\\\n")


def _md_destination(href: str) -> str:
    from markdown_writer import _destination
    return _destination(href)


def _blocks_markdown(blocks: list, pictures: _Pictures, media_dir: str,
                     media_name: str) -> str:
    out = []
    previous = ""
    used: set = set()
    for block in blocks:
        if block.kind == "h":
            out.append("#" * block.level + " " + _md_text(block.text.replace("\n", " ")))
        elif block.kind == "p":
            out.append(_md_text(block.text))
        elif block.kind == "li":
            line = "- " + _md_text(block.text)
            if previous == "li":
                out[-1] += "\n" + line
            else:
                out.append(line)
        elif block.kind == "table" and block.rows:
            width = max(len(row) for row in block.rows) or 1
            lines = []
            for index, row in enumerate(block.rows):
                cells = [_md_text(c).replace("\\\n", " ") for c in row] + [""] * (width - len(row))
                lines.append("| " + " | ".join(cells) + " |")
                if index == 0:
                    lines.append("|" + " --- |" * width)
            out.append("\n".join(lines))
        elif block.kind == "img":
            found = pictures.read(block.picture)
            if found is None:
                continue
            data, ext = found
            os.makedirs(media_dir, exist_ok=True)
            stem = re.sub(r"[^\w.-]+", "-", os.path.splitext(os.path.basename(block.picture))[0])
            name = free_path(os.path.join(media_dir, (stem or "picture") + ext), used)
            used.add(name)
            save_document(name, data)
            out.append("![]({})".format(_md_destination(
                media_name + "/" + os.path.basename(name))))
        previous = block.kind
    return "\n\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# pandoc                                                                      #
# --------------------------------------------------------------------------- #

def _pandoc_program() -> Optional[str]:
    """The pandoc executable this app would run, or None when there is none."""
    folder, _source = rmd_toolchain.find_pandoc()
    if not folder:
        return None
    program = os.path.join(folder, "pandoc.exe" if sys.platform == "win32" else "pandoc")
    return program if os.path.isfile(program) else None


# pandoc before 3.1.6 wrote what --extract-media extracts wherever the
# document's own names for its pictures pointed, parent folders included
# (CVE-2023-35936, CVE-2023-38745): a document could write files outside
# the folder it was given. Older versions are not asked to extract.
_SAFE_EXTRACT_VERSION = (3, 1, 6)
_VERSIONS: dict = {}


def _subprocess_options() -> dict:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _pandoc_version(program: str) -> Optional[tuple]:
    """The version of the pandoc at ``program`` as numbers, asked once per
    program (and again when the program file changes); None when it does
    not say."""
    try:
        key = (program, os.stat(program).st_mtime_ns)
    except OSError:
        return None
    if key in _VERSIONS:
        return _VERSIONS[key]
    version = None
    try:
        proc = subprocess.run([program, "--version"], capture_output=True, timeout=10,
                              env=rmd_toolchain.program_environment(),
                              stdin=subprocess.DEVNULL, **_subprocess_options())
        first = proc.stdout.decode("utf-8", "replace").splitlines()[:1]
        match = re.match(r"\s*pandoc(?:\.exe)?\s+(\d+(?:\.\d+)*)", first[0]) if first else None
        if proc.returncode == 0 and match:
            version = tuple(int(part) for part in match.group(1).split("."))
    except (OSError, subprocess.SubprocessError, ValueError):
        version = None
    _VERSIONS[key] = version
    return version


def _extracts_safely(program: str) -> bool:
    """Whether the pandoc at ``program`` keeps --extract-media inside the
    folder it is given (3.1.6 and later)."""
    version = _pandoc_version(program)
    return version is not None and version >= _SAFE_EXTRACT_VERSION


def _run_pandoc(arguments: list, *, timeout: float, cwd: Optional[str] = None) -> str:
    """pandoc's output for ``arguments``; raises OSError, TimeoutExpired or
    DocumentUnreadable."""
    program = _pandoc_program()
    if program is None:
        raise FileNotFoundError("pandoc")
    proc = subprocess.run([program, *arguments], capture_output=True, timeout=timeout,
                          env=rmd_toolchain.program_environment(), cwd=cwd,
                          stdin=subprocess.DEVNULL, **_subprocess_options())
    if proc.returncode != 0:
        _log.info("pandoc failed: %s", proc.stderr.decode("utf-8", "replace")[-2000:])
        raise DocumentUnreadable(_("pandoc could not read this document."))
    if len(proc.stdout) > HTML_CHARS_LIMIT:
        raise DocumentUnreadable(_("This document is too large for the reading view."))
    return proc.stdout.decode("utf-8", "replace")


# --------------------------------------------------------------------------- #
# Public                                                                      #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RenderedDocument:
    """A reading view's HTML and how it was made: ``with_pandoc`` when
    pandoc made it, ``pandoc_missing`` when the plain reading was used
    because pandoc is not on this computer (then the view offers to
    install it)."""

    html: str
    with_pandoc: bool
    pandoc_missing: bool


def render_document(path: str, *, media_dir: Optional[str] = None,
                    use_pandoc: bool = True) -> RenderedDocument:
    """The reading view of the .docx or .odt at ``path``. Pictures are
    written into ``media_dir`` (a folder of the caller's own, emptied by
    it) and named by their path there; without one they are carried as
    data: addresses. Raises DocumentUnreadable with a sentence to show."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in _PANDOC_FORMATS:
        raise DocumentUnreadable(_("MyEditor has no reading view for this kind of file."))
    program = _pandoc_program() if use_pandoc else None
    if program is not None and media_dir and not _extracts_safely(program):
        # Its pictures would go where the document says; the plain reading
        # writes them only into ``media_dir``.
        _log.info("pandoc is older than 3.1.6; reading %s plainly", path)
        program, use_pandoc = None, False
    if program is not None:
        arguments = ["--from", _PANDOC_FORMATS[ext], "--to", "html", "--wrap=none"]
        if media_dir:
            arguments += ["--extract-media", os.path.abspath(media_dir)]
        try:
            text = _run_pandoc(arguments + [os.path.abspath(path)], timeout=READING_SECONDS)
            return RenderedDocument(text, True, False)
        except (OSError, subprocess.SubprocessError, DocumentUnreadable):
            _log.info("pandoc gave no reading view of %s; reading it plainly", path,
                      exc_info=True)
    deadline = _Deadline(READING_SECONDS)
    blocks, archive = _read_blocks(path, deadline)
    with archive:
        text = _blocks_html(blocks, _Pictures(archive), media_dir, deadline.hit)
    return RenderedDocument(text, False, use_pandoc and program is None)


def document_html(path: str, *, media_dir: Optional[str] = None) -> str:
    """HTML for reading the .docx or .odt at ``path`` (see render_document)."""
    return render_document(path, media_dir=media_dir).html


def _claim_import_target(path: str) -> tuple[str, str]:
    """(new note path, its media folder): "<stem>.md" next to ``path``, or
    "<stem> 2.md" and on, where neither the note nor its folder exists;
    both are made here (an empty note, an empty folder), each in a step
    that fails when the name is taken, so two imports at the same moment
    never get the same names, and nothing of anyone else's is written
    over. ``_release_import_target`` takes them away again."""
    folder = os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    taken: set = set()
    while True:
        note = free_path(os.path.join(folder, stem + ".md"), taken)
        media = os.path.splitext(note)[0] + "_media"
        taken.add(note)
        if os.path.lexists(media):
            continue
        try:
            os.close(os.open(note, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666))
        except FileExistsError:
            continue                            # taken in the moment between
        try:
            os.mkdir(media)
        except FileExistsError:
            os.remove(note)
            continue
        return note, media


def _release_import_target(note: str, media: str, *, keep_note: bool) -> None:
    """After an import: its media folder goes when it holds no picture,
    and the note too when the import failed."""
    if not keep_note:
        _remove_quietly(note)
        shutil.rmtree(media, ignore_errors=True)
        return
    try:
        os.rmdir(media)                         # only when empty
    except OSError:
        pass


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _rewrite_pandoc_media(text: str, media_name: str) -> str:
    """pandoc names extracted pictures by the folder it was given; give
    every such address the app's own form (in <...> when it holds a space
    or a parenthesis, see markdown_writer._destination)."""
    if not any(ch in media_name for ch in " ()"):
        return text
    pattern = re.compile(r"\]\((" + re.escape(media_name) + r"/[^)\n]*?)\)")
    return pattern.sub(lambda m: "](" + _md_destination(m.group(1)) + ")", text)


def import_as_markdown(path: str, *, use_pandoc: bool = True) -> str:
    """Make a note of the .docx or .odt at ``path``: "<stem>.md" next to it
    (a free name, never replacing a file), its pictures in "<stem>_media".
    The original stays untouched. Returns the new note's path; raises
    DocumentUnreadable or OSError."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in _PANDOC_FORMATS:
        raise DocumentUnreadable(_("MyEditor cannot make a note of this kind of file."))
    note, media = _claim_import_target(path)
    try:
        text = _import_text(path, ext, note, media, use_pandoc)
        save_text_document(note, text)
    except BaseException:
        _release_import_target(note, media, keep_note=False)
        raise
    _release_import_target(note, media, keep_note=True)
    return note


def _import_text(path: str, ext: str, note: str, media: str, use_pandoc: bool) -> str:
    """The Markdown of the document at ``path`` for the note ``note``,
    its pictures written into ``media``."""
    media_name = os.path.basename(media)
    folder = os.path.dirname(note)
    text = None
    program = _pandoc_program() if use_pandoc else None
    if program is not None and not _extracts_safely(program):
        _log.info("pandoc is older than 3.1.6; importing %s plainly", path)
        program = None
    if program is not None:
        try:
            text = _run_pandoc(["--from", _PANDOC_FORMATS[ext], "--to", "gfm", "--wrap=none",
                                "--extract-media", media_name, os.path.abspath(path)],
                               timeout=IMPORT_SECONDS, cwd=folder)
            text = _rewrite_pandoc_media(text, media_name)
        except (OSError, subprocess.SubprocessError, DocumentUnreadable):
            _log.info("pandoc could not import %s; reading it plainly", path, exc_info=True)
            text = None
    if text is None:
        deadline = _Deadline(IMPORT_SECONDS)
        blocks, archive = _read_blocks(path, deadline)
        with archive:
            text = _blocks_markdown(blocks, _Pictures(archive), media, media_name)
    return text
