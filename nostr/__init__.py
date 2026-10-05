# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nostr protocol primitives and NIP-46 client for the minimal text editor.

This package implements the subset of Nostr needed to publish kind 1 (short notes)
and kind 30023 (long-form articles) through a remote signer (Amber, nsec.app, etc.)
via NIP-46. No private key ever lives inside the editor process.
"""

# NIP-89-style client identifier. Honoured by readers like Coracle and others
# to display "Published from MyEditor" under the note. The publisher attaches
# ["client", CLIENT_NAME] to every event it builds; remove the tag there to opt
# out per-event.
CLIENT_NAME: str = "MyEditor"


# General-purpose relays MyEditor uses when nothing better is known.
#
# The same list as nostr/outbox/defaults.py FALLBACK_RELAYS, which is
# where relay choices are made and checked (tests/smoke_relay_qualify.py);
# a test keeps the two identical. It cannot simply be imported from there:
# the outbox package imports this module. New code asks nostr/outbox for a
# set by role instead of using this constant.
#
# Frozen as a tuple so a future caller can't accidentally mutate the shared
# constant; any override must be a deliberate copy.
DEFAULT_RELAYS: tuple[str, ...] = (
    "wss://relay.primal.net",
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.ditto.pub",
    "wss://relay.nostr.com",
)
