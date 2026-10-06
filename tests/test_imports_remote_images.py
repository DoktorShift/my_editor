# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Images of posts not imported yet: fetched guarded, kept in memory.

The fetcher is the importer's BlobFetcher in production (network guard,
size cap); here a fake stands in. Only what decodes through the image
allow-list is kept; an address that failed is not asked again; at most
four are fetched at once; the most recent ones are kept.
"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

from nostr.imports import remote_images
from nostr.imports.remote_images import RemoteImages


def png() -> bytes:
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


class FakeFetcher:
    def __init__(self, hold=False):
        self.asked = []
        self.hold = hold
        self.waiting = []

    def fetch(self, url, *, on_success, on_failure):
        self.asked.append(url)
        answer = (lambda: on_failure("nope")) if "bad" in url else (
            lambda: on_success(b"<svg/>" if "svg" in url else png(), "image/png"))
        if self.hold:
            self.waiting.append(answer)
        else:
            answer()


def test_an_image_arrives_and_is_kept():
    fetcher = FakeFetcher()
    images = RemoteImages(fetcher=fetcher)
    ready = []
    images.ready.connect(ready.append)
    assert images.image("https://x.example/a.png") is None
    images.request("https://x.example/a.png")
    assert ready == ["https://x.example/a.png"]
    assert images.image("https://x.example/a.png").width() == 4
    images.request("https://x.example/a.png")
    assert fetcher.asked == ["https://x.example/a.png"]


def test_what_fails_or_does_not_decode_is_not_asked_again():
    fetcher = FakeFetcher()
    images = RemoteImages(fetcher=fetcher)
    images.request("https://x.example/bad.png")
    images.request("https://x.example/a.svg")      # not an allowed image format
    images.request("https://x.example/bad.png")
    images.request("https://x.example/a.svg")
    assert fetcher.asked == ["https://x.example/bad.png", "https://x.example/a.svg"]
    assert images.image("https://x.example/a.svg") is None


def test_four_at_a_time():
    fetcher = FakeFetcher(hold=True)
    images = RemoteImages(fetcher=fetcher)
    for n in range(6):
        images.request(f"https://x.example/{n}.png")
    assert len(fetcher.asked) == 4
    fetcher.waiting.pop(0)()
    assert len(fetcher.asked) == 5


def test_the_most_recent_are_kept(monkeypatch):
    monkeypatch.setattr(remote_images, "MAX_KEPT", 2)
    images = RemoteImages(fetcher=FakeFetcher())
    for n in range(3):
        images.request(f"https://x.example/{n}.png")
    assert images.image("https://x.example/0.png") is None
    assert images.image("https://x.example/2.png") is not None


def test_the_default_fetcher_is_the_guarded_one():
    from nostr.imports.fetch import BlobFetcher
    images = RemoteImages()
    assert isinstance(images._fetcher, BlobFetcher)
