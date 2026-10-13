# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Article Details: the inspector at the trailing edge of the window, as
Pages keeps a document's details beside it, for the article in the tab in
front.

It shows and edits the one model of the article's details
(nostr/article_details.py, kept with the tab by the window), the same
details the publish window offers: title, summary, cover image, hashtags,
and under Advanced the article's address (its slug) and when it first
went out. What is typed goes to the tab at once; nothing here publishes
or saves.

The address of an article that already has one (a saved draft's, a
published article's) is shown, not edited: another address would be
another article. A tab that holds no article (a note's draft, a PDF)
says so instead of showing empty fields.

Co-authors are chosen the way a mention is, from people whose Nostr
address is confirmed; they are tagged as authors of the article. Zaps to
it can be shared equally between the author and them.

It exists only while a Nostr account is in use (decision D-1).
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

import announce
import theme
from constants import (
    DARK_BG, DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, LIGHT_BG, LIGHT_BORDER, LIGHT_FG,
    LIGHT_MUTED_FG,
)
from i18n import _, format_moment

from ..article_details import ArticleDetails, equal_zaps, hashtags_from
from ..blossom.store import MediaStore
from ..media.media_visibility import MediaVisibility
from ..media.private_library import PrivateLibrary
from ..media.publish_copy import PublicCopyMaker
from ..profiles import Profile
from ..publisher import slugify
from .cover_field import CoverImageField
from .eliding_label import ElidingLabel
from .secret_key_check import slug_problem

MIN_WIDTH = 260
DEFAULT_WIDTH = 300
MAX_WIDTH = 420


class ArticleDetailsPanel(QFrame):
    """The inspector. ``changed(ArticleDetails)`` as the person edits;
    ``close_requested`` from its close button."""

    changed = Signal(object)
    close_requested = Signal()

    def __init__(self, *, media_store: Optional[MediaStore] = None,
                 media_visibility: Optional[MediaVisibility] = None,
                 copy_maker: Optional[PublicCopyMaker] = None,
                 private_library: Optional[PrivateLibrary] = None,
                 profile: Callable[[], Optional[Profile]] = lambda: None,
                 pick_person: Optional[Callable[[QWidget, Callable], None]] = None,
                 name_of: Callable[[str], str] = lambda pubkey: pubkey,
                 is_dark: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Who a co-author is asked of (the window's picker, at a widget,
        # answering with a Person), and how a person is called.
        self._pick_person = pick_person
        self._name_of = name_of
        self._profile = profile
        self.setObjectName("ArticleDetailsPanel")
        self.setAccessibleName(_("Article Details"))
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)
        self._is_dark = is_dark
        self._shown = ArticleDetails()
        self._filling = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        self._pages = QStackedWidget()
        outer.addWidget(self._pages, 1)

        self._form = self._build_form(media_store, media_visibility, copy_maker,
                                      private_library, profile)
        self._pages.addWidget(self._form)
        self._no_article = QLabel()
        self._no_article.setObjectName("article_details_note")
        self._no_article.setWordWrap(True)
        self._no_article.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._no_article.setContentsMargins(16, 16, 16, 16)
        self._pages.addWidget(self._no_article)
        self._apply_theme()

    # -- building -------------------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("article_details_header")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 10, 8, 10)
        title = QLabel(_("Article Details"))
        title.setObjectName("article_details_title")
        row.addWidget(title, 1)
        close = QToolButton()
        close.setObjectName("article_details_close")
        close.setText("×")
        close.setToolTip(_("Close Article Details"))
        close.setAccessibleName(_("Close Article Details"))
        close.setAutoRaise(True)
        close.clicked.connect(self.close_requested)
        row.addWidget(close)
        return header

    def _build_form(self, media_store, media_visibility, copy_maker, private_library,
                    profile) -> QWidget:
        scroll = QScrollArea()
        scroll.setObjectName("article_details_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        body = QWidget()
        body.setObjectName("article_details_body")
        form = QVBoxLayout(body)
        form.setContentsMargins(16, 4, 16, 16)
        form.setSpacing(6)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(_("Article title"))
        self._add_labeled(form, _("Title"), self.title_edit)

        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText(_("Short summary (one sentence)"))
        # Tab goes on to the next field, as in a form; the summary is short.
        self.summary_edit.setTabChangesFocus(True)
        lines = self.summary_edit.fontMetrics().lineSpacing()
        self.summary_edit.setFixedHeight(lines * 3 + 12)
        self._add_labeled(form, _("Summary"), self.summary_edit)

        self.cover = CoverImageField(
            media_store=media_store, media_visibility=media_visibility,
            copy_maker=copy_maker, private_library=private_library, profile=profile,
            is_dark=self._is_dark, stacked=True)
        self.cover.problem.connect(self._say_cover_problem)
        form.addSpacing(6)
        form.addWidget(self.cover)
        self.cover_problem = self._problem_label()
        form.addWidget(self.cover_problem)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText(_("comma, separated, hashtags"))
        self._add_labeled(form, _("Hashtags"), self.tags_edit)

        form.addSpacing(6)
        coauthors_label = QLabel(_("Co-Authors"))
        coauthors_label.setObjectName("article_details_label")
        form.addWidget(coauthors_label)
        self.coauthor_rows = QVBoxLayout()
        self.coauthor_rows.setContentsMargins(0, 0, 0, 0)
        self.coauthor_rows.setSpacing(2)
        form.addLayout(self.coauthor_rows)
        self.add_coauthor_button = QPushButton(_("Add Co-Author…"))
        self.add_coauthor_button.setAutoDefault(False)
        self.add_coauthor_button.clicked.connect(self._ask_coauthor)
        self.add_coauthor_button.setEnabled(self._pick_person is not None)
        form.addWidget(self.add_coauthor_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.share_zaps = QCheckBox(_("Share zaps equally with co-authors"))
        self.share_zaps.toggled.connect(self._share_zaps_toggled)
        form.addWidget(self.share_zaps)
        self.zaps_note = QLabel()
        self.zaps_note.setObjectName("article_details_hint")
        self.zaps_note.setWordWrap(True)
        form.addWidget(self.zaps_note)

        # Advanced: what most people never change, closed at first.
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setObjectName("article_details_disclosure")
        self.advanced_toggle.setText(_("Advanced"))
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.toggled.connect(self._show_advanced)
        form.addSpacing(8)
        form.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced = QVBoxLayout(self.advanced)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(6)
        self.identifier_edit = QLineEdit()
        self._add_labeled(advanced, _("Slug (article ID)"), self.identifier_edit)
        self.identifier_note = QLabel()
        self.identifier_note.setObjectName("article_details_hint")
        self.identifier_note.setWordWrap(True)
        advanced.addWidget(self.identifier_note)
        self.identifier_problem = self._problem_label()
        advanced.addWidget(self.identifier_problem)
        self.first_published = QLabel()
        self.first_published.setObjectName("article_details_hint")
        self.first_published.setWordWrap(True)
        advanced.addWidget(self.first_published)
        self.advanced.setVisible(False)
        form.addWidget(self.advanced)
        form.addStretch(1)
        scroll.setWidget(body)

        for edit in (self.title_edit, self.tags_edit, self.identifier_edit):
            edit.textChanged.connect(self._edited)
        self.summary_edit.textChanged.connect(self._edited)
        self.cover.changed.connect(self._edited)
        return scroll

    @staticmethod
    def _add_labeled(layout, text: str, field) -> None:
        label = QLabel(text)
        label.setObjectName("article_details_label")
        label.setBuddy(field)
        field.setAccessibleName(text)
        layout.addSpacing(6)
        layout.addWidget(label)
        layout.addWidget(field)

    @staticmethod
    def _problem_label() -> QLabel:
        label = QLabel()
        label.setObjectName("article_details_problem")
        label.setWordWrap(True)
        label.hide()
        return label

    def _apply_theme(self) -> None:
        dark = self._is_dark
        background, border, field, text, muted = (
            (DARK_BG, DARK_BORDER, DARK_MENU_BG, DARK_FG, DARK_MUTED_FG) if dark
            else (LIGHT_BG, LIGHT_BORDER, LIGHT_BG, LIGHT_FG, LIGHT_MUTED_FG))
        self.setStyleSheet(
            theme.dialog_stylesheet(dark)
            + f"#ArticleDetailsPanel {{ background: {background};"
              f" border-left: 1px solid {border}; }}"
            + f"#article_details_header {{ border-bottom: 1px solid {border}; }}"
            + "#article_details_title { font-weight: 600; }"
            + f"#article_details_scroll, #article_details_body {{ background: {background}; }}"
            + f"QLineEdit, QPlainTextEdit {{ background: {field}; color: {text};"
              f" border: 1px solid {border}; border-radius: 4px; padding: 3px 6px; }}"
            + f"#article_details_label, #article_field_label {{ color: {muted}; }}"
            + f"#article_details_hint, #article_field_hint, #article_details_note"
              f" {{ color: {muted}; }}"
            + f"#article_details_problem {{ color: {theme.error_text_color(dark)}; }}"
            + f"QLabel#article_cover_preview {{ border: 1px solid {border};"
              f" border-radius: 4px; color: {muted}; }}"
            + "QToolButton#article_details_close, QToolButton#article_details_disclosure"
              " { border: none; padding: 2px 4px; }")

    def set_dark(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self.cover.set_dark(is_dark)
        self._apply_theme()

    # -- showing a tab's article ------------------------------------------------

    def show_article(self, details: ArticleDetails, *, fixed_identifier: bool) -> None:
        """Show ``details``, the article in the tab in front, without
        handing them back. ``fixed_identifier``: the article has its
        address already (a saved draft, a published article)."""
        # What the fields hold already (the person's own typing, handed
        # back and shown again) is left as typed: a space at the end of a
        # word being written stays.
        typed = self._pages.currentWidget() is self._form and details == self.details()
        self._filling = True
        try:
            self._shown = details
            if not typed:
                self._fill_fields(details)
            else:
                self._show_people(details)
            self.identifier_edit.setReadOnly(fixed_identifier)
            self.identifier_note.setText(
                _("A saved or published article keeps its address; saving it under "
                  "another one makes another article.") if fixed_identifier else "")
            self.identifier_note.setVisible(fixed_identifier)
            self._show_first_published(details.published_at)
            self._check_identifier()
            self._suggest_identifier()
        finally:
            self._filling = False
        self._pages.setCurrentWidget(self._form)

    def _fill_fields(self, details: ArticleDetails) -> None:
        self._set_text(self.title_edit, details.title)
        if self.summary_edit.toPlainText() != details.summary:
            self.summary_edit.setPlainText(details.summary)
        if self.cover.text() != details.image:
            self.cover.setText(details.image)
        if hashtags_from(self.tags_edit.text()) != details.hashtags:
            self.tags_edit.setText(", ".join(details.hashtags))
        self._set_text(self.identifier_edit, details.identifier)
        self._show_people(details)

    def show_no_article(self, reason: str) -> None:
        """The tab in front holds no article: ``reason`` instead of fields."""
        self._no_article.setText(reason)
        self._pages.setCurrentWidget(self._no_article)

    @staticmethod
    def _set_text(edit: QLineEdit, text: str) -> None:
        if edit.text() != text:
            edit.setText(text)

    def _show_first_published(self, when: Optional[int]) -> None:
        if when:
            moment = QDateTime.fromSecsSinceEpoch(int(when))
            self.first_published.setText(
                _("First published {moment}").format(moment=format_moment(moment)))
        else:
            self.first_published.setText(_("Not published yet."))

    # -- co-authors -------------------------------------------------------------

    def _show_people(self, details: ArticleDetails) -> None:
        """The co-authors, each with Remove, and how zaps are shared."""
        while self.coauthor_rows.count():
            item = self.coauthor_rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for pubkey, _hint in details.coauthors:
            self.coauthor_rows.addWidget(self._coauthor_row(pubkey))
        self.share_zaps.blockSignals(True)
        self.share_zaps.setChecked(bool(details.zaps))
        self.share_zaps.blockSignals(False)
        self.share_zaps.setEnabled(bool(details.coauthors) or bool(details.zaps))
        foreign = bool(details.zaps) and details.zaps != self._equal_zaps(details.coauthors)
        self.zaps_note.setText(_("Zaps are shared the way another app set them.")
                               if foreign else "")
        self.zaps_note.setVisible(foreign)

    def _coauthor_row(self, pubkey: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        name = self._name_of(pubkey)
        label = ElidingLabel()
        label.set_full_text(name)
        layout.addWidget(label, 1)
        remove = QPushButton(_("Remove"))
        remove.setAutoDefault(False)
        remove.setAccessibleName(_("Remove {name}").format(name=name))
        remove.clicked.connect(lambda _checked=False, key=pubkey: self._remove_coauthor(key))
        layout.addWidget(remove)
        return row

    def _ask_coauthor(self) -> None:
        if self._pick_person is not None:
            self._pick_person(self.add_coauthor_button, self._add_coauthor)

    def _add_coauthor(self, person) -> None:
        own = self._profile()
        if any(pubkey == person.pubkey for pubkey, _hint in self._shown.coauthors) or (
                own is not None and person.pubkey == own.user_pubkey):
            return                     # already there, or the author: nothing to add
        self._people_changed(self._shown.coauthors + ((person.pubkey, person.relay_hint or ""),))

    def _remove_coauthor(self, pubkey: str) -> None:
        self._people_changed(tuple(p for p in self._shown.coauthors if p[0] != pubkey))

    def _people_changed(self, coauthors) -> None:
        """New co-authors: shared zaps follow them (equally); zaps another
        app shared are left as they were until changed here."""
        zaps = self._shown.zaps
        if zaps and zaps == self._equal_zaps(self._shown.coauthors):
            zaps = self._equal_zaps(coauthors)
        self._hand_back(self._shown.with_changes(coauthors=coauthors, zaps=zaps))

    def _share_zaps_toggled(self, shared: bool) -> None:
        zaps = self._equal_zaps(self._shown.coauthors) if shared else ()
        self._hand_back(self._shown.with_changes(zaps=zaps))

    def _equal_zaps(self, coauthors):
        own = self._profile()
        return equal_zaps(own.user_pubkey, "", coauthors) if own is not None else ()

    def _hand_back(self, details: ArticleDetails) -> None:
        self._shown = details
        self._show_people(details)
        self.changed.emit(details)

    # -- editing ----------------------------------------------------------------

    def details(self) -> ArticleDetails:
        """What the fields hold, on the details shown."""
        return self._shown.with_changes(
            title=self.title_edit.text().strip(),
            summary=self.summary_edit.toPlainText().strip(),
            image=self.cover.text().strip(),
            hashtags=hashtags_from(self.tags_edit.text()),
            identifier=self.identifier_edit.text().strip())

    def _edited(self, *_args) -> None:
        self._check_identifier()
        self._suggest_identifier()
        if self._filling:
            return
        self.cover_problem.hide()
        details = self.details()
        if details != self._shown:
            self._shown = details
            self.changed.emit(details)

    def _check_identifier(self) -> None:
        announce.field_problem(self.identifier_edit, self.identifier_problem,
                               slug_problem(self.identifier_edit.text()) or "")
        if not self.identifier_problem.isHidden():
            self.advanced_toggle.setChecked(True)

    def _suggest_identifier(self) -> None:
        """An empty address shows the one publishing would make of the
        title, as a placeholder: it follows the title until it is typed."""
        title = self.title_edit.text().strip()
        self.identifier_edit.setPlaceholderText(slugify(title) if title else _("article-slug"))

    def _say_cover_problem(self, reason: str) -> None:
        announce.field_problem(self.cover.url_edit, self.cover_problem, reason)

    def _show_advanced(self, shown: bool) -> None:
        self.advanced.setVisible(shown)
        self.advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
