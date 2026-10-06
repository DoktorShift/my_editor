# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The words around joining, and the checks that run before anything is signed.

Alerts (:func:`humanize`), inline field messages, and the input checks
that spare the user a signer prompt for a request certain to be refused.
The person reading these is joining a club, not debugging a client, so
the copy says what happened and what to do next, and nothing else.

Pure: no network.
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional, Tuple

from i18n import _, ngettext

from .models import ApiError, ErrorCode
from .prices import format_number


# --------------------------------------------------------------------------- #
# Limits                                                                       #
# --------------------------------------------------------------------------- #

# The spec caps these. Checked locally so a request that is certain to be
# refused does not cost the user a signer prompt first.
APPLICATION_TEXT_MAX_LENGTH: int = 2000
EMAIL_MAX_LENGTH: int = 255
NIP05_HANDLE_MAX_LENGTH: int = 255


# --------------------------------------------------------------------------- #
# Checking input before anything is signed                                     #
# --------------------------------------------------------------------------- #

_HANDLE_ALLOWED = re.compile(r"\A[a-z0-9_-]+\Z")
_EMAIL_SHAPE = re.compile(r"\A[^@\s]+@[^@\s]+\Z")

_HANDLE_CHARACTERS = _(
    "Use only lowercase letters, numbers, hyphens (-) and underscores (_)."
)


def nip05_handle_problem(handle: str) -> Optional[str]:
    """What is wrong with a requested name, in plain words, or None.

    The rule is the server's: lowercase letters, digits, hyphen and
    underscore, at most 255 characters, because the name becomes part
    of a public address. Whether it is still free only the server knows;
    :func:`handle_field_message` words that answer. An empty field means
    "no name" and should not be passed here.
    """
    value = handle if isinstance(handle, str) else ""
    if not value:
        return _("Enter a name.")
    if len(value) > NIP05_HANDLE_MAX_LENGTH:
        return _("Use {count} characters or fewer.").format(count=NIP05_HANDLE_MAX_LENGTH)
    if _HANDLE_ALLOWED.match(value):
        return None
    if "@" in value:
        return _("Enter only the part before the @ sign.")
    if any(ch.isspace() for ch in value):
        return _("Spaces are not allowed.") + " " + _HANDLE_CHARACTERS
    if _HANDLE_ALLOWED.match(value.lower()):
        return _("Use lowercase letters only.")
    return _HANDLE_CHARACTERS


def email_problem(address: str) -> Optional[str]:
    """A plain-words reason an e-mail address will be refused, or None."""
    value = address if isinstance(address, str) else ""
    if len(value) > EMAIL_MAX_LENGTH:
        return _("Use an address of {count} characters or fewer.").format(
            count=EMAIL_MAX_LENGTH)
    if not _EMAIL_SHAPE.match(value):
        return _("Enter a valid email address.")
    return None


def _message_too_long() -> str:
    return _("Keep your message to {count} characters or fewer.").format(
        count=format_number(APPLICATION_TEXT_MAX_LENGTH))


def application_text_problem(text: str) -> Optional[str]:
    """A plain-words reason the application message will be refused, or None."""
    if isinstance(text, str) and len(text) > APPLICATION_TEXT_MAX_LENGTH:
        return _message_too_long()
    return None


# --------------------------------------------------------------------------- #
# Plain-language copy                                                          #
# --------------------------------------------------------------------------- #

def _wait_advice(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return _("Wait a few minutes, then try again.")
    if seconds < 60:
        return ngettext("Wait {count} second, then try again.",
                        "Wait {count} seconds, then try again.", seconds).format(count=seconds)
    minutes = math.ceil(seconds / 60)
    if minutes < 60:
        return ngettext("Wait about {count} minute, then try again.",
                        "Wait about {count} minutes, then try again.",
                        minutes).format(count=minutes)
    hours = math.ceil(minutes / 60)
    if hours < 24:
        return ngettext("Try again in about {count} hour.",
                        "Try again in about {count} hours.", hours).format(count=hours)
    return _("Try again tomorrow.")


_COPY: Dict[str, Tuple[str, str]] = {
    ErrorCode.UNAVAILABLE: (
        _("Joining in the app isn't available right now"),
        _("You can join on the EINUNDZWANZIG website instead."),
    ),
    ErrorCode.OFFLINE: (
        _("Cannot reach EINUNDZWANZIG"),
        _("Check your internet connection and try again."),
    ),
    ErrorCode.TIMEOUT: (
        _("EINUNDZWANZIG is taking too long to respond"),
        _("Check your internet connection and try again in a moment."),
    ),
    ErrorCode.UNAUTHORIZED: (
        _("EINUNDZWANZIG could not confirm it is you"),
        _("Make sure the date and time on this computer are set "
          "automatically, then try again. If it keeps happening, check for "
          "a MyEditor update."),
    ),
    ErrorCode.VALIDATION: (
        _("Some details need another look"),
        _("Check the highlighted fields and try again."),
    ),
    ErrorCode.NOT_FOUND: (
        _("No application on file yet"),
        _("EINUNDZWANZIG has nothing on file for this account yet. Send your "
          "membership application first. If you already did, reload and "
          "try again."),
    ),
    ErrorCode.CONFLICT: (
        _("Your membership changed in the meantime"),
        _("Reload to see where things stand, then try again."),
    ),
    ErrorCode.SERVER: (
        _("EINUNDZWANZIG is having trouble right now"),
        _("Try again in a few minutes."),
    ),
    ErrorCode.SIGNER_DECLINED: (
        _("The request was not approved"),
        _("Your signer app did not approve it. Try again, and approve the "
          "request when your signer app asks."),
    ),
    ErrorCode.SIGNER_UNREACHABLE: (
        _("Your signer did not answer"),
        _("Open your signer app, make sure it is running, and try again."),
    ),
    ErrorCode.BAD_RESPONSE: (
        _("Something went wrong"),
        _("MyEditor could not understand the answer from EINUNDZWANZIG. Try "
          "again later. If it keeps happening, check for a MyEditor update."),
    ),
}


def humanize(error: ApiError) -> Tuple[str, str]:
    """``(title, message)`` for an alert about ``error``.

    The title says what happened, the message says what to do next. No
    protocol words, status numbers or internals: the person reading it
    is joining a club, not debugging a client.
    """
    code = getattr(error, "code", "")
    if code == ErrorCode.RATE_LIMITED:
        return (_("Too many attempts"), _wait_advice(getattr(error, "retry_after", None)))
    if code == ErrorCode.VALIDATION and not getattr(error, "field_errors", None):
        return (_COPY[code][0], _("Check your details and try again."))
    return _COPY.get(code, _COPY[ErrorCode.BAD_RESPONSE])


def _field_reasons(error: ApiError, name: str) -> List[str]:
    reasons = (getattr(error, "field_errors", None) or {}).get(name)
    return [r for r in reasons if isinstance(r, str)] if isinstance(reasons, list) else []


def handle_field_message(error: ApiError) -> Optional[str]:
    """A short inline message for the name field, or None.

    The server's wording is a framework default that may be English or
    German; it is read for its meaning and replaced with plain words.
    """
    reasons = _field_reasons(error, "nip05_handle")
    if not reasons:
        return None
    text = " ".join(reasons).lower()
    if any(word in text for word in ("taken", "vergeben", "exists", "already", "bereits")):
        return _("That name is taken. Try another one.")
    if any(word in text for word in ("255", "greater than", "too long", "max", "lang")):
        return _("Use {count} characters or fewer.").format(count=NIP05_HANDLE_MAX_LENGTH)
    if any(word in text for word in ("format", "invalid", "ungültig", "lowercase", "characters")):
        return _HANDLE_CHARACTERS
    return _("This name cannot be used. Try another one.")


def field_message(error: ApiError, name: str) -> Optional[str]:
    """A short inline message for any application field, or None."""
    if name == "nip05_handle":
        return handle_field_message(error)
    reasons = _field_reasons(error, name)
    if not reasons:
        return None
    if name == "email":
        return _("Enter a valid email address.")
    if name == "application_text":
        return _message_too_long()
    if name == "statutes_accepted":
        return _("Agree to the statutes to continue.")
    return _("This entry was not accepted.")
