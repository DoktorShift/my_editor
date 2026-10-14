# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reviewing the images an import copies, as thumbnails.

The list is made the way the import works: each post's cover, then the
images of the Markdown the import makes of it, so every image that would
be copied can be reviewed (also from sources that deliver Markdown), and
one left unchecked is the very address the import leaves alone. The
thumbnails come through the guarded image source.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QImage

from nostr.imports.snapshots import has_images, images_to_copy
from nostr.ui.image_review_dialog import ImageReviewDialog
from tests.accessibility import unnamed_controls
from tests.imports_fakes import make_item
import pytest
from tests.widget_lifetime import delete_new_windows


@pytest.fixture(autouse=True)
def _windows_deleted():
    """Every window and panel a test makes is deleted after it: left to
    the cycle collector, one without a parent can crash it."""
    yield from delete_new_windows()


class Images(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.kept = {}
        self.requested = []

    def image(self, url):
        return self.kept.get(url)

    def request(self, url, size=None, *, urgent=False):
        self.requested.append(url)

    def arrive(self, url):
        image = QImage(40, 20, QImage.Format.Format_RGB32)
        image.fill(QColor("#225588"))
        self.kept[url] = image
        self.ready.emit(url)


class TestTheList:
    def test_covers_then_the_markdown_images_each_once(self):
        first = make_item("One", guid="1", image="https://x.example/cover.png",
                          content_html="<p>Text</p><img src='https://x.example/a.png'>"
                                       "<img src='https://x.example/cover.png'>")
        second = make_item("Two", guid="2", image=None,
                           content_html="<img src='https://x.example/a.png'>"
                                        "<img src='data:image/png;base64,AAAA'>"
                                        "<img src='https://x.example/b.png'>")
        assert images_to_copy([first, second]) == [
            "https://x.example/cover.png", "https://x.example/a.png",
            "https://x.example/b.png"]

    def test_sources_that_deliver_markdown_are_reviewed_too(self):
        nostr = make_item("Long-form", guid="n", content_html="",
                          content_markdown="Text\n\n![shot](https://x.example/n.png)")
        assert images_to_copy([nostr]) == ["https://x.example/n.png"]
        assert has_images(nostr)

    def test_nothing_to_copy(self):
        plain = make_item("Plain", guid="p", content_html="<p>Only words</p>", image=None)
        assert images_to_copy([plain]) == []
        assert not has_images(plain)


class TestTheSheet:
    def test_thumbnails_come_through_the_image_source(self):
        images = Images()
        dialog = ImageReviewDialog(["https://x.example/a.png", "https://x.example/b.png"],
                                   image_source=images)
        assert images.requested == ["https://x.example/a.png", "https://x.example/b.png"]
        row = dialog._items["https://x.example/a.png"]
        before = row.icon().cacheKey()
        images.arrive("https://x.example/a.png")
        assert row.icon().cacheKey() != before
        assert dialog.windowModality() == Qt.WindowModality.WindowModal

    def test_unchecked_images_stay_where_they_are(self):
        dialog = ImageReviewDialog(["https://x.example/a.png", "https://x.example/b.png"],
                                   skip_urls={"https://x.example/b.png"},
                                   image_source=Images())
        assert dialog.skip_urls() == {"https://x.example/b.png"}
        assert dialog._count.text() == "1 of 2 images will be copied."
        dialog._items["https://x.example/a.png"].setCheckState(Qt.CheckState.Unchecked)
        assert dialog.skip_urls() == {"https://x.example/a.png", "https://x.example/b.png"}
        assert dialog._count.text() == "0 of 2 images will be copied."

    def test_every_control_has_a_name(self):
        dialog = ImageReviewDialog(["https://x.example/a.png"], image_source=Images())
        assert unnamed_controls(dialog) == []
