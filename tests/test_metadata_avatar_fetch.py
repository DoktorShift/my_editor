"""AvatarLoader.fetch: one answer for one picture, for its own key only."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.metadata import AvatarLoader  # noqa: E402


def test_fetch_answers_once_for_its_key(tmp_path, monkeypatch):
    QApplication.instance() or QApplication([])
    loader = AvatarLoader(cache_dir=tmp_path)
    monkeypatch.setattr(loader, "load", lambda key, url: None)
    answers = []
    loader.fetch("me#banner", "https://media.example/wide.png", answers.append)
    pixmap = QPixmap(4, 4)
    loader.ready.emit("someone-else", QPixmap(2, 2))
    assert answers == []
    loader.ready.emit("me#banner", pixmap)
    loader.ready.emit("me#banner", QPixmap(3, 3))
    assert answers == [pixmap]


def test_fetch_says_none_when_the_picture_cant_be_had(tmp_path):
    QApplication.instance() or QApplication([])
    loader = AvatarLoader(cache_dir=tmp_path)
    answers = []
    loader.fetch("me#picture", "file:///etc/passwd", answers.append)
    assert answers == [None]
