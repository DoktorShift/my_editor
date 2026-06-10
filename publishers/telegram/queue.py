"""Persistent queue of client-side scheduled Telegram posts.

The Bot API has no server-side scheduling for bots, so any "send at
9am tomorrow" instruction is held in this local queue and dispatched
by :class:`publishers.telegram.scheduler.TelegramScheduler` when its
``QTimer`` fires.

Queue entries are immutable after creation except for status, attempt
counts, and per-target delivery records. The store is the source of
truth - there is no "ask Telegram what's pending" because that API
does not exist for bots.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from ._atomic import SETTINGS_DIR, load_json, save_json


QUEUE_FILE: Path = SETTINGS_DIR / "telegram_scheduled.json"

SCHEMA_VERSION: int = 1

# Status state machine
STATUS_PENDING: str = "pending"
STATUS_SENDING: str = "sending"
STATUS_SENT: str = "sent"
STATUS_PARTIAL: str = "partial"      # some targets succeeded, others failed
STATUS_FAILED: str = "failed"
STATUS_CANCELLED: str = "cancelled"
STATUS_ORPHANED: str = "orphaned"    # bot was removed; do not fire

VALID_STATUSES: frozenset[str] = frozenset({
    STATUS_PENDING, STATUS_SENDING, STATUS_SENT, STATUS_PARTIAL,
    STATUS_FAILED, STATUS_CANCELLED, STATUS_ORPHANED,
})

MAX_ATTEMPTS: int = 5


@dataclass
class ScheduledTarget:
    """One target within a scheduled entry."""

    chat_id: int
    topic_id: Optional[int] = None


@dataclass
class DeliveryRecord:
    """Per-target outcome captured after the scheduler fires."""

    chat_id: int
    ok: bool
    message_id: int = 0
    permalink: str = ""
    sent_at: int = 0
    error: str = ""


@dataclass
class ScheduledPost:
    """One post the user has scheduled for a future fire time."""

    id: str
    bot_id: str
    targets: List[ScheduledTarget]
    body_markdown: str
    scheduled_for: int                      # unix seconds
    created_at: int
    status: str = STATUS_PENDING
    attempts: int = 0
    last_error: str = ""
    delivered: List[DeliveryRecord] = field(default_factory=list)
    silent: bool = False
    disable_link_preview: bool = False
    protect_content: bool = False
    pin_after_sending: bool = False


# ---------------------------------------------------------------------------- #
# Store                                                                        #
# ---------------------------------------------------------------------------- #


class ScheduledQueue:
    """Atomic JSON-backed queue. All mutations write through."""

    def __init__(self, path: Path = QUEUE_FILE) -> None:
        self._path = path
        self._posts: List[ScheduledPost] = []
        self._load()

    # -- read -------------------------------------------------------- #

    def all(self) -> List[ScheduledPost]:
        return list(self._posts)

    def by_id(self, post_id: str) -> Optional[ScheduledPost]:
        for p in self._posts:
            if p.id == post_id:
                return p
        return None

    def pending(self) -> List[ScheduledPost]:
        return [p for p in self._posts if p.status == STATUS_PENDING]

    def next_due(self, *, before: Optional[int] = None) -> Optional[ScheduledPost]:
        """Earliest pending post whose ``scheduled_for`` <= ``before``.

        If ``before`` is ``None``, returns the earliest pending post
        regardless of its time (caller computes the wait).
        """
        candidates = [p for p in self._posts if p.status == STATUS_PENDING]
        if not candidates:
            return None
        candidates.sort(key=lambda p: p.scheduled_for)
        if before is None:
            return candidates[0]
        if candidates[0].scheduled_for <= before:
            return candidates[0]
        return None

    def by_bot(self, bot_id: str) -> List[ScheduledPost]:
        return [p for p in self._posts if p.bot_id == bot_id]

    # -- mutate ------------------------------------------------------ #

    def add(
        self,
        *,
        bot_id: str,
        targets: List[ScheduledTarget],
        body_markdown: str,
        scheduled_for: int,
        silent: bool = False,
        disable_link_preview: bool = False,
        protect_content: bool = False,
        pin_after_sending: bool = False,
    ) -> ScheduledPost:
        if not targets:
            raise ValueError("at least one target is required")
        if not bot_id:
            raise ValueError("bot_id is required")
        post = ScheduledPost(
            id="s_" + secrets.token_hex(6),
            bot_id=bot_id,
            targets=list(targets),
            body_markdown=body_markdown,
            scheduled_for=int(scheduled_for),
            created_at=int(time.time()),
            silent=silent,
            disable_link_preview=disable_link_preview,
            protect_content=protect_content,
            pin_after_sending=pin_after_sending,
        )
        self._posts.append(post)
        self._save()
        return post

    def cancel(self, post_id: str) -> None:
        post = self.by_id(post_id)
        if post is None or post.status not in (STATUS_PENDING, STATUS_SENDING):
            return
        post.status = STATUS_CANCELLED
        self._save()

    def reschedule(self, post_id: str, scheduled_for: int) -> None:
        """User-initiated reschedule.

        Allowed only from terminal-ish states; the scheduler uses
        :meth:`retry_after` for internal back-off because that can fire
        from ``SENDING``.
        """
        post = self.by_id(post_id)
        if post is None or post.status not in (STATUS_PENDING, STATUS_FAILED):
            return
        post.scheduled_for = int(scheduled_for)
        post.status = STATUS_PENDING
        post.attempts = 0
        post.last_error = ""
        self._save()

    def retry_after(self, post_id: str, delay_seconds: int) -> None:
        """Scheduler-only retry hook.

        Flips the entry back to ``pending`` with a new ``scheduled_for``
        equal to ``now + delay_seconds``. Unlike :meth:`reschedule`, this
        is allowed from ``SENDING`` (the entry we just attempted) and
        does NOT reset ``attempts`` or ``last_error`` because those are
        the back-off signals.
        """
        post = self.by_id(post_id)
        if post is None:
            return
        post.scheduled_for = int(time.time()) + max(1, int(delay_seconds))
        post.status = STATUS_PENDING
        self._save()

    def orphan_bot_posts(self, bot_id: str) -> int:
        """Mark every pending post for a removed bot as orphaned.

        Returns the count of orphaned entries.
        """
        n = 0
        for p in self._posts:
            if p.bot_id == bot_id and p.status == STATUS_PENDING:
                p.status = STATUS_ORPHANED
                n += 1
        if n:
            self._save()
        return n

    def mark_sending(self, post_id: str) -> None:
        post = self.by_id(post_id)
        if post is None:
            return
        post.status = STATUS_SENDING
        post.attempts += 1
        self._save()

    def record_delivery(
        self,
        post_id: str,
        *,
        chat_id: int,
        ok: bool,
        message_id: int = 0,
        permalink: str = "",
        error: str = "",
    ) -> None:
        post = self.by_id(post_id)
        if post is None:
            return
        # Replace any prior record for this chat (retries).
        post.delivered = [d for d in post.delivered if d.chat_id != chat_id]
        post.delivered.append(
            DeliveryRecord(
                chat_id=chat_id,
                ok=ok,
                message_id=int(message_id) if ok else 0,
                permalink=permalink,
                sent_at=int(time.time()) if ok else 0,
                error=error,
            )
        )
        self._save()

    def finalize(self, post_id: str, *, last_error: str = "") -> None:
        """Set the terminal status based on the delivery records."""
        post = self.by_id(post_id)
        if post is None:
            return
        oks = sum(1 for d in post.delivered if d.ok)
        if oks == 0 and post.attempts >= MAX_ATTEMPTS:
            post.status = STATUS_FAILED
        elif oks == 0:
            post.status = STATUS_PENDING       # leave for retry
        elif oks == len(post.targets):
            post.status = STATUS_SENT
        else:
            post.status = STATUS_PARTIAL
        if last_error:
            post.last_error = last_error
        self._save()

    def delete(self, post_id: str) -> None:
        before = len(self._posts)
        self._posts = [p for p in self._posts if p.id != post_id]
        if len(self._posts) != before:
            self._save()

    # -- internals --------------------------------------------------- #

    def _load(self) -> None:
        data = load_json(self._path)
        if not isinstance(data, dict):
            return
        if data.get("version") != SCHEMA_VERSION:
            return
        for raw in data.get("posts") or []:
            post = _post_from_dict(raw)
            if post is not None:
                self._posts.append(post)

    def _save(self) -> None:
        save_json(
            self._path,
            {
                "version": SCHEMA_VERSION,
                "posts": [_post_to_dict(p) for p in self._posts],
            },
        )


# ---------------------------------------------------------------------------- #
# Serialization helpers                                                        #
# ---------------------------------------------------------------------------- #


def _post_to_dict(p: ScheduledPost) -> dict:
    return {
        "id": p.id,
        "bot_id": p.bot_id,
        "targets": [asdict(t) for t in p.targets],
        "body_markdown": p.body_markdown,
        "scheduled_for": p.scheduled_for,
        "created_at": p.created_at,
        "status": p.status,
        "attempts": p.attempts,
        "last_error": p.last_error,
        "delivered": [asdict(d) for d in p.delivered],
        "silent": p.silent,
        "disable_link_preview": p.disable_link_preview,
        "protect_content": p.protect_content,
        "pin_after_sending": p.pin_after_sending,
    }


def _post_from_dict(raw: dict) -> Optional[ScheduledPost]:
    try:
        targets = [
            ScheduledTarget(
                chat_id=int(t["chat_id"]),
                topic_id=int(t["topic_id"]) if t.get("topic_id") else None,
            )
            for t in (raw.get("targets") or [])
        ]
        status = str(raw.get("status") or STATUS_PENDING)
        if status not in VALID_STATUSES:
            status = STATUS_PENDING
        post = ScheduledPost(
            id=str(raw["id"]),
            bot_id=str(raw["bot_id"]),
            targets=targets,
            body_markdown=str(raw.get("body_markdown") or ""),
            scheduled_for=int(raw["scheduled_for"]),
            created_at=int(raw.get("created_at") or 0),
            status=status,
            attempts=int(raw.get("attempts") or 0),
            last_error=str(raw.get("last_error") or ""),
            silent=bool(raw.get("silent") or False),
            disable_link_preview=bool(raw.get("disable_link_preview") or False),
            protect_content=bool(raw.get("protect_content") or False),
            pin_after_sending=bool(raw.get("pin_after_sending") or False),
        )
    except (KeyError, TypeError, ValueError):
        return None
    for d_raw in raw.get("delivered") or []:
        try:
            post.delivered.append(
                DeliveryRecord(
                    chat_id=int(d_raw["chat_id"]),
                    ok=bool(d_raw.get("ok") or False),
                    message_id=int(d_raw.get("message_id") or 0),
                    permalink=str(d_raw.get("permalink") or ""),
                    sent_at=int(d_raw.get("sent_at") or 0),
                    error=str(d_raw.get("error") or ""),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return post
