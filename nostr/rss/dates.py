# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A post's date as Unix seconds, however its source writes it."""

from __future__ import annotations

from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional


def to_unix(value: Any) -> Optional[int]:
    """Unix seconds for a date as sources write one: a ``datetime`` or
    ``date`` (a parsed front matter), ISO 8601 (with ``Z``, or a space
    before the time as WordPress writes it), RFC 822 (RSS, e-mail), or a
    text that starts with ``YYYY-MM-DD``. A time without a zone is UTC.
    None for anything else, and for a time not after 1970 (WordPress's
    ``0000-00-00`` rows)."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime(value.year, value.month, value.day)
    elif isinstance(value, str) and value.strip():
        moment = _read(value.strip())
        if moment is None:
            return None
    else:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        seconds = int(moment.timestamp())
    except (OverflowError, OSError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _read(text: str) -> Optional[datetime]:
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"  # fromisoformat reads "Z" only since 3.11
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return None
