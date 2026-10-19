# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins where a picture, audio or video of a document is, and that the
person can always tell: on this computer only until the document is
published, on its way, failed, or on the media servers.

The defect classes this file guards against:
- a picture on this computer looking just like one anyone can fetch,
- a badge drawn where the object is not, or on an object too small for it,
- a badge or a menu command showing without a Nostr account,
- publishing waiting forever, or going on before every file is there,
- a document whose file name has a space losing its pictures and files
  when it is reopened.
"""

import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QUrl
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import QApplication, QMenu

import image_safety
import media_badge
from editor import HtmlEditor
from main_window import MainWindow
from markdown_writer import holds_faithfully, link_markdown
from nostr.media.assets import AssetIndex, AssetState
from nostr.media.manager import AssetManager
from nostr.media.upload_wait import UploadWait

from tests.media_fakes import GIF_BYTES, PNG_BYTES, FakeBlobStore, FakeUploader, sha_of


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _manager(tmp_path, profile=object()):
    return AssetManager(blob_store=FakeBlobStore(Path(tmp_path) / "cache"),
                        uploader=FakeUploader(), profile_provider=lambda: profile,
                        decoder=image_safety.decode_image_bytes,
                        index=AssetIndex(path=Path(tmp_path) / "media_assets.json"))


def _uploaded(manager, data=PNG_BYTES):
    adopted = manager.adopt_bytes(data, alt="harbour")
    return manager.adopt_library_file(sha256=adopted.sha256, mime=adopted.mime, alt="harbour",
                                      remote_url=f"https://cdn.example/{adopted.sha256}.png")


def _finish(manager):
    uploader = manager._uploader
    call = uploader.calls[-1]
    uploader.finish(call.name, types.SimpleNamespace(
        url=f"https://cdn.example/{sha_of(call.body)}.png", size=len(call.body), servers=()))


# --------------------------------------------------------------------------- #
# The badge, where the object is
# --------------------------------------------------------------------------- #

def _editor_with_picture(width, height, name="myeditor-asset:" + "a" * 64):
    ed = HtmlEditor()
    ed.resize(700, 500)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    ed.document().addResource(QTextDocument.ImageResource, QUrl(name), image)
    cursor = ed.textCursor()
    cursor.insertText("Above")
    cursor.insertBlock()
    fmt = QTextImageFormat()
    fmt.setName(name)
    cursor.insertImage(fmt)
    ed.show()
    QApplication.processEvents()
    return ed


def test_an_object_is_found_where_it_is_drawn():
    ed = _editor_with_picture(240, 120)
    [(name, box)] = list(media_badge.objects_in_sight(ed))
    at = ed.document().find("￼")
    at.setPosition(at.selectionStart())
    caret = ed.cursorRect(at)
    assert name == "myeditor-asset:" + "a" * 64
    assert (round(box.width()), round(box.height())) == (240, 120)
    assert (box.left(), box.top()) == (caret.left(), caret.top())
    assert media_badge.object_at(ed, box.center().toPoint()) == name
    assert media_badge.object_at(ed, QPoint(int(box.right()) + 20, int(box.center().y()))) is None
    ed.close()


def _painted(ed, badge_of):
    asked = []
    image = QImage(ed.viewport().size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    media_badge.paint(ed, painter, lambda name: asked.append(name) or badge_of(name))
    painter.end()
    return asked, image


def test_a_badge_is_drawn_on_the_objects_top_left_corner():
    ed = _editor_with_picture(240, 120)
    [(_name, box)] = list(media_badge.objects_in_sight(ed))
    badge = media_badge.Badge(media_badge.LOCAL, "On This Computer")
    asked, image = _painted(ed, lambda name: badge)
    assert len(asked) == 1
    inside = box.topLeft().toPoint() + QPoint(14, 18)
    outside = box.bottomRight().toPoint() - QPoint(10, 10)
    assert image.pixelColor(inside).alpha() > 0
    assert image.pixelColor(outside).alpha() == 0
    ed.close()


def test_an_object_too_small_for_a_badge_wears_none():
    ed = _editor_with_picture(40, 20)
    asked, _image = _painted(ed, lambda name: media_badge.Badge(media_badge.LOCAL, "x"))
    assert asked == []
    ed.close()


def test_the_editor_shows_the_badge_the_window_answers_as_its_tooltip():
    ed = _editor_with_picture(240, 120)
    ed.set_media_badges(lambda name: media_badge.Badge(media_badge.LOCAL, "On", "Only here."))
    [(_name, box)] = list(media_badge.objects_in_sight(ed))
    assert ed._badge_at(box.center().toPoint()).hint == "Only here."
    assert ed.media_object_at(box.center().toPoint()) == _name
    ed.close()


# --------------------------------------------------------------------------- #
# What the window answers
# --------------------------------------------------------------------------- #

class _Window:
    _asset_named = MainWindow._asset_named
    _local_image_bytes = staticmethod(MainWindow._local_image_bytes)
    _media_badge = MainWindow._media_badge
    _media_context = MainWindow._media_context
    _upload_object = MainWindow._upload_object
    _copy_link = staticmethod(MainWindow._copy_link)

    def __init__(self, tmp_path, *, nostr=True):
        self._asset_manager = _manager(tmp_path)
        self.nostr_state = types.SimpleNamespace(active=nostr)
        self.status = types.SimpleNamespace(showMessage=lambda *a: None)


def _places(win, ed, *names):
    return [getattr(win._media_badge(ed, name), "place", None) for name in names]


def test_each_object_wears_where_it_is(tmp_path):
    win = _Window(tmp_path)
    manager = win._asset_manager
    local = manager.adopt_bytes(GIF_BYTES, alt="sketch")
    hosted = _uploaded(manager)
    ed = HtmlEditor()
    assert _places(win, ed, local.key, hosted.key, hosted.remote_url,
                   "https://elsewhere.example/cat.png", "Trip_media/sea.png") == [
        media_badge.LOCAL, media_badge.UPLOADED, media_badge.UPLOADED, None, media_badge.LOCAL]
    manager.request_upload(local.sha256)
    assert _places(win, ed, local.key) == [media_badge.UPLOADING]
    manager._uploader.fail(manager._uploader.calls[-1].name, "UPLOAD_FAILED")
    failed = win._media_badge(ed, local.key)
    assert failed.place == media_badge.FAILED and failed.hint.endswith("Publishing tries again.")


def test_nothing_of_it_shows_without_a_nostr_account(tmp_path):
    win = _Window(tmp_path, nostr=False)
    local = win._asset_manager.adopt_bytes(GIF_BYTES, alt="sketch")
    assert win._media_badge(HtmlEditor(), local.key) is None


def _menu_on(win, ed, name):
    ed.media_object_at = lambda pos: name
    menu = QMenu()
    win._media_context(menu, ed, QPoint(0, 0))
    return menu


def test_a_local_object_can_be_uploaded_from_its_menu(tmp_path):
    win = _Window(tmp_path)
    local = win._asset_manager.adopt_bytes(GIF_BYTES, alt="sketch")
    menu = _menu_on(win, HtmlEditor(), local.key)
    [upload] = [a for a in menu.actions() if a.text() == "Upload to Media Servers"]
    upload.trigger()
    assert win._asset_manager.get(local.sha256).upload_state is AssetState.SIGNING


def test_an_uploaded_object_gives_its_link(tmp_path):
    win = _Window(tmp_path)
    hosted = _uploaded(win._asset_manager)
    menu = _menu_on(win, HtmlEditor(), hosted.key)
    [copy] = [a for a in menu.actions() if a.text() == "Copy Link"]
    copy.trigger()
    assert QGuiApplication.clipboard().text() == hosted.remote_url


def test_the_menu_offers_nothing_of_it_without_an_account(tmp_path):
    win = _Window(tmp_path, nostr=False)
    local = win._asset_manager.adopt_bytes(GIF_BYTES, alt="sketch")
    assert _menu_on(win, HtmlEditor(), local.key).actions() == []


# --------------------------------------------------------------------------- #
# Waiting for uploads
# --------------------------------------------------------------------------- #

_WAITS = []                  # held as the window holds its wait


def _wait(manager, *assets):
    wait = UploadWait(manager, [asset.sha256 for asset in assets])
    _WAITS.append(wait)
    seen = []
    wait.done.connect(lambda: seen.append("done"))
    wait.failed.connect(lambda sha, code: seen.append(("failed", sha, code)))
    wait.start()
    return seen


def test_a_wait_for_files_already_there_is_done_at_once(tmp_path):
    manager = _manager(tmp_path)
    assert _wait(manager, _uploaded(manager)) == ["done"]


def test_a_wait_is_done_only_once_every_file_is_there(tmp_path):
    manager = _manager(tmp_path)
    first = manager.adopt_bytes(GIF_BYTES, alt="one")
    second = manager.adopt_bytes(PNG_BYTES, alt="two")
    seen = _wait(manager, first, second)
    _finish(manager)
    assert seen == []
    _finish(manager)
    assert seen == ["done"]


def test_a_wait_ends_at_the_first_failure_saying_which(tmp_path):
    manager = _manager(tmp_path)
    local = manager.adopt_bytes(GIF_BYTES, alt="one")
    seen = _wait(manager, local)
    manager._uploader.fail(manager._uploader.calls[-1].name, "UPLOAD_FAILED")
    _finish(manager)
    assert seen == [("failed", local.sha256, "UPLOAD_FAILED")]


def test_a_wait_cancelled_says_nothing_more(tmp_path):
    manager = _manager(tmp_path)
    local = manager.adopt_bytes(GIF_BYTES, alt="one")
    wait = UploadWait(manager, [local.sha256])
    seen = []
    wait.done.connect(lambda: seen.append("done"))
    wait.start()
    wait.cancel()
    _finish(manager)
    assert seen == []


# --------------------------------------------------------------------------- #
# A document whose name has a space
# --------------------------------------------------------------------------- #

def test_an_address_with_a_space_is_written_so_it_reads_back():
    assert link_markdown("Walk [1].mp4", "My Trip_media/a.mp4") == (
        "[Walk \\[1\\].mp4](<My Trip_media/a.mp4>)")
    assert link_markdown("Walk.mp4", "Trip_media/a.mp4") == "[Walk.mp4](Trip_media/a.mp4)"


@pytest.mark.parametrize("markdown, faithful", [
    ("Look:\n\n![sea](<My Trip_media/a.png>)\n", True),
    ("Listen:\n\n[song.mp3](<My Trip_media/a.mp3>)\n", True),
    ("a <b>bold</b> word\n", False),
    ("[a](<b c>) and <span>x</span>\n", False),
])
def test_a_link_address_in_angle_brackets_is_not_raw_html(markdown, faithful):
    # It opened as its Markdown text, the pictures and files gone from view.
    assert holds_faithfully(markdown) is faithful
