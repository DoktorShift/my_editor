# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fakes for the nostr/outbox tests: relays, a signer, and lookups.

Signatures are real (throwaway keys), because every rule under test
starts with "only a validly signed event counts".
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from nostr import crypto, events
from nostr.outbox.lookup import Lookup
from nostr.outbox.policy import LookupState

SK = bytes.fromhex("2a" * 32)
PK = crypto.get_public_key(SK).hex()
OTHER_SK = bytes.fromhex("3b" * 32)
OTHER_PK = crypto.get_public_key(OTHER_SK).hex()
NOW = 1_800_000_000


def signed(kind: int, tags=None, content: str = "", *, sk: bytes = SK,
           created_at: int = NOW - 100) -> dict:
    return events.sign_event({"kind": kind, "content": content, "tags": tags or [],
                              "created_at": created_at}, sk)


def settle(rounds: int = 6) -> None:
    for _ in range(rounds):
        QApplication.processEvents()


class Profile:
    def __init__(self, pubkey: str = PK):
        self.user_pubkey = pubkey
        self.bunker_relays = []


class FakeClient:
    def __init__(self, *, sk: bytes = SK, fail: Optional[str] = None, tamper=None):
        self.sk = sk
        self.fail = fail
        self.tamper = tamper
        self.requests: List[dict] = []

    def sign_event(self, unsigned, on_success, on_failure, **_kw):
        self.requests.append(dict(unsigned))
        if self.fail:
            on_failure(self.fail)
            return
        event = events.sign_event(dict(unsigned), self.sk)
        if self.tamper:
            event = self.tamper(event)
        on_success(event)


class FakeSessionPool:
    def __init__(self, client: FakeClient):
        self.client = client

    def get(self, profile, on_ready, on_error):
        on_ready(self.client)


class FakeJob(QObject):
    all_done = Signal(list)


class FakeSubscription(QObject):
    event = Signal(dict)
    eose = Signal()
    closed = Signal(str)
    relay_eose = Signal(str)
    relay_closed = Signal(str, str)

    def close(self):
        pass


class FakePool:
    """Relays that accept (or refuse) publishes and remember what they kept."""

    def __init__(self, *, refuse=(), keep=True):
        self.refuse = set(refuse)
        self.keep = keep
        self.published: List[tuple] = []
        self.stored: Dict[str, dict] = {}
        self.subscriptions: List[tuple] = []

    def publish(self, urls, event):
        self.published.append((list(urls), event))
        results = [(u, u not in self.refuse, "" if u not in self.refuse else "blocked")
                   for u in urls]
        if self.keep and any(ok for _u, ok, _m in results):
            self.stored[event["id"]] = event
        job = FakeJob()
        QTimer.singleShot(0, lambda: job.all_done.emit(results))
        return job

    def subscribe(self, urls, filters):
        sub = FakeSubscription()
        self.subscriptions.append((list(urls), filters))

        def answer():
            for flt in filters:
                for event_id in flt.get("ids", []):
                    if event_id in self.stored:
                        sub.event.emit(self.stored[event_id])
            sub.eose.emit()
        QTimer.singleShot(0, answer)
        return sub


class FakeQuery:
    """Stands in for lookup.fetch_replaceable: answers from a table by kind."""

    def __init__(self, answers: Optional[Dict[int, Lookup]] = None):
        self.answers = answers or {}
        self.calls: List[dict] = []

    def __call__(self, pool, relays, *, kind, author, on_done, timeout_ms=6000, parent=None):
        self.calls.append({"relays": list(relays), "kind": kind, "author": author})
        on_done(self.answers.get(kind, Lookup(LookupState.UNKNOWN)))


def found(event: dict) -> Lookup:
    return Lookup(LookupState.FOUND, event=event, answered=("wss://a",), asked=("wss://a",))


ABSENT = Lookup(LookupState.ABSENT, answered=("wss://purplepag.es", "wss://nos.lol"))
UNKNOWN = Lookup(LookupState.UNKNOWN)
