"""Edit Profile's card: the banner, the picture in a circle over it, the
name and the address, or the name's initials without a picture."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nostr.ui import profile_card  # noqa: E402
from nostr.ui.profile_card import ProfileCard, initials  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def _filled(color: str) -> QPixmap:
    pixmap = QPixmap(60, 30)
    pixmap.fill(QColor(color))
    return pixmap


def _drawn(card: ProfileCard):
    card.resize(card.sizeHint())
    return card.grab().toImage()


def test_initials_are_the_first_letters_of_up_to_two_words():
    assert initials("Ann Bell") == "AB"
    assert initials("DrShift") == "D"
    assert initials("ann marie bell") == "AM"
    assert initials("  ") == "" and initials("⚡ Lightning") == "L"


def test_the_picture_fills_its_circle_and_the_banner_the_top():
    _app()
    card = ProfileCard()
    card.set_picture(_filled("#FF0000"))
    card.set_banner(_filled("#0000FF"))
    image = _drawn(card)
    middle = profile_card.INSET + profile_card.AVATAR // 2
    avatar_center = image.pixelColor(middle, profile_card.BANNER_HEIGHT)
    banner = image.pixelColor(card.width() - 20, 10)
    assert avatar_center.red() > 200 and avatar_center.blue() < 60
    assert banner.blue() > 200 and banner.red() < 60


def test_without_pictures_the_card_is_quiet_and_says_what_it_shows():
    _app()
    card = ProfileCard()
    card.set_text("Ann Bell", "ann@example.com")
    assert card.accessibleName() == "Your profile as others see it"
    assert card.accessibleDescription() == "Ann Bell, ann@example.com"
    image = _drawn(card)
    banner = image.pixelColor(card.width() - 20, 10)
    assert abs(banner.red() - banner.blue()) < 30          # a tint, no color of its own
    card.set_text("", "")
    assert card.accessibleDescription() == "No name yet"
