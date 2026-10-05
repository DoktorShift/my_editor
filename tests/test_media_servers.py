# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how the media library treats each server, the members' one included.

What must hold:

  Every server reports what it holds (count and bytes), so the library
  can say how full the members' server is.

  A server that cannot be listed keeps the files the library saw there
  last time: a server that is briefly down has deleted nothing.

  Copies are ordered with the configured primary first, so the URL the
  app copies and inserts does not depend on which server answered first.

  A server known to list publicly (the members' server) is listed
  without asking the signer; one that turns out to want a token still
  gets one.

  A new server (a membership that resolved) makes the library stale at
  once, so its files appear without waiting out the freshness window.

  An upload leaves out a server where the file would not fit in the
  allowance, says so, and still succeeds elsewhere.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.blossom.client import BlossomClient  # noqa: E402
from nostr.blossom.settings import BlossomSettings  # noqa: E402
from nostr.blossom.store import MediaStore  # noqa: E402
from nostr.ui.media_library_dialog import storage_text  # noqa: E402
from nostr.blossom.store import ServerListing  # noqa: E402
from tests.blossom_fakes import (  # noqa: E402
    BODY, MIRROR, SERVER, SHA, FakeBlossomServer, FakeNam, FakeProfile,
    FakeSessionPool, FakeSigner, descriptor, error_reply,
)

E21 = "https://blossom.einundzwanzig.space"
A, B, C = "a" * 64, "b" * 64, "c" * 64


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class Router:
    """Several fake servers behind one transport, by host."""

    def __init__(self, *servers):
        self.by_host = {QUrl(s.origin).host(): s for s in servers}
        self.down = set()
        self.wants_token = set()

    def __call__(self, verb, request, body):
        host = request.url().host()
        if host in self.down and verb == "get":
            return error_reply(503, reason="Down for maintenance.")
        if host in self.wants_token and verb == "get" and \
                not request.hasRawHeader("Authorization"):
            return error_reply(401, reason="Sign in to list.")
        return self.by_host[host](verb, request, body)


def make(tmp_path, router, *, servers=(SERVER, MIRROR), entitled=(), quota=None):
    settings = BlossomSettings(tmp_path / "blossom_servers.json")
    settings.set_custom_servers(list(servers))
    nam = FakeNam(None, responder=router)
    signer = FakeSigner()
    pool = FakeSessionPool(signer)
    entitled_list = list(entitled)
    store = MediaStore(
        session_pool=pool,
        profile_provider=lambda: FakeProfile(),
        settings=settings,
        client=BlossomClient(nam=nam),
        entitled_servers=lambda: entitled_list,
        server_quota=quota,
    )
    return store, nam, signer, entitled_list


def test_each_server_reports_what_it_holds(tmp_path):
    router = Router(
        FakeBlossomServer(SERVER, blobs=[descriptor(A, size=100), descriptor(B, size=50)]),
        FakeBlossomServer(MIRROR, blobs=[descriptor(B, server=MIRROR, size=50)]))
    store, nam, _signer, _ = make(tmp_path, router)
    store.fetch()
    nam.settle()
    listings = store.server_listings()
    assert (listings[SERVER].ok, listings[SERVER].count, listings[SERVER].bytes) == (True, 2, 150)
    assert (listings[MIRROR].count, listings[MIRROR].bytes) == (1, 50)
    assert store.bytes_on(MIRROR) == 50 and store.bytes_on(SERVER) == 150


def test_an_unreachable_server_keeps_its_files_from_last_time(tmp_path):
    router = Router(FakeBlossomServer(SERVER, blobs=[descriptor(A)]),
                    FakeBlossomServer(MIRROR, blobs=[descriptor(C, server=MIRROR)]))
    store, nam, _signer, _ = make(tmp_path, router)
    store.fetch()
    nam.settle()
    assert set(store.files) == {A, C}

    router.down.add("mirror.example")
    store.fetch(force=True)
    nam.settle()
    assert set(store.files) == {A, C}            # C is not gone, only unconfirmed
    assert not store.server_listings()[MIRROR].ok
    assert store.files[C].urls[0]["server"] == MIRROR


def test_copies_are_ordered_primary_first(tmp_path):
    router = Router(FakeBlossomServer(SERVER, blobs=[descriptor(A)]),
                    FakeBlossomServer(MIRROR, blobs=[descriptor(A, server=MIRROR)]))
    store, nam, _signer, _ = make(tmp_path, router, servers=(MIRROR, SERVER))
    store.fetch()
    nam.settle()
    media = store.files[A]
    assert [u["server"] for u in media.urls] == [MIRROR, SERVER]
    assert media.url == media.urls[0]["url"]


def test_the_members_server_is_listed_without_a_signer_prompt(tmp_path):
    e21 = FakeBlossomServer(E21, blobs=[descriptor(B, server=E21, size=7)])
    router = Router(FakeBlossomServer(SERVER, blobs=[descriptor(A)]), e21)
    store, nam, signer, _ = make(tmp_path, router, servers=(SERVER,), entitled=[E21])
    store.fetch()
    nam.settle()
    assert B in store.files
    servers_signed_for = [t for t in signer.requests if ["t", "list"] in t["tags"]]
    assert len(servers_signed_for) == 1          # the configured server only
    assert all("einundzwanzig" not in str(t["tags"]) for t in servers_signed_for)


def test_a_public_listing_that_wants_a_token_gets_one(tmp_path):
    router = Router(FakeBlossomServer(SERVER, blobs=[descriptor(A)]),
                    FakeBlossomServer(E21, blobs=[descriptor(B, server=E21)]))
    router.wants_token.add("blossom.einundzwanzig.space")
    store, nam, signer, _ = make(tmp_path, router, servers=(SERVER,), entitled=[E21])
    store.fetch()
    nam.settle()
    assert B in store.files
    assert store.server_listings()[E21].ok


def test_a_new_server_makes_the_library_stale_at_once(tmp_path):
    router = Router(FakeBlossomServer(SERVER, blobs=[descriptor(A)]),
                    FakeBlossomServer(E21, blobs=[descriptor(B, server=E21)]))
    store, nam, _signer, entitled = make(tmp_path, router, servers=(SERVER,))
    store.fetch()
    nam.settle()
    assert set(store.files) == {A}
    calls = len(nam.calls)
    store.fetch()
    assert len(nam.calls) == calls               # nothing changed: fresh, no request

    entitled.append(E21)                         # the membership resolved
    store.fetch()
    nam.settle()
    assert set(store.files) == {A, B}


def test_a_full_server_is_left_out_of_an_upload_and_it_says_so(tmp_path):
    router = Router(FakeBlossomServer(SERVER), FakeBlossomServer(E21))
    store, nam, _signer, _ = make(
        tmp_path, router, servers=(SERVER,), entitled=[E21],
        quota=lambda origin: 1 if origin == E21 else None)
    skipped, finished = [], []
    store.server_skipped.connect(lambda n, h, r: skipped.append((n, h, r)))
    store.upload_finished.connect(lambda n, m: finished.append(m))
    store.upload_bytes(BODY, name="photo.png", mime_type="image/png")
    nam.settle()
    assert skipped == [("photo.png", "blossom.einundzwanzig.space", "full")]
    assert finished and finished[0].hash == SHA
    assert all("einundzwanzig" not in u["server"] for u in finished[0].urls)


def test_no_room_anywhere_fails_plainly(tmp_path):
    router = Router(FakeBlossomServer(E21))
    store, nam, _signer, _ = make(tmp_path, router, servers=(E21,),
                                  quota=lambda origin: 1)
    failed = []
    store.upload_failed.connect(lambda n, r: failed.append(r))
    store.upload_bytes(BODY, name="photo.png", mime_type="image/png")
    assert failed and "enough space" in failed[0]
    assert nam.calls == []                       # nothing left the machine


def test_the_members_server_accepts_files_up_to_a_gigabyte():
    from nostr.blossom.plan import get_effective_max_file, lists_publicly
    assert get_effective_max_file(E21) == 1024 ** 3
    assert lists_publicly(E21) and not lists_publicly(SERVER)


# -- the storage meter's words ---------------------------------------------------------

GB = 1000 ** 3


def test_storage_text_says_the_number_and_warns_in_words():
    ok = ServerListing(origin=E21, ok=True)
    assert storage_text(int(1.2 * GB), 5 * GB, ok) == ("1.2 GB of 5 GB used", 0.24)
    text, _ = storage_text(int(4.6 * GB), 5 * GB, ok)
    assert text.startswith("Almost full:")
    text, fraction = storage_text(6 * GB, 5 * GB, ok)
    assert text.startswith("Full:") and fraction == 1.0
    assert storage_text(640 * 1000 ** 2, 5 * GB, ok)[0] == "640 MB of 5 GB used"


def test_storage_text_is_honest_about_what_it_does_not_know():
    assert storage_text(0, 5 * GB, None)[0] == "Checking…"
    down = ServerListing(origin=E21, ok=False)
    assert storage_text(0, 5 * GB, down)[0] == "Usage unavailable right now."
    cut = ServerListing(origin=E21, ok=True, truncated=True)
    assert storage_text(GB, 5 * GB, cut)[0].startswith("At least")
