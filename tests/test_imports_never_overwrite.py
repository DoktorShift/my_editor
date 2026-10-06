# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An import never overwrites a draft that exists (T-M3-1).

The pipeline asks whether a post is already imported as soon as its
identifier is known, before anything is fetched, copied or signed, and
once more right before signing. Such a post is reported as existing and
nothing of it reaches the signer. The signed wrap of every other post is
handed out before it is sent, for the job's checkpoint.
"""

from __future__ import annotations

from nostr.imports.pipeline import ImportItemsJob
from nostr.imports.snapshots import identifier_of
from tests.imports_fakes import (
    PROFILE,
    FakeFetcher,
    FakeLongFormFetcher,
    RecordingPacer,
    inline_run_blocking,
    make_factory,
    make_item,
)
from tests.outbox_fakes import FakeRelayDirectory, settle

IMAGE_HTML = ("<p>A body long enough not to be thin, with an image in it, and more prose "
              "to clear the threshold.</p><img src='https://a.example/x.png'>")


def make_job(items, is_imported, *, image_mirror=None):
    factory, created = make_factory()
    job = ImportItemsJob(
        items=items, feed_url="https://blog.example/feed", profile=PROFILE,
        relay_pool=None, relay_directory=FakeRelayDirectory(), session_pool=None,
        fetcher=FakeFetcher(), long_form_fetcher=FakeLongFormFetcher(None),
        publish_job_factory=factory, run_blocking=inline_run_blocking,
        pacer=RecordingPacer(), is_imported=is_imported, image_mirror=image_mirror)
    return job, created


def test_an_existing_post_is_never_signed_or_copied():
    existing = make_item("Edited elsewhere", guid="g-1", content_html=IMAGE_HTML)
    fresh = make_item("Fresh", guid="g-2")
    mirrored = []
    job, created = make_job([existing, fresh],
                            lambda d: d == identifier_of(existing),
                            image_mirror=lambda url, ok, err: mirrored.append(url))
    seen = []
    job.item_existing.connect(lambda i, d: seen.append((i, d)))
    done = []
    job.completed.connect(lambda s, a: done.append((s, a)))
    job.start()
    settle()
    assert seen == [(0, identifier_of(existing))]
    assert [j.identifier for j in created] == [identifier_of(fresh)]
    assert mirrored == []
    assert done == [(1, 1)]


def test_asked_again_right_before_signing():
    # The other app made the draft while this one was being prepared.
    item = make_item("Race", guid="g-race")
    answers = iter([False, True])
    job, created = make_job([item], lambda d: next(answers))
    seen = []
    job.item_existing.connect(lambda i, d: seen.append(d))
    job.start()
    settle()
    assert seen == [identifier_of(item)]
    assert created == []


def test_a_failing_question_counts_as_existing():
    def broken(_d):
        raise RuntimeError("no answer")

    job, created = make_job([make_item("x", guid="g-x")], broken)
    job.start()
    settle()
    assert created == []


def test_the_signed_wrap_is_handed_out_for_the_checkpoint():
    item = make_item("Keep me", guid="g-keep")
    job, _created = make_job([item], lambda d: False)
    kept = []
    job.item_signed.connect(lambda i, event: kept.append((i, event["id"])))
    job.start()
    settle()
    assert kept == [(0, "ev-" + identifier_of(item))]
