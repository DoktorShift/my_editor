# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pictures in the text are shown no wider than the text, as Pages and
Notes show them, without changing the picture, the document or its undo
history."""

import pytest
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import QApplication

from editor import HtmlEditor


@pytest.fixture
def editor():
    QApplication.instance() or QApplication([])
    ed = HtmlEditor()
    ed.resize(700, 500)
    ed.show()
    QApplication.processEvents()
    yield ed
    ed.document().setModified(False)
    ed.close()
    ed.deleteLater()


def _png(width, height):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("teal"))
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return QByteArray(buf.data())


def _insert(ed, name, payload):
    ed.add_picture_resource(name, payload)
    fmt = QTextImageFormat()
    fmt.setName(name)
    cursor = ed.textCursor()
    cursor.insertImage(fmt)
    ed.setTextCursor(cursor)
    QApplication.processEvents()


def _shown_width(ed, name):
    found = ed.document().resource(QTextDocument.ImageResource, QUrl(name))
    return found.width() / found.devicePixelRatio(), found


def test_a_photo_wider_than_the_text_is_shown_at_the_texts_width(editor):
    _insert(editor, "asset:photo", _png(3000, 2000))
    shown, image = _shown_width(editor, "asset:photo")
    assert shown <= editor._room_for_pictures() + 0.5
    assert image.width() == 3000      # every pixel kept
    assert editor.document().size().width() <= editor.viewport().width() + 1


def test_a_small_picture_keeps_its_own_size(editor):
    _insert(editor, "asset:icon", _png(120, 80))
    shown, image = _shown_width(editor, "asset:icon")
    assert shown == 120
    assert image.devicePixelRatio() == 1.0


def test_fitting_neither_marks_the_document_edited_nor_adds_an_undo_step(editor):
    _insert(editor, "asset:photo", _png(2400, 1600))
    editor.document().setModified(False)
    steps = editor.document().availableUndoSteps()
    editor.resize(400, 500)
    QApplication.processEvents()
    QApplication.processEvents()
    assert not editor.document().isModified()
    assert editor.document().availableUndoSteps() == steps


def test_the_picture_follows_the_width_of_the_window(editor):
    _insert(editor, "asset:photo", _png(3000, 2000))
    editor.resize(420, 500)
    QApplication.processEvents()
    QApplication.processEvents()
    narrow, _ = _shown_width(editor, "asset:photo")
    editor.resize(1200, 500)
    QApplication.processEvents()
    QApplication.processEvents()
    wide, _ = _shown_width(editor, "asset:photo")
    assert narrow <= editor._room_for_pictures()
    assert narrow < wide <= 3000


def test_paper_mode_fits_the_picture_to_the_page(editor):
    editor.set_prose(True)
    _insert(editor, "asset:photo", _png(3000, 2000))
    editor.resize(1600, 600)
    QApplication.processEvents()
    editor.set_paper_mode(True)
    QApplication.processEvents()
    QApplication.processEvents()
    shown, _ = _shown_width(editor, "asset:photo")
    assert shown <= editor._room_for_pictures() + 0.5
    assert shown < 1400


def test_a_picture_read_beside_the_file_is_fitted_too(editor):
    big = QImage(2600, 1000, QImage.Format.Format_RGB32)
    big.fill(QColor("orange"))
    editor.set_local_image_resolver(lambda name: big)
    editor.setHtml('<p>x<img src="pictures/big.png"></p>')
    QApplication.processEvents()
    QApplication.processEvents()
    assert editor.document().size().width() <= editor.viewport().width() + 1
