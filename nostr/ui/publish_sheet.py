# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What Publish as Article and Publish as Note share.

Both are sheets on the document's window (window-modal; on macOS they
slide down from its title bar), never taller than that window, in the
theme's colours, light and dark. Both show who the event will tag, and
who publishes it, the same way:

``PeopleRow``
    Everyone the event tags (its ``p`` tags, read from the event the
    publisher builds, so the row is the truth): by name with their
    avatar, never by key, each labelled as mentioned in the text, quoted
    (the author of a note or article the text quotes) or co-author. A
    co-author has × to remove them; the article's row ends in Add
    Co-Author…. A click on a person shows their name and Nostr address.

``AccountRow``
    "Publishing as" with the account's avatar and name, and a menu to
    publish as another account.

``StatusLine``
    Progress in muted words, a failure in the theme's red, above the
    buttons; hidden while there is nothing to say.

``BottomRow``
    "Publishing as" at the leading edge, Cancel and Publish at the
    trailing edge; in a window too narrow for both, the buttons go under
    the account.
"""

from __future__ import annotations

import sys
from typing import Iterable, List, Optional, Tuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QBoxLayout, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QStyle, QToolButton, QToolTip, QWidget,
)

import announce
import theme
from constants import DARK_BORDER, DARK_MUTED_FG, LIGHT_BORDER, LIGHT_MUTED_FG
from i18n import _
from popup_placement import fitting_size

from .. import refs
from ..article_details import AUTHOR_MARKER
from ..avatar_store import AvatarStore
from ..bech32 import encode_npub
from ..known_people import KnownPeople
from ..profiles import Profile, ProfileStore
from ..publisher import extract_inline_mentions
from .assistant import stylesheet as assistant_stylesheet
from .avatar import (
    AVATAR_SIZE, CHIP_TOTAL_WIDTH, compose_chip_icon, make_avatar_pixmap_from_image,
    pixmap_for_profile,
)
from .flow_layout import FlowLayout

# How a person is tagged.
MENTIONED = "mentioned"
QUOTED = "quoted"
COAUTHOR = "co-author"

_PERSON_AVATAR = 18
# Room kept between a sheet and the edges of the window it hangs from.
_WINDOW_MARGIN = 24


def make_sheet(dialog: QDialog) -> None:
    """``dialog`` as a sheet on its window: window-modal, and on macOS a
    sheet that slides down from the window's title bar."""
    dialog.setWindowModality(Qt.WindowModality.WindowModal)
    if sys.platform == "darwin":
        dialog.setWindowFlag(Qt.WindowType.Sheet, True)


def fit_to_window(dialog: QDialog, wanted: QSize) -> None:
    """Open ``dialog`` at ``wanted``, made small enough for the window it
    is attached to and for the screen: never taller than its window (its
    content scrolls instead)."""
    size = fitting_size(dialog, wanted)
    parent = dialog.parentWidget()
    window = parent.window() if parent is not None else None
    if window is not None and window.isVisible():
        size = QSize(min(size.width(), window.width() - _WINDOW_MARGIN),
                     min(size.height(), window.height() - _WINDOW_MARGIN))
    least = dialog.minimumSize()
    dialog.setMinimumSize(QSize(min(least.width(), size.width()),
                                min(least.height(), size.height())))
    dialog.resize(size)


def sheet_stylesheet(is_dark: bool) -> str:
    """The app's dialog look (the assistants'), with the chips of people."""
    border = DARK_BORDER if is_dark else LIGHT_BORDER
    muted = DARK_MUTED_FG if is_dark else LIGHT_MUTED_FG
    ring = theme.focus_ring_color(is_dark)
    return assistant_stylesheet(is_dark) + f"""
    QFrame#person_chip {{ background: {border}; border-radius: 11px; }}
    QLabel#person_role {{ color: {muted}; font-size: 11px; }}
    QToolButton#person_remove {{
        color: {muted}; background: transparent; border: 1px solid transparent;
        border-radius: 8px; padding: 0 4px;
    }}
    QToolButton#person_remove:focus {{ border-color: {ring}; }}
    QToolButton#profile_switch {{
        background: transparent; border: 1px solid transparent; border-radius: 4px;
        padding: 3px 6px;
    }}
    QToolButton#profile_switch:hover {{ background: {border}; }}
    QToolButton#profile_switch:focus {{ border-color: {ring}; }}
    QToolButton#profile_switch::menu-indicator {{ image: none; width: 0; }}
    QLabel#article_cover_preview {{
        border: 1px solid {border}; border-radius: 4px; color: {muted}; font-size: 11px;
    }}
    QToolButton#disclosure {{ border: 1px solid transparent; padding: 2px 0; }}
    QToolButton#disclosure:focus {{ border-color: {ring}; }}
    """


def person_name(known_people: KnownPeople, pubkey: str) -> str:
    """How a person is called: their name when known, else their short
    Nostr address."""
    person = known_people.get(pubkey)
    if person is not None and person.display_name:
        return person.display_name
    return refs.short_key(encode_npub(pubkey))


def people_tagged(event: dict, content: str, *, markdown: bool) -> List[Tuple[str, str]]:
    """``(pubkey, role)`` for each person ``event`` tags, in its order:
    a co-author (the author marker), someone the text mentions, or the
    author of something it quotes. The signer's own author tag, written
    first when there are co-authors, is not one of them."""
    mentioned = {pubkey for pubkey, _hint in extract_inline_mentions(content,
                                                                      markdown=markdown)}
    signer = event.get("pubkey", "")
    people = []
    for tag in event.get("tags", ()):
        if tag[:1] != ["p"] or len(tag) < 2:
            continue
        author = len(tag) >= 4 and tag[3] == AUTHOR_MARKER
        if author and tag[1] == signer:
            continue
        role = COAUTHOR if author else MENTIONED if tag[1] in mentioned else QUOTED
        people.append((tag[1], role))
    return people


class _Person(QFrame):
    """One person: avatar, name and how they are tagged; × for a co-author."""

    def __init__(self, pubkey: str, name: str, role: str, avatar,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.pubkey = pubkey
        self.setObjectName("person_chip")
        words = {MENTIONED: _("mentioned"), QUOTED: _("quoted"),
                 COAUTHOR: _("co-author")}[role]
        self.setAccessibleName(_("{name}, {role}").format(name=name, role=words))
        try:
            address = encode_npub(pubkey)
        except (ValueError, TypeError):
            address = pubkey
        self.setToolTip(f"{name}\n{address}")
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 2, 8 if role != COAUTHOR else 2, 2)
        row.setSpacing(5)
        self.avatar = QLabel()
        self.avatar.setFixedSize(_PERSON_AVATAR, _PERSON_AVATAR)
        row.addWidget(self.avatar)
        self.set_avatar(avatar, name)
        label = QLabel(name)
        label.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(label)
        role_label = QLabel(words)
        role_label.setObjectName("person_role")
        row.addWidget(role_label)
        self.remove: Optional[QToolButton] = None
        if role == COAUTHOR:
            self.remove = QToolButton()
            self.remove.setObjectName("person_remove")
            self.remove.setText("×")
            remove = _("Remove {name}").format(name=name)
            self.remove.setAccessibleName(remove)
            self.remove.setToolTip(remove)
            self.remove.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(self.remove)

    def set_avatar(self, picture, name: str) -> None:
        self.avatar.setPixmap(
            make_avatar_pixmap_from_image(picture, size=_PERSON_AVATAR)
            if picture is not None and not picture.isNull()
            else pixmap_for_profile(name, self.pubkey, None, size=_PERSON_AVATAR))

    def mousePressEvent(self, event) -> None:  # noqa: N802, Qt's name
        # Who it is, at a click too: their name and Nostr address.
        QToolTip.showText(event.globalPosition().toPoint(), self.toolTip(), self)
        super().mousePressEvent(event)


class PeopleRow(QWidget):
    """Everyone tagged (see the module docstring). ``remove_requested`` with
    a co-author's pubkey when their × is chosen."""

    remove_requested = Signal(str)

    def __init__(self, known_people: KnownPeople, avatars: AvatarStore, *,
                 add_button: Optional[QPushButton] = None, nobody: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._known_people = known_people
        self._avatars = avatars
        self._people: List[_Person] = []
        self._flow = FlowLayout(self, spacing=6)
        self._nobody = QLabel(nobody)
        self._nobody.setObjectName("muted")
        self._nobody.setWordWrap(True)
        self._flow.addWidget(self._nobody)
        self._add_button = add_button
        if add_button is not None:
            self._flow.addWidget(add_button)
        avatars.avatar_added.connect(self._on_avatar_added)
        self.show_people(())

    def people(self) -> List[Tuple[str, str]]:
        """``(pubkey, accessible name)`` of each person shown."""
        return [(person.pubkey, person.accessibleName()) for person in self._people]

    def show_people(self, people: Iterable[Tuple[str, str]]) -> None:
        """Show ``people``, ``(pubkey, role)`` each, in order."""
        for person in self._people:
            self._flow.removeWidget(person)
            person.hide()
            person.deleteLater()
        self._flow.removeWidget(self._nobody)
        if self._add_button is not None:
            self._flow.removeWidget(self._add_button)
        self._people = []
        for pubkey, role in people:
            name = person_name(self._known_people, pubkey)
            person = _Person(pubkey, name, role, self._avatars.get(pubkey), self)
            if person.remove is not None:
                person.remove.clicked.connect(
                    lambda _checked=False, key=pubkey: self.remove_requested.emit(key))
            self._flow.addWidget(person)
            person.show()
            self._people.append(person)
        self._nobody.setVisible(not self._people and bool(self._nobody.text()))
        self._flow.addWidget(self._nobody)
        if self._add_button is None:
            return              # nobody to remove: a note tags no co-author
        self._flow.addWidget(self._add_button)
        # Tab goes left to right: each ×, then Add Co-Author… (each × follows
        # the button first, where Tab already goes; then the button moves
        # after the last of them).
        previous = self._add_button
        for person in self._people:
            if person.remove is not None:
                QWidget.setTabOrder(previous, person.remove)
                previous = person.remove
        if previous is not self._add_button:
            QWidget.setTabOrder(previous, self._add_button)

    def _on_avatar_added(self, pubkey: str, picture) -> None:
        for person in self._people:
            if person.pubkey == pubkey:
                person.set_avatar(picture, person_name(self._known_people, pubkey))


class AccountRow(QWidget):
    """"Publishing as" and the account, with a menu of the others.
    ``switched(profile)`` when another is chosen; it becomes the account
    in use, as at the window's own account menu."""

    switched = Signal(object)

    def __init__(self, profile: Profile, store: ProfileStore, avatars: AvatarStore, *,
                 is_dark: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._profile = profile
        self._store = store
        self._avatars = avatars
        self._is_dark = is_dark
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        label = QLabel(_("Publishing as"))
        row.addWidget(label)
        self.button = QToolButton()
        self.button.setObjectName("profile_switch")
        self.button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.button.setIconSize(QSize(CHIP_TOTAL_WIDTH, AVATAR_SIZE))
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu = QMenu(self.button)
        self.button.setMenu(self.menu)
        row.addWidget(self.button)
        avatars.avatar_added.connect(self._on_avatar_added)
        self._show()

    def profile(self) -> Profile:
        return self._profile

    def set_dark(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self._show()

    def _show(self) -> None:
        profile = self._profile
        avatar = pixmap_for_profile(profile.display_name, profile.user_pubkey,
                                    self._avatars.get(profile.user_pubkey), size=AVATAR_SIZE)
        muted = DARK_MUTED_FG if self._is_dark else LIGHT_MUTED_FG
        self.button.setIcon(compose_chip_icon(avatar, QColor(muted)))
        name = profile.display_name or profile.npub_short()
        self.button.setText(f"  {name}")
        self.button.setToolTip(profile.npub_short())
        self.button.setAccessibleName(_("Publishing as {name}").format(name=name))
        self.menu.clear()
        profiles = self._store.list()
        if not profiles:
            self.menu.addAction(_("(no accounts)")).setEnabled(False)
            return
        for other in profiles:
            action = self.menu.addAction(other.display_name or other.npub_short())
            action.setCheckable(True)
            action.setChecked(other.user_pubkey == profile.user_pubkey)
            action.triggered.connect(lambda _checked=False, p=other: self._switch(p))

    def _switch(self, profile: Profile) -> None:
        if profile.user_pubkey == self._profile.user_pubkey:
            return
        self._profile = profile
        self._store.set_default(profile.user_pubkey)
        self._show()
        self.switched.emit(profile)

    def _on_avatar_added(self, pubkey: str, _picture) -> None:
        if pubkey == self._profile.user_pubkey:
            self._show()


class StatusLine(QLabel):
    """What is going on, or what went wrong, above the buttons."""

    def __init__(self, is_dark: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._is_dark = is_dark
        self._error = False
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setVisible(False)

    def say(self, text: str, *, error: bool = False) -> None:
        self.setText(text)
        self.setVisible(bool(text))
        self._error = error
        self._color()
        if text and error:
            announce.announce(self, text)

    def set_dark(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self._color()

    def _color(self) -> None:
        color = (theme.error_text_color(self._is_dark) if self._error
                 else DARK_MUTED_FG if self._is_dark else LIGHT_MUTED_FG)
        self.setStyleSheet(f"color: {color};")


class BottomRow(QWidget):
    """The account and the buttons (see the module docstring)."""

    def __init__(self, account: AccountRow, buttons: QDialogButtonBox,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._account = account
        self._buttons = buttons
        self._row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(8)
        self._row.addWidget(account, 0, Qt.AlignmentFlag.AlignLeft)
        self._row.addStretch(1)
        self._row.addWidget(buttons, 0, Qt.AlignmentFlag.AlignRight)
        # Tab goes left to right: the account, then the buttons in the order
        # the platform puts them in (Cancel first on a Mac and in GNOME),
        # known before they are placed. (Never asked of the box's own layout
        # items: wrapping those leaves Python holding items the box deletes.)
        layout = buttons.style().styleHint(QStyle.StyleHint.SH_DialogButtonLayout, None,
                                           buttons)
        reject_first = layout in (QDialogButtonBox.ButtonLayout.MacLayout.value,
                                  QDialogButtonBox.ButtonLayout.GnomeLayout.value)
        accepts = [b for b in buttons.buttons()
                   if buttons.buttonRole(b) != QDialogButtonBox.ButtonRole.RejectRole]
        rejects = [b for b in buttons.buttons()
                   if buttons.buttonRole(b) == QDialogButtonBox.ButtonRole.RejectRole]
        previous = account.button
        for button in (rejects + accepts if reject_first else accepts + rejects):
            QWidget.setTabOrder(previous, button)
            previous = button

    def resizeEvent(self, event) -> None:  # noqa: N802, Qt's name
        super().resizeEvent(event)
        beside = (self._account.sizeHint().width() + self._row.spacing()
                  + self._buttons.sizeHint().width())
        direction = (QBoxLayout.Direction.LeftToRight if self.width() >= beside
                     else QBoxLayout.Direction.TopToBottom)
        if self._row.direction() != direction:
            self._row.setDirection(direction)
