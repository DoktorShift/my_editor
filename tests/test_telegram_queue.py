"""Unit tests for the scheduled-post queue."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from publishers.telegram.queue import (
    MAX_ATTEMPTS,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_ORPHANED,
    STATUS_PARTIAL,
    STATUS_PENDING,
    STATUS_SENT,
    ScheduledQueue,
    ScheduledTarget,
)


@pytest.fixture
def q(tmp_path: Path) -> ScheduledQueue:
    return ScheduledQueue(path=tmp_path / "scheduled.json")


def _add(q, bot_id="b_1", when=None, target=-1001):
    return q.add(
        bot_id=bot_id,
        targets=[ScheduledTarget(chat_id=target)],
        body_markdown="hi",
        scheduled_for=when if when is not None else int(time.time()) + 60,
    )


def test_add_requires_target(q):
    with pytest.raises(ValueError):
        q.add(bot_id="b", targets=[], body_markdown="x",
              scheduled_for=int(time.time()) + 30)


def test_add_requires_bot_id(q):
    with pytest.raises(ValueError):
        q.add(bot_id="", targets=[ScheduledTarget(chat_id=1)],
              body_markdown="x", scheduled_for=int(time.time()) + 30)


def test_next_due_returns_earliest_pending(q):
    p1 = _add(q, when=int(time.time()) + 100)
    p2 = _add(q, when=int(time.time()) + 10)
    assert q.next_due(before=None).id == p2.id


def test_next_due_with_before_filters_to_already_due(q):
    p1 = _add(q, when=int(time.time()) + 100)
    assert q.next_due(before=int(time.time())) is None


def test_cancel_only_works_on_pending(q):
    p = _add(q)
    q.cancel(p.id)
    assert q.by_id(p.id).status == STATUS_CANCELLED
    # Second cancel is a no-op.
    q.cancel(p.id)
    assert q.by_id(p.id).status == STATUS_CANCELLED


def test_reschedule_resets_status_and_attempts(q):
    p = _add(q)
    q.mark_sending(p.id)
    assert q.by_id(p.id).status != STATUS_PENDING
    q.reschedule(p.id, int(time.time()) + 600)
    # Reschedule from SENDING is a no-op (only PENDING / FAILED allowed).
    assert q.by_id(p.id).status != STATUS_PENDING
    q.finalize(p.id, last_error="boom")
    # Now FAILED - reschedule should work.
    q.reschedule(p.id, int(time.time()) + 600)
    assert q.by_id(p.id).status == STATUS_PENDING
    assert q.by_id(p.id).attempts == 0
    assert q.by_id(p.id).last_error == ""


def test_orphan_marks_pending_posts_only(q):
    a = _add(q, bot_id="bot-a", when=int(time.time()) + 60)
    b = _add(q, bot_id="bot-a", when=int(time.time()) + 60)
    q.cancel(b.id)
    affected = q.orphan_bot_posts("bot-a")
    assert affected == 1                       # only the pending one
    assert q.by_id(a.id).status == STATUS_ORPHANED
    assert q.by_id(b.id).status == STATUS_CANCELLED


def test_record_delivery_replaces_prior_for_same_chat(q):
    p = _add(q)
    q.record_delivery(p.id, chat_id=-1001, ok=False, error="x")
    q.record_delivery(p.id, chat_id=-1001, ok=True, message_id=99)
    rec = q.by_id(p.id).delivered
    assert len(rec) == 1
    assert rec[0].ok is True
    assert rec[0].message_id == 99


def test_finalize_marks_sent_when_all_ok(q):
    p = q.add(bot_id="b", targets=[ScheduledTarget(chat_id=1),
                                     ScheduledTarget(chat_id=2)],
              body_markdown="x", scheduled_for=int(time.time()) + 30)
    q.mark_sending(p.id)
    q.record_delivery(p.id, chat_id=1, ok=True, message_id=10)
    q.record_delivery(p.id, chat_id=2, ok=True, message_id=11)
    q.finalize(p.id)
    assert q.by_id(p.id).status == STATUS_SENT


def test_finalize_marks_partial_when_some_failed(q):
    p = q.add(bot_id="b", targets=[ScheduledTarget(chat_id=1),
                                     ScheduledTarget(chat_id=2)],
              body_markdown="x", scheduled_for=int(time.time()) + 30)
    q.mark_sending(p.id)
    q.record_delivery(p.id, chat_id=1, ok=True, message_id=10)
    q.record_delivery(p.id, chat_id=2, ok=False, error="kicked")
    q.finalize(p.id)
    assert q.by_id(p.id).status == STATUS_PARTIAL


def test_finalize_marks_failed_after_max_attempts(q):
    p = _add(q)
    for _ in range(MAX_ATTEMPTS):
        q.mark_sending(p.id)
    q.finalize(p.id, last_error="exhausted")
    assert q.by_id(p.id).status == STATUS_FAILED
    assert q.by_id(p.id).last_error == "exhausted"


def test_retry_after_works_from_sending(q):
    """The internal retry path must work even when status is SENDING."""
    p = _add(q)
    q.mark_sending(p.id)
    assert q.by_id(p.id).status != STATUS_PENDING
    q.retry_after(p.id, 60)
    assert q.by_id(p.id).status == STATUS_PENDING
    assert q.by_id(p.id).scheduled_for >= int(time.time()) + 50
    # Attempts is preserved (the retry path uses it for back-off).
    assert q.by_id(p.id).attempts == 1


def test_retry_after_preserves_attempts_counter(q):
    p = _add(q)
    q.mark_sending(p.id)
    q.mark_sending(p.id)
    q.retry_after(p.id, 10)
    assert q.by_id(p.id).attempts == 2


def test_retry_after_clamps_negative_delays(q):
    p = _add(q)
    q.retry_after(p.id, -100)
    assert q.by_id(p.id).scheduled_for >= int(time.time())


def test_disk_round_trip(q, tmp_path):
    p = _add(q)
    q.record_delivery(p.id, chat_id=-1001, ok=True, message_id=42,
                      permalink="https://t.me/x/42")
    q2 = ScheduledQueue(path=q._path)
    assert len(q2.all()) == 1
    restored = q2.by_id(p.id)
    assert restored is not None
    assert restored.delivered[0].message_id == 42
    assert restored.delivered[0].permalink == "https://t.me/x/42"
