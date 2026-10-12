# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nostr relay WebSocket pool - Qt-native, no asyncio.

References:
  - NIP-01: https://github.com/nostr-protocol/nips/blob/master/01.md
  - NIP-20 (OK response): folded into NIP-01
  - NIP-42: https://github.com/nostr-protocol/nips/blob/master/42.md

Wire messages we send (client -> relay):
  ["EVENT", <event>]                           # publish
  ["REQ", <sub_id>, <filter>, ...]             # subscribe
  ["CLOSE", <sub_id>]                          # cancel subscription
  ["AUTH", <signed kind 22242 event>]          # sign in (NIP-42)

Wire messages we handle (relay -> client):
  ["OK", <event_id>, <bool>, <message>]        # publish ack
  ["EVENT", <sub_id>, <event>]                 # stored or live event
  ["EOSE", <sub_id>]                           # end of stored events
  ["NOTICE", <message>]                        # human-readable info / error
  ["CLOSED", <sub_id>, <message>]              # subscription terminated
  ["AUTH", <challenge>]                        # what a sign-in has to sign

Design notes:
  - One Relay per URL, shared across all jobs. WebSocket stays warm so
    repeated publishes don't re-handshake.
  - PublishJob fans out a single EVENT to N relays and tracks per-relay
    state. Eager semantics: ``first_accept`` fires the instant one relay
    OKs; ``all_done`` fires after every relay has reported (success,
    rejection, error, or per-relay timeout).
  - A relay that carries open subscriptions reconnects by itself when
    its socket drops (after 1 s, then twice as long each time, at most a
    minute), and every open subscription sends its REQ again once it is
    back. That is what keeps a signer app's channel (NIP-46) and live
    draft sync working through a dropped connection. A relay nobody
    subscribes to is not reopened: the next publish opens a fresh socket.
  - Signing in (NIP-42) happens only when a relay refuses a request with
    "auth-required:", never unasked: signing in tells the relay who you
    are. Who signs in, and where they may, is the ``sign_in`` of the
    request or of the pool (nostr/relay_auth.py). The request is sent
    again once after the relay accepted the sign-in; a second refusal is
    final. A sign-in lasts as long as the connection.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

from i18n import _

from . import crypto, events
from .outbox.policy import normalize_relay_url


# Per-relay ceiling for a publish ack. Buho_go uses 8 s; we match it.
DEFAULT_PUBLISH_TIMEOUT_MS: int = 8000

# How long to give a freshly-opened socket to complete its TLS handshake
# before we declare it dead. Folded into the publish timeout - this is
# just the wait for ``connected`` to fire.
DEFAULT_CONNECT_TIMEOUT_MS: int = 5000

# Reconnecting a relay that still carries subscriptions: the first wait,
# doubled after every failed attempt, up to the last.
RECONNECT_FIRST_MS: int = 1000
RECONNECT_LONGEST_MS: int = 60_000

# A relay that refuses a request until you sign in starts its reason so.
AUTH_REQUIRED: str = "auth-required:"

# Signing in: how long a relay that asked for it has to send the challenge
# to sign, and to accept or refuse the signed answer. Signing itself has
# no limit here: a signer app waits for the person, as long as it waits.
SIGN_IN_CHALLENGE_WAIT_MS: int = 5000
SIGN_IN_ANSWER_MS: int = DEFAULT_PUBLISH_TIMEOUT_MS

# Why a sign-in did not happen, when the relay is not one to sign in to.
NOT_CHOSEN: str = "not a relay this account chose"

AUTH_KIND: int = 22242


def sign_in_event(url: str, challenge: str, *, created_at: Optional[int] = None) -> dict:
    """The unsigned answer to a relay's challenge (NIP-42)."""
    return {"kind": AUTH_KIND, "content": "",
            "tags": [["relay", url], ["challenge", challenge]],
            "created_at": int(created_at if created_at is not None else time.time())}


class KeySignIn:
    """Signs in with a key kept here, wherever it is asked: a signer app's
    channel key (nostr/bunker.py), which says nothing about the account.
    The account's own sign-in is nostr/relay_auth.py."""

    def __init__(self, secret_key: bytes) -> None:
        self._sk = bytes(secret_key)
        self.pubkey = crypto.get_public_key(self._sk).hex()

    def may_sign_in(self, url: str) -> bool:
        return True

    def sign(self, url: str, challenge: str, on_signed: Callable[[dict], None],
             on_failed: Callable[[str], None]) -> None:
        on_signed(events.sign_event(sign_in_event(url, challenge), self._sk))


PublishResult = Tuple[str, bool, str]  # (url, ok, message)


@dataclass
class _SignIn:
    """A sign-in under way on one relay, for one key."""
    signer: Any
    waiters: List[Callable[[bool, str], None]]
    timer: QTimer
    asked: bool = False                 # the signer was asked to sign
    event_id: Optional[str] = None      # the signed answer, once sent


# --------------------------------------------------------------------------- #
# Relay - one WebSocket connection to one URL                                 #
# --------------------------------------------------------------------------- #

class Relay(QObject):
    """A single, long-lived WebSocket to one relay URL.

    Signals:
      connected()          - handshake completed
      disconnected()       - socket closed for any reason
      message(list)        - a parsed JSON array from the relay
      error(str)           - connection or socket-level error
      sign_in_started()    - signing in to this relay began
      sign_in_ended(bool, str, bool)
                           - it ended: whether the relay accepted it, why
                             not, and whether a signed answer was sent
    """

    connected = Signal()
    disconnected = Signal()
    message = Signal(list)
    error = Signal(str)
    sign_in_started = Signal()
    sign_in_ended = Signal(bool, str, bool)

    def __init__(self, url: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._url = url
        self._ws = QWebSocket(parent=self)
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_text_message)
        self._ws.errorOccurred.connect(self._on_error)
        self._connected = False
        self._opening = False
        self._stopped = False
        # Open subscriptions on this relay; while there are any, a dropped
        # socket is reopened.
        self._holds = 0
        self._retry_ms = RECONNECT_FIRST_MS
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self.open)
        # Signing in (NIP-42): the challenge this connection was given, the
        # keys signed in on it, and the sign-ins under way by key.
        self._challenge: Optional[str] = None
        self._signed_in: set = set()
        self._sign_ins: Dict[str, _SignIn] = {}
        self._connection = 0                 # counts connections lost

    @property
    def url(self) -> str:
        return self._url

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_opening(self) -> bool:
        return self._opening

    def open(self) -> None:
        """Begin the TLS handshake. No-op if already open or opening."""
        self._stopped = False
        if self._connected or self._opening:
            return
        self._opening = True
        self._ws.open(QUrl(self._url))

    def close(self) -> None:
        """Close the socket for good: it is not reopened by itself."""
        self._stopped = True
        self._retry.stop()
        self._ws.close()

    def hold(self) -> None:
        """A subscription needs this relay: reopen it when it drops."""
        self._holds += 1

    def release(self) -> None:
        """A subscription is done with this relay."""
        self._holds = max(0, self._holds - 1)
        if not self._holds:
            self._retry.stop()

    def _reconnect_later(self) -> None:
        if self._stopped or not self._holds or self._retry.isActive():
            return
        self._retry.start(self._retry_ms)
        self._retry_ms = min(self._retry_ms * 2, RECONNECT_LONGEST_MS)

    def send(self, message: list) -> bool:
        """Serialize and send a JSON message. Returns False if not connected."""
        if not self._connected:
            return False
        # Compact JSON - relays parse anything legal, but this is what every
        # mainstream client emits and keeps the bytes small.
        text = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
        return self._ws.sendTextMessage(text) > 0

    # -- Qt slots -----------------------------------------------------------

    def _on_connected(self) -> None:
        self._opening = False
        self._connected = True
        self._retry_ms = RECONNECT_FIRST_MS
        self.connected.emit()

    def _on_disconnected(self) -> None:
        self._opening = False
        self._connected = False
        # A sign-in belongs to the connection: the next one asks again.
        # Requests hear of the lost connection first, so one waiting for a
        # sign-in counts the relay as lost, not as refusing.
        self._connection += 1
        self._challenge = None
        self._signed_in.clear()
        self.disconnected.emit()
        for key in list(self._sign_ins):
            self._end_sign_in(key, False, "relay disconnected")
        self._reconnect_later()

    def _on_text_message(self, text: str) -> None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return
        if not isinstance(parsed, list) or not parsed:
            return
        if parsed[0] == "AUTH" and len(parsed) >= 2 and isinstance(parsed[1], str):
            self._on_challenge(parsed[1])
        elif parsed[0] == "OK" and len(parsed) >= 3:
            self._on_sign_in_answer(parsed)
        self.message.emit(parsed)

    def _on_error(self, _code) -> None:
        self._opening = False
        self.error.emit(self._ws.errorString())
        if not self._connected:
            self._reconnect_later()     # an attempt that never connected

    # -- signing in (NIP-42) --------------------------------------------------

    def sign_in(self, signer, on_done: Callable[[bool, str], None]) -> None:
        """Sign in to this relay as ``signer``, for a request it refused
        with "auth-required:"; ``on_done(ok, reason)`` once.

        ``signer`` (nostr/relay_auth.py) says whose key it is (``pubkey``),
        whether this relay is one to sign in to (``may_sign_in``), and
        signs the answer to the challenge (``sign``). A key already
        signed in on this connection is done at once; a sign-in already
        under way for it is joined, so the signer is asked once."""
        key = signer.pubkey
        if not key or not signer.may_sign_in(self._url):
            on_done(False, NOT_CHOSEN)
            return
        if key in self._signed_in:
            on_done(True, "")
            return
        pending = self._sign_ins.get(key)
        if pending is not None:
            pending.waiters.append(on_done)
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda k=key: self._sign_in_ran_out(k))
        self._sign_ins[key] = _SignIn(signer, [on_done], timer)
        self.sign_in_started.emit()
        if self._challenge is None:
            timer.start(SIGN_IN_CHALLENGE_WAIT_MS)     # the relay sends it any moment
        else:
            self._ask_signer(key)

    def _on_challenge(self, challenge: str) -> None:
        self._challenge = challenge
        for key, pending in list(self._sign_ins.items()):
            if not pending.asked:
                self._ask_signer(key)

    def _ask_signer(self, key: str) -> None:
        pending = self._sign_ins[key]
        pending.asked = True
        pending.timer.stop()                 # the signer takes the time it takes
        challenge, connection = self._challenge, self._connection

        def signed(event: dict) -> None:
            if self._sign_ins.get(key) is not pending:
                return
            if self._connection != connection or not self.send(["AUTH", event]):
                self._end_sign_in(key, False, "relay disconnected")
                return
            pending.event_id = event.get("id")
            pending.timer.start(SIGN_IN_ANSWER_MS)

        def failed(reason: str) -> None:
            if self._sign_ins.get(key) is pending:
                self._end_sign_in(key, False, reason)

        pending.signer.sign(self._url, challenge, signed, failed)

    def _on_sign_in_answer(self, msg: list) -> None:
        for key, pending in list(self._sign_ins.items()):
            if pending.event_id is not None and msg[1] == pending.event_id:
                self._end_sign_in(key, bool(msg[2]), str(msg[3]) if len(msg) >= 4 else "")
                return

    def _sign_in_ran_out(self, key: str) -> None:
        pending = self._sign_ins.get(key)
        if pending is not None:
            self._end_sign_in(key, False, "the relay did not answer the sign-in"
                              if pending.asked else "the relay sent nothing to sign")

    def _end_sign_in(self, key: str, ok: bool, reason: str) -> None:
        pending = self._sign_ins.pop(key)
        pending.timer.stop()
        pending.timer.deleteLater()
        if ok:
            self._signed_in.add(key)
        self.sign_in_ended.emit(ok, reason, pending.event_id is not None)
        for done in pending.waiters:
            done(ok, reason)


# --------------------------------------------------------------------------- #
# PublishJob - one EVENT, N relays, eager-first-accept                        #
# --------------------------------------------------------------------------- #

class PublishJob(QObject):
    """Track one EVENT publish across N relays in parallel.

    Signals:
      first_accept(str)              - URL of the first relay that OK'd ok=True.
                                       Fires at most once. May never fire if
                                       every relay rejects.
      relay_result(str, bool, str)   - per-relay outcome: (url, ok, message).
      all_done(list)                 - list of PublishResult tuples in the
                                       order results landed. Fires exactly once.
    """

    first_accept = Signal(str)
    relay_result = Signal(str, bool, str)
    all_done = Signal(list)

    def __init__(
        self,
        pool: "RelayPool",
        urls: List[str],
        event: dict,
        timeout_ms: int = DEFAULT_PUBLISH_TIMEOUT_MS,
        parent: Optional[QObject] = None,
        *,
        sign_in=None,
    ) -> None:
        super().__init__(parent)
        if "id" not in event or "sig" not in event:
            raise ValueError("PublishJob requires a fully signed event")

        self._pool = pool
        # Who signs in when a relay asks before it takes the event, and the
        # relays asked so far (once each) and still signing in.
        self._sign_in = sign_in if sign_in is not None else pool.sign_in
        self._sign_in_asked: set[str] = set()
        self._signing_in: set[str] = set()
        self._event = event
        self._event_id = event["id"]
        self._timeout_ms = timeout_ms
        # Preserve order but dedupe (a caller might pass the same URL twice).
        self._urls: List[str] = list(dict.fromkeys(_normalize(u) for u in urls))
        self._pending: set[str] = set(self._urls)
        self._results: List[PublishResult] = []
        self._first_accept_emitted = False
        self._sent: set[str] = set()
        # Track our signal connections so we can disconnect cleanly on finish.
        self._connections: List[tuple[Relay, str, object]] = []

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._on_overall_timeout)

        if not self._urls:
            # Edge case: empty URL list - fire all_done on the next tick
            # so callers can wire up signals before the result lands.
            QTimer.singleShot(0, lambda: self.all_done.emit([]))
            return

        for url in self._urls:
            self._start_one(url)
        self._timer.start()

    # -- per-relay wiring ---------------------------------------------------

    def _start_one(self, url: str) -> None:
        relay = self._pool.get_or_create(url)

        msg_slot = lambda payload, u=url: self._on_message(u, payload)
        disc_slot = lambda u=url: self._on_disconnected(u)
        err_slot = lambda err, u=url: self._on_error(u, err)
        relay.message.connect(msg_slot)
        relay.disconnected.connect(disc_slot)
        relay.error.connect(err_slot)
        self._connections.append((relay, "message", msg_slot))
        self._connections.append((relay, "disconnected", disc_slot))
        self._connections.append((relay, "error", err_slot))

        if relay.is_connected:
            self._send(url)
            return

        conn_slot = lambda u=url: self._send(u)
        relay.connected.connect(conn_slot)
        self._connections.append((relay, "connected", conn_slot))
        relay.open()

    def _send(self, url: str) -> None:
        if url in self._sent or url not in self._pending:
            return
        self._sent.add(url)
        relay = self._pool.get_or_create(url)
        if not relay.send(["EVENT", self._event]):
            self._finish(url, False, _("send failed (not connected)"))

    # -- relay signal handlers ---------------------------------------------

    def _on_message(self, url: str, msg: list) -> None:
        if url not in self._pending:
            return
        # We only care about ["OK", <event_id>, <bool>, <message>] for this event.
        if len(msg) < 3 or msg[0] != "OK" or msg[1] != self._event_id:
            return
        ok = bool(msg[2])
        message = str(msg[3]) if len(msg) >= 4 else ""
        if (not ok and message.startswith(AUTH_REQUIRED) and self._sign_in is not None
                and url not in self._sign_in_asked):
            self._sign_in_asked.add(url)
            self._signing_in.add(url)
            self._pool.get_or_create(url).sign_in(
                self._sign_in, lambda done, _why, u=url, m=message: self._signed_in(u, done, m))
            return
        self._finish(url, ok, message)

    def _signed_in(self, url: str, ok: bool, refusal: str) -> None:
        """Signing in ended: send the event again once, or the relay's
        refusal stands."""
        self._signing_in.discard(url)
        if url not in self._pending:
            return
        if not ok:
            self._finish(url, False, refusal)
        elif not self._pool.get_or_create(url).send(["EVENT", self._event]):
            self._finish(url, False, _("send failed (not connected)"))
        elif not self._timer.isActive():
            self._timer.start()             # a full wait for the answer

    def _on_disconnected(self, url: str) -> None:
        if url in self._pending:
            self._finish(url, False, _("relay disconnected before ack"))

    def _on_error(self, url: str, err: str) -> None:
        if url in self._pending:
            self._finish(url, False, _("socket error: {error}").format(error=err))

    def _on_overall_timeout(self) -> None:
        # A relay still signing in waits for that (a signer app waits for
        # the person); it gets its full wait once it sent the event again.
        for url in list(self._pending - self._signing_in):
            self._finish(url, False, _("publish timeout"))

    # -- finalize -----------------------------------------------------------

    def _finish(self, url: str, ok: bool, message: str) -> None:
        if url not in self._pending:
            return
        self._pending.remove(url)
        self._results.append((url, ok, message))
        self.relay_result.emit(url, ok, message)
        if ok and not self._first_accept_emitted:
            self._first_accept_emitted = True
            self.first_accept.emit(url)
        if not self._pending:
            self._timer.stop()
            self._disconnect_all()
            self.all_done.emit(list(self._results))

    def _disconnect_all(self) -> None:
        for relay, sig_name, slot in self._connections:
            try:
                getattr(relay, sig_name).disconnect(slot)
            except (RuntimeError, TypeError):
                # Already disconnected, relay destroyed, etc. - harmless.
                pass
        self._connections.clear()


# --------------------------------------------------------------------------- #
# RelayPool - dict of Relay keyed by normalized URL                           #
# --------------------------------------------------------------------------- #

class RelayPool(QObject):
    """Process-wide cache of WebSocket connections.

    URLs are normalized (trailing slash stripped, lowercased scheme/host)
    so that callers using slightly different forms of the same URL share
    the same connection.

    Signals:
      signing_in(str)                 - signing in to this relay began (url)
      sign_in_failed(str, str, bool)  - it did not work: url, why, and
                                        whether a signed answer was sent
                                        (False: the signer did not sign)
    """

    signing_in = Signal(str)
    sign_in_failed = Signal(str, str, bool)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._relays: Dict[str, Relay] = {}
        # Who signs in when a relay asks (nostr/relay_auth.py), for every
        # request that does not name its own; None: nobody does.
        self.sign_in = None

    def get_or_create(self, url: str) -> Relay:
        normalized = _normalize(url)
        relay = self._relays.get(normalized)
        if relay is None:
            relay = Relay(normalized, parent=self)
            relay.sign_in_started.connect(lambda u=normalized: self.signing_in.emit(u))
            relay.sign_in_ended.connect(
                lambda ok, why, sent, u=normalized: self._sign_in_ended(u, ok, why, sent))
            self._relays[normalized] = relay
        return relay

    def _sign_in_ended(self, url: str, ok: bool, why: str, sent: bool) -> None:
        if not ok:
            self.sign_in_failed.emit(url, why, sent)

    def publish(
        self,
        urls: List[str],
        event: dict,
        timeout_ms: int = DEFAULT_PUBLISH_TIMEOUT_MS,
        *,
        sign_in=None,
    ) -> PublishJob:
        return PublishJob(self, urls, event, timeout_ms=timeout_ms, parent=self,
                          sign_in=sign_in)

    def subscribe(
        self,
        urls: List[str],
        filters: List[Dict[str, Any]],
        sub_id: Optional[str] = None,
        *,
        sign_in=None,
    ) -> "Subscription":
        return Subscription(self, urls, filters, sub_id=sub_id, parent=self,
                            sign_in=sign_in)

    def close_all(self) -> None:
        for relay in self._relays.values():
            relay.close()
        self._relays.clear()


# --------------------------------------------------------------------------- #
# Subscription - REQ/EVENT/EOSE/CLOSED lifecycle                              #
# --------------------------------------------------------------------------- #

class Subscription(QObject):
    """A live subscription across N relays.

    Signals:
      event(dict)               - one inner event from ["EVENT", sub_id, event]
      relay_event(str, dict)    - the same event with the relay that sent it
                                  (url, event), for a caller that has to
                                  know where something is kept
      eose()                    - every relay has signalled EOSE (initial
                                  backlog done); never fires when one of
                                  them closed or failed instead
      closed(str)               - at least one relay closed the sub with the
                                  given reason
      relay_eose(str)           - this relay ended its stored events (url)
      relay_closed(str, str)    - this relay closed the sub (url, reason)
      relay_failed(str, str)    - this relay could not be reached, or
                                  dropped, before it answered (url, reason)

    The per-relay signals let a caller tell "this relay has nothing" apart
    from "this relay never answered" (nostr/outbox/lookup.py): each relay
    ends in at most one of relay_eose, relay_closed and relay_failed.

    The subscription stays open until ``close()`` is called; new events
    matching the filter continue to fire ``event`` after EOSE. When a
    relay's socket drops and comes back, the REQ is sent again, so the
    subscription keeps receiving from it (a relay's end is still reported
    once, the first time).

    A relay that closes it with "auth-required:" is signed in to (once
    per connection) and asked again; only if that does not work is it
    reported closed, with the relay's reason.
    """

    event = Signal(dict)
    relay_event = Signal(str, dict)     # url, event
    eose = Signal()
    closed = Signal(str)
    relay_eose = Signal(str)            # url
    relay_closed = Signal(str, str)     # url, reason
    relay_failed = Signal(str, str)     # url, reason

    def __init__(
        self,
        pool: "RelayPool",
        urls: List[str],
        filters: List[Dict[str, Any]],
        sub_id: Optional[str] = None,
        parent: Optional[QObject] = None,
        *,
        sign_in=None,
    ) -> None:
        super().__init__(parent)
        self._pool = pool
        self._sign_in = sign_in if sign_in is not None else pool.sign_in
        self._sign_in_asked: set[str] = set()    # relays asked, this connection
        self._urls: List[str] = list(dict.fromkeys(_normalize(u) for u in urls))
        self._filters = list(filters)
        # 8 random bytes -> 16 hex chars; well under the 64-char NIP-01 cap.
        self._sub_id = sub_id or secrets.token_hex(8)
        self._eose_seen: set[str] = set()
        self._ended: set[str] = set()       # relays that answered, closed or failed
        self._eose_emitted = False
        self._closed = False
        self._connections: List[tuple[Relay, str, object]] = []

        for url in self._urls:
            self._start_one(url)

    @property
    def sub_id(self) -> str:
        return self._sub_id

    # -- per-relay wiring ---------------------------------------------------

    def _start_one(self, url: str) -> None:
        relay = self._pool.get_or_create(url)

        msg_slot = lambda payload, u=url: self._on_message(u, payload)
        err_slot = lambda err, u=url: self._on_relay_lost(u, f"socket error: {err}")
        disc_slot = lambda u=url: self._on_relay_lost(u, "disconnected before answering")
        relay.message.connect(msg_slot)
        relay.error.connect(err_slot)
        relay.disconnected.connect(disc_slot)
        self._connections.append((relay, "message", msg_slot))
        self._connections.append((relay, "error", err_slot))
        self._connections.append((relay, "disconnected", disc_slot))

        # Every (re)connection gets the REQ, not only the first.
        conn_slot = lambda u=url: self._reconnected(u)
        relay.connected.connect(conn_slot)
        self._connections.append((relay, "connected", conn_slot))
        relay.hold()
        if relay.is_connected:
            self._send_req(url)
        else:
            relay.open()

    def _reconnected(self, url: str) -> None:
        self._sign_in_asked.discard(url)     # a new connection signs in anew
        self._send_req(url)

    def _send_req(self, url: str) -> None:
        if self._closed:
            return
        relay = self._pool.get_or_create(url)
        if not relay.send(["REQ", self._sub_id] + self._filters):
            self._on_relay_lost(url, "send failed (not connected)")

    # -- relay signal handlers ---------------------------------------------

    def _on_message(self, url: str, msg: list) -> None:
        if self._closed:
            return
        if len(msg) < 2 or msg[1] != self._sub_id:
            return
        verb = msg[0]
        if verb == "EVENT" and len(msg) >= 3 and isinstance(msg[2], dict):
            self.event.emit(msg[2])
            self.relay_event.emit(url, msg[2])
        elif verb == "EOSE":
            if url not in self._ended:
                self._ended.add(url)
                self.relay_eose.emit(url)
            self._eose_seen.add(url)
            if not self._eose_emitted and self._eose_seen >= set(self._urls):
                self._eose_emitted = True
                self.eose.emit()
        elif verb == "CLOSED":
            reason = str(msg[2]) if len(msg) >= 3 else ""
            if (reason.startswith(AUTH_REQUIRED) and self._sign_in is not None
                    and url not in self._sign_in_asked):
                self._sign_in_asked.add(url)
                self._pool.get_or_create(url).sign_in(
                    self._sign_in, lambda ok, _why, u=url, r=reason: self._signed_in(u, ok, r))
                return
            self._report_closed(url, reason)

    def _signed_in(self, url: str, ok: bool, refusal: str) -> None:
        """Signing in ended: ask again, or the relay's refusal stands."""
        if self._closed:
            return
        if ok:
            self._send_req(url)
        else:
            self._report_closed(url, refusal)

    def _report_closed(self, url: str, reason: str) -> None:
        if url not in self._ended:
            self._ended.add(url)
            self.relay_closed.emit(url, reason)
        self.closed.emit(reason)

    def _on_relay_lost(self, url: str, reason: str) -> None:
        """A relay that will not answer this subscription any more."""
        if self._closed or url in self._ended:
            return
        self._ended.add(url)
        self.relay_failed.emit(url, reason)

    # -- close --------------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for url in self._urls:
            relay = self._pool.get_or_create(url)
            if relay.is_connected:
                relay.send(["CLOSE", self._sub_id])
            relay.release()
        for relay, sig_name, slot in self._connections:
            try:
                getattr(relay, sig_name).disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._connections.clear()


# --------------------------------------------------------------------------- #
# Utilities                                                                    #
# --------------------------------------------------------------------------- #

def _normalize(url: str) -> str:
    """The relay URL as nostr/outbox/policy.normalize_relay_url writes it
    (lowercased scheme and host, no default port, no trailing slash), so
    a URL the routing chose and the connection it gets are spelt alike.

    Anything that normalizer refuses is still kept apart by its own
    spelling: trailing slashes stripped, scheme and host lowercased, the
    path left alone (path-aware relays exist).
    """
    normalized = normalize_relay_url(url)
    if normalized is not None:
        return normalized
    s = url.strip().rstrip("/")
    # split scheme://host[/path]
    if "://" in s:
        scheme, rest = s.split("://", 1)
        if "/" in rest:
            host, path = rest.split("/", 1)
            return f"{scheme.lower()}://{host.lower()}/{path}"
        return f"{scheme.lower()}://{rest.lower()}"
    return s
