# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What kind of file a file in the folder sidebar is, and how it is named
and drawn there.

Every file falls in one of a few kinds the sidebar sorts, counts and
shows by: notes MyEditor writes, plain text it opens, PDFs, pictures,
films, sound, spreadsheets, documents, presentations, and anything
else. The kind follows the file's extension, read from the one table
the app already keeps (media_files.py), so a type is never named two
ways in two places.

Also here: which names the sidebar never shows (dotfiles, Finder and
Explorer leftovers, the lock files office apps leave next to an open
document), which lock file says another app has a file open right now,
and which folders are macOS document packages (a .numbers or a .pages
that is a folder on disk but one file to a person).
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtGui import QColor

import media_files
from i18n import _

NOTE = "note"
TEXT = "text"
PDF = "pdf"
IMAGE = "image"
VIDEO = "video"
AUDIO = "audio"
SHEET = "sheet"
DOCUMENT = "document"
SLIDES = "slides"
OTHER = "other"
# A folder's Entry carries this kind (folder_library.py); it is not one of
# the file kinds below.
FOLDER = "folder"

KINDS = (NOTE, TEXT, PDF, IMAGE, VIDEO, AUDIO, SHEET, DOCUMENT, SLIDES, OTHER)

NOTE_EXTS = frozenset({".md", ".markdown", ".rmd"})
SHEET_EXTS = frozenset({".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv", ".numbers"})
DOCUMENT_EXTS = frozenset({".docx", ".doc", ".odt", ".rtf", ".pages"})
SLIDES_EXTS = frozenset({".pptx", ".ppt", ".odp", ".key"})
# Plain text MyEditor opens as text: every type the shared table calls
# text, apart from notes and the tables a spreadsheet view shows.
TEXT_EXTS = frozenset(
    ext for ext, mime in media_files.DOCUMENT_TYPES.items()
    if media_files.is_text(mime)) - NOTE_EXTS - SHEET_EXTS

# The spreadsheets MyEditor edits itself (sheet_io.py); the others in
# SHEET_EXTS are shown, and opened in their own app.
EDITABLE_SHEET_EXTS = frozenset({".xlsx", ".xlsm", ".ods", ".csv", ".tsv"})
# Documents shown as a reading view (office_preview.document_html).
READING_VIEW_EXTS = frozenset({".docx", ".odt"})

# macOS document packages: folders on disk that the Finder shows, opens
# and copies as one file. They are listed as files and never entered.
PACKAGE_EXTS = frozenset({
    ".numbers", ".pages", ".key", ".rtfd", ".app", ".bundle", ".framework",
    ".plugin", ".kext", ".appex", ".photoslibrary", ".musiclibrary",
    ".photolibrary", ".aplibrary", ".imovielibrary", ".fcpbundle",
    ".tvlibrary", ".theater", ".band", ".logicx", ".sparsebundle",
    ".xcodeproj", ".xcworkspace", ".playground", ".scptd", ".mpkg", ".pkg",
    ".lpdf", ".epub",
})

_BY_EXT = {
    **{ext: NOTE for ext in NOTE_EXTS},
    **{ext: TEXT for ext in TEXT_EXTS},
    ".pdf": PDF,
    **{ext: IMAGE for ext in media_files.IMAGE_TYPES},
    **{ext: VIDEO for ext in media_files.VIDEO_TYPES},
    **{ext: AUDIO for ext in media_files.AUDIO_TYPES},
    **{ext: SHEET for ext in SHEET_EXTS},
    **{ext: DOCUMENT for ext in DOCUMENT_EXTS},
    **{ext: SLIDES for ext in SLIDES_EXTS},
}

# The colour each kind's icon is tinted with in the file list, as in the
# approved design: grey for text, the colours people know the office
# formats by, the system colours for pictures, films and sound.
_KIND_TINTS = {
    NOTE: "#8e8e93", TEXT: "#8e8e93", PDF: "#ff3b30", IMAGE: "#34c759",
    VIDEO: "#af52de", AUDIO: "#ff2d55", SHEET: "#1d8a4e", DOCUMENT: "#2b5797",
    SLIDES: "#d24726", OTHER: "#8e8e93", FOLDER: "#1e88e5",
}
_FORMAT_TINTS = {
    ".ods": "#2e9d48", ".numbers": "#30a14e", ".odt": "#1e6fd9", ".pages": "#ff9500",
    ".odp": "#e0592a", ".key": "#0a84ff",
}

# Names in the app's glyph set (format_icons._GLYPHS), the one table the
# file list and a viewer's bar both draw by. A note is a page with lines
# of text ("note" there is the speech bubble of a Nostr note). Folders
# have no glyph there: the file list draws the system's folder icon for
# them.
_KIND_ICONS = {
    NOTE: "article", TEXT: "text", PDF: "pages", IMAGE: "photo", VIDEO: "video",
    AUDIO: "audio", SHEET: "spreadsheet", DOCUMENT: "document", SLIDES: "presentation",
    OTHER: "document", FOLDER: "folder",
}

# Files the system writes for itself, never shown.
_SYSTEM_NAMES = frozenset({"thumbs.db", "desktop.ini", "ehthumbs.db", "$recycle.bin",
                           "system volume information"})


def extension(path: str) -> str:
    """The lower-case extension of ``path``, with its dot ("" for none)."""
    return os.path.splitext(os.path.basename(os.fspath(path).rstrip("/\\")))[1].lower()


def kind_of_path(path: str) -> str:
    """One of KINDS for a file, by its extension."""
    return _BY_EXT.get(extension(path), OTHER)


def format_label(path: str) -> str:
    """The name of a file's format as a person knows it: "Excel
    Spreadsheet", "OpenDocument Text", "PNG Image"."""
    ext = extension(path)
    labels = _format_labels()
    if ext in labels:
        return labels[ext]
    kind = kind_of_path(path)
    short = ext[1:].upper()
    if kind == NOTE:
        return _("Note")
    if kind == TEXT:
        return _("Text File")
    if kind == PDF:
        return _("PDF Document")
    if kind == IMAGE:
        return _("{format} Image").format(format=short)
    if kind == VIDEO:
        return _("{format} Video").format(format=short)
    if kind == AUDIO:
        return _("{format} Audio").format(format=short)
    family = media_files.file_family(media_files.type_of(path) or "")
    if family == "archive":
        return _("{format} Archive").format(format=short)
    if family == "book":
        return _("EPUB Book")
    if short and short.isascii() and short.isalnum() and len(short) <= 8:
        return _("{format} File").format(format=short)
    return _("File")


def _format_labels() -> dict:
    """Each named format's label, looked up when asked (the language can
    change while the app runs)."""
    return {
        ".rmd": _("R Markdown Note"),
        ".txt": _("Plain Text"), ".text": _("Plain Text"),
        ".html": _("HTML Document"), ".htm": _("HTML Document"),
        ".xlsx": _("Excel Spreadsheet"), ".xlsm": _("Excel Spreadsheet"),
        ".xls": _("Excel Spreadsheet (Old Format)"),
        ".ods": _("OpenDocument Spreadsheet"), ".numbers": _("Numbers Spreadsheet"),
        ".csv": _("CSV Spreadsheet"), ".tsv": _("TSV Spreadsheet"),
        ".docx": _("Word Document"), ".doc": _("Word Document (Old Format)"),
        ".odt": _("OpenDocument Text"), ".rtf": _("RTF Document"),
        ".pages": _("Pages Document"),
        ".pptx": _("PowerPoint Presentation"),
        ".ppt": _("PowerPoint Presentation (Old Format)"),
        ".odp": _("OpenDocument Presentation"), ".key": _("Keynote Presentation"),
    }


def kind_tint(kind: str) -> QColor:
    """The colour a kind's icon is drawn in."""
    return QColor(_KIND_TINTS.get(kind, _KIND_TINTS[OTHER]))


def format_tint(path: str) -> QColor:
    """``kind_tint`` of the file's kind, or its format's own colour where
    the format has one (Pages orange, Keynote blue)."""
    tint = _FORMAT_TINTS.get(extension(path))
    return QColor(tint) if tint else kind_tint(kind_of_path(path))


def kind_icon_name(kind: str, path: str = "") -> str:
    """The glyph a kind is drawn with (format_icons.paint); "folder" for
    folders, which take the system's own folder icon. Given the file's
    ``path``, an archive among the other files is drawn as one."""
    if kind == OTHER and path and media_files.file_glyph(media_files.type_of(path) or "") == "archive":
        return "archive"
    return _KIND_ICONS.get(kind, _KIND_ICONS[OTHER])


def is_hidden_name(name: str) -> bool:
    """Whether a file or folder named ``name`` stays out of the sidebar:
    dotfiles (LibreOffice's ``.~lock.*#`` among them), the ``~$`` owner
    files Word and Excel keep next to an open document, and what Windows
    and the Finder write for themselves (Thumbs.db, desktop.ini, the
    ``Icon\\r`` of a folder with its own icon)."""
    if not name:
        return True
    if name.startswith((".", "~$")):
        return True
    if name == "Icon\r":
        return True
    return name.lower() in _SYSTEM_NAMES


def lock_owner_file(path: str) -> Optional[str]:
    """The lock file another app keeps next to ``path`` while it has the
    file open, or None.

    LibreOffice writes ``.~lock.<name>#``. Excel writes ``~$<name>``;
    Word and PowerPoint write the same, except that for a name of seven
    characters before the extension the first one is replaced, and for a
    longer one the first two, by the ``~$`` (Microsoft's documented
    owner file rule). A crash can leave one behind, so this is a hint to
    show, never a reason to refuse to open the file."""
    folder, name = os.path.split(os.path.abspath(os.fspath(path)))
    if not name:
        return None
    candidates = [f".~lock.{name}#", "~$" + name]
    stem_length = len(os.path.splitext(name)[0])
    if stem_length == 7:
        candidates.append("~$" + name[1:])
    elif stem_length >= 8:
        candidates.append("~$" + name[2:])
    for candidate in candidates:
        lock = os.path.join(folder, candidate)
        if os.path.isfile(lock):
            return lock
    return None


def is_package(path: str) -> bool:
    """Whether ``path`` is a folder the Finder treats as one file."""
    return extension(path) in PACKAGE_EXTS and os.path.isdir(path)


# A package's size is counted over at most this many of its entries.
PACKAGE_WALK_LIMIT = 5_000


def package_size(path: str, limit: int = PACKAGE_WALK_LIMIT) -> int:
    """The bytes of the files in the package folder ``path``, counted over
    at most ``limit`` entries, folders as well as files: a package of
    nothing but folders costs no more than one of files. Links are not
    followed. The walk reads the disk; callers on the GUI thread should
    not ask for it per row."""
    total = seen = 0
    pending = [path]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as found:
                for item in found:
                    seen += 1
                    if seen > limit:
                        return total
                    try:
                        if item.is_dir(follow_symlinks=False):
                            pending.append(item.path)
                        elif item.is_file(follow_symlinks=False):
                            total += item.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total
