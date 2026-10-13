# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the cover image field (nostr/ui/cover_field.py) that Publish as
Article and Article Details share.

What must hold:

  Choose… (with the real ellipsis) opens the Media Library's picker.
  Remove takes the cover away and shows only while there is one.

  A picture file dropped on the well is uploaded without what it reveals
  besides its pixels (the scrubbed bytes, never the file's own), and is
  the cover once its upload is done; until then the well says it is
  uploading. A file that is no picture, a failed upload, and a window
  without media servers each say why, and leave the cover as it was.

  A cover from the account's media library shows its picture; an
  address typed shows none (nothing typed is downloaded), and typing
  over a library cover takes its picture away.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QObject, Qt, Signal  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.blossom.store import MediaFile  # noqa: E402
from nostr.ui import cover_field  # noqa: E402

SHA = "ab" * 32
LIBRARY_URL = f"https://blossom.example/{SHA}.png"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class FakeLoader(QObject):
    ready = Signal(str, str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.asked = []

    def load(self, sha, url):
        self.asked.append((sha, url))


class FakeStore(QObject):
    upload_finished = Signal(str, object)
    upload_failed = Signal(str, str)
    upload_progress = Signal(str, int, int)

    def __init__(self):
        super().__init__()
        self.files = {SHA: MediaFile(hash=SHA, url=LIBRARY_URL, mime_type="image/png")}
        self.uploads = []

    def upload_bytes(self, body, *, name, mime_type="application/octet-stream"):
        self.uploads.append((body, name, mime_type))


@pytest.fixture
def field(monkeypatch):
    monkeypatch.setattr(cover_field, "ThumbnailLoader", FakeLoader)
    store = FakeStore()
    made = cover_field.CoverImageField(media_store=store)
    problems = []
    made.problem.connect(problems.append)
    return made, store, problems


def picture_file(tmp_path, name="cover.png"):
    image = QImage(4, 3, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    image.setText("Location", "52.5200,13.4050")          # what must not be published
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    path = tmp_path / name
    path.write_bytes(bytes(buffer.data()))
    return path


def test_choose_and_remove_say_what_they_do(field):
    made, _store, _problems = field
    assert made.pick_button.text() == "Choose…"
    assert made.remove_button.isHidden()
    changed = []
    made.changed.connect(changed.append)
    made.setText("https://cdn.example/c.jpg")
    assert not made.remove_button.isHidden()
    made.remove_button.click()
    assert made.text() == "" and changed[-1] == "" and made.remove_button.isHidden()


def test_a_dropped_picture_is_uploaded_scrubbed_and_becomes_the_cover(field, tmp_path):
    made, store, problems = field
    path = picture_file(tmp_path)
    made.drop(str(path))
    [(body, name, mime)] = store.uploads
    assert body != path.read_bytes() and b"52.5200" not in body
    assert mime == "image/png" and made.text() == ""
    assert made.preview.text() == "Uploading…"
    store.upload_finished.emit("other.png", MediaFile(hash="cd" * 32, url="https://x/o.png"))
    assert made.text() == ""                                  # another upload: not ours
    url = f"https://blossom.example/{'ef' * 32}.png"
    store.upload_finished.emit(name, MediaFile(hash="ef" * 32, url=url, mime_type="image/png"))
    assert made.text() == url and problems == []
    assert made._loader.asked[-1] == ("ef" * 32, url)


def test_what_cannot_be_a_cover_is_said_and_changes_nothing(field, tmp_path):
    made, store, problems = field
    made.setText(LIBRARY_URL)
    notes = tmp_path / "notes.txt"
    notes.write_text("words", encoding="utf-8")
    made.drop(str(notes))
    assert problems == ["Only a picture can be a cover."] and store.uploads == []
    made.drop(str(picture_file(tmp_path)))
    store.upload_failed.emit(store.uploads[0][1], "No media server is set up.")
    assert problems[-1] == "The picture was not uploaded: No media server is set up."
    assert made.text() == LIBRARY_URL and made.preview.text() != "Uploading…"
    alone = cover_field.CoverImageField()
    said = []
    alone.problem.connect(said.append)
    alone.drop(str(picture_file(tmp_path, "other.png")))
    assert said and "web address" in said[0]


def test_a_library_cover_shows_its_picture_and_a_typed_one_none(field):
    made, _store, _problems = field
    pictures = []
    made.picture_changed.connect(lambda: pictures.append(made.picture().isNull()))
    made.setText(LIBRARY_URL)
    assert made._loader.asked == [(SHA, LIBRARY_URL)]
    made._loader.ready.emit(SHA, "", QPixmap(16, 9))
    assert not made.picture().isNull() and pictures == [False]
    made.setText("https://cdn.example/typed.jpg")
    assert made.picture().isNull() and pictures == [False, True]
    assert made.preview.text() == "Preview shown\nfor library picks"
    assert len(made._loader.asked) == 1                      # nothing typed is fetched


def test_a_read_only_cover_takes_no_drop_and_offers_no_change(field, tmp_path):
    made, store, _problems = field
    made.setReadOnly(True)
    made.drop(str(picture_file(tmp_path)))
    assert store.uploads == []
    assert not made.pick_button.isEnabled() and not made.remove_button.isEnabled()
    assert not made.preview.acceptDrops() or not made.preview.isEnabled()


def test_the_picker_is_titled_in_title_case_as_its_button_names_it(field, monkeypatch):
    made, _store, _problems = field
    titles = []

    class Picker:
        def __init__(self, **_kwargs):
            self._filter_combo = type("Combo", (), {"setCurrentIndex": lambda *_a: None})()
            self.file_picked = type("Signal", (), {"connect": lambda *_a: None})()

        def setWindowTitle(self, title):
            titles.append(title)

        def exec(self):
            return 0

    monkeypatch.setattr(cover_field, "MediaLibraryDialog", Picker)
    made.choose()
    assert titles == ["Choose Cover Image"] == [made.pick_button.accessibleName()]


def test_the_well_shows_how_far_the_upload_is_and_says_when_it_is_done(field, tmp_path):
    made, store, _problems = field
    busy = []
    made.uploading_changed.connect(busy.append)
    assert not made.uploading()
    made.drop(str(picture_file(tmp_path)))
    name = store.uploads[0][1]
    assert made.uploading() and busy == [True]
    store.upload_progress.emit("other.png", 50, 100)
    assert made.preview.text() == "Uploading\u2026"           # another upload
    store.upload_progress.emit(name, 42, 100)
    assert made.preview.text() == "Uploading\u2026 42 %"
    store.upload_finished.emit(name, MediaFile(hash="ef" * 32, url="https://x/" + "ef" * 32))
    assert not made.uploading() and busy == [True, False]
    made.drop(str(picture_file(tmp_path, "b.png")))
    store.upload_failed.emit(store.uploads[1][1], "the server said no")
    assert not made.uploading() and busy == [True, False, True, False]
