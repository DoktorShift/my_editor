"""Small dataclasses shared between the publisher, scheduler, and UI.

Kept separate from :mod:`publishers.telegram.settings` because they
are *transient* (live for the duration of one send) rather than
persisted to disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .settings import Chat


@dataclass(frozen=True)
class Target:
    """One destination chat plus optional forum topic."""

    chat: Chat
    topic_id: Optional[int] = None

    @property
    def label(self) -> str:
        base = self.chat.title or f"chat {self.chat.id}"
        if self.topic_id is not None:
            topic = self.chat.topic_by_id(self.topic_id)
            if topic is not None:
                return f"{base} / {topic.name}"
            return f"{base} / topic {self.topic_id}"
        return base


@dataclass(frozen=True)
class SendOptions:
    """Per-send toggles surfaced under the "More" disclosure."""

    silent: bool = False
    disable_link_preview: bool = False
    protect_content: bool = False
    pin_after_sending: bool = False
