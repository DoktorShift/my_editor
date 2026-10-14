# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The selected framing must reach the uploaded bytes, including after tab changes."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

import image_safety
import i18n
from nostr.blossom.store import MediaFile
from nostr.media.cover_crop import WIDE_ASPECT, crop_rect, export_cover, output_size
from nostr.ui import cover_crop_dialog, cover_field
from nostr.ui.cover_crop_dialog import CoverCropDialog, CropCanvas
from tests.accessibility import unnamed_controls
from tests.test_cover_field import LIBRARY_URL, SHA, field  # noqa: F401, pytest fixture


def portrait(width=320, height=640):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("green"))
    painter = QPainter(image)
    painter.fillRect(0, 0, width, height // 3, QColor("red"))
    painter.fillRect(0, height * 2 // 3, width, height, QColor("blue"))
    painter.end()
    image.setText("Location", "private camera location")
    return image


@pytest.mark.parametrize("size", [QSize(320, 640), QSize(1920, 1080), QSize(3000, 600)])
@pytest.mark.parametrize("aspect", [WIDE_ASPECT, 1.5, 1.0])
@pytest.mark.parametrize("focus", [QPointF(0, 0), QPointF(0.5, 0.5), QPointF(1, 1)])
def test_crop_stays_inside_source_and_keeps_aspect(size, aspect, focus):
    rect = crop_rect(size, aspect, focus, zoom=2)
    assert rect.left() >= 0 and rect.top() >= 0
    assert rect.right() <= size.width() and rect.bottom() <= size.height()
    assert rect.width() / rect.height() == pytest.approx(aspect)


def test_recommended_output_is_1600_by_900_and_small_images_are_not_upscaled():
    assert output_size(crop_rect(QSize(4000, 3000))) == QSize(1600, 900)
    assert output_size(crop_rect(QSize(320, 640))) == QSize(320, 180)


@pytest.mark.parametrize("focus,color", [(QPointF(0.5, 0), "red"), (QPointF(0.5, 1), "blue")])
def test_export_applies_the_focal_point_and_strips_metadata(focus, color):
    source = portrait()
    result = export_cover(source, crop_rect(source.size(), focus=focus))
    image = image_safety.decode_image_bytes(result.data)
    assert result.mime == "image/png" and image.size() == QSize(320, 180)
    assert image.pixelColor(160, 90) == QColor(color)
    assert image.textKeys() == [] and b"private camera" not in result.data
    assert source.text("Location") == "private camera location"   # original untouched


def test_jpeg_cover_stays_small_and_transparency_is_preserved():
    image = portrait()
    result = export_cover(image, crop_rect(image.size()), jpeg=True)
    assert image_safety.sniff_image_mime(result.data) == "image/jpeg"
    transparent = QImage(320, 180, QImage.Format.Format_ARGB32)
    transparent.fill(Qt.GlobalColor.transparent)
    result = export_cover(transparent, crop_rect(transparent.size()), jpeg=True)
    assert result.mime == "image/png"
    assert image_safety.decode_image_bytes(result.data).pixelColor(0, 0).alpha() == 0


def test_drag_click_and_keyboard_move_the_same_crop_and_announce_position(monkeypatch):
    spoken = []
    monkeypatch.setattr(cover_crop_dialog.announce, "announce", lambda _source, text: spoken.append(text))
    canvas = CropCanvas(portrait())
    canvas.resize(400, 340)
    canvas.show()
    try:
        initial = canvas.source_rect().top()
        QTest.keyClick(canvas, Qt.Key.Key_Up)
        assert canvas.source_rect().top() < initial and "down" in spoken[-1]
        area = canvas.image_rect()
        QTest.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=(area.topLeft() + QPointF(3, 3)).toPoint())
        assert canvas.source_rect().top() == 0
        start = canvas.frame_rect().center().toPoint()
        QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas, (area.bottomLeft() + QPointF(10, -10)).toPoint())
        QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton)
        assert canvas.source_rect().top() > initial
        assert not canvas.grab().isNull()
    finally:
        canvas.close()
        canvas.deleteLater()


def test_format_zoom_and_reset_have_live_feedback_and_cancel_creates_no_cover():
    dialog = CoverCropDialog(portrait(), is_dark=False)
    try:
        assert unnamed_controls(dialog) == []
        assert dialog.format.currentData() == WIDE_ASPECT
        assert "320 × 180" in dialog.dimensions.text()
        dialog.format.setCurrentIndex(2)
        dialog.zoom.setValue(200)
        assert "160 × 160" in dialog.dimensions.text() and dialog.zoom_value.text() == "200 %"
        dialog.reset_button.click()
        assert dialog.format.currentData() == WIDE_ASPECT and dialog.zoom.value() == 100
        assert dialog.canvas.focus == QPointF(0.5, 0.5)
        dialog.reject()
        assert dialog.cover is None
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("language", ["en", "de"])
def test_small_crop_dialog_keeps_action_labels_whole(language):
    i18n.install(language)
    dialog = CoverCropDialog(portrait(), is_dark=False)
    try:
        dialog.show()
        dialog.resize(340, 260)
        QApplication.processEvents()
        assert dialog.width() <= 340
        for button in (dialog.reset_button, *dialog.buttons.buttons()):
            assert button.isVisible() and button.width() >= button.sizeHint().width()
            area = button.rect()
            area.moveTopLeft(button.mapTo(dialog, area.topLeft()))
            assert dialog.rect().contains(area)
    finally:
        dialog.close()
        dialog.deleteLater()
        i18n.install(i18n.ENGLISH)


def show_source(made):
    made.setText(LIBRARY_URL)
    made._loader.ready.emit(SHA, "", QPixmap.fromImage(portrait()))


def test_cover_field_says_the_recommendation_and_shows_the_card_crop(field):
    made, _store, _problems = field
    assert "16:9" in made._guidance.text() and "1600 × 900" in made._guidance.text()
    assert not made.adjust_button.isEnabled()
    show_source(made)
    assert "320 × 640" in made._dimensions.text() and made.adjust_button.isEnabled()
    assert made.preview.pixmap().width() / made.preview.pixmap().height() == pytest.approx(16 / 9, abs=0.03)
    made.setReadOnly(True)
    assert made.adjust_button.isHidden() and made._guidance.isHidden()


def test_cover_controls_follow_visual_order_with_the_keyboard(field):
    made, _store, _problems = field
    show_source(made)
    made.show()
    made.activateWindow()
    QApplication.processEvents()
    try:
        made.pick_button.setFocus()
        QTest.keyClick(made.pick_button, Qt.Key.Key_Tab)
        assert made.adjust_button.hasFocus()
        QTest.keyClick(made.adjust_button, Qt.Key.Key_Tab)
        assert made.remove_button.hasFocus()
    finally:
        made.close()


def test_accepted_crop_uploads_new_bytes_and_waits_for_the_real_public_url(field, monkeypatch):
    made, store, problems = field
    show_source(made)
    changed = []
    made.changed.connect(changed.append)

    def apply(dialog):
        dialog.canvas._move_focus(QPointF(0.5, 0))
        dialog.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CoverCropDialog, "exec", apply)
    made.adjust()
    [(data, name, mime)] = store.uploads
    assert mime == "image/png" and image_safety.decode_image_bytes(data).pixelColor(160, 90) == QColor("red")
    assert made.text() == LIBRARY_URL and made.uploading() and not made.adjust_button.isEnabled()
    url = "https://blossom.example/" + "cd" * 32 + ".png"
    store.upload_finished.emit(name, MediaFile(hash="cd" * 32, url=url, mime_type=mime))
    assert made.text() == url and changed == [url] and not made.uploading() and not problems


@pytest.mark.parametrize("change", ["cancel", "tab", "cover", "account", "busy", "read_only"])
def test_cancel_or_context_change_during_adjustment_does_not_upload(field, monkeypatch, change):
    made, store, _problems = field
    show_source(made)
    account = ["alice"]
    made._profile = lambda: SimpleNamespace(user_pubkey=account[0])

    def apply(dialog):
        if change == "cancel":
            dialog.reject()
            return QDialog.DialogCode.Rejected
        dialog.accept()
        if change == "tab":
            made.set_owner(object(), "https://cdn.example/another.png")
        elif change == "cover":
            made.setText("https://cdn.example/another.png")
        elif change == "account":
            account[0] = "bob"
        elif change == "busy":
            made.setBusy(True)
        elif change == "read_only":
            made.setReadOnly(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CoverCropDialog, "exec", apply)
    made.adjust()
    assert store.uploads == []


def test_crop_upload_failure_keeps_existing_cover_and_says_why(field, monkeypatch):
    made, store, problems = field
    show_source(made)
    monkeypatch.setattr(CoverCropDialog, "exec", lambda dialog: (dialog.accept(), QDialog.DialogCode.Accepted)[1])
    made.adjust()
    store.upload_failed.emit(store.uploads[0][1], "server refused")
    assert made.text() == LIBRARY_URL and not made.uploading() and "server refused" in problems[-1]
