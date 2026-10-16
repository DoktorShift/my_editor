# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether Nostr is in use: the one signal the editor's features follow.

MyEditor is a local editor first. Nostr features (publishing, drafts,
mentions, media) show up once the person has chosen to use Nostr, which
means: an account is active (connected through a signer app, or kept on
this computer). Until then the editor shows nothing of Nostr beyond the
Nostr menu itself, which is the way in.

Every surface asks the same object instead of checking the profile store
its own way: :attr:`NostrState.active` now, and :attr:`NostrState.changed`
when it flips. The window calls :meth:`NostrState.refresh` wherever the
active account may have changed (connecting, switching, signing out,
creating or restoring an account); refreshing when nothing changed emits
nothing, so calling it often is free.

Whether the signer app answers right now is a different question (the
drafts panel and the publish flow handle an unreachable signer); this
one is only "has the person chosen Nostr".
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal


class NostrState(QObject):
    """``active`` while an account is in use; ``changed(bool)`` when that flips."""

    changed = Signal(bool)

    def __init__(self, profile_provider: Callable[[], Optional[object]],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._profile_provider = profile_provider
        self._active = self._read()

    def _read(self) -> bool:
        return self._profile_provider() is not None

    @property
    def active(self) -> bool:
        return self._active

    def refresh(self) -> None:
        """Look again; announce only a real change."""
        active = self._read()
        if active != self._active:
            self._active = active
            self.changed.emit(active)
