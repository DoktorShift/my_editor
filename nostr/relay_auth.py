# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signing in as the account to its relays that ask for it (NIP-42).

Some relays refuse a request until you sign in: they answer it with a
reason that starts "auth-required:", having given the connection a
challenge to sign. nostr/relay.py does the asking and the sending; this
decides whether a relay is one the account signs in to, and has the
account's signer sign the answer.

Signing in tells the relay who you are. So MyEditor signs in only when a
relay asks (never unasked), and as the account only to relays the
account itself chose: its relay list (NIP-65), the relays it keeps its
drafts on (NIP-37), those it still reads drafts left behind on, those
being chosen for drafts right now, and the relays a membership gives it
(RelayDirectory.own_relays, and ``entitled``). Any other relay that asks
is told nothing, and its refusal stands.

The channel to a signer app (NIP-46) signs in with the channel's own key
instead (relay.KeySignIn, used by nostr/bunker.py).
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from .bunker import is_signer_answer
from .local_signer import LOCAL
from .outbox.policy import normalize_relay_url
from .outbox.writer import signed_matches
from .relay import sign_in_event

# Why a sign-in did not happen, when the signer app was asked and said no.
DECLINED = "the signer app declined the sign-in"


class AccountSignIn:
    """Signs in as the account in use, with its signer (a key kept here or
    a signer app), to the relays that account chose.

    ``profile()`` is the account in use (None: none); ``entitled()`` the
    relays a membership gives it. A failure is the signer's own reason,
    or DECLINED when a signer app answered no."""

    def __init__(self, *, profile: Callable[[], Optional[object]], session_pool, directory,
                 entitled: Callable[[], Sequence[str]] = lambda: ()) -> None:
        self._profile = profile
        self._session_pool = session_pool
        self._directory = directory
        self._entitled = entitled

    @property
    def pubkey(self) -> str:
        profile = self._profile()
        return profile.user_pubkey.lower() if profile is not None else ""

    def may_sign_in(self, url: str) -> bool:
        author = self.pubkey
        if not author:
            return False
        chosen = self._directory.own_relays(author) + list(self._entitled() or ())
        return _spelt(url) in {_spelt(relay) for relay in chosen if relay}

    def sign(self, url: str, challenge: str, on_signed: Callable[[dict], None],
             on_failed: Callable[[str], None]) -> None:
        profile = self._profile()
        if profile is None:
            on_failed("no account in use")
            return
        unsigned = sign_in_event(url, challenge)
        author = profile.user_pubkey.lower()
        with_app = getattr(profile, "signer", "remote") != LOCAL

        def signed(event: dict) -> None:
            # The answer names this relay and challenge, as this account,
            # or it is not sent.
            if signed_matches(event, unsigned, author):
                on_signed(event)
            else:
                on_failed("the signer signed something other than what was asked")

        def refused(reason: str) -> None:
            on_failed(DECLINED if with_app and is_signer_answer(reason) else reason)

        self._session_pool.get(
            profile, lambda client: client.sign_event(unsigned, on_success=signed,
                                                      on_failure=refused),
            on_failed)


def _spelt(url: str) -> str:
    return normalize_relay_url(url) or url
