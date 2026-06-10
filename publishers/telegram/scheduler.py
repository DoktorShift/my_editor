"""QTimer-driven dispatcher for the local scheduled-post queue.

Started once by the main window on application launch. Holds a single
``QTimer`` aimed at the next due post; when it fires, it builds a
:class:`TelegramPublishJob` for that post and updates the queue
entry's status / delivery records as the job emits results.

The Bot API has no server-side scheduling for bots, so the editor's
process must be alive at the scheduled instant. Entries that were due
while the editor was closed fire on the next launch with the
``status_changed`` signal carrying a "sent N minutes late" note - the
UI surfaces it in the queue dialog.

Retry policy: on partial / total failure, the entry returns to
``pending`` with an incremented ``attempts`` counter. After
:data:`MAX_ATTEMPTS` failures the entry is finalized as ``failed`` and
the user is notified via :attr:`event` (``"failed"``).
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer, Signal

from .base_types import SendOptions, Target
from .bots import BotRegistry
from .file_ids import FileIdCache
from .publisher import PublishResult, TelegramPublishJob
from .queue import (
    MAX_ATTEMPTS,
    STATUS_FAILED,
    STATUS_PARTIAL,
    STATUS_PENDING,
    STATUS_SENT,
    ScheduledPost,
    ScheduledQueue,
)
from .settings import TelegramSettings


# Earliest we'll re-arm after a fire (clamps tight back-to-back posts).
_MIN_REARM_MS: int = 250

# Cap on QTimer.singleShot's interval. Beyond this we re-arm in chunks
# because Qt's int-ms parameter overflows around 24 days.
_MAX_TIMER_MS: int = 23 * 60 * 60 * 1000


class TelegramScheduler(QObject):
    """Owns the per-process timer for due scheduled posts."""

    # ``("late_sent" | "sent" | "partial" | "failed", post_id, human note)``
    event = Signal(str, str, str)

    def __init__(
        self,
        *,
        settings: TelegramSettings,
        queue: ScheduledQueue,
        bots: BotRegistry,
        file_ids: FileIdCache,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._queue = queue
        self._bots = bots
        self._file_ids = file_ids
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_tick)
        self._active_job: Optional[TelegramPublishJob] = None
        self._active_post_id: str = ""

    # -- lifecycle --------------------------------------------------- #

    def start(self) -> None:
        """Arm the timer for the next due post (if any)."""
        self._arm()

    def stop(self) -> None:
        self._timer.stop()

    def kick(self) -> None:
        """Re-arm after an external mutation (add / cancel / reschedule)."""
        self._arm()

    # -- internals --------------------------------------------------- #

    def _arm(self) -> None:
        if self._active_job is not None:
            return  # busy; the active job's completion re-arms.
        post = self._queue.next_due(before=None)
        if post is None:
            self._timer.stop()
            return
        now = int(time.time())
        delay_s = max(0, post.scheduled_for - now)
        delay_ms = min(_MAX_TIMER_MS, max(_MIN_REARM_MS, delay_s * 1000))
        if delay_s == 0:
            # Already due (or overdue).
            delay_ms = _MIN_REARM_MS
        self._timer.start(delay_ms)

    def _on_tick(self) -> None:
        post = self._queue.next_due(before=int(time.time()))
        if post is None:
            self._arm()
            return
        self._fire(post)

    def _fire(self, post: ScheduledPost) -> None:
        bot = self._settings.bot_by_id(post.bot_id)
        api = self._bots.api_for(post.bot_id)
        if bot is None or api is None:
            # Bot is gone: orphan the entry and move on.
            self._queue.orphan_bot_posts(post.bot_id)
            self.event.emit("failed", post.id, "Bot is no longer configured.")
            self._arm()
            return

        targets = []
        for st in post.targets:
            chat = bot.chat_by_id(st.chat_id)
            if chat is None:
                # Build a minimal Chat record so the send can still try.
                from .settings import Chat as _Chat
                chat = _Chat(id=st.chat_id, type="", title=f"chat {st.chat_id}")
            targets.append(Target(chat=chat, topic_id=st.topic_id))

        self._queue.mark_sending(post.id)
        self._active_post_id = post.id
        late_seconds = max(0, int(time.time()) - post.scheduled_for)
        if late_seconds > 60:
            mins = late_seconds // 60
            self.event.emit(
                "late_sent", post.id,
                f"Sent {mins} minute{'s' if mins != 1 else ''} late "
                "(editor was closed at the scheduled time).",
            )

        job = TelegramPublishJob(
            api=api,
            settings=self._settings,
            bot_id=post.bot_id,
            body_markdown=post.body_markdown,
            targets=targets,
            options=SendOptions(
                silent=post.silent,
                disable_link_preview=post.disable_link_preview,
                protect_content=post.protect_content,
                pin_after_sending=post.pin_after_sending,
            ),
            file_ids=self._file_ids,
            parent=self,
        )
        job.target_done.connect(self._on_target_done)
        job.completed.connect(self._on_job_completed)
        job.failed.connect(self._on_job_failed)
        self._active_job = job
        job.start()

    def _on_target_done(self, result: PublishResult) -> None:
        if not self._active_post_id:
            return
        self._queue.record_delivery(
            self._active_post_id,
            chat_id=result.chat_id,
            ok=result.ok,
            message_id=result.message_id,
            permalink=result.permalink,
            error="" if result.ok else result.message,
        )

    def _on_job_completed(self, results: list) -> None:
        post_id = self._active_post_id
        self._active_post_id = ""
        if self._active_job is not None:
            self._active_job.deleteLater()
            self._active_job = None
        post = self._queue.by_id(post_id)
        if post is None:
            self._arm()
            return
        oks = sum(1 for r in results if r.ok)
        if oks == 0 and post.attempts >= MAX_ATTEMPTS:
            self._queue.finalize(post_id, last_error="all attempts failed")
            self.event.emit("failed", post_id, "All retries failed.")
        elif oks == 0:
            # Schedule a retry with exponential back-off, capped at 30 min.
            backoff = min(1800, 30 * (2 ** (post.attempts - 1)))
            self._queue.retry_after(post_id, backoff)
            self.event.emit("failed", post_id, f"Send failed; retrying in {backoff}s.")
        elif oks == len(results):
            self._queue.finalize(post_id)
            self.event.emit("sent", post_id, f"Sent to {oks} chats.")
        else:
            self._queue.finalize(post_id)
            self.event.emit(
                "partial", post_id,
                f"Sent to {oks} of {len(results)} chats.",
            )
        self._arm()

    def _on_job_failed(self, reason: str) -> None:
        post_id = self._active_post_id
        self._active_post_id = ""
        if self._active_job is not None:
            self._active_job.deleteLater()
            self._active_job = None
        if not post_id:
            self._arm()
            return
        post = self._queue.by_id(post_id)
        if post is None:
            self._arm()
            return
        if post.attempts >= MAX_ATTEMPTS:
            self._queue.finalize(post_id, last_error=reason)
            self.event.emit("failed", post_id, reason)
        else:
            backoff = min(1800, 30 * (2 ** (post.attempts - 1)))
            self._queue.retry_after(post_id, backoff)
            self.event.emit(
                "failed", post_id,
                f"{reason}; retrying in {backoff}s.",
            )
        self._arm()
