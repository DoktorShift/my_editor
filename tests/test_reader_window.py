# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the full preview detached into a window of its own
(nostr/ui/reader_window.py).

What must hold, seen in the app: the cover the preview showed stays in
the window (it was given before the window first showed, and dropped).
A cover the preview had not got yet is asked for and shown when it comes;
one that can't be had says so, as the preview does.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QObject, QSize, Signal  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.ui.preview_parts import IMAGE_UNAVAILABLE, IMAGE_WAITING, PreviewFields  # noqa: E402
from nostr.ui.reader_window import ReaderWindow  # noqa: E402

COVER = "https://cdn.example/cover.jpg"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    yield QApplication.instance() or QApplication(sys.argv)


class Loader(QObject):
    url_ready = Signal(str, str, object)
    url_failed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.asked = []

    def load_url(self, url):
        self.asked.append(url)


def window():
    fields = PreviewFields("Title", "Summary", "Published today", "1 min read", "#tag", "", COVER)
    return ReaderWindow(fields=fields, kind=30023, content="Body", actions=(),
                        size=QSize(480, 640))


def picture():
    made = QPixmap(64, 36)
    made.fill(QColor("teal"))
    return made


def test_the_cover_given_before_the_window_shows_stays():
    made = window()
    made.set_image(picture())                 # before show(), as detach() does
    made.show()
    QApplication.processEvents()
    assert made.hero.has_pixmap()
    made.close()


def test_a_cover_not_here_yet_is_asked_for_and_shown_when_it_comes():
    made, loader = window(), Loader()
    made.load_image(loader, COVER)
    made.show()
    assert loader.asked == [COVER] and made.hero.note_text() == IMAGE_WAITING
    loader.url_ready.emit("https://cdn.example/other.jpg", "", picture())
    assert not made.hero.has_pixmap()          # another cover's: not this one
    loader.url_ready.emit(COVER, "", picture())
    assert made.hero.has_pixmap()
    made.close()


def test_a_cover_that_cant_be_had_says_so():
    made, loader = window(), Loader()
    made.load_image(loader, COVER)
    loader.url_failed.emit(COVER, "gone")
    assert made.hero.note_text() == IMAGE_UNAVAILABLE
    made.close()
