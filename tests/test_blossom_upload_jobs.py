# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An upload the Media Library starts is a job the person can act on.

What must hold:

  Two files with the same name in flight at once are two jobs: each
  keeps its own progress and its own outcome.

  Cancel stops an upload while its bytes are on their way: the request
  is aborted, nothing is reported failed or finished, and a signer that
  answers late sends nothing. Once a server holds the file, the copies
  on the other servers finish in the background and Cancel is refused.

  A failed upload can be retried as it was, and dismissed.

  The name a file had when it was added is remembered, also across a
  restart, because media servers keep no names.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.blossom.client import BlossomClient  # noqa: E402
from nostr.blossom.names import MediaNames  # noqa: E402
from nostr.blossom.settings import BlossomSettings  # noqa: E402
from nostr.blossom.store import MediaStore  # noqa: E402
from tests.blossom_fakes import (  # noqa: E402
    BODY, MIRROR, SERVER, SHA, FakeBlossomServer, FakeNam, FakeProfile,
    FakeSessionPool, FakeSigner, error_reply,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class Jobs:
    def __init__(self, tmp_path, *, servers=(SERVER,), replies=None, defer=False):
        settings = BlossomSettings(tmp_path / "blossom_servers.json")
        settings.set_custom_servers(list(servers))
        self.server = FakeBlossomServer(SERVER)
        self.nam = FakeNam(replies, responder=self.server)
        self.signer = FakeSigner(defer=defer)
        self.names = MediaNames(tmp_path / "media_names.json")
        self.store = MediaStore(
            session_pool=FakeSessionPool(self.signer),
            profile_provider=FakeProfile,
            settings=settings,
            client=BlossomClient(nam=self.nam),
            names=self.names,
        )
        self.events = []
        s = self.store
        s.upload_started.connect(lambda n: self.events.append(("started", n)))
        s.upload_finished.connect(lambda n, m: self.events.append(("finished", n)))
        s.upload_failed.connect(lambda n, r: self.events.append(("failed", n)))
        s.upload_cancelled.connect(lambda n: self.events.append(("cancelled", n)))
        s.upload_progress.connect(lambda n, a, b: self.events.append(("progress", n, a)))

    def kinds(self, name):
        return [e[0] for e in self.events if e[1] == name]


def _file(tmp_path, name="photo.png", body=BODY):
    path = tmp_path / name
    path.write_bytes(body)
    return str(path)


def test_two_files_of_one_name_are_two_jobs(tmp_path):
    jobs = Jobs(tmp_path, defer=True)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = jobs.store.upload_file(_file(tmp_path / "a"))
    second = jobs.store.upload_file(_file(tmp_path / "b", body=b"other bytes"))
    assert (first, second) == ("photo.png", "photo (2).png")
    jobs.signer.release_all()
    jobs.nam.settle()
    assert jobs.kinds(first)[-1] == "finished"
    assert jobs.kinds(second)[-1] == "finished"
    # A name is free again once its job is over.
    (tmp_path / "c").mkdir()
    assert jobs.store.upload_file(_file(tmp_path / "c", body=b"third")) == "photo.png"


def test_cancel_while_sending_aborts_and_reports_nothing_else(tmp_path):
    jobs = Jobs(tmp_path, replies=[error_reply(0)])
    name = jobs.store.upload_file(_file(tmp_path))
    put = jobs.nam.issued[-1]
    assert jobs.store.cancel_upload(name) is True
    assert put.aborted
    put.finish()                       # Qt answers an aborted request too
    assert jobs.kinds(name) == ["started", "cancelled"]
    assert jobs.store.upload_job(name) is None


def test_cancel_while_the_signer_is_asked_sends_nothing(tmp_path):
    jobs = Jobs(tmp_path, defer=True)
    name = jobs.store.upload_file(_file(tmp_path))
    assert jobs.store.cancel_upload(name) is True
    jobs.signer.release_all()           # the signer answers late
    assert jobs.nam.calls == []
    assert jobs.kinds(name) == ["started", "cancelled"]


def test_once_a_server_holds_the_file_cancel_is_refused(tmp_path):
    router_mirror = FakeBlossomServer(MIRROR)

    def responder(verb, request, body):
        host = request.url().host()
        return (router_mirror if host == "mirror.example" else jobs.server)(verb, request, body)

    jobs = Jobs(tmp_path, servers=(SERVER, MIRROR))
    jobs.nam._responder = responder
    jobs.signer.defer = True
    name = jobs.store.upload_file(_file(tmp_path))
    jobs.signer.release()               # the upload's token
    jobs.nam.settle_one()               # the primary has it; the copy waits on the signer
    assert jobs.store.upload_job(name).status == "mirroring"
    assert jobs.store.cancel_upload(name) is False
    jobs.signer.release_all()
    jobs.nam.settle()
    assert jobs.kinds(name)[-1] == "finished"


def test_a_failed_upload_is_retried_as_it_was(tmp_path):
    jobs = Jobs(tmp_path, replies=[error_reply(0)])
    name = jobs.store.upload_file(_file(tmp_path))
    jobs.nam.settle()
    assert jobs.kinds(name)[-1] == "failed"
    assert jobs.store.can_retry(name)
    jobs.store.retry_upload(name)
    jobs.nam.settle()
    assert jobs.kinds(name)[-1] == "finished"
    assert jobs.server.uploads == [BODY]
    assert not jobs.store.can_retry(name)


def test_a_pasted_image_is_retried_from_its_bytes(tmp_path):
    jobs = Jobs(tmp_path, replies=[error_reply(0)])
    name = jobs.store.add_bytes(BODY, label="Pasted Image.png", mime_type="image/png")
    jobs.nam.settle()
    jobs.store.retry_upload(name)
    jobs.nam.settle()
    assert jobs.kinds(name)[-1] == "finished"
    assert jobs.store.name_of(SHA) == "Pasted Image.png"


def test_a_dismissed_failure_cannot_be_retried(tmp_path):
    jobs = Jobs(tmp_path, replies=[error_reply(0)])
    name = jobs.store.upload_file(_file(tmp_path))
    jobs.nam.settle()
    jobs.store.dismiss_upload(name)
    assert not jobs.store.can_retry(name)
    assert jobs.store.upload_file(_file(tmp_path)) == name    # the name is free


def test_the_name_a_file_was_added_with_is_remembered(tmp_path):
    jobs = Jobs(tmp_path)
    jobs.store.upload_file(_file(tmp_path, "Holiday 2026.png"))
    jobs.nam.settle()
    assert jobs.store.name_of(SHA) == "Holiday 2026.png"
    assert MediaNames(tmp_path / "media_names.json").get(SHA) == "Holiday 2026.png"


def test_an_editor_upload_keeps_its_own_job_name_and_no_file_name(tmp_path):
    """The asset manager names its jobs by asset key and follows them by
    that name, so the store must not rename them; and an asset key is
    not a file name to remember."""
    jobs = Jobs(tmp_path)
    jobs.store.upload_bytes(BODY, name=f"asset:{SHA}", mime_type="image/png")
    jobs.nam.settle()
    assert jobs.kinds(f"asset:{SHA}")[-1] == "finished"
    assert jobs.store.name_of(SHA) == ""


def test_cancel_as_soon_as_the_upload_starts_sends_nothing(tmp_path):
    """The window's × can be pressed the moment the tile starts moving,
    which is while the store is still telling everyone it started."""
    jobs = Jobs(tmp_path)
    jobs.store.upload_started.connect(jobs.store.cancel_upload)
    name = jobs.store.upload_file(_file(tmp_path))
    jobs.nam.settle()
    assert jobs.nam.calls == []
    assert jobs.kinds(name) == ["started", "cancelled"]
