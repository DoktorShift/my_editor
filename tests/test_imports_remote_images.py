# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Images of posts not imported yet: fetched guarded, kept within bounds.

The fetcher is the importer's BlobFetcher in production (network guard,
size cap); here a fake stands in. Only what decodes through the image
allow-list is kept, decoded no larger than the view needs, within a
byte budget; an address that failed is not asked again; at most four
are fetched at once, the article's first (review H4, M8).
"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QColor, QImage

from nostr.imports import remote_images
from nostr.imports.remote_images import RemoteImages
from tests.imports_fakes import inline_run_blocking


def png(width: int = 4, height: int = 4) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


class FakeFetcher:
    def __init__(self, hold=False, size=(4, 4)):
        self.asked = []
        self.hold = hold
        self.waiting = []
        self.size = size

    def fetch(self, url, *, on_success, on_failure):
        self.asked.append(url)
        answer = (lambda: on_failure("nope")) if "bad" in url else (
            lambda: on_success(b"<svg/>" if "svg" in url else png(*self.size), "image/png"))
        if self.hold:
            self.waiting.append(answer)
        else:
            answer()


def cache(fetcher):
    return RemoteImages(fetcher=fetcher, run_blocking=inline_run_blocking)


def test_an_image_arrives_and_is_kept():
    fetcher = FakeFetcher()
    images = cache(fetcher)
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
    images = cache(fetcher)
    images.request("https://x.example/bad.png")
    images.request("https://x.example/a.svg")      # not an allowed image format
    images.request("https://x.example/bad.png")
    images.request("https://x.example/a.svg")
    assert fetcher.asked == ["https://x.example/bad.png", "https://x.example/a.svg"]
    assert images.image("https://x.example/a.svg") is None


def test_four_at_a_time():
    fetcher = FakeFetcher(hold=True)
    images = cache(fetcher)
    for n in range(6):
        images.request(f"https://x.example/{n}.png")
    assert len(fetcher.asked) == 4
    fetcher.waiting.pop(0)()
    assert len(fetcher.asked) == 5


def test_decoded_no_larger_than_the_view_needs():
    # A 64 px cover of a large photo costs a 64 px image, not the photo.
    images = cache(FakeFetcher(size=(800, 600)))
    images.request("https://x.example/photo.png", QSize(64, 64))
    image = images.image("https://x.example/photo.png")
    assert (image.width(), image.height()) == (85, 64)
    assert images.kept_bytes() == image.sizeInBytes()


def test_a_sharper_decode_comes_from_the_kept_bytes():
    fetcher = FakeFetcher(size=(800, 600))
    images = cache(fetcher)
    ready = []
    images.ready.connect(ready.append)
    images.request("https://x.example/photo.png", QSize(64, 64))
    images.request("https://x.example/photo.png", QSize(400, 0), urgent=True)
    assert images.image("https://x.example/photo.png").width() == 400
    assert fetcher.asked == ["https://x.example/photo.png"]      # not fetched again
    assert ready == ["https://x.example/photo.png"] * 2
    # Never larger than the image itself.
    images.request("https://x.example/photo.png", QSize(2000, 0))
    assert images.image("https://x.example/photo.png").width() == 800


def test_kept_within_a_byte_budget(monkeypatch):
    image_bytes = QImage(40, 40, QImage.Format.Format_RGB32).sizeInBytes()
    monkeypatch.setattr(remote_images, "MAX_CACHE_BYTES", image_bytes * 2)
    images = cache(FakeFetcher(size=(40, 40)))
    for n in range(3):
        images.request(f"https://x.example/{n}.png")
    assert images.image("https://x.example/0.png") is None
    assert images.image("https://x.example/2.png") is not None
    assert images.kept_bytes() <= image_bytes * 2


def test_the_article_goes_first_then_the_newest_rows():
    fetcher = FakeFetcher(hold=True)
    images = cache(fetcher)
    for n in range(4):
        images.request(f"https://x.example/busy{n}.png")
    for n in range(3):
        images.request(f"https://x.example/row{n}.png")
    images.request("https://x.example/article.png", urgent=True)
    fetcher.waiting.pop(0)()
    fetcher.waiting.pop(0)()
    # The article first, then the row asked for last (on screen now).
    assert fetcher.asked[4:] == ["https://x.example/article.png", "https://x.example/row2.png"]


def test_rows_long_scrolled_past_are_not_fetched(monkeypatch):
    monkeypatch.setattr(remote_images, "MAX_QUEUED", 2)
    fetcher = FakeFetcher(hold=True)
    images = cache(fetcher)
    for n in range(4):
        images.request(f"https://x.example/busy{n}.png")
    for n in range(5):
        images.request(f"https://x.example/row{n}.png")
    while fetcher.waiting:
        fetcher.waiting.pop(0)()
    assert "https://x.example/row0.png" not in fetcher.asked
    assert "https://x.example/row4.png" in fetcher.asked


def test_one_image_however_its_address_is_written():
    # Review M8: a cover cached as "B%C3%A4ume.jpg" showed as an empty
    # frame in the article, which asks for "Bäume.jpg".
    fetcher = FakeFetcher()
    images = cache(fetcher)
    ready = []
    images.ready.connect(ready.append)
    images.request("https://x.example/B%C3%A4ume.png")
    assert images.image("https://x.example/Bäume.png") is not None
    images.request("https://x.example/Bäume.png")
    assert len(fetcher.asked) == 1
    images.request("https://x.example/a%20b.png")
    assert images.image("https://x.example/a b.png") is not None


def test_the_default_fetcher_is_the_guarded_one():
    from nostr.imports.fetch import BlobFetcher
    images = RemoteImages()
    assert isinstance(images._fetcher, BlobFetcher)


def test_the_review_sheet_goes_when_it_closes(tmp_path):
    """Review H5: every Review Images left the sheet and its images in
    memory. The window's sheet uses the window's images and is deleted
    when it closes."""
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    from nostr.ui.image_review_dialog import ImageReviewDialog
    from nostr.ui.imports_window import ImportsWindow
    from tests.test_imports_create import Jobs, make_controller
    controller = make_controller(tmp_path, Jobs())
    window = ImportsWindow(controller)
    opened = []
    original = ImageReviewDialog.open
    ImageReviewDialog.open = lambda dialog: opened.append(dialog)
    try:
        controller.images_of = lambda posts, on_ready: on_ready(["https://x.example/a.png"])
        window._review_images()
    finally:
        ImageReviewDialog.open = original
    (dialog,) = opened
    assert dialog._images is controller.images
    dialog.done(ImageReviewDialog.DialogCode.Rejected)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(dialog)
    controller.account_changed(None)


def test_the_article_finds_a_cached_image_with_an_escaped_address():
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument
    from nostr.preview import NostrPreview
    images = cache(FakeFetcher(size=(4, 3)))
    images.request("https://x.example/B%C3%A4ume.png")
    preview = NostrPreview(images=images.image)
    found = preview.loadResource(QTextDocument.ResourceType.ImageResource.value,
                                 QUrl("https://x.example/B%C3%A4ume.png"))
    assert (found.width(), found.height()) == (4, 3)
