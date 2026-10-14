# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files MyEditor uploads to media servers and presents in its library.

One table for the whole app, whatever the computer's own one says: the
platform's MIME table differs between systems (".m4a" is
"audio/mp4a-latm" on a Mac and unknown without a table), and the type a
file is uploaded with is what a reader goes by. Everything that names a
file's type reads this module: the Insert command's file filter, a
dropped or pasted file, the Media Library's upload and its Save, the
media servers' upload, and the preview.
"""

from __future__ import annotations

import os
from typing import Optional

import i18n
from i18n import _

AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
               ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/opus",
               ".wav": "audio/wav", ".flac": "audio/flac",
               ".aiff": "audio/aiff", ".aif": "audio/aiff"}
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
               ".m4v": "video/mp4", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
               ".bmp": "image/bmp", ".heic": "image/heic", ".heif": "image/heif",
               ".avif": "image/avif"}
DOCUMENT_TYPES = {
    ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/markdown",
    ".markdown": "text/markdown", ".log": "text/plain", ".csv": "text/csv",
    ".tsv": "text/tab-separated-values", ".json": "application/json",
    ".xml": "application/xml", ".yaml": "application/yaml", ".yml": "application/yaml",
    ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".rtf": "application/rtf", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".pages": "application/vnd.apple.pages", ".numbers": "application/vnd.apple.numbers",
    ".key": "application/vnd.apple.keynote",
    ".zip": "application/zip", ".tar": "application/x-tar",
    ".gz": "application/gzip", ".bz2": "application/x-bzip2",
    ".xz": "application/x-xz", ".7z": "application/x-7z-compressed",
    ".rar": "application/vnd.rar", ".epub": "application/epub+zip",
}
TYPES = {**AUDIO_TYPES, **VIDEO_TYPES, **IMAGE_TYPES, **DOCUMENT_TYPES}
# What Insert > Audio or Video takes, and what the Media Library does.
AUDIO_VIDEO_EXTENSIONS = tuple({**AUDIO_TYPES, **VIDEO_TYPES})
EXTENSIONS = tuple(TYPES)

AUDIO = "audio"
VIDEO = "video"


def library_kind(mime: str) -> str:
    """The library's four categories, including files with unknown types."""
    mime = (mime or "").lower().split(";", 1)[0].strip()
    for kind in ("image", AUDIO, VIDEO):
        if mime.startswith(kind + "/"):
            return kind
    return "file"


def file_family(mime: str) -> str:
    """A presentation family, independent of language and platform tables."""
    mime = (mime or "").lower().split(";", 1)[0].strip()
    kind = library_kind(mime)
    if kind == "image":
        return "image"
    if kind == VIDEO:
        return "video"
    if kind == AUDIO:
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    if mime in ("text/csv", "text/tab-separated-values"):
        return "spreadsheet"
    if "spreadsheet" in mime or mime in ("application/vnd.ms-excel", "application/vnd.apple.numbers"):
        return "spreadsheet"
    if "presentation" in mime or mime in ("application/vnd.ms-powerpoint", "application/vnd.apple.keynote"):
        return "presentation"
    if mime in ("application/msword", "application/rtf") or "wordprocessing" in mime \
            or mime in ("application/vnd.oasis.opendocument.text", "application/vnd.apple.pages"):
        return "document"
    if mime in ("application/zip", "application/x-tar", "application/gzip",
                "application/x-bzip2", "application/x-xz", "application/vnd.rar",
                "application/x-7z-compressed"):
        return "archive"
    if mime == "application/epub+zip":
        return "book"
    if is_text(mime):
        return "text"
    return "file"


def file_type_label(mime: str) -> str:
    """A human-readable type, shared by tile captions and Quick Look."""
    labels = {"image": _("Image"), "video": _("Video"), "audio": _("Audio"),
              "pdf": _("PDF Document"), "spreadsheet": _("Spreadsheet"),
              "presentation": _("Presentation"), "document": _("Text Document"),
              "archive": _("Archive"), "book": _("EPUB Book"), "text": _("Text File")}
    return labels.get(file_family(mime), _("File"))


def file_glyph(mime: str) -> str:
    """Name in the app's vector glyph set, with a fallback for unknown files."""
    family = file_family(mime)
    return family if family in ("video", "audio", "spreadsheet", "presentation",
                                "archive", "book", "text") else "document"


def is_text(mime: str) -> bool:
    """Types safe to preview as plain text, never as executable HTML."""
    mime = (mime or "").lower().split(";", 1)[0].strip()
    return mime.startswith("text/") or mime in (
        "application/json", "application/xml", "application/yaml", "application/x-yaml")


def type_of(name: str) -> Optional[str]:
    """The type of a file named ``name`` (a path or an address), by its
    extension; None for a kind of file not in the table."""
    path = (name or "").split("?", 1)[0].split("#", 1)[0]
    return TYPES.get(os.path.splitext(path)[1].lower())


def is_audio_or_video(name: str) -> bool:
    return kind_of(type_of(name) or "") is not None


def kind_of(mime: str) -> Optional[str]:
    """AUDIO or VIDEO for a type, None for any other."""
    mime = (mime or "").lower()
    if mime.startswith("audio/"):
        return AUDIO
    if mime.startswith("video/"):
        return VIDEO
    return None


def extension_for(mime: str) -> str:
    """The extension readers know a type by (".mp3" for "audio/mpeg")."""
    return next((ext for ext, known in TYPES.items() if known == (mime or "").lower()), "")


def address_with_extension(url: str, mime: str) -> str:
    """``url`` with the extension of ``mime`` when its last part has none,
    so a reader can tell audio from video by it (as standup's
    embeddableMediaUrl does)."""
    tail = url.split("?", 1)[0].rsplit("/", 1)[-1]
    return url if "." in tail else url + extension_for(mime)


def link_words(mime: str, name: str) -> str:
    """The words of a link to an audio or video file, where readers show a
    player: "▶ Audio: name" or "▶ Video: name"."""
    if kind_of(mime) == VIDEO:
        return _("▶ Video: {name}").format(name=name)
    return _("▶ Audio: {name}").format(name=name)


def format_size(byte_count: int) -> str:
    """A file's size the way a person reads it: 117 KB, 1.2 MB, with the
    decimal point the app's language writes (1,2 MB in German)."""
    if byte_count >= 1024 * 1024:
        text = f"{byte_count / (1024 * 1024):.1f} MB"
        return text.replace(".", i18n.display_locale().decimalPoint())
    if byte_count >= 1024:
        return f"{round(byte_count / 1024)} KB"
    return f"{byte_count} B"
