"""Checking a person's Nostr address (NIP-05).

Anyone can write any address into their profile, so a name like
``jack@cash.app`` proves nothing by itself. An address counts as a
person's only once its domain confirms it: the domain's
``/.well-known/nostr.json`` must map the name to that person's key.
Until then, and whenever the check fails, the app does not show the
address, so a stranger cannot pass as someone else with a borrowed one.

The check follows NIP-05: https only, redirects are not followed, names
are compared without regard to case, and the key must be the hex key of
the profile that names the address. A domain that points into the
person's own network (``localhost``, ``router``, a private address) is
never asked, because the address comes from a stranger's profile.
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from url_safety import is_safe_mirror_source

# NIP-05: the name may hold a-z, 0-9 and "-_.", without regard to case.
_ADDRESS_RE = re.compile(r"^([a-z0-9._-]+)@([a-z0-9.-]+\.[a-z0-9-]{2,})$")
_HEX_KEY_RE = re.compile(r"^[0-9a-f]{64}$")

CHECK_TIMEOUT_MS = 8000
MAX_ANSWER_BYTES = 512 * 1024   # some domains list thousands of names
AT_ONCE = 4                     # checks running at the same time

# How long a verdict holds before the domain is asked again.
CONFIRMED_FOR_S = 24 * 3600
REFUSED_FOR_S = 3600
UNREACHABLE_FOR_S = 10 * 60

_USER_AGENT = b"MyEditor"


@dataclass(frozen=True)
class Address:
    name: str
    domain: str

    @property
    def url(self) -> str:
        return f"https://{self.domain}/.well-known/nostr.json?name={self.name}"


def parse_address(text: str) -> Optional[Address]:
    """The address in ``name@domain`` form, lowercased, or None when it is
    not a NIP-05 address or its domain may not be asked."""
    match = _ADDRESS_RE.match((text or "").strip().lower())
    if match is None:
        return None
    address = Address(match.group(1), match.group(2).rstrip("."))
    if not is_safe_mirror_source(f"https://{address.domain}/"):
        return None
    return address


def shown_address(text: str) -> str:
    """How an address is shown: ``_@example.com`` is the domain's own
    name and shows as ``example.com`` (NIP-05)."""
    text = (text or "").strip()
    return text[2:] if text.lower().startswith("_@") else text


def names_key(answer: object, name: str) -> Optional[str]:
    """The hex key a ``nostr.json`` answer gives for ``name``, or None."""
    names = answer.get("names") if isinstance(answer, dict) else None
    if not isinstance(names, dict):
        return None
    key = names.get(name)
    if key is None:
        key = next((value for listed, value in names.items()
                    if isinstance(listed, str) and listed.lower() == name), None)
    if not isinstance(key, str):
        return None
    key = key.strip().lower()
    return key if _HEX_KEY_RE.match(key) else None


class Nip05Check(QObject):
    """Confirms people's Nostr addresses with their domains.

    ``confirmed(pubkey, address)`` answers at once from what is known:
    True when the domain confirmed the address for this key, False when it
    did not (or could not be asked), None when it has not answered yet. A
    None starts a check; ``checked(pubkey, address)`` fires when its
    verdict arrives, so a list can show the address then. Verdicts are
    kept for a while (a confirmation for a day) and asked again after.
    ``nam`` and ``clock`` are seams for tests.
    """

    checked = Signal(str, str)

    def __init__(self, *, nam: Optional[QNetworkAccessManager] = None,
                 clock: Callable[[], float] = time.time,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._nam = nam or QNetworkAccessManager(self)
        self._clock = clock
        # (name, domain) -> (key the domain gave or None, valid until)
        self._answers: Dict[Address, Tuple[Optional[str], float]] = {}
        # address -> [(pubkey, address as the profile wrote it)] waiting
        self._waiting: Dict[Address, List[Tuple[str, str]]] = {}
        self._queue: Deque[Address] = deque()
        self._running = 0

    def confirmed(self, pubkey: str, text: str) -> Optional[bool]:
        address = parse_address(text)
        if address is None or not _HEX_KEY_RE.match((pubkey or "").lower()):
            return False
        known = self._answers.get(address)
        if known is not None and known[1] > self._clock():
            return known[0] == pubkey.lower()
        self._ask(address, pubkey.lower(), text)
        return None

    def forget(self) -> None:
        """Drops every verdict, for example when the network came back."""
        self._answers.clear()

    # -- asking ------------------------------------------------------------

    def _ask(self, address: Address, pubkey: str, text: str) -> None:
        waiting = self._waiting.get(address)
        if waiting is not None:
            if (pubkey, text) not in waiting:
                waiting.append((pubkey, text))
            return
        self._waiting[address] = [(pubkey, text)]
        self._queue.append(address)
        self._next()

    def _next(self) -> None:
        while self._running < AT_ONCE and self._queue:
            self._start(self._queue.popleft())

    def _start(self, address: Address) -> None:
        request = QNetworkRequest(QUrl(address.url))
        request.setRawHeader(b"Accept", b"application/json")
        request.setRawHeader(b"User-Agent", _USER_AGENT)
        request.setTransferTimeout(CHECK_TIMEOUT_MS)
        # NIP-05: a redirect from nostr.json must be ignored.
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        self._running += 1
        reply = self._nam.get(request)
        if reply is None:
            # Never answer inside confirmed(): its caller expects None now
            # and a signal later.
            QTimer.singleShot(0, lambda: self._done(address, None, UNREACHABLE_FOR_S))
            return

        def progress(received: int, _total: int) -> None:
            if received > MAX_ANSWER_BYTES:
                reply.abort()

        def finished() -> None:
            key, lasts = None, UNREACHABLE_FOR_S
            try:
                status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if reply.error() == QNetworkReply.NetworkError.NoError and status == 200:
                    raw = bytes(reply.readAll())
                    if len(raw) <= MAX_ANSWER_BYTES:
                        key = names_key(json.loads(raw.decode("utf-8")), address.name)
                        lasts = CONFIRMED_FOR_S if key else REFUSED_FOR_S
                elif reply.error() == QNetworkReply.NetworkError.ContentNotFoundError \
                        or status in (301, 302, 303, 307, 308, 404):
                    lasts = REFUSED_FOR_S
            except (ValueError, UnicodeDecodeError):
                lasts = REFUSED_FOR_S
            finally:
                reply.deleteLater()
            self._done(address, key, lasts)

        reply.downloadProgress.connect(progress)
        reply.finished.connect(finished)

    def _done(self, address: Address, key: Optional[str], lasts: float) -> None:
        self._running -= 1
        self._answers[address] = (key, self._clock() + lasts)
        for pubkey, text in self._waiting.pop(address, []):
            self.checked.emit(pubkey, text)
        self._next()
