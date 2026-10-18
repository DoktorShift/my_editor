# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The progress of an upload, as its indicator shows it (Apple's
guidelines for progress indicators): indeterminate until something can be
measured, never at its end before a server confirms, always moving, never
going back, and saying so when it takes longer than usual."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nostr.blossom import upload_progress  # noqa: E402
from nostr.blossom.upload_progress import UploadProgress  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def pass_(self, seconds: float) -> None:
        self.now += seconds


def meter(copies: int = 1):
    clock = Clock()
    progress = UploadProgress(clock=clock)
    progress.expect(copies)
    return progress, clock


def settled(progress, clock, seconds: float = 1.0) -> float:
    """What is shown once the easing has caught up."""
    clock.pass_(seconds)
    progress.fraction()
    clock.pass_(0.3)
    return progress.fraction()


def test_nothing_measured_yet_is_indeterminate_and_says_what_it_waits_for():
    progress, _clock = meter()
    assert progress.fraction() is None
    assert progress.words() == "Preparing…"
    progress.signing(remote=True)
    assert progress.fraction() is None
    assert progress.words() == "Waiting for your signer app…"


def test_bytes_handed_over_at_once_do_not_fill_the_indicator():
    """A small file is "sent" the moment it is handed to the system: the
    indicator jumped from 0 to 100 % and then waited on the servers."""
    progress, clock = meter(copies=1)
    progress.sending(1000, 1000)
    first = progress.fraction()
    assert first < 0.1                                     # eased, not a jump
    shown = settled(progress, clock, 0.0)
    assert shown < 2 / 3                                   # the copy is still to come
    assert progress.words().startswith("Uploading…")


def test_it_keeps_moving_while_the_server_receives_but_never_claims_the_end():
    progress, clock = meter(copies=0)
    progress.sending(0, 1000)
    clock.pass_(2.0)
    progress.sending(1000, 1000)                           # handed over after 2 s
    readings = [settled(progress, clock, 1.0) for _ in range(30)]
    assert all(b > a for a, b in zip(readings, readings[1:]))
    assert readings[-1] < 0.98


def test_copies_are_steps_of_the_same_indicator_and_each_ends_when_confirmed():
    progress, clock = meter(copies=2)
    progress.sending(1000, 1000)
    clock.pass_(1.0)
    progress.copying(2)
    sent = settled(progress, clock, 0.0)
    assert 0.45 < sent < 0.6                               # the upload is half of it
    creeping = settled(progress, clock, 3.0)
    assert sent < creeping < 0.75                          # the first copy, unconfirmed
    progress.copied()
    assert settled(progress, clock, 0.0) >= 0.75           # the first copy, confirmed
    assert progress.words().startswith("Copying…")
    progress.copied()
    progress.done()
    assert settled(progress, clock, 0.0) == 1.0


def test_it_never_goes_back_when_another_server_is_tried():
    progress, clock = meter(copies=1)
    progress.sending(800, 1000)
    before = settled(progress, clock, 0.0)
    progress.retrying()
    progress.sending(0, 1000)
    assert settled(progress, clock, 0.0) >= before


def test_a_long_silence_is_said_plainly():
    progress, clock = meter()
    progress.sending(500, 1000)
    clock.pass_(upload_progress.STALL_SECONDS + 1)
    assert progress.stalled()
    assert progress.words() == "Taking longer than usual…"
    progress.sending(600, 1000)
    assert not progress.stalled()
