# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the Blossom error taxonomy and its user-facing copy.

Codes travel across signal boundaries and settle into files, so they
must stay plain strings equal to their own names. The copy is part of
the product: sentence case, no "we", no em-dash, no Qt transport string
leaking through.
"""

from __future__ import annotations

import re

from nostr.blossom.errors import ERROR_CODES, friendly_message


ALL_CODES = [
    value for name, value in vars(ERROR_CODES).items()
    if not name.startswith("_") and isinstance(value, str)
]


def test_every_code_is_its_own_name():
    for name, value in vars(ERROR_CODES).items():
        if name.startswith("_") or not isinstance(value, str):
            continue
        assert value == name


def test_every_code_has_friendly_copy():
    assert len(ALL_CODES) == 9
    for code in ALL_CODES:
        message = friendly_message(code)
        assert message
        assert message[0].isupper()
        assert message.endswith(".")


def test_unknown_code_still_says_something():
    assert friendly_message("NO_SUCH_CODE")
    assert friendly_message("")


def test_copy_never_uses_we_or_an_em_dash():
    for code in ALL_CODES + ["NO_SUCH_CODE"]:
        message = friendly_message(code)
        # Escaped rather than literal so the banned character appears
        # nowhere in the tree, including in the test that forbids it.
        assert "\u2014" not in message
        assert not re.search(r"\bwe\b", message, re.IGNORECASE)


def test_raw_transport_text_is_never_echoed():
    message = friendly_message(
        ERROR_CODES.UPLOAD_FAILED,
        "Error transferring https://x.example - server replied: 502",
    )
    assert "502" not in message
    assert message == friendly_message(ERROR_CODES.UPLOAD_FAILED)
