# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How far one upload has got, in a form a progress indicator can show
honestly, as Apple's guidelines for progress indicators ask:

- Nothing is measured yet while the file is read or the signer app is
  asked: the indicator is indeterminate (``fraction`` is None) and says
  what it waits for. It turns determinate once bytes move, and never back.
- It is as accurate as the transport lets it be. Qt counts bytes as sent
  once they are handed to the system, so a small file reads "100 %" before
  the server has it, and copies to the other servers follow after that.
  So the bytes handed over fill only part of the sending step, and the
  rest of a step is filled only when a server confirms it.
- It keeps moving. Where nothing can be measured (a server receiving, a
  server copying from another), the step creeps on, paced by how long
  sending took, slower and slower, and never reaches its end unconfirmed.
- It evens out its pace: what is shown eases towards the estimate and
  never goes back. Nothing for a while, and ``stalled`` says so.

The whole upload is one indicator: the upload to the first server, then
one step for each copy to another server, which counts for less (the
servers copy among themselves). Pure Python with an injectable clock, so
the pacing is tested without waiting.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from i18n import _

# What the upload is doing, as an indicator shows it.
WAITING = "waiting"       # nothing to measure: being read, or the signer asked
SENDING = "sending"       # the bytes are on their way to the first server
COPYING = "copying"       # the first server has it; copies to the others follow
DONE = "done"

_SEND_UNITS = 2.0         # the upload, against
_COPY_UNITS = 1.0         # each copy to another server
_HANDED_OVER_SHARE = 0.85  # of the sending step, for the bytes handed to the system
_UNCONFIRMED_LIMIT = 0.97  # of a step, at most, before a server confirms it
_EASE_SECONDS = 0.25      # what is shown catches up with the estimate this fast
STALL_SECONDS = 20.0      # no news for this long: say it takes longer than usual


def _creep(elapsed: float, pace: float) -> float:
    """0 at the start, towards 1, never there: the share of a step that
    nothing measures, ``elapsed`` seconds in, at the pace of ``pace``."""
    return 1.0 - math.exp(-max(0.0, elapsed) / max(0.5, pace))


class UploadProgress:
    """The indicator of one upload, fed what the upload does."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        now = clock()
        self._phase = WAITING
        self._signer = False           # waiting for a signer app, not the file
        self._copies = 0
        self._copied = 0
        self._handed = 0.0             # share of the bytes handed to the system
        self._step_started = now
        self._handed_over_at: Optional[float] = None
        self._send_seconds = 1.0       # how long sending took, to pace the copies
        self._news_at = now
        self._shown = 0.0
        self._shown_at = now

    @property
    def phase(self) -> str:
        return self._phase

    # -- what the upload does ------------------------------------------------

    def expect(self, copies: int) -> None:
        """How many servers get a copy once the first has it, as planned
        before anything is sent, so the upload is only its share of the
        indicator from the start."""
        self._copies = max(0, copies)

    def signing(self, *, remote: bool) -> None:
        """The signature is asked for; ``remote``: of a signer app."""
        self._signer = remote and self._phase == WAITING
        self._news()

    def sending(self, sent: int, total: int) -> None:
        """``sent`` of ``total`` bytes handed to the system."""
        now = self._news()
        if self._phase == WAITING:
            self._phase, self._signer, self._step_started = SENDING, False, now
        if self._phase != SENDING or total <= 0:
            return
        self._handed = max(self._handed, min(1.0, sent / total))
        if self._handed >= 1.0 and self._handed_over_at is None:
            self._handed_over_at = now

    def retrying(self) -> None:
        """The upload starts again on another server. What is shown stays:
        an indicator does not go back."""
        self._handed, self._handed_over_at = 0.0, None
        self._step_started = self._news()

    def copying(self, copies: int) -> None:
        """A server has the file; ``copies`` other servers get it in turn."""
        now = self._news()
        if self._phase == SENDING:
            self._send_seconds = max(0.5, now - self._step_started)
        self._phase = COPYING if copies > 0 else DONE
        self._copies, self._copied = max(0, copies), 0
        self._signer, self._step_started = False, now

    def copied(self) -> None:
        """One copy is made, or refused: the next one starts."""
        self._copied = min(self._copies, self._copied + 1)
        self._step_started = self._news()

    def done(self) -> None:
        self._phase = DONE
        self._news()

    # -- what the indicator shows --------------------------------------------

    def fraction(self) -> Optional[float]:
        """What the indicator shows now, 0 to 1, eased and never going
        back; None while nothing can be measured (indeterminate)."""
        target = self._estimate()
        if target is None:
            return None
        now = self._clock()
        step = min(1.0, (now - self._shown_at) / _EASE_SECONDS)
        self._shown_at = now
        self._shown = max(self._shown, self._shown + (target - self._shown) * step)
        return self._shown

    def stalled(self) -> bool:
        """Whether nothing has happened for longer than usual."""
        return (self._phase in (SENDING, COPYING)
                and self._clock() - self._news_at > STALL_SECONDS)

    def words(self) -> str:
        """What the indicator says, briefly: what it waits for, or how far
        the upload has got."""
        fraction = self.fraction()
        if fraction is None:
            return _("Waiting for your signer app…") if self._signer else _("Preparing…")
        if self.stalled():
            return _("Taking longer than usual…")
        percent = min(99, int(fraction * 100)) if self._phase != DONE else 100
        if self._phase == COPYING:
            return _("Copying… {percent} %").format(percent=percent)
        return _("Uploading… {percent} %").format(percent=percent)

    # -- internals -----------------------------------------------------------

    def _news(self) -> float:
        self._news_at = self._clock()
        return self._news_at

    def _estimate(self) -> Optional[float]:
        if self._phase == WAITING:
            return None
        if self._phase == DONE:
            return 1.0
        now = self._clock()
        total = _SEND_UNITS + _COPY_UNITS * self._copies
        if self._phase == SENDING:
            if self._handed_over_at is None:
                step = _HANDED_OVER_SHARE * self._handed
            else:
                handing = self._handed_over_at - self._step_started
                step = _HANDED_OVER_SHARE + (_UNCONFIRMED_LIMIT - _HANDED_OVER_SHARE) * _creep(
                    now - self._handed_over_at, handing)
            return _SEND_UNITS * step / total
        step = _UNCONFIRMED_LIMIT * _creep(now - self._step_started, self._send_seconds)
        return (_SEND_UNITS + _COPY_UNITS * (self._copied + step)) / total
