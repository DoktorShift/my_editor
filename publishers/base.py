"""Publisher protocol and shared dataclasses.

A ``Publisher`` is anything that can take a markdown body, a set of
targets, and either send immediately or schedule for later. The
protocol is intentionally tiny so that adding a new destination
(Discord, Mastodon, Bluesky, ...) is mostly UI work plus a thin
adapter over that platform's API.

Concrete dataclasses:

* :class:`PublishTarget` - a destination row in the picker (a chat, a
  channel, a timeline).
* :class:`PublishResult` - the outcome of one target after a send.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True)
class PublishTarget:
    """One destination row offered by a publisher."""

    id: str
    """Opaque per-publisher target id (e.g. Telegram ``chat_id`` as str)."""

    label: str
    """Human label shown in the picker row."""

    kind: str
    """Coarse type: ``"group"``, ``"channel"``, ``"dm"``, ``"topic"``, ..."""

    badges: tuple[str, ...] = field(default_factory=tuple)
    """Short tags shown after the label (e.g. ``("admin",)``)."""

    parent_id: Optional[str] = None
    """For nested targets (e.g. forum topic inside a supergroup)."""


@dataclass(frozen=True)
class PublishResult:
    """The outcome of one target's send attempt."""

    target_id: str
    ok: bool
    message: str
    """Human-readable success note or error reason."""

    permalink: Optional[str] = None
    """URL to view the sent message, if the platform supports it."""

    message_id: Optional[int] = None
    """Platform-native message id, where applicable."""


class Publisher(Protocol):
    """The minimum surface a destination must expose to the editor."""

    name: str
    """Stable machine name, e.g. ``"telegram"``."""

    display_name: str
    """Human label, e.g. ``"Telegram"``."""

    def is_configured(self) -> bool:
        """``True`` once the user has supplied any credentials."""
        ...

    def open_setup(self, parent: QWidget) -> None:
        """Open the publisher's setup / management dialog."""
        ...

    def open_publish(self, parent: QWidget, body_markdown: str) -> None:
        """Open the publisher's compose dialog with ``body_markdown``."""
        ...
