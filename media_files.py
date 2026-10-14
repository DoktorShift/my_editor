# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The files MyEditor uploads to media servers, and their types: audio
and video it puts into a text, pictures, and PDF files in the Media
Library.

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
               ".wav": "audio/wav", ".flac": "audio/flac"}
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
               ".m4v": "video/mp4"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
               ".bmp": "image/bmp", ".heic": "image/heic", ".heif": "image/heif",
               ".avif": "image/avif"}
DOCUMENT_TYPES = {".pdf": "application/pdf"}
TYPES = {**AUDIO_TYPES, **VIDEO_TYPES, **IMAGE_TYPES, **DOCUMENT_TYPES}
# What Insert > Audio or Video takes, and what the Media Library does.
AUDIO_VIDEO_EXTENSIONS = tuple({**AUDIO_TYPES, **VIDEO_TYPES})
EXTENSIONS = tuple(TYPES)

AUDIO = "audio"
VIDEO = "video"


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
