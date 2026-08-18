# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stable error codes + friendly copy for Blossom.

Same shape as ``nostr.imports.errors``: a namespace of plain strings
(codes cross signal boundaries and settle into files, so not an enum),
a copy map, and one :func:`friendly_message`. Qt transport strings and
protocol jargon never reach the user; a code does, and the code picks
the sentence.

Codes are additive. Later work extends this module, never replaces it.
"""

from __future__ import annotations


class ERROR_CODES:
    """Namespace of stable error-code strings."""

    # Transport boundary.
    REDIRECT_REFUSED = "REDIRECT_REFUSED"
    UNSAFE_URL = "UNSAFE_URL"
    TOO_LARGE = "TOO_LARGE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    HOST_MISMATCH = "HOST_MISMATCH"

    # Upload lifecycle.
    SIGNER_REJECTED = "SIGNER_REJECTED"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    SIGNER_IDENTITY_MISMATCH = "SIGNER_IDENTITY_MISMATCH"


# Friendly copy per code. Sentence case, no "we", no blame, says what
# happened and what to do next. An image never disappears from a
# document because of any of these, and the copy says so where it is
# the user's first question.
_FRIENDLY = {
    ERROR_CODES.REDIRECT_REFUSED: (
        "The server redirected this request. For safety it was not sent "
        "again. Try a different server."
    ),
    ERROR_CODES.UNSAFE_URL: (
        "That address is not allowed. Only regular web links can be used "
        "here."
    ),
    ERROR_CODES.TOO_LARGE: (
        "The transfer was larger than allowed and was stopped."
    ),
    ERROR_CODES.UNSUPPORTED_FORMAT: (
        "That file format cannot be shown here. The file itself is "
        "unaffected."
    ),
    ERROR_CODES.HOST_MISMATCH: (
        "The signed authorization does not match this server. Nothing was "
        "sent."
    ),
    ERROR_CODES.SIGNER_REJECTED: (
        "The signer declined the request. Approve it in the signer app and "
        "try again."
    ),
    ERROR_CODES.UPLOAD_FAILED: (
        "The upload did not finish. The image stays in your document and "
        "can be retried."
    ),
    ERROR_CODES.NETWORK_UNAVAILABLE: (
        "The server could not be reached. The image stays in your document "
        "and can be uploaded later."
    ),
    ERROR_CODES.SIGNER_IDENTITY_MISMATCH: (
        "The signer answered for a different identity. Nothing was sent."
    ),
}

_FALLBACK = "Something went wrong with that media request. Nothing was lost."


def friendly_message(code: str, raw: str = "") -> str:
    """Copy for ``code``, suitable to show a user directly.

    ``raw`` is the diagnostic string the transport produced. It is
    accepted so callers can pass it without branching, and deliberately
    not rendered: Qt error strings are not user copy.
    """
    return _FRIENDLY.get(code) or _FALLBACK
