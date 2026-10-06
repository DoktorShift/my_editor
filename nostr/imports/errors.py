# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stable error codes + friendly copy for the importer.

Every failure that can reach the user travels as a :class:`SourceError`
carrying a machine-readable ``code`` from :data:`ERROR_CODES` plus a raw
message. The UI renders :func:`friendly_message`; the raw message stays
available for diagnostics. Resolvers added later contribute their codes
here so the whole importer shares one taxonomy.
"""

from __future__ import annotations

from typing import Optional

from i18n import _


class ERROR_CODES:
    """Namespace of stable error-code strings. Not an enum on purpose:
    codes cross signal boundaries and settle files as plain strings."""

    # Transport layer.
    FETCH_ERROR = "FETCH_ERROR"
    TOO_LARGE = "TOO_LARGE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    # The address, or a redirect, points into the local network.
    LOCAL_NETWORK = "LOCAL_NETWORK"

    # Resolution / format layer.
    NOT_A_FEED = "NOT_A_FEED"
    NO_FEED_FOUND = "NO_FEED_FOUND"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    # A resolver-specific failure whose message is already user-ready
    # (e.g. "GitHub: API rate limit exceeded"); shown verbatim.
    SOURCE_ERROR = "SOURCE_ERROR"

    # Nostr source resolvers.
    NOSTR_INVALID = "NOSTR_INVALID"
    NOSTR_NOT_FOUND = "NOSTR_NOT_FOUND"
    NIP05_NOT_FOUND = "NIP05_NOT_FOUND"
    NO_RELAY_ACCESS = "NO_RELAY_ACCESS"

    # Platform exports.
    WXR_EMPTY = "WXR_EMPTY"
    GHOST_EMPTY = "GHOST_EMPTY"
    ARCHIVE_UNREADABLE = "ARCHIVE_UNREADABLE"

    # Web source resolvers.
    SITEMAP_EMPTY = "SITEMAP_EMPTY"
    BLUESKY_INVALID = "BLUESKY_INVALID"
    BLUESKY_NOT_FOUND = "BLUESKY_NOT_FOUND"
    BLUESKY_NOT_THREAD = "BLUESKY_NOT_THREAD"

    UNKNOWN = "UNKNOWN"


class SourceError(Exception):
    """A failure with a stable code and a raw (diagnostic) message."""

    def __init__(self, message: str, code: str = ERROR_CODES.UNKNOWN) -> None:
        super().__init__(message)
        self.code = code

    @property
    def message(self) -> str:
        return str(self.args[0]) if self.args else ""


# Friendly copy per code. Wording is part of the UX: calm, actionable,
# and (per the platform writing guidelines) never "we", never blame,
# always a next step where one exists. Codes absent here fall back to
# the error's own message.
# The owner's decision Q-6: such an address is not followed, and the
# message says why in plain words.
LOCAL_NETWORK_MESSAGE = _(
    "This address is on this computer or its local network. MyEditor follows "
    "public websites only.")

_FRIENDLY = {
    ERROR_CODES.LOCAL_NETWORK: LOCAL_NETWORK_MESSAGE,
    ERROR_CODES.NO_FEED_FOUND: _(
        "No feed found at this site. "
        "Try pasting the feed URL directly, "
        "or look for an RSS / Atom link in the page footer."
    ),
    ERROR_CODES.NOT_A_FEED: _(
        "That response isn't a feed. Expected RSS, Atom, or JSON Feed."
    ),
    ERROR_CODES.UNSUPPORTED_SOURCE: _(
        "This kind of source can't be imported yet."
    ),
    ERROR_CODES.EMPTY_RESPONSE: _(
        "The server returned an empty response."
    ),
    ERROR_CODES.NOSTR_INVALID: _(
        "That isn't a recognisable Nostr address."
    ),
    ERROR_CODES.NOSTR_NOT_FOUND: _(
        "That Nostr event was not found on the relays."
    ),
    ERROR_CODES.NIP05_NOT_FOUND: _(
        "No Nostr profile is published at that address."
    ),
    ERROR_CODES.NO_RELAY_ACCESS: _(
        "Importing from Nostr needs a relay connection, which isn't "
        "available right now."
    ),
    ERROR_CODES.WXR_EMPTY: _(
        "No published posts or pages were found in that WordPress export."
    ),
    ERROR_CODES.GHOST_EMPTY: _(
        "No published posts were found in that Ghost export."
    ),
    ERROR_CODES.ARCHIVE_UNREADABLE: _(
        "Couldn't read that archive. Export it again from the "
        "platform and retry."
    ),
    ERROR_CODES.SITEMAP_EMPTY: _(
        "That sitemap had no importable article URLs."
    ),
    ERROR_CODES.BLUESKY_INVALID: _(
        "That is not a Bluesky post link."
    ),
    ERROR_CODES.BLUESKY_NOT_FOUND: _(
        "That Bluesky post could not be found."
    ),
    ERROR_CODES.BLUESKY_NOT_THREAD: _(
        "That is a single Bluesky post, not a thread. Only multi-post "
        "threads stitch into an article."
    ),
}


def friendly_message(error: SourceError, *, detail: Optional[str] = None) -> str:
    """Headline copy for ``error``, suitable to show a user directly.

    ``FETCH_ERROR`` embeds the transport reason (Qt's errorString is
    already human-readable); mapped codes use their fixed copy; anything
    else falls back to the error's own message so nothing is swallowed.
    """
    if error.code == ERROR_CODES.FETCH_ERROR:
        reason = (detail or error.message or _("network error")).rstrip(".")
        return _("Couldn't reach that URL: {reason}.").format(reason=reason)
    mapped = _FRIENDLY.get(error.code)
    if mapped:
        return mapped
    return error.message or _("Something went wrong while importing.")
