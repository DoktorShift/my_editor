# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Your own drafts never expire; imported ones you leave alone do (D-3).

Every draft used to expire 90 days after its last save, so a draft left
alone for three months vanished without a word. Now only an import
dates its drafts' end (NIP-40); a save from the editor, also of an
imported draft the person changed, makes a draft that stays.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import MagicMock

from main_window import MainWindow
from nostr.drafts import DEFAULT_EXPIRATION_SECONDS, build_inner_event
from nostr.publisher import DraftPublishJob
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


def wrap_of(job: DraftPublishJob) -> dict:
    """The unsigned wrap a draft job asks the signer to sign."""
    asked = []
    client = MagicMock()
    client.sign_event.side_effect = lambda unsigned, on_success, on_failure: asked.append(
        unsigned)
    job._on_encrypted("ciphertext", client, ["wss://r.example"])
    return asked[0]


def job(**kw) -> DraftPublishJob:
    inner = build_inner_event(kind=30023, content="Body", pubkey_hex=PROFILE.user_pubkey)
    return DraftPublishJob(relay_pool=MagicMock(), relay_directory=MagicMock(),
                           session_pool=MagicMock(), profile=PROFILE, inner_event=inner,
                           identifier="my-draft", **kw)


def test_a_draft_saved_from_the_editor_never_expires():
    tags = wrap_of(job())["tags"]
    assert not any(tag[0] == "expiration" for tag in tags)


def test_an_import_dates_the_end_of_its_drafts():
    wrap = wrap_of(job(expiration_seconds=DEFAULT_EXPIRATION_SECONDS))
    (expiration,) = [tag[1] for tag in wrap["tags"] if tag[0] == "expiration"]
    assert int(expiration) == wrap["created_at"] + 90 * 24 * 3600


def test_the_import_pipeline_asks_for_the_ninety_days():
    from nostr.imports.pipeline import ImportItemsJob
    factory, created = make_factory()
    importer = ImportItemsJob(
        items=[make_item("Post", guid="g")], feed_url="https://blog.example/feed",
        profile=PROFILE, relay_pool=None, relay_directory=FakeRelayDirectory(),
        session_pool=None, fetcher=FakeFetcher(), long_form_fetcher=FakeLongFormFetcher(None),
        publish_job_factory=factory, run_blocking=inline_run_blocking, pacer=RecordingPacer())
    importer.start()
    settle()
    assert created[0].kwargs["expiration_seconds"] == DEFAULT_EXPIRATION_SECONDS


def test_the_editor_save_passes_no_expiration():
    # Saving from the editor, also an imported draft the person changed,
    # makes a draft that stays.
    tree = ast.parse(textwrap.dedent(inspect.getsource(MainWindow)))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "id", "") == "DraftPublishJob"]
    assert calls
    for call in calls:
        assert "expiration_seconds" not in {k.arg for k in call.keywords}
