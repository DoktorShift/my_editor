# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Save As of a Media Library file, through the real loader and download,
against a small blob server on this computer.

What must hold, whatever goes wrong:

  Every save ends: it is saved, or the window says why not. A save that
  cannot begin (a folder that is gone or may not be written in) says so
  like any other, and the next save still runs.

  Nothing is left behind: the hidden part file a download is written to
  goes when the save ends, saved or not.
"""

from __future__ import annotations

import hashlib
import http.server
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import nostr.ui.media_library_dialog as dialog_module  # noqa: E402
from nostr.blossom.store import MediaFile  # noqa: E402
from nostr.ui.media_library_dialog import MediaLibraryDialog  # noqa: E402
from nostr.ui.thumbnail_loader import ThumbnailLoader  # noqa: E402
from tests.test_media_library import FakeStore, select  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(autouse=True)
def close_windows():
    yield
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, MediaLibraryDialog) and widget.isVisible():
            widget.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --------------------------------------------------------------------------- #
# A blob server on this computer
# --------------------------------------------------------------------------- #

class _Blobs:
    files: dict = {}         # sha256 -> bytes
    delay = 0.0              # seconds after each 64 KiB


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        body = _Blobs.files.get(self.path.strip("/").split(".")[0])
        if body is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            for start in range(0, len(body), 65536):
                self.wfile.write(body[start:start + 65536])
                self.wfile.flush()
                if _Blobs.delay:
                    time.sleep(_Blobs.delay)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def quick_server():
    _Blobs.delay = 0.0
    yield
    _Blobs.delay = 0.0


def wait_for(condition, timeout=10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


def blob(server, body: bytes, *, day: int = 0, mime: str = "video/mp4") -> MediaFile:
    sha = hashlib.sha256(body).hexdigest()
    _Blobs.files[sha] = body
    url = f"{server}/{sha}.mp4"
    return MediaFile(hash=sha, url=url, urls=[{"server": server, "url": url}],
                     mime_type=mime, size=len(body),
                     uploaded_at_ms=1_780_000_000_000 - day * 86_400_000)


def library(tmp_path, monkeypatch, files, *, names=None):
    """The library window, with the real loader and its cache in tmp_path."""
    cache = tmp_path / "cache"
    monkeypatch.setattr(dialog_module, "ThumbnailLoader",
                        lambda parent=None: ThumbnailLoader(parent, cache_dir=cache))
    dialog = MediaLibraryDialog(store=FakeStore(files, names=names or {}), is_dark=False,
                                pick_mode=False)
    dialog.resize(860, 560)
    dialog.show()
    QCoreApplication.processEvents()
    return SimpleNamespace(dialog=dialog, model=dialog._model, grid=dialog._grid)


def save_as(monkeypatch, world, media: MediaFile, target) -> None:
    monkeypatch.setattr(dialog_module.QFileDialog, "getSaveFileName",
                        lambda *a, **k: (str(target), ""))
    select(world, media.hash)
    world.dialog._save_as()


def saving(world) -> bool:
    return not world.dialog._save_bar.isHidden()


# --------------------------------------------------------------------------- #
# A save that cannot begin
# --------------------------------------------------------------------------- #

def test_a_download_that_cannot_begin_says_so_to_whoever_started_it(tmp_path, server):
    """The refusal comes after the caller has connected to the download,
    not before, when nobody hears it."""
    body = b"A" * 5000
    media = blob(server, body)
    loader = ThumbnailLoader(cache_dir=tmp_path / "cache")
    job = loader.download_to(media.url, media.hash, tmp_path / "gone" / "A.mp4",
                             size=len(body))
    done = []
    job.finished.connect(done.append)
    assert wait_for(lambda: done, 3)
    assert done == ["write"]


def test_cancel_ends_a_download_that_never_began(tmp_path, server):
    media = blob(server, b"C" * 100)
    loader = ThumbnailLoader(cache_dir=tmp_path / "cache")
    job = loader.download_to(media.url, media.hash, tmp_path / "gone" / "C.mp4", size=100)
    done = []
    job.finished.connect(done.append)
    job.cancel()
    wait_for(lambda: False, 0.2)
    assert done == ["cancelled"]


def test_save_as_into_a_folder_that_cannot_be_written_says_so_and_the_next_save_runs(
        tmp_path, monkeypatch, server):
    first, second = blob(server, b"A" * 5000), blob(server, b"B" * 5000, day=1)
    world = library(tmp_path, monkeypatch, [first, second])
    save_as(monkeypatch, world, first, tmp_path / "gone" / "A.mp4")
    assert wait_for(lambda: not saving(world), 3), "the save never ended"
    assert world.dialog._status.text() == "“A.mp4” couldn’t be saved there. Choose another folder."
    out = tmp_path / "out"
    out.mkdir()
    save_as(monkeypatch, world, second, out / "B.mp4")
    assert wait_for(lambda: (out / "B.mp4").exists(), 5)
    assert (out / "B.mp4").read_bytes() == b"B" * 5000
