# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Article Details' Authors: who writes the article, and how much of the
zaps to it each of them receives.

    Alice · you                                   60%
    Bob                                    [ 25 %]  ⊖
    ━━━━━━━━━━●───────────────────────
    Carol                                  [ 15 %]  ⊖
    ━━━━●─────────────────────────────
    Add Co-Author…                       Split Evenly
    Alice 60% · Bob 25% · Carol 15%
    Apps that support zap splits, like Amethyst and noStrudel, pay each
    person their share. Other apps pay you.

You come first and keep what the others leave, so the shares always add
up to 100 % (nostr/zap_shares.py). A co-author is credited and receives
nothing until given a share, set with a slider or exactly in the field
beside it, as Apple's guidelines advise for a precise value. A co-author
whose profile has no lightning address can't be paid: their share stays
at 0 and the row says why. The note only says what is true: whether a
zap is shared depends on the reader's app.

The rows are kept as they are while a share changes, so a slider being
dragged is never rebuilt under the pointer.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from i18n import _

from .. import zap_shares
from ..article_details import ArticleDetails
from .coauthors import Coauthors
from .eliding_label import ElidingLabel


class _Row(QWidget):
    """One co-author: their name, their share (a slider and the exact
    value), Remove, and why they get no share when they can't be paid."""

    share_set = Signal(str, int)
    remove = Signal(str)

    def __init__(self, pubkey: str, name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.pubkey = pubkey
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 2, 0, 2)
        column.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        # Each made with its parent, in the order Tab reaches them: the
        # slider, the exact value, Remove.
        self.name = ElidingLabel(parent=self)
        self.name.set_full_text(name)
        top.addWidget(self.name, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, 100)
        self.slider.setAccessibleName(_("Share for {name}").format(name=name))
        self.slider.valueChanged.connect(lambda value: self.share_set.emit(self.pubkey, value))
        self.field = QSpinBox(self)
        self.field.setRange(0, 100)
        self.field.setSuffix(" %")
        self.field.setAccessibleName(_("Share for {name}").format(name=name))
        self.field.valueChanged.connect(lambda value: self.share_set.emit(self.pubkey, value))
        top.addWidget(self.field)
        self.remove_button = QPushButton(_("Remove"), self)
        self.remove_button.setAutoDefault(False)
        self.remove_button.setAccessibleName(_("Remove {name}").format(name=name))
        self.remove_button.clicked.connect(lambda _checked=False: self.remove.emit(self.pubkey))
        top.addWidget(self.remove_button)
        column.addLayout(top)
        column.addWidget(self.slider)
        self.unpaid = QLabel(_("No lightning address in their profile, so they can’t be "
                               "paid a share."), self)
        self.unpaid.setObjectName("article_details_hint")
        self.unpaid.setWordWrap(True)
        self.unpaid.hide()
        column.addWidget(self.unpaid)

    def show_share(self, share: int, *, payable: bool, editable: bool) -> None:
        for control in (self.slider, self.field):
            control.blockSignals(True)
            control.setValue(share)
            control.blockSignals(False)
            control.setEnabled(payable and editable)
        self.remove_button.setEnabled(editable)
        self.unpaid.setVisible(not payable)


class AuthorsSection(QWidget):
    """The Authors section (see the module). ``changed(ArticleDetails)`` as
    co-authors and shares are changed."""

    changed = Signal(object)

    def __init__(self, *, coauthors: Coauthors, own_pubkey: Callable[[], str],
                 name_of: Callable[[str], str],
                 pick_person: Optional[Callable[[QWidget, Callable], None]] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._coauthors = coauthors
        self._own_pubkey = own_pubkey
        self._name_of = name_of
        self._pick_person = pick_person
        self._details = ArticleDetails()
        self._editable = True
        self._rows: Dict[str, _Row] = {}

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        you = QHBoxLayout()
        you.setContentsMargins(0, 0, 0, 0)
        self.you = ElidingLabel()
        you.addWidget(self.you, 1)
        self.you_share = QLabel()
        self.you_share.setObjectName("article_details_share")
        you.addWidget(self.you_share)
        column.addLayout(you)
        self._row_column = QVBoxLayout()
        self._row_column.setContentsMargins(0, 0, 0, 0)
        self._row_column.setSpacing(4)
        column.addLayout(self._row_column)

        self.add_button = QPushButton(_("Add Co-Author…"), self)
        self.add_button.setAutoDefault(False)
        self.add_button.clicked.connect(self._ask)
        column.addSpacing(4)
        column.addWidget(self.add_button, 0, Qt.AlignmentFlag.AlignLeft)

        # Who gets what, and Split Evenly beside it: the line it changes.
        shares = QHBoxLayout()
        shares.setContentsMargins(0, 4, 0, 0)
        shares.setSpacing(6)
        self.sentence = QLabel(self)
        self.sentence.setObjectName("article_details_share_sentence")
        self.sentence.setWordWrap(True)
        shares.addWidget(self.sentence, 1)
        self.even_button = QPushButton(_("Split Evenly"), self)
        self.even_button.setObjectName("article_details_link")
        self.even_button.setFlat(True)
        self.even_button.setAutoDefault(False)
        self.even_button.setToolTip(_("Give you and every co-author who can be paid an "
                                      "equal share of the zaps."))
        self.even_button.clicked.connect(
            lambda: self._hand_back(self._coauthors.split_evenly(self._details)))
        shares.addWidget(self.even_button, 0, Qt.AlignmentFlag.AlignTop)
        column.addLayout(shares)
        self.note = QLabel(_("Apps that support zap splits, like Amethyst and noStrudel, "
                             "pay each person their share. Other apps pay you."))
        self.note.setObjectName("article_details_hint")
        self.note.setWordWrap(True)
        column.addWidget(self.note)

    # -- showing ------------------------------------------------------------------

    def show_details(self, details: ArticleDetails, *, editable: bool = True) -> None:
        """Show ``details``' authors and shares; ``editable`` False when the
        file keeps them as written."""
        self._details, self._editable = details, editable
        own = self._own_pubkey()
        shares = self._coauthors.shares(details)
        unpaid = set(self._coauthors.unzappable(details.coauthors))
        self.you.set_full_text(_("{name} · you").format(name=self._name_of(own)) if own
                               else _("You"))
        self.you_share.setText(f"{zap_shares.author_share(shares)}%")
        wanted = [pubkey for pubkey, _hint in details.coauthors]
        for pubkey in list(self._rows):
            if pubkey not in wanted:
                row = self._rows.pop(pubkey)
                row.hide()
                row.deleteLater()
        for index, pubkey in enumerate(wanted):
            row = self._rows.get(pubkey) or self._add_row(pubkey)
            self._row_column.insertWidget(index, row)
            row.show_share(shares.get(pubkey, 0), payable=pubkey not in unpaid,
                           editable=editable)
        self._order_for_tab([self._rows[pubkey] for pubkey in wanted])
        self.add_button.setEnabled(self._pick_person is not None and editable)
        payable = [pubkey for pubkey in wanted if pubkey not in unpaid]
        self.even_button.setVisible(bool(payable))
        self.even_button.setEnabled(editable)
        shared = any(share > 0 for share in shares.values())
        self.sentence.setVisible(shared)
        self.note.setVisible(shared)
        if shared:
            parts = ([f"{self._name_of(own)} {zap_shares.author_share(shares)}%"]
                     if zap_shares.author_share(shares) else [])
            parts += [f"{self._name_of(pubkey)} {share}%" for pubkey, share in shares.items()
                      if share > 0]
            self.sentence.setText(" · ".join(parts))
        # Last: an answer at once shows the authors again.
        self._coauthors.ask(details.coauthors, self._answered)

    def _order_for_tab(self, rows) -> None:
        """Tab goes through the rows as they are seen (rows made later would
        come last), each slider, value and Remove, then the buttons."""
        # What Tab reaches before the section: the rows follow it.
        previous = self.previousInFocusChain()
        while previous is not None and previous is not self and (
                self.isAncestorOf(previous)
                or not previous.focusPolicy() & Qt.FocusPolicy.TabFocus):
            previous = previous.previousInFocusChain()
        for row in rows:
            if previous is not None:
                QWidget.setTabOrder(previous, row.slider)
            QWidget.setTabOrder(row.slider, row.field)
            QWidget.setTabOrder(row.field, row.remove_button)
            previous = row.remove_button
        if previous is not None:
            QWidget.setTabOrder(previous, self.add_button)
        QWidget.setTabOrder(self.add_button, self.even_button)

    def rows(self) -> Dict[str, _Row]:
        """Each co-author's row, by their key."""
        return dict(self._rows)

    def _add_row(self, pubkey: str) -> _Row:
        row = _Row(pubkey, self._name_of(pubkey), self)
        row.share_set.connect(self._share_set)
        row.remove.connect(lambda key: self._hand_back(self._coauthors.removed(self._details, key)))
        self._rows[pubkey] = row
        return row

    # -- changing -----------------------------------------------------------------

    def _share_set(self, pubkey: str, share: int) -> None:
        self._hand_back(self._coauthors.with_share(self._details, pubkey, share))

    def _ask(self) -> None:
        if self._pick_person is not None:
            self._pick_person(self.add_button, self.add)

    def add(self, person) -> None:
        """Add ``person`` (a Person) as a co-author, credited with no share."""
        details = self._coauthors.added(self._details, person)
        if details is not None:        # None: already there, or the author
            self._hand_back(details)

    def _answered(self, pubkey: str, able: Optional[bool]) -> None:
        """A co-author who can't receive zaps loses their share; one whose
        profile couldn't be read keeps it and nothing is said."""
        details = self._coauthors.learned(self._details, pubkey, able)
        if details is None:
            return
        if details != self._details:
            self._hand_back(details)
        else:
            self.show_details(details, editable=self._editable)

    def _hand_back(self, details: ArticleDetails) -> None:
        # Handed back before it is shown: showing it may learn at once that a
        # co-author can't be paid, and hand back what follows from that.
        self._details = details
        self.changed.emit(details)
        self.show_details(details, editable=self._editable)
