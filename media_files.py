# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The audio and video files MyEditor puts into a text, and their types.

One table, whatever the computer's own one says: the platform's MIME
table differs between systems (".m4a" is "audio/mp4a-latm" on a Mac and
unknown without a table), and the type a file is uploaded with is what a
reader goes by. Everything about audio and video in the app reads this
module: the Insert command's file filter, a dropped or pasted file, the
media servers' upload, and the preview.
"""

from __future__ import annotations

import os
from typing import Optional

from i18n import _

AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
               ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/opus",
               ".wav": "audio/wav", ".flac": "audio/flac"}
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
               ".m4v": "video/mp4"}
TYPES = {**AUDIO_TYPES, **VIDEO_TYPES}
EXTENSIONS = tuple(TYPES)

AUDIO = "audio"
VIDEO = "video"


def type_of(name: str) -> Optional[str]:
    """The type of an audio or video file named ``name`` (a path or an
    address), by its extension; None for any other file."""
    path = (name or "").split("?", 1)[0].split("#", 1)[0]
    return TYPES.get(os.path.splitext(path)[1].lower())


def is_audio_or_video(name: str) -> bool:
    return type_of(name) is not None


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
