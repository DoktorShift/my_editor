"""End-to-end send job for one composition to N targets.

A ``TelegramPublishJob`` is constructed with:

* one bot id
* one body markdown string
* a sequence of ``(chat, topic_id)`` targets
* per-send option flags (silent, no-preview, protect, pin)

It walks targets serially (Telegram's per-chat rate limit is the
binding constraint; serial keeps the progress UI honest and avoids
hitting the 20-msg/min per-group cap on accidental bursts).

Signals (fired on the Qt event loop):

  ``status_changed(str)``                 - human-readable progress.
  ``target_started(int)``                 - chat_id about to be sent to.
  ``target_done(PublishResult)``          - one chat finished.
  ``completed(list[PublishResult])``      - terminal.
  ``failed(str)``                         - terminal alternative (job-level).

Cancellation is best-effort: ``cancel()`` stops scheduling further
targets but lets the currently in-flight send finish.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QObject, QTimer, Signal

from .api import BotApi
from .base_types import SendOptions, Target  # local re-exports
from .errors import ApiError, StaleFileId
from .file_ids import FileIdCache, sha256_file
from .format import CAPTION_LIMIT, Formatted, Photo, format_for_send
from .permalinks import permalink_for
from .settings import Chat, TelegramSettings


# Defensive delay between targets. Below all of Telegram's documented
# bot rate limits (30/sec global broadcast, 1/sec per chat, 20/min per
# group). Cheap insurance against accidental bursts.
_INTER_TARGET_DELAY_MS: int = 200


@dataclass
class PublishResult:
    """Per-chat outcome carried by ``target_done`` and ``completed``."""

    chat_id: int
    ok: bool
    message: str
    """Success label or error description."""

    message_id: int = 0
    """First sent message id; useful for building a permalink."""

    permalink: str = ""
    """Telegram URL, or empty when not applicable."""

    additional_message_ids: List[int] = field(default_factory=list)
    """Extra ids when an auto-split sent multiple messages."""


class TelegramPublishJob(QObject):
    """Drive one publish from compose -> per-target dispatch -> done."""

    status_changed = Signal(str)
    target_started = Signal(int)
    target_done = Signal(object)            # PublishResult
    completed = Signal(list)                # list[PublishResult]
    failed = Signal(str)

    def __init__(
        self,
        *,
        api: BotApi,
        settings: TelegramSettings,
        bot_id: str,
        body_markdown: str,
        targets: Sequence[Target],
        options: SendOptions,
        file_ids: FileIdCache,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        if not targets:
            raise ValueError("at least one target is required")
        self._api = api
        self._settings = settings
        self._bot_id = bot_id
        self._body = body_markdown
        self._targets: List[Target] = list(targets)
        self._options = options
        self._file_ids = file_ids
        self._formatted: Optional[Formatted] = None
        self._results: List[PublishResult] = []
        self._idx: int = 0
        self._cancelled: bool = False
        # When the body has a local image, we hash + (re)upload the first
        # time and reuse the resulting file_id for subsequent targets.
        self._photo_sha: Optional[str] = None
        self._photo_file_id_cached: Optional[str] = None

    # -- public ------------------------------------------------------ #

    def start(self) -> None:
        try:
            self._formatted = format_for_send(
                self._body, auto_split=self._settings.ui.auto_split
            )
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"could not format body: {exc}")
            return

        if self._formatted.photo and self._formatted.photo.is_local:
            try:
                self._photo_sha = sha256_file(Path(self._formatted.photo.src))
            except OSError as exc:
                self.failed.emit(f"could not read image: {exc}")
                return
            cached = self._file_ids.get(self._photo_sha, self._bot_id, "photo")
            self._photo_file_id_cached = cached

        chunk_count = len(self._formatted.chunks)
        total = len(self._targets)
        photo_note = " with photo" if self._formatted.photo else ""
        self.status_changed.emit(
            f"Sending to {total} chat{'s' if total != 1 else ''} "
            f"({chunk_count} message{'s' if chunk_count != 1 else ''}{photo_note})."
        )
        self._dispatch_next()

    def cancel(self) -> None:
        self._cancelled = True

    # -- per-target dispatch ----------------------------------------- #

    def _dispatch_next(self) -> None:
        if self._cancelled or self._idx >= len(self._targets):
            self.completed.emit(list(self._results))
            return
        target = self._targets[self._idx]
        self.target_started.emit(target.chat.id)
        self._send_to(target, on_done=self._finalize_target)

    def _finalize_target(self, target: Target, result: PublishResult) -> None:
        self._results.append(result)
        self.target_done.emit(result)
        if result.ok:
            self._settings.mark_chat_sent(self._bot_id, target.chat.id, int(time.time()))
        self._idx += 1
        if self._idx < len(self._targets) and not self._cancelled:
            QTimer.singleShot(_INTER_TARGET_DELAY_MS, self._dispatch_next)
        else:
            QTimer.singleShot(0, self._dispatch_next)

    # -- one target's pipeline --------------------------------------- #

    def _send_to(
        self,
        target: Target,
        *,
        on_done: Callable[[Target, PublishResult], None],
    ) -> None:
        formatted = self._formatted
        assert formatted is not None
        photo = formatted.photo

        if photo is not None:
            self._send_with_photo(target, formatted, photo, on_done)
        else:
            self._send_text_chunks(target, formatted, on_done)

    def _send_with_photo(
        self,
        target: Target,
        formatted: Formatted,
        photo: Photo,
        on_done: Callable[[Target, PublishResult], None],
    ) -> None:
        first_chunk = formatted.chunks[0] if formatted.chunks else ""
        caption_fits = len(first_chunk) <= CAPTION_LIMIT and not formatted.is_threaded
        caption = first_chunk if caption_fits else (photo.alt or "")

        def _on_photo_ok(result: dict) -> None:
            msg_id = int(result.get("message_id") or 0)
            new_file_id = _extract_largest_photo_file_id(result)
            if new_file_id and self._photo_sha:
                self._file_ids.put(
                    self._photo_sha, self._bot_id, "photo", new_file_id
                )
                # Cache for use by the next target in this same job.
                self._photo_file_id_cached = new_file_id

            extra_ids: List[int] = []
            if caption_fits:
                self._finish_target(target, msg_id, extra_ids, on_done)
                return
            # The body did not fit the caption: send the body as a reply.
            self._send_text_chunks(
                target,
                formatted,
                lambda t, r: on_done(t, _merge_with_photo_id(r, msg_id)),
                reply_to=msg_id,
            )

        def _on_photo_fail(err: ApiError) -> None:
            if isinstance(err, StaleFileId) and self._photo_sha:
                # Evict and retry by upload / URL.
                self._file_ids.evict(self._photo_sha, self._bot_id, "photo")
                self._photo_file_id_cached = None
                self._send_with_photo(target, formatted, photo, on_done)
                return
            on_done(
                target,
                PublishResult(
                    chat_id=target.chat.id,
                    ok=False,
                    message=err.user_message,
                ),
            )

        if self._photo_file_id_cached:
            call = self._api.send_photo_by_file_id(
                chat_id=target.chat.id,
                file_id=self._photo_file_id_cached,
                caption=caption,
                message_thread_id=target.topic_id,
                disable_notification=self._options.silent,
                protect_content=self._options.protect_content,
            )
        elif photo.is_local:
            call = self._api.send_photo_upload(
                chat_id=target.chat.id,
                file_path=photo.src,
                caption=caption,
                message_thread_id=target.topic_id,
                disable_notification=self._options.silent,
                protect_content=self._options.protect_content,
            )
        else:
            call = self._api.send_photo_by_url(
                chat_id=target.chat.id,
                url=photo.src,
                caption=caption,
                message_thread_id=target.topic_id,
                disable_notification=self._options.silent,
                protect_content=self._options.protect_content,
            )
        call.succeeded.connect(_on_photo_ok)
        call.failed.connect(_on_photo_fail)

    def _send_text_chunks(
        self,
        target: Target,
        formatted: Formatted,
        on_done: Callable[[Target, PublishResult], None],
        *,
        reply_to: Optional[int] = None,
    ) -> None:
        chunks = list(formatted.chunks)
        if not chunks:
            on_done(
                target,
                PublishResult(chat_id=target.chat.id, ok=False, message="empty body"),
            )
            return

        # When attaching as a follow-up to a photo, skip the first chunk
        # because it was used as the caption (caller decides via reply_to).
        chunk_iter = iter(enumerate(chunks))
        first_chunk_message_id: Optional[int] = None
        extra_ids: List[int] = []
        prev_reply: Optional[int] = reply_to

        def _send_one() -> None:
            nonlocal prev_reply, first_chunk_message_id
            try:
                idx, chunk = next(chunk_iter)
            except StopIteration:
                if first_chunk_message_id is None:
                    on_done(
                        target,
                        PublishResult(
                            chat_id=target.chat.id, ok=False, message="empty body",
                        ),
                    )
                    return
                self._finish_target(target, first_chunk_message_id, extra_ids, on_done)
                return

            def _on_ok(result: dict) -> None:
                nonlocal prev_reply, first_chunk_message_id
                msg_id = int(result.get("message_id") or 0)
                if first_chunk_message_id is None:
                    first_chunk_message_id = msg_id
                else:
                    extra_ids.append(msg_id)
                prev_reply = msg_id
                _send_one()

            def _on_fail(err: ApiError) -> None:
                on_done(
                    target,
                    PublishResult(
                        chat_id=target.chat.id, ok=False, message=err.user_message,
                    ),
                )

            call = self._api.send_message(
                chat_id=target.chat.id,
                text=chunk,
                parse_mode="HTML",
                message_thread_id=target.topic_id,
                reply_to_message_id=prev_reply,
                disable_notification=self._options.silent,
                protect_content=self._options.protect_content,
                disable_link_preview=self._options.disable_link_preview,
            )
            call.succeeded.connect(_on_ok)
            call.failed.connect(_on_fail)

        _send_one()

    def _finish_target(
        self,
        target: Target,
        first_message_id: int,
        extra_ids: List[int],
        on_done: Callable[[Target, PublishResult], None],
    ) -> None:
        permalink = permalink_for(target.chat, first_message_id) or ""
        result = PublishResult(
            chat_id=target.chat.id,
            ok=True,
            message="OK",
            message_id=first_message_id,
            permalink=permalink,
            additional_message_ids=extra_ids,
        )
        if self._options.pin_after_sending and first_message_id:
            pin = self._api.pin_chat_message(
                chat_id=target.chat.id,
                message_id=first_message_id,
                disable_notification=True,
            )
            pin.succeeded.connect(lambda _r: on_done(target, result))
            # Pin failures are non-fatal: the send already succeeded.
            pin.failed.connect(
                lambda err: on_done(target, _with_pin_warning(result, err.user_message))
            )
            return
        on_done(target, result)


# ---------------------------------------------------------------------------- #
# Helpers                                                                       #
# ---------------------------------------------------------------------------- #


def _extract_largest_photo_file_id(message: dict) -> str:
    """Pick the largest size's file_id from a sendPhoto response.

    Telegram returns the same photo at multiple sizes; the largest is
    the original. We cache only that one - smaller renders are derived
    server-side at send time.
    """
    photos = message.get("photo")
    if not isinstance(photos, list) or not photos:
        return ""
    largest = max(
        photos,
        key=lambda p: (
            int(p.get("file_size") or 0),
            int(p.get("width") or 0) * int(p.get("height") or 0),
        ),
    )
    return str(largest.get("file_id") or "")


def _with_pin_warning(result: PublishResult, reason: str) -> PublishResult:
    return PublishResult(
        chat_id=result.chat_id,
        ok=result.ok,
        message=f"OK (pin failed: {reason})",
        message_id=result.message_id,
        permalink=result.permalink,
        additional_message_ids=result.additional_message_ids,
    )


def _merge_with_photo_id(result: PublishResult, photo_msg_id: int) -> PublishResult:
    """Reframe a text-follow-up result as anchored on the photo message."""
    if not result.ok:
        return result
    return PublishResult(
        chat_id=result.chat_id,
        ok=True,
        message=result.message,
        message_id=photo_msg_id,
        permalink=result.permalink or "",
        additional_message_ids=[result.message_id, *result.additional_message_ids],
    )
