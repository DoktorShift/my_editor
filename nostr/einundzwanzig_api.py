# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Joining the EINUNDZWANZIG association from inside the app.

``nostr/einundzwanzig.py`` answers "is this account on the public roster"
without telling the association who asked. This module is the other
direction: the signed membership API, through which a user applies,
pays the annual fee, reads their own record back, downloads it, or has
it erased. Every call here is made on purpose by the user, so here the
association does learn who is asking, and that is the point.

Spec: https://verein.einundzwanzig.space/docs/api.json (OpenAPI 3.1).
Only the main surface under ``/api/v1/membership`` is used. The "native
app" branch exists for clients that cannot sign, cannot read a
membership back and has no refresh; this app can sign.

Requests go through a membership service, never straight to the
association. The association names the calling application by a client
key (``X-Api-Key``) that must stay secret, so it lives only on that
service (``sidecar/`` in this repository, run 24/7 by whoever publishes
a build: rinbal's server for official builds, your own if you self-host).
The app never holds the key. Which service a build talks to is
:func:`service_url`; without one, or when the service says it has no
key, joining in the app is simply not offered.

What the app does add is a NIP-98 signature (``nostr/nip98.py``) naming
the end user, on every call but :meth:`MembershipApi.config`. Its ``u``
tag is the association's own URL for the request (that is what the
association verifies), even though the request travels to the service;
the service checks it too before it lends its key. The association
accepts each event id once and only within 60 seconds of its timestamp,
so every attempt builds and signs a FRESH event immediately before it is
sent, a retry included.

Signing goes through a plain callable rather than the bunker, so tests
can fake it and the window can adapt whatever signer it holds (see
:func:`session_signer`). With a NIP-46 phone signer each signed call may
be a prompt on the user's phone, which shapes two decisions below: a
slow approval gets exactly one automatic re-sign, and payment polling
asks for one signature per check, not two.

The server is a third party. Its answers are parsed defensively and
every failure, malformed or not, reaches the caller as an
:class:`ApiError` through ``on_failure``; nothing raises into Qt.
"""

from __future__ import annotations

import copy
import email.utils
import json
import math
import os
import re
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QByteArray, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

import url_safety

from . import nip98
from .bech32 import bech32_decode
from .bunker import is_signer_silent


# --------------------------------------------------------------------------- #
# Endpoint and limits                                                          #
# --------------------------------------------------------------------------- #

# The server rebuilds the URL it verifies the NIP-98 ``u`` tag against from
# its own configured scheme and host, so this must be exactly the server
# URL the spec lists: no trailing slash, no explicit port.
BASE_URL: str = "https://verein.einundzwanzig.space"
API_PREFIX: str = "/api/v1/membership"

# Network budget per request. Qt resets this whenever bytes move, so a
# slow but progressing answer is not cut off.
NETWORK_TIMEOUT_MS: int = 15_000

# How long a remote signer gets to answer one signing request. Passed to
# ``BunkerClient.sign_event`` by :func:`session_signer`. It is long enough
# for a person to find their phone and short enough that an approved
# event usually still lands inside the server's 60 second window.
SIGN_TIMEOUT_MS: int = 60_000

# A credential older than this when it is sent may already be outside the
# server's 60 second window by the time it arrives. If such a request is
# refused with 401, it is signed once more and resent; see ``_on_finished``.
STALE_SIGNATURE_SECONDS: int = 45

# Every answer of this API is a small JSON document. The data export is
# the largest and is still a few kilobytes.
MAX_RESPONSE_BYTES: int = 1024 * 1024

# Same-origin redirects only, and not many of them.
_MAX_REDIRECTS: int = 3

# The spec caps these. Checked locally so a request that is certain to be
# refused does not cost the user a signer prompt first.
APPLICATION_TEXT_MAX_LENGTH: int = 2000
EMAIL_MAX_LENGTH: int = 255
NIP05_HANDLE_MAX_LENGTH: int = 255

# Polling after a payment, mirroring the reference client: every five
# seconds for two minutes.
POLL_INTERVAL_MS: int = 5_000
POLL_ATTEMPTS: int = 24

# A Retry-After further out than this is treated as this. Invoice creation
# has a daily quota, so a day is the longest wait that means anything.
_MAX_RETRY_AFTER_SECONDS: int = 24 * 60 * 60

_MAX_MESSAGE_CHARS: int = 300
_MAX_FIELD_ERRORS: int = 20
_MAX_MESSAGES_PER_FIELD: int = 5

_USER_AGENT = b"my-editor-membership/1"

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


# --------------------------------------------------------------------------- #
# The membership service                                                       #
# --------------------------------------------------------------------------- #

# Developers and self-hosters set this; it wins over the build's service.
ENV_SERVICE_URL: str = "MYEDITOR_MEMBERSHIP_SERVICE"

# How long the app waits for the service to say whether it can help.
STATUS_TIMEOUT_MS: int = 8_000


def _usable_service(value: Any) -> str:
    """An https URL (or http on this computer, for development), without a
    trailing slash, or ``""``."""
    text = value.strip().rstrip("/") if isinstance(value, str) else ""
    if not text or not url_safety.origin_of(text):
        return ""
    lowered = text.lower()
    if lowered.startswith("https://"):
        return text
    local = ("http://localhost", "http://127.0.0.1", "http://[::1]")
    return text if lowered.startswith(local) else ""


def service_url() -> str:
    """The membership service this build uses, or ``""``.

    ``MYEDITOR_MEMBERSHIP_SERVICE`` from the environment first, then the
    build's ``MEMBERSHIP_SERVICE_URL`` (constants.py). Anything that is
    not an https URL counts as none.
    """
    from_env = _usable_service(os.environ.get(ENV_SERVICE_URL, ""))
    if from_env:
        return from_env
    try:
        from constants import MEMBERSHIP_SERVICE_URL
    except ImportError:
        return ""
    return _usable_service(MEMBERSHIP_SERVICE_URL)


def has_service() -> bool:
    """True when this build names a membership service at all."""
    return bool(service_url())


# --------------------------------------------------------------------------- #
# Errors                                                                       #
# --------------------------------------------------------------------------- #

class ErrorCode:
    """The stable error codes. Plain strings, so they survive a signal."""

    NO_KEY = "no_key"                          # this build has no client key
    OFFLINE = "offline"                        # no connection to the server
    TIMEOUT = "timeout"                        # the server stopped answering
    UNAUTHORIZED = "unauthorized"              # 401: key or signature refused
    VALIDATION = "validation"                  # 422, or refused before sending
    RATE_LIMITED = "rate_limited"              # 429, see ``retry_after``
    NOT_FOUND = "not_found"                    # 404: nothing on record
    CONFLICT = "conflict"                      # 409
    SERVER = "server"                          # 5xx: the server's own trouble
    SIGNER_DECLINED = "signer_declined"        # the signer said no, or answered nonsense
    SIGNER_UNREACHABLE = "signer_unreachable"  # the signer never answered
    BAD_RESPONSE = "bad_response"              # an answer this client cannot use


ERROR_CODES = frozenset(
    value for name, value in vars(ErrorCode).items() if name.isupper()
)


@dataclass(frozen=True)
class ApiError:
    """Why a call failed.

    ``code`` is one of :data:`ERROR_CODES` and is what callers branch on.
    ``status`` is the HTTP status when the server answered. ``message``
    is the server's own summary (or a diagnostic for the non-HTTP
    codes); it is for logs, not for the user, who gets :func:`humanize`.
    ``field_errors`` maps a request field to the reasons it was refused.
    ``retry_after`` is in seconds, when the server said how long to wait.
    """

    code: str
    status: Optional[int] = None
    message: str = ""
    field_errors: Dict[str, List[str]] = field(default_factory=dict)
    retry_after: Optional[int] = None


def _signer_error(reason: str, secret: str = "") -> ApiError:
    """Sort a signer failure into "said no" and "never answered".

    The second is recoverable on the user's phone and gets different
    advice, so it must not read as a refusal. ``not connected`` is what
    ``BunkerClient`` says when its channel is down.
    """
    text = str(reason or "")
    lowered = text.lower()
    unreachable = is_signer_silent(text) or "not connected" in lowered
    code = ErrorCode.SIGNER_UNREACHABLE if unreachable else ErrorCode.SIGNER_DECLINED
    return ApiError(code, message=_clean_text(text, secret))


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _clean_text(value: Any, secret: str = "") -> str:
    """Server or signer text, made safe to keep in an error.

    Control characters go, whitespace collapses, and the client key is
    removed BEFORE truncating, so a cut can never leave half of it.
    """
    if not isinstance(value, str):
        return ""
    text = " ".join(_CONTROL_CHARS.sub(" ", value).split())
    if secret:
        text = text.replace(secret, "[hidden]")
    return text[:_MAX_MESSAGE_CHARS]


def _clean_field_errors(value: Any, secret: str = "") -> Dict[str, List[str]]:
    """``{"field": ["reason", ...]}`` from a 422 body, whatever arrived."""
    if not isinstance(value, dict):
        return {}
    cleaned: Dict[str, List[str]] = {}
    for name, reasons in list(value.items())[:_MAX_FIELD_ERRORS]:
        if not isinstance(name, str) or not name:
            continue
        if isinstance(reasons, str):
            reasons = [reasons]
        if not isinstance(reasons, list):
            continue
        texts = [_clean_text(r, secret) for r in reasons[:_MAX_MESSAGES_PER_FIELD]]
        texts = [t for t in texts if t]
        cleaned[_clean_text(name, secret)] = texts
    return cleaned


def parse_retry_after(value: Any, *, now: float) -> Optional[int]:
    """Seconds to wait from a Retry-After header, or None.

    Either form the HTTP spec allows: a number of seconds or a date.
    Clamped to a day, and never negative.
    """
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("latin-1", errors="replace")
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        seconds = int(text)
    else:
        try:
            when = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            from datetime import timezone
            when = when.replace(tzinfo=timezone.utc)
        seconds = math.ceil(when.timestamp() - now)
    return max(0, min(seconds, _MAX_RETRY_AFTER_SECONDS))


# --------------------------------------------------------------------------- #
# Responses                                                                    #
# --------------------------------------------------------------------------- #

STATUS_NONE = "none"
STATUS_AWAITING_PAYMENT = "awaiting_payment"
STATUS_MEMBER = "member"
STATUS_LAPSED = "lapsed"
MEMBERSHIP_STATUSES = frozenset(
    {STATUS_NONE, STATUS_AWAITING_PAYMENT, STATUS_MEMBER, STATUS_LAPSED}
)


@dataclass(frozen=True)
class MembershipConfig:
    """What joining costs and what an application carries.

    ``fee`` is an integer in ``currency``. The spec calls it "the
    smallest unit" while its example reads 21 CHF and the association's
    own client renders it as whole units; show it as the server sends it
    and do not divide.
    """

    fee: int
    currency: str
    year: int
    statutes_url: str
    statutes_version: str
    statutes_adopted_at: str
    required_fields: Tuple[str, ...]
    optional_fields: Tuple[str, ...]
    application_text_max_length: int


@dataclass(frozen=True)
class CurrentYear:
    """The fee year being collected, and whether this person paid it."""

    year: int
    fee: int
    currency: str
    paid: bool
    receipt_url: Optional[str]


@dataclass(frozen=True)
class MembershipStatus:
    """The signing user's own record, as ``GET /me`` reports it.

    Render ``membership_status``. ``association_status`` is the category
    the board assigned and does not lapse, so on its own it calls an
    unpaid member active; the spec is emphatic about this.
    """

    pubkey: str
    membership_status: str
    association_status: str
    statutes_accepted_at: Optional[str]
    applied_at: Optional[str]
    current_year: CurrentYear

    @property
    def is_member(self) -> bool:
        return self.membership_status == STATUS_MEMBER

    @property
    def needs_application(self) -> bool:
        return self.membership_status == STATUS_NONE

    @property
    def needs_payment(self) -> bool:
        return self.membership_status in (STATUS_AWAITING_PAYMENT, STATUS_LAPSED)


@dataclass(frozen=True)
class FeeEntry:
    """One annual fee on record."""

    year: int
    amount: int
    currency: str
    paid: bool
    receipt_url: Optional[str]


@dataclass(frozen=True)
class Invoice:
    """A checkout for one fee year.

    ``bolt11`` is optional and additive: None means "use the checkout
    page", never "something went wrong" and never "expired". On a refresh
    a None ``checkout_url`` means the invoice expired or was invalidated
    and the year is free for a new checkout; see :attr:`expired`.
    """

    checkout_url: Optional[str]
    bolt11: Optional[str]
    created: bool
    payment: FeeEntry

    @property
    def expired(self) -> bool:
        return self.checkout_url is None and not self.payment.paid

    @property
    def amount_sats(self) -> Optional[int]:
        return bolt11_amount_sats(self.bolt11) if self.bolt11 else None


@dataclass(frozen=True)
class Erasure:
    """The answer to an erasure request.

    ``retained_payments`` counts the fees kept as anonymised bookkeeping
    and is None on every call after the first: the link needed to count
    them is what the erasure destroyed.
    """

    erased: bool
    retained_payments: Optional[int]


@dataclass(frozen=True)
class MembershipExport:
    """The data-subject access export.

    ``document`` is the server's ``data`` object as decoded, kept whole
    so it can be saved for the user unchanged. Only the two fields a
    caller needs to label it are lifted out.
    """

    pubkey: str
    membership_status: str
    document: Dict[str, Any]


def _require_dict(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} is not an object")
    return value


def _require_int(obj: Dict[str, Any], key: str) -> int:
    value = obj.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} is not an integer")
    return value


def _require_str(obj: Dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is not a string")
    return value


def _require_bool(obj: Dict[str, Any], key: str) -> bool:
    value = obj.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} is not a boolean")
    return value


def _optional_str(obj: Dict[str, Any], key: str) -> Optional[str]:
    """A nullable string. Absent reads as null: the reader is tolerant
    about the fields it can do without and strict about the rest."""
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} is not a string or null")
    return value


def _optional_url(obj: Dict[str, Any], key: str) -> Optional[str]:
    """A nullable link the user may be sent to.

    A link the browser gate would refuse (``javascript:``, ``file:``)
    makes the whole answer unusable rather than being dropped, so a
    hostile or broken server cannot hand the UI something to open.
    """
    value = _optional_str(obj, key)
    if value is None:
        return None
    if not url_safety.is_safe_external_url(value):
        raise ValueError(f"{key} is not a usable web address")
    return value


def _require_url(obj: Dict[str, Any], key: str) -> str:
    value = _optional_url(obj, key)
    if value is None:
        raise ValueError(f"{key} is missing")
    return value


def _str_tuple(value: Any, what: str) -> Tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{what} is not a list")
    return tuple(v for v in value if isinstance(v, str))


def parse_config(data: Any) -> MembershipConfig:
    """``MembershipConfigResource``. Raises ValueError when malformed."""
    obj = _require_dict(data, "config")
    statutes = _require_dict(obj.get("statutes"), "statutes")
    application = _require_dict(obj.get("application"), "application")
    return MembershipConfig(
        fee=_require_int(obj, "fee"),
        currency=_require_str(obj, "currency"),
        year=_require_int(obj, "year"),
        statutes_url=_require_url(statutes, "url"),
        statutes_version=_require_str(statutes, "version"),
        statutes_adopted_at=_require_str(statutes, "adopted_at"),
        required_fields=_str_tuple(application.get("required_fields"), "required_fields"),
        optional_fields=_str_tuple(application.get("optional_fields"), "optional_fields"),
        application_text_max_length=_require_int(application, "application_text_max_length"),
    )


def _parse_current_year(data: Any) -> CurrentYear:
    obj = _require_dict(data, "current_year")
    return CurrentYear(
        year=_require_int(obj, "year"),
        fee=_require_int(obj, "fee"),
        currency=_require_str(obj, "currency"),
        paid=_require_bool(obj, "paid"),
        receipt_url=_optional_url(obj, "receipt_url"),
    )


def _membership_status(obj: Dict[str, Any]) -> str:
    value = obj.get("membership_status")
    if value not in MEMBERSHIP_STATUSES:
        raise ValueError("membership_status is not one of the documented values")
    return value


def parse_membership(data: Any) -> MembershipStatus:
    """``MembershipResource``. Raises ValueError when malformed."""
    obj = _require_dict(data, "membership")
    pubkey = obj.get("pubkey")
    if not isinstance(pubkey, str) or not _HEX64.match(pubkey):
        raise ValueError("pubkey is not 64 lowercase hex characters")
    return MembershipStatus(
        pubkey=pubkey,
        membership_status=_membership_status(obj),
        association_status=_require_str(obj, "association_status"),
        statutes_accepted_at=_optional_str(obj, "statutes_accepted_at"),
        applied_at=_optional_str(obj, "applied_at"),
        current_year=_parse_current_year(obj.get("current_year")),
    )


def parse_fee_entry(data: Any) -> FeeEntry:
    """``PaymentEventResource``. Raises ValueError when malformed."""
    obj = _require_dict(data, "payment")
    return FeeEntry(
        year=_require_int(obj, "year"),
        amount=_require_int(obj, "amount"),
        currency=_require_str(obj, "currency"),
        paid=_require_bool(obj, "paid"),
        receipt_url=_optional_url(obj, "receipt_url"),
    )


def parse_payments(data: Any) -> Tuple[FeeEntry, ...]:
    """A list of ``PaymentEventResource``, newest year first."""
    if not isinstance(data, list):
        raise ValueError("payments is not a list")
    return tuple(parse_fee_entry(entry) for entry in data)


def parse_invoice(data: Any) -> Invoice:
    """``InvoiceResource``. Raises ValueError when malformed."""
    obj = _require_dict(data, "invoice")
    created = obj.get("created", False)
    if not isinstance(created, bool):
        raise ValueError("created is not a boolean")
    return Invoice(
        checkout_url=_optional_url(obj, "checkout_url"),
        bolt11=_optional_str(obj, "bolt11"),
        created=created,
        payment=parse_fee_entry(obj.get("payment")),
    )


def parse_erasure(data: Any) -> Erasure:
    """The ``DELETE /me`` answer. Raises ValueError when malformed."""
    obj = _require_dict(data, "erasure")
    retained = obj.get("retained_payments")
    if retained is not None and (
        not isinstance(retained, int) or isinstance(retained, bool) or retained < 0
    ):
        raise ValueError("retained_payments is not a count")
    return Erasure(erased=_require_bool(obj, "erased"), retained_payments=retained)


def parse_export(data: Any) -> MembershipExport:
    """``MembershipExportResource``. Raises ValueError when malformed."""
    obj = _require_dict(data, "export")
    subject = _require_dict(obj.get("subject"), "subject")
    pubkey = subject.get("pubkey")
    if not isinstance(pubkey, str) or not _HEX64.match(pubkey):
        raise ValueError("subject.pubkey is not 64 lowercase hex characters")
    for key in ("payments", "membership_grants"):
        if not isinstance(obj.get(key, []), list):
            raise ValueError(f"{key} is not a list")
    for key in ("member", "nostr_profile"):
        if obj.get(key) is not None and not isinstance(obj.get(key), dict):
            raise ValueError(f"{key} is not an object or null")
    return MembershipExport(
        pubkey=pubkey,
        membership_status=_membership_status(obj),
        document=copy.deepcopy(obj),
    )


# --------------------------------------------------------------------------- #
# Lightning amounts                                                            #
# --------------------------------------------------------------------------- #

# BOLT 11: "ln" + currency prefix + optional amount, then the bech32
# separator. Longer prefixes first so "bcrt" is not read as "bc" + "rt".
_BOLT11_HRP = re.compile(r"\Aln(?:bcrt|bc|tbs|tb|sb)(?:(?P<amount>[0-9]+)(?P<unit>[munp]?))?\Z")

# Millisatoshis per unit of each multiplier, one bitcoin being 10^11 msat.
# ``p`` is a tenth of a millisatoshi and handled separately.
_MSAT_PER_UNIT = {"": 100_000_000_000, "m": 100_000_000, "u": 100_000, "n": 100}


def bolt11_amount_sats(bolt11: str) -> Optional[int]:
    """The amount a Lightning invoice asks for, in whole sats, or None.

    None when the invoice names no amount or is not a valid invoice at
    all (bad checksum included). A sub-satoshi remainder is rounded up,
    so the figure shown is never less than what the wallet will pay.
    This reads the amount only; it is not a payment-request decoder.
    """
    if not isinstance(bolt11, str):
        return None
    text = bolt11.strip()
    if text.lower().startswith("lightning:"):
        text = text[len("lightning:"):]
    try:
        hrp, _data = bech32_decode(text)
    except ValueError:
        return None
    match = _BOLT11_HRP.match(hrp)
    if match is None or match.group("amount") is None:
        return None
    digits, unit = match.group("amount"), match.group("unit")
    if digits.startswith("0"):
        return None
    amount = int(digits)
    if unit == "p":
        # BOLT 11 makes a pico amount that is not a whole millisatoshi
        # invalid, rather than rounding it.
        if amount % 10:
            return None
        msat = amount // 10
    else:
        msat = amount * _MSAT_PER_UNIT[unit]
    return -(-msat // 1000)


# --------------------------------------------------------------------------- #
# Checking input before anything is signed                                     #
# --------------------------------------------------------------------------- #

_HANDLE_ALLOWED = re.compile(r"\A[a-z0-9_-]+\Z")
_EMAIL_SHAPE = re.compile(r"\A[^@\s]+@[^@\s]+\Z")

_HANDLE_CHARACTERS = (
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
        return "Enter a name."
    if len(value) > NIP05_HANDLE_MAX_LENGTH:
        return f"Use {NIP05_HANDLE_MAX_LENGTH} characters or fewer."
    if _HANDLE_ALLOWED.match(value):
        return None
    if "@" in value:
        return "Enter only the part before the @ sign."
    if any(ch.isspace() for ch in value):
        return "Spaces are not allowed. " + _HANDLE_CHARACTERS
    if _HANDLE_ALLOWED.match(value.lower()):
        return "Use lowercase letters only."
    return _HANDLE_CHARACTERS


def email_problem(address: str) -> Optional[str]:
    """A plain-words reason an e-mail address will be refused, or None."""
    value = address if isinstance(address, str) else ""
    if len(value) > EMAIL_MAX_LENGTH:
        return f"Use an address of {EMAIL_MAX_LENGTH} characters or fewer."
    if not _EMAIL_SHAPE.match(value):
        return "Enter a valid email address."
    return None


def application_text_problem(text: str) -> Optional[str]:
    """A plain-words reason the application message will be refused, or None."""
    if isinstance(text, str) and len(text) > APPLICATION_TEXT_MAX_LENGTH:
        return f"Keep your message to {APPLICATION_TEXT_MAX_LENGTH:,} characters or fewer."
    return None


# --------------------------------------------------------------------------- #
# Plain-language copy                                                          #
# --------------------------------------------------------------------------- #

def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _wait_advice(seconds: Optional[int]) -> str:
    if not seconds or seconds <= 0:
        return "Wait a few minutes, then try again."
    if seconds < 60:
        return f"Wait {_plural(seconds, 'second')}, then try again."
    minutes = math.ceil(seconds / 60)
    if minutes < 60:
        return f"Wait about {_plural(minutes, 'minute')}, then try again."
    hours = math.ceil(minutes / 60)
    if hours < 24:
        return f"Try again in about {_plural(hours, 'hour')}."
    return "Try again tomorrow."


_COPY: Dict[str, Tuple[str, str]] = {
    ErrorCode.NO_KEY: (
        "Joining is not available in this version",
        "This copy of MyEditor cannot connect to EINUNDZWANZIG. Install "
        "MyEditor from the official download page, or join on the "
        "EINUNDZWANZIG website.",
    ),
    ErrorCode.OFFLINE: (
        "Cannot reach EINUNDZWANZIG",
        "Check your internet connection and try again.",
    ),
    ErrorCode.TIMEOUT: (
        "EINUNDZWANZIG is taking too long to respond",
        "Check your internet connection and try again in a moment.",
    ),
    ErrorCode.UNAUTHORIZED: (
        "EINUNDZWANZIG could not confirm it is you",
        "Make sure the date and time on this computer are set "
        "automatically, then try again. If it keeps happening, check for "
        "a MyEditor update.",
    ),
    ErrorCode.VALIDATION: (
        "Some details need another look",
        "Check the highlighted fields and try again.",
    ),
    ErrorCode.NOT_FOUND: (
        "No application on file yet",
        "EINUNDZWANZIG has nothing on file for this account yet. Send your "
        "membership application first. If you already did, reload and "
        "try again.",
    ),
    ErrorCode.CONFLICT: (
        "Your membership changed in the meantime",
        "Reload to see where things stand, then try again.",
    ),
    ErrorCode.SERVER: (
        "EINUNDZWANZIG is having trouble right now",
        "Try again in a few minutes.",
    ),
    ErrorCode.SIGNER_DECLINED: (
        "The request was not approved",
        "Your signer app did not approve it. Try again, and approve the "
        "request when your signer app asks.",
    ),
    ErrorCode.SIGNER_UNREACHABLE: (
        "Your signer did not answer",
        "Open your signer app, make sure it is running, and try again.",
    ),
    ErrorCode.BAD_RESPONSE: (
        "Something went wrong",
        "MyEditor could not understand the answer from EINUNDZWANZIG. Try "
        "again later. If it keeps happening, check for a MyEditor update.",
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
        return ("Too many attempts", _wait_advice(getattr(error, "retry_after", None)))
    if code == ErrorCode.VALIDATION and not getattr(error, "field_errors", None):
        return (_COPY[code][0], "Check your details and try again.")
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
        return "That name is taken. Try another one."
    if any(word in text for word in ("255", "greater than", "too long", "max", "lang")):
        return f"Use {NIP05_HANDLE_MAX_LENGTH} characters or fewer."
    if any(word in text for word in ("format", "invalid", "ungültig", "lowercase", "characters")):
        return _HANDLE_CHARACTERS
    return "This name cannot be used. Try another one."


def field_message(error: ApiError, name: str) -> Optional[str]:
    """A short inline message for any application field, or None."""
    if name == "nip05_handle":
        return handle_field_message(error)
    reasons = _field_reasons(error, name)
    if not reasons:
        return None
    if name == "email":
        return "Enter a valid email address."
    if name == "application_text":
        return f"Keep your message to {APPLICATION_TEXT_MAX_LENGTH:,} characters or fewer."
    if name == "statutes_accepted":
        return "Agree to the statutes to continue."
    return "This entry was not accepted."


# --------------------------------------------------------------------------- #
# The client                                                                   #
# --------------------------------------------------------------------------- #

# ``sign(unsigned_event, on_success, on_failure)``: hand an unsigned event
# to whatever signer the window holds. Exactly one callback must fire.
SignFn = Callable[[dict, Callable[[dict], None], Callable[[str], None]], None]


class _Unset:
    """Marks an application field the caller did not pass."""

    def __repr__(self) -> str:
        return "UNSET"

    def __bool__(self) -> bool:
        return False


UNSET: Any = _Unset()


@dataclass
class _Call:
    """One API call, from the first signature to the one callback."""

    method: str
    path: str
    body: Optional[bytes]
    signed: bool
    parse: Callable[[Any], Any]
    on_success: Callable[[Any], None]
    on_failure: Callable[[ApiError], None]
    generation: int
    resigned: bool = False
    done: bool = False


def _json_body(payload: Dict[str, Any]) -> bytes:
    """The one serialisation of a body: these bytes are hashed AND sent."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _reply_header(reply, name: str) -> bytes:
    """A response header's raw value, or ``b""``.

    The name goes in as ``str`` first: PySide6 6.11 accepts nothing
    else for ``QNetworkReply.rawHeader``, and a ``bytes`` name raises
    TypeError there. Older bindings took a QByteArray, hence the second
    try.
    """
    for key in (name, name.encode("ascii")):
        try:
            return bytes(reply.rawHeader(key))
        except (AttributeError, TypeError):
            continue
    return b""


def _year_segment(year: int) -> str:
    if not isinstance(year, int) or isinstance(year, bool) or not 1000 <= year <= 9999:
        raise ValueError("year must be a four-digit integer")
    return str(year)


class MembershipApi(QObject):
    """Callback-style client for the membership API's main surface.

    Every method takes ``on_success`` and ``on_failure`` and exactly one
    of them fires, ``on_failure`` always with an :class:`ApiError`. A
    failure that needs no network (no key, a field that would be
    refused) is reported before the method returns and before anything
    is signed, so the user is never asked to approve a request that
    cannot succeed.

    Seams: ``sign`` (see :data:`SignFn`), ``service_url`` (None reads
    :func:`service_url`), ``nam`` and ``clock`` (unix seconds), so no test
    touches the network, a signer or the wall clock.

    ``base_url`` is the association's own address: what the signatures
    name. ``service_url`` is where the requests travel.
    """

    def __init__(
        self,
        sign: SignFn,
        *,
        service_url: Optional[str] = None,
        base_url: str = BASE_URL,
        nam: Optional[QNetworkAccessManager] = None,
        clock: Optional[Callable[[], float]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        base = str(base_url or "").rstrip("/")
        if not url_safety.origin_of(base):
            raise ValueError("base_url must be an absolute http(s) URL")
        self._sign = sign
        self._service_url = (_usable_service(service_url) if service_url is not None
                             else _resolve_service_url())
        self._service_ok: Optional[bool] = None
        self._base_url = base
        self._nam = nam or QNetworkAccessManager(self)
        self._clock = clock or time.time
        self._generation = 0
        self._inflight: Dict[int, Any] = {}

    # -- public surface ----------------------------------------------------

    @property
    def configured(self) -> bool:
        """This build names a membership service."""
        return bool(self._service_url)

    @property
    def available(self) -> bool:
        """The service answered that it can sign people up (check_service)."""
        return self.configured and self._service_ok is True

    def check_service(self, on_done: Callable[[bool], None]) -> None:
        """Ask the service whether joining is possible; ``on_done(bool)`` once.

        A service with no key, an unreachable one, or no service at all
        answers False, and the window then offers the association's
        website instead of a flow that would end in an error.
        """
        if not self._service_url:
            QTimer.singleShot(0, lambda: on_done(False))
            return
        request = QNetworkRequest(QUrl(f"{self._service_url}/status"))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", _USER_AGENT)
        request.setTransferTimeout(STATUS_TIMEOUT_MS)
        reply = self._nam.get(request)

        def finished() -> None:
            ok = False
            try:
                if reply.error() == QNetworkReply.NetworkError.NoError:
                    data = json.loads(bytes(reply.readAll())[:65536].decode("utf-8"))
                    ok = isinstance(data, dict) and data.get("membership") is True
            except (ValueError, UnicodeDecodeError):
                ok = False
            finally:
                reply.deleteLater()
            self._service_ok = ok
            on_done(ok)

        reply.finished.connect(finished)

    def url_for(self, path: str) -> str:
        """The association's own URL for ``path``: what the NIP-98 ``u`` tag
        names. One place builds it, so signature and request always agree."""
        return f"{self._base_url}{API_PREFIX}{path}"

    def request_url_for(self, path: str) -> str:
        """Where the request for ``path`` actually travels: the service."""
        return f"{self._service_url}{API_PREFIX}{path}"

    def config(self, on_success: Callable[[MembershipConfig], None],
               on_failure: Callable[[ApiError], None]) -> None:
        """The fee, the current fee year and the statutes. Key only, no
        signature: the fee has to be visible before anybody signs."""
        self._start("GET", "/config", None, False, parse_config, on_success, on_failure)

    def me(self, on_success: Callable[[MembershipStatus], None],
           on_failure: Callable[[ApiError], None]) -> None:
        """The signing user's own membership."""
        self._start("GET", "/me", None, True, parse_membership, on_success, on_failure)

    def apply(
        self,
        on_success: Callable[[MembershipStatus], None],
        on_failure: Callable[[ApiError], None],
        *,
        statutes_accepted: Any = True,
        application_text: Any = UNSET,
        email: Any = UNSET,
        no_email: Any = UNSET,
        nip05_handle: Any = UNSET,
    ) -> None:
        """File the application, or update the contact data of one.

        Only the fields passed are sent. A field left out is left alone
        on the server; a field passed as None is sent as null, which
        CLEARS the stored value. The body is signed as a whole, so the
        difference is real: sending a key the user never touched would
        overwrite what they have on file.

        ``statutes_accepted`` is True (the consent, required the first
        time) or UNSET (a repeat application that only updates contact
        data). The server refuses an explicit false, so it is not
        accepted here either. ``no_email`` is a bool or UNSET; the server
        has no null for it. Misuse raises TypeError: it is a bug in the
        caller, not a condition to report to the user.
        """
        payload: Dict[str, Any] = {}
        if statutes_accepted is True:
            payload["statutes_accepted"] = True
        elif statutes_accepted is not UNSET:
            raise TypeError("statutes_accepted must be True or UNSET")
        for name, value in (
            ("application_text", application_text),
            ("email", email),
            ("nip05_handle", nip05_handle),
        ):
            if value is UNSET:
                continue
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string, None or UNSET")
            payload[name] = value
        if no_email is not UNSET:
            if not isinstance(no_email, bool):
                raise TypeError("no_email must be a bool or UNSET")
            payload["no_email"] = no_email

        problems: Dict[str, List[str]] = {}
        for name, check in (
            ("nip05_handle", nip05_handle_problem),
            ("email", email_problem),
            ("application_text", application_text_problem),
        ):
            value = payload.get(name)
            if isinstance(value, str):
                problem = check(value)
                if problem:
                    problems[name] = [problem]
        call = self._new_call("POST", "/applications", _json_body(payload), True,
                              parse_membership, on_success, on_failure)
        # A missing service outranks a field problem: without one nothing
        # works, and fixing the field would only lead to the next refusal.
        if problems and self._service_url:
            self._fail(call, ApiError(ErrorCode.VALIDATION, field_errors=problems))
            return
        self._run(call)

    def create_invoice(
        self,
        year: int,
        on_success: Callable[[Invoice], None],
        on_failure: Callable[[ApiError], None],
        *,
        return_url: Optional[str] = None,
    ) -> None:
        """A checkout for ``year``, which must be the current fee year.

        Idempotent on the server: a second call hands back the existing
        invoice with ``created`` False. ``return_url`` must be on the
        association's allowlist or the call is refused; without one the
        body is left out entirely, as the spec allows. Raises ValueError
        for a year that is not four digits.
        """
        body = _json_body({"return_url": return_url}) if return_url else None
        self._start("POST", f"/payments/{_year_segment(year)}/invoice", body, True,
                    parse_invoice, on_success, on_failure)

    def refresh_payment(
        self,
        year: int,
        on_success: Callable[[Invoice], None],
        on_failure: Callable[[ApiError], None],
    ) -> None:
        """Re-read the invoice for ``year`` from the payment processor.

        This is also the repair path for a lost payment notification:
        a settled invoice found here grants the membership exactly as
        the notification would have. Creates nothing.
        """
        self._start("POST", f"/payments/{_year_segment(year)}/refresh", None, True,
                    parse_invoice, on_success, on_failure)

    def payments(self, on_success: Callable[[Tuple[FeeEntry, ...]], None],
                 on_failure: Callable[[ApiError], None]) -> None:
        """Every fee on record, newest year first."""
        self._start("GET", "/payments", None, True, parse_payments, on_success, on_failure)

    def export_data(self, on_success: Callable[[MembershipExport], None],
                    on_failure: Callable[[ApiError], None]) -> None:
        """Everything the association stores about the signing user."""
        self._start("GET", "/export", None, True, parse_export, on_success, on_failure)

    def erase(self, on_success: Callable[[Erasure], None],
              on_failure: Callable[[ApiError], None]) -> None:
        """Erase the signing user's personal data. Not a ban, and paid
        fees stay behind as anonymised bookkeeping."""
        self._start("DELETE", "/me", None, True, parse_erasure, on_success, on_failure)

    def cancel(self) -> None:
        """Drop every call in progress. No callback fires for them.

        A signer prompt already on the user's phone cannot be withdrawn;
        its answer is simply ignored.
        """
        self._generation += 1
        inflight, self._inflight = self._inflight, {}
        for reply in inflight.values():
            try:
                reply.abort()
            except RuntimeError:  # already deleted on the C++ side
                pass

    # -- the pipeline ------------------------------------------------------

    def _new_call(self, method, path, body, signed, parse, on_success, on_failure) -> _Call:
        return _Call(method, path, body, signed, parse, on_success, on_failure,
                     self._generation)

    def _start(self, method, path, body, signed, parse, on_success, on_failure) -> None:
        self._run(self._new_call(method, path, body, signed, parse, on_success, on_failure))

    def _run(self, call: _Call) -> None:
        if not self._service_url:
            self._fail(call, ApiError(ErrorCode.NO_KEY))
            return
        if call.signed:
            self._sign_and_send(call)
        else:
            self._send(call, None)

    def _live(self, call: _Call) -> bool:
        return not call.done and call.generation == self._generation

    def _sign_and_send(self, call: _Call) -> None:
        # Built here, immediately before signing, every time: an event id
        # is accepted once and its timestamp only for 60 seconds.
        unsigned = nip98.build_unsigned_auth_event(
            self.url_for(call.path), call.method, call.body, now=int(self._clock()),
        )
        answered = {"done": False}

        def _signed(event: dict) -> None:
            if answered["done"]:
                return
            answered["done"] = True
            if not self._live(call):
                return
            problem = nip98.signed_event_problem(event, unsigned)
            if problem:
                self._fail(call, ApiError(ErrorCode.SIGNER_DECLINED, message=problem))
                return
            self._send(call, event)

        def _refused(reason: str) -> None:
            if answered["done"]:
                return
            answered["done"] = True
            if self._live(call):
                self._fail(call, _signer_error(reason, ""))

        try:
            # A copy, so a signer that edits what it is handed cannot
            # change what its answer is checked against.
            self._sign(copy.deepcopy(unsigned), _signed, _refused)
        except Exception as exc:  # noqa: BLE001, a broken adapter must not reach Qt
            if not answered["done"]:
                answered["done"] = True
                if self._live(call):
                    self._fail(call, ApiError(
                        ErrorCode.SIGNER_UNREACHABLE,
                        message=_clean_text(f"signer unavailable: {exc}", ""),
                    ))

    def _send(self, call: _Call, signed: Optional[dict]) -> None:
        request = QNetworkRequest(QUrl(self.request_url_for(call.path)))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", _USER_AGENT)
        request.setTransferTimeout(NETWORK_TIMEOUT_MS)
        # A redirect re-sends the headers, the signature included, so it
        # may only stay on the service's own origin.
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy,
        )
        request.setMaximumRedirectsAllowed(_MAX_REDIRECTS)

        stale = False
        if signed is not None:
            request.setRawHeader(b"Authorization", nip98.authorization_header_value(signed))
            stale = (self._clock() - signed["created_at"]) > STALE_SIGNATURE_SECONDS

        if nip98.has_body(call.body):
            # Exactly this value: anything else is refused with 415.
            request.setRawHeader(b"Content-Type", nip98.JSON_CONTENT_TYPE.encode("ascii"))
            reply = self._nam.post(request, QByteArray(call.body))
        elif call.method == "GET":
            reply = self._nam.get(request)
        elif call.method == "DELETE":
            reply = self._nam.deleteResource(request)
        else:
            # No body means no Content-Type and no payload tag. Qt adds no
            # Content-Type of its own to an empty POST.
            reply = self._nam.post(request, QByteArray())

        key = id(reply)
        self._inflight[key] = reply
        flags = {"oversize": False}

        def _guard(received: int, total: int) -> None:
            if flags["oversize"]:
                return
            if received > MAX_RESPONSE_BYTES or total > MAX_RESPONSE_BYTES:
                flags["oversize"] = True
                reply.abort()

        reply.downloadProgress.connect(_guard)
        reply.finished.connect(lambda: self._on_finished(call, reply, key, flags, stale))

    def _on_finished(self, call: _Call, reply, key: int, flags: dict, stale: bool) -> None:
        self._inflight.pop(key, None)
        try:
            if not self._live(call):
                return
            outcome = self._read(call, reply, flags)
        finally:
            reply.deleteLater()

        if isinstance(outcome, ApiError):
            # A remote signer that took most of the 60 second window can
            # hand back an event that expires on the way. That is the one
            # 401 a fresh signature can cure, so it gets one, and only one.
            if (
                outcome.code == ErrorCode.UNAUTHORIZED
                and call.signed
                and stale
                and not call.resigned
            ):
                call.resigned = True
                self._sign_and_send(call)
                return
            self._fail(call, outcome)
            return
        self._succeed(call, outcome[0])

    def _read(self, call: _Call, reply, flags: dict):
        """``(value,)`` on success, else the :class:`ApiError`."""
        status = int(reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0)
        if flags["oversize"]:
            return ApiError(ErrorCode.BAD_RESPONSE, status=status or None,
                            message="the answer was larger than allowed")
        if 300 <= status < 400:
            return ApiError(ErrorCode.BAD_RESPONSE, status=status,
                            message="the server redirected the request")
        if status == 0:
            return self._transport_error(reply.error())

        raw = bytes(reply.readAll())
        if len(raw) > MAX_RESPONSE_BYTES:
            return ApiError(ErrorCode.BAD_RESPONSE, status=status,
                            message="the answer was larger than allowed")
        if not 200 <= status < 300:
            return self._http_error(status, raw, reply)
        try:
            envelope = json.loads(raw.decode("utf-8"))
            if not isinstance(envelope, dict) or "data" not in envelope:
                raise ValueError("the answer has no data")
            return (call.parse(envelope["data"]),)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            return ApiError(ErrorCode.BAD_RESPONSE, status=status,
                            message=_clean_text(str(exc), ""))

    @staticmethod
    def _transport_error(error) -> ApiError:
        if error in (QNetworkReply.NetworkError.TimeoutError,
                     QNetworkReply.NetworkError.OperationCanceledError):
            # Qt reports its own transfer timeout as a cancelled operation.
            # Our deliberate aborts never get here: an oversize answer is
            # caught above and a cancelled call is no longer live.
            return ApiError(ErrorCode.TIMEOUT)
        if error in (QNetworkReply.NetworkError.InsecureRedirectError,
                     QNetworkReply.NetworkError.TooManyRedirectsError):
            return ApiError(ErrorCode.BAD_RESPONSE, message="the server redirected the request")
        if error == QNetworkReply.NetworkError.NoError:
            return ApiError(ErrorCode.BAD_RESPONSE, message="the answer carried no status")
        return ApiError(ErrorCode.OFFLINE)

    def _http_error(self, status: int, raw: bytes, reply) -> ApiError:
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError, RecursionError):
            body = {}
        if not isinstance(body, dict):
            body = {}
        message = _clean_text(body.get("message"), "")

        retry_after = None
        if status in (429, 503):
            retry_after = parse_retry_after(
                _reply_header(reply, "Retry-After"), now=self._clock(),
            )

        if status == 503 and body.get("code") == "not_configured":
            # The service runs but holds no key: joining isn't offered here.
            code = ErrorCode.NO_KEY
        elif status in (401, 403):
            code = ErrorCode.UNAUTHORIZED
        elif status == 404:
            code = ErrorCode.NOT_FOUND
        elif status == 409:
            code = ErrorCode.CONFLICT
        elif status == 422:
            return ApiError(
                ErrorCode.VALIDATION, status=status, message=message,
                field_errors=_clean_field_errors(body.get("errors"), ""),
            )
        elif status == 429:
            code = ErrorCode.RATE_LIMITED
        elif 500 <= status < 600:
            code = ErrorCode.SERVER
        else:
            # 400, 405, 413, 415 and the rest: the request itself was
            # wrong, which only a bug or a changed server explains. There
            # is nothing the user could fix.
            code = ErrorCode.BAD_RESPONSE
        return ApiError(code, status=status, message=message, retry_after=retry_after)

    # -- delivery ----------------------------------------------------------

    def _succeed(self, call: _Call, value: Any) -> None:
        if not self._live(call):
            return
        call.done = True
        try:
            call.on_success(value)
        except Exception:  # noqa: BLE001, a caller's bug must not unwind into Qt
            traceback.print_exc()

    def _fail(self, call: _Call, error: ApiError) -> None:
        if not self._live(call):
            return
        call.done = True
        try:
            call.on_failure(error)
        except Exception:  # noqa: BLE001
            traceback.print_exc()


# ``api_key`` is shadowed by the constructor argument of the same name.
_resolve_service_url = service_url


# --------------------------------------------------------------------------- #
# Signer adapter                                                               #
# --------------------------------------------------------------------------- #

def session_signer(pool, profile, *, timeout_ms: int = SIGN_TIMEOUT_MS) -> SignFn:
    """A :data:`SignFn` over a ``BunkerSessionPool`` for ``profile``.

    Typed loosely on purpose so this module never imports the pool: it
    only needs ``pool.get(profile, on_ready, on_error)`` and the client's
    ``sign_event(unsigned, on_success, on_failure, timeout_ms=...)``.
    """

    def sign(unsigned: dict, on_success, on_failure) -> None:
        def _ready(client) -> None:
            client.sign_event(unsigned, on_success, on_failure, timeout_ms=timeout_ms)

        pool.get(profile, _ready, on_failure)

    return sign


# --------------------------------------------------------------------------- #
# Waiting for a payment                                                        #
# --------------------------------------------------------------------------- #

# A quota wait longer than this ends the watch instead of stretching it:
# the user is better told to come back later than shown a spinner.
_MAX_POLL_BACKOFF_SECONDS: int = 60

# Failures worth another poll. Anything else (the signer said no or is
# gone, the key or the signature was refused, nothing on record) will not
# be cured by asking again, and asking again would only prompt the user's
# phone for nothing.
_POLL_AGAIN = frozenset({
    ErrorCode.OFFLINE,
    ErrorCode.TIMEOUT,
    ErrorCode.SERVER,
    ErrorCode.BAD_RESPONSE,
    ErrorCode.RATE_LIMITED,
})


class PaymentWatcher(QObject):
    """Polls for a settled payment after the user has paid.

    Polls ``refresh``, not ``me``. Both need a signature, and with a
    phone signer every signature may be a prompt, so one call per check
    matters. ``refresh`` is also the right one: it re-reads the invoice
    from the payment processor and, when the payment notification to
    the association was lost, books the payment itself. Polling ``me``
    would wait forever on exactly that failure. Once :attr:`paid` fires,
    one ``me`` call fetches the new membership.

    One request at a time: the next poll is scheduled only after the
    previous one answered. The timer is a seam; anything with
    ``timeout``, ``setSingleShot``, ``start(ms)`` and ``stop`` will do.

    Signals:
      paid(Invoice)        settled. Polling has stopped.
      still_waiting(int)   that many checks done, not settled yet.
      expired(Invoice)     the invoice expired or was invalidated, so
                           waiting is pointless; start a new checkout.
      gave_up()            the attempts ran out. Offer a manual check.
      failed(ApiError)     a failure another poll would not cure.
    """

    paid = Signal(object)
    still_waiting = Signal(int)
    expired = Signal(object)
    gave_up = Signal()
    failed = Signal(object)

    def __init__(
        self,
        api: MembershipApi,
        year: int,
        *,
        interval_ms: int = POLL_INTERVAL_MS,
        max_attempts: int = POLL_ATTEMPTS,
        timer=None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._year = year
        self._interval_ms = max(1, int(interval_ms))
        self._max_attempts = max(1, int(max_attempts))
        self._timer = timer if timer is not None else QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._poll)
        self._running = False
        self._polling = False
        self._attempts = 0
        self._token = 0
        self._last_error: Optional[ApiError] = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def attempts(self) -> int:
        return self._attempts

    @property
    def last_error(self) -> Optional[ApiError]:
        """The most recent failure that did not stop the watcher."""
        return self._last_error

    def start(self, *, poll_now: bool = False) -> None:
        """Start (or restart) watching. The first check waits one
        interval unless ``poll_now``."""
        self.stop()
        self._running = True
        self._attempts = 0
        self._last_error = None
        if poll_now:
            self._poll()
        else:
            self._timer.start(self._interval_ms)

    def check_now(self) -> None:
        """Check immediately, for a "Check again" button. Counts as an
        attempt. Does nothing while a check is already in flight."""
        if not self._running or self._polling:
            return
        self._timer.stop()
        self._poll()

    def stop(self) -> None:
        """Stop watching. An answer still in flight is ignored."""
        self._running = False
        self._polling = False
        self._token += 1
        self._timer.stop()

    # -- internals ---------------------------------------------------------

    def _poll(self) -> None:
        if not self._running or self._polling:
            return
        self._polling = True
        token = self._token
        self._api.refresh_payment(
            self._year,
            lambda invoice, t=token: self._on_invoice(t, invoice),
            lambda error, t=token: self._on_error(t, error),
        )

    def _on_invoice(self, token: int, invoice: Invoice) -> None:
        if token != self._token:
            return
        self._polling = False
        self._attempts += 1
        self._last_error = None
        if invoice.payment.paid:
            self._finish()
            self.paid.emit(invoice)
        elif invoice.expired:
            self._finish()
            self.expired.emit(invoice)
        else:
            self._again(self._interval_ms)

    def _on_error(self, token: int, error: ApiError) -> None:
        if token != self._token:
            return
        self._polling = False
        self._attempts += 1
        too_long = (error.code == ErrorCode.RATE_LIMITED
                    and (error.retry_after or 0) > _MAX_POLL_BACKOFF_SECONDS)
        if error.code not in _POLL_AGAIN or too_long:
            self._finish()
            self.failed.emit(error)
            return
        self._last_error = error
        delay = self._interval_ms
        if error.retry_after:
            delay = max(delay, error.retry_after * 1000)
        self._again(delay)

    def _again(self, delay_ms: int) -> None:
        if self._attempts >= self._max_attempts:
            self._finish()
            self.gave_up.emit()
            return
        self.still_waiting.emit(self._attempts)
        # A slot on still_waiting may have stopped or restarted us.
        if self._running and not self._polling and not self._timer.isActive():
            self._timer.start(int(delay_ms))

    def _finish(self) -> None:
        self._running = False
        self._timer.stop()
