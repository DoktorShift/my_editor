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

from typing import Callable, Optional, Tuple

from PySide6.QtCore import QDateTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

import announce
import theme
from constants import (
    DARK_BG, DARK_BORDER, DARK_FG, DARK_MENU_BG, DARK_MUTED_FG, LIGHT_BG, LIGHT_BORDER, LIGHT_FG,
    LIGHT_MENU_BG, LIGHT_MUTED_FG,
)
from i18n import _, format_moment, ngettext

from ..article_details import ArticleDetails
from ..blossom.store import MediaStore
from ..media.media_visibility import MediaVisibility
from ..media.private_library import PrivateLibrary
from ..media.publish_copy import PublicCopyMaker
from .media_saver import MediaSaver
from ..profiles import Profile
from ..publisher import link_name, slugify
from .coauthors import Coauthors
from .cover_field import CoverImageField
from .eliding_label import ElidingLabel
from .secret_key_check import slug_problem
from .tag_field import TagField

MIN_WIDTH = 260
DEFAULT_WIDTH = 300
MAX_WIDTH = 420


class _WrappingCheckBox(QWidget):
    """A checkbox whose words wrap in a narrow panel (a QCheckBox's own text
    never does, and would widen the panel past its edge); a click on the
    words toggles it too, as on the checkbox's own text."""

    def __init__(self, text: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.box = QCheckBox()
        self.box.setAccessibleName(text)
        row.addWidget(self.box, 0, Qt.AlignmentFlag.AlignTop)
        self.words = QLabel(text)
        self.words.setWordWrap(True)
        self.words.mousePressEvent = lambda _event: (
            self.box.isEnabled() and self.box.toggle())
        row.addWidget(self.words, 1)


class ArticleDetailsPanel(QFrame):
    """The inspector. ``changed(ArticleDetails)`` as the person edits;
    ``close_requested`` from its close button."""

    changed = Signal(object)
    close_requested = Signal()
    # (document, address): a picture dropped as the cover of a document no
    # longer in front is uploaded: that document's cover.
    cover_uploaded = Signal(object, str)

    def __init__(self, *, media_store: Optional[MediaStore] = None,
                 media_visibility: Optional[MediaVisibility] = None,
                 copy_maker: Optional[PublicCopyMaker] = None,
                 private_library: Optional[PrivateLibrary] = None,
                 media_saver: Optional[MediaSaver] = None,
                 profile: Callable[[], Optional[Profile]] = lambda: None,
                 pick_person: Optional[Callable[[QWidget, Callable], None]] = None,
                 name_of: Callable[[str], str] = lambda pubkey: pubkey,
                 can_receive_zaps: Optional[Callable[[str, str, Callable], None]] = None,
                 relay_hint: Callable[[str], str] = lambda pubkey: "",
                 is_dark: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # Who a co-author is asked of (the window's picker, at a widget,
        # answering with a Person), and how a person is called.
        self._pick_person = pick_person
        self._name_of = name_of
        # Adding and removing co-authors, and sharing zaps with them, as
        # Publish as Article does: whether someone can receive zaps
        # (pubkey, relay hint, answer(bool)) and the relay a zap to someone
        # is announced on decide the shares.
        self._coauthors = Coauthors(profile=profile, relay_hint=relay_hint,
                                    can_receive_zaps=can_receive_zaps)
        self.setObjectName("ArticleDetailsPanel")
        self.setAccessibleName(_("Article Details"))
        self.setMinimumWidth(MIN_WIDTH)
        self.setMaximumWidth(MAX_WIDTH)
        self._is_dark = is_dark
        self._shown = ArticleDetails()
        # The link name of the article shown, kept letter for letter, and
        # the one publishing would make when none is typed (a file's).
        self._kept_identifier = ""
        self._made_identifier = ""
        self._filling = False
        # The details the file writes in a way that is kept as written
        # (article_details.kept_in_file), and the line under each.
        self._kept: frozenset = frozenset()
        self._kept_notes: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_header())
        self._pages = QStackedWidget()
        outer.addWidget(self._pages, 1)

        self._form = self._build_form(media_store, media_visibility, copy_maker,
                                      private_library, profile, media_saver)
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
        """The toolbar band's section over this pane: the band's background
        and line, as tall as the format toolbar (``align_band``)."""
        header = QFrame()
        header.setObjectName("article_details_header")
        self._header = header
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 6, 8, 6)
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
                    profile, media_saver=None) -> QWidget:
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

        # When the file keeps every detail as written: said once, here.
        self._all_kept_note = QLabel(_("Every detail is kept as written in the file: "
                                       "MyEditor can’t read its front matter in full."))
        self._all_kept_note.setObjectName("article_details_hint")
        self._all_kept_note.setWordWrap(True)
        self._all_kept_note.hide()
        form.addWidget(self._all_kept_note)

        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText(_("Article title"))
        self._add_labeled(form, _("Title"), self.title_edit)
        self._add_kept_note(form, "title")

        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setPlaceholderText(_("Short summary (one sentence)"))
        # Tab goes on to the next field, as in a form; the summary is short.
        self.summary_edit.setTabChangesFocus(True)
        lines = self.summary_edit.fontMetrics().lineSpacing()
        self.summary_edit.setFixedHeight(lines * 3 + 12)
        self._add_labeled(form, _("Summary"), self.summary_edit)
        self._add_kept_note(form, "summary")

        self.cover = CoverImageField(
            media_store=media_store, media_visibility=media_visibility,
            copy_maker=copy_maker, private_library=private_library, profile=profile,
            saver=media_saver, is_dark=self._is_dark, stacked=True)
        self.cover.problem.connect(self._say_cover_problem)
        self.cover.landed_for.connect(self.cover_uploaded)
        form.addSpacing(6)
        form.addWidget(self.cover)
        self.cover_problem = self._problem_label()
        form.addWidget(self.cover_problem)
        self._add_kept_note(form, "image")

        self.hashtags = TagField(is_dark=self._is_dark)
        self._add_labeled(form, _("Hashtags"), self.hashtags)
        self._add_kept_note(form, "tags")

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
        self._share_row = share = _WrappingCheckBox(_("Share zaps equally with co-authors"))
        self.share_zaps = share.box
        self.share_zaps.toggled.connect(self._share_zaps_toggled)
        form.addWidget(share)
        self.zaps_note = QLabel()
        self.zaps_note.setObjectName("article_details_hint")
        self.zaps_note.setWordWrap(True)
        form.addWidget(self.zaps_note)
        self._add_kept_note(form, "coauthors")

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
        self._add_labeled(advanced, _("Link name"), self.identifier_edit)
        self._add_kept_note(advanced, "slug")
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

        for edit in (self.title_edit, self.identifier_edit):
            edit.textChanged.connect(self._edited)
        self.hashtags.changed.connect(self._edited)
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

    def _add_kept_note(self, layout, detail: str) -> None:
        """The plain line under a detail the file keeps as written."""
        note = QLabel(_("Kept as written in the file."))
        note.setObjectName("article_details_hint")
        note.setWordWrap(True)
        note.hide()
        layout.addWidget(note)
        self._kept_notes[detail] = note

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
        band = DARK_MENU_BG if dark else LIGHT_MENU_BG
        self.setStyleSheet(
            theme.dialog_stylesheet(dark)
            # No edge of its own: the window's divider is its left edge.
            + f"#ArticleDetailsPanel {{ background: {background}; }}"
            + f"#article_details_header {{ background: {band};"
              f" border: none; border-bottom: 1px solid {border}; }}"
            + "#article_details_title { font-weight: 600; }"
            + f"#article_details_scroll, #article_details_body {{ background: {background}; }}"
            + f"QLineEdit, QPlainTextEdit {{ background: {field}; color: {text};"
              f" border: 1px solid {border}; border-radius: 4px; padding: 3px 6px; }}"
            # Shown, not edited: on the panel's own ground, in muted ink.
            + f"QLineEdit:read-only, QPlainTextEdit[readOnly=\"true\"] {{"
              f" background: {background}; color: {muted}; }}"
            + f"#article_details_label, #article_field_label {{ color: {muted}; }}"
            + f"#article_details_hint, #article_field_hint, #article_details_note"
              f" {{ color: {muted}; }}"
            + f"#article_details_problem {{ color: {theme.error_text_color(dark)}; }}"
            + f"QLabel#article_cover_preview {{ border: 1px solid {border};"
              f" border-radius: 4px; color: {muted}; }}"
            + "QToolButton#article_details_close, QToolButton#article_details_disclosure"
              " { border: none; padding: 2px 4px; }")

    def align_band(self, toolbar_height: int) -> None:
        """The header as tall as the format toolbar beside it, so their
        lines meet (0: its own height)."""
        self._header.setMinimumHeight(max(0, int(toolbar_height)))

    def set_dark(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        self.cover.set_dark(is_dark)
        self.hashtags.set_dark(is_dark)
        self._apply_theme()

    # -- showing a tab's article ------------------------------------------------

    def show_article(self, details: ArticleDetails, *, fixed_identifier: bool,
                     kept_in_file: frozenset = frozenset(), document: object = None,
                     made_identifier: str = "") -> None:
        """Show ``details``, the article in the tab in front, without
        handing them back. ``fixed_identifier``: the article has its
        address already (a saved draft, a published article).
        ``kept_in_file``: the details its file writes in a way that stays as
        written (article_details.kept_in_file), shown read-only.
        ``document``: whose article it is, which a cover dropped for it
        lands on (cover_uploaded) once another is in front.
        ``made_identifier``: the link name Publish as Article starts with
        when none is typed (a file's article: made of the file's name), as
        the field's placeholder; without it, the one made of the title."""
        self._made_identifier = made_identifier
        # What the fields hold already (the person's own typing, handed
        # back and shown again) is left as typed: a space at the end of a
        # word being written stays.
        typed = self._pages.currentWidget() is self._form and details == self.details()
        self._filling = True
        try:
            # Another document's cover is shown as it is, and a picture
            # uploading for the one shown before goes on for that one.
            self.cover.set_owner(document, details.image)
            self._shown = details
            self._show_kept(kept_in_file)
            if not typed:
                self._fill_fields(details)
            else:
                self._show_people(details)
            self.identifier_edit.setReadOnly(fixed_identifier or "slug" in self._kept)
            self.identifier_note.setText(
                _("A saved or published article keeps its link name; saving it under "
                  "another one makes another article.") if fixed_identifier else "")
            self.identifier_note.setVisible(fixed_identifier)
            self._show_first_published(details.published_at)
            self._check_identifier()
            self._suggest_identifier()
        finally:
            self._filling = False
        self._pages.setCurrentWidget(self._form)

    def _show_kept(self, kept: frozenset) -> None:
        """The details kept as written in the file: read-only, saying so."""
        self._kept = frozenset(kept)
        every = set(self._kept_notes) | {"date", "zap_split"} <= self._kept
        self._all_kept_note.setVisible(every)
        for detail, note in self._kept_notes.items():
            # One line for the co-authors and how zaps are shared.
            note.setVisible(not every and (detail in self._kept or detail == "coauthors"
                                           and "zap_split" in self._kept))
        self.title_edit.setReadOnly("title" in self._kept)
        self.summary_edit.setReadOnly("summary" in self._kept)
        self.cover.setReadOnly("image" in self._kept)
        self.hashtags.setReadOnly("tags" in self._kept)
        # The read-only look comes from a property the style sheet reads.
        self.summary_edit.style().unpolish(self.summary_edit)
        self.summary_edit.style().polish(self.summary_edit)

    def _fill_fields(self, details: ArticleDetails) -> None:
        self._set_text(self.title_edit, details.title)
        if self.summary_edit.toPlainText() != details.summary:
            self.summary_edit.setPlainText(details.summary)
        self._set_text(self.cover.url_edit, details.image)
        if self.hashtags.tags() != details.hashtags:
            self.hashtags.set_tags(details.hashtags)
        self._set_text(self.identifier_edit, details.identifier)
        self._kept_identifier = details.identifier
        self._show_people(details)

    def show_no_article(self, reason: str) -> None:
        """The tab in front holds no article: ``reason`` instead of fields."""
        self.cover.set_owner(None)
        self._no_article.setText(reason)
        self._pages.setCurrentWidget(self._no_article)

    @staticmethod
    def _set_text(edit: QLineEdit, text: str) -> None:
        if edit.text() != text:
            edit.setText(text)
            if edit.isReadOnly():
                edit.setCursorPosition(0)       # shown from its start

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
                # Gone from sight now, not when the event loop deletes it.
                item.widget().hide()
                item.widget().deleteLater()
        # Tab reaches each Remove in turn, between the hashtags and Add
        # Co-Author, where they are seen (rows made later would come last).
        previous = self.hashtags.entry
        for pubkey, _hint in details.coauthors:
            row, remove = self._coauthor_row(pubkey)
            remove.setEnabled("coauthors" not in self._kept)
            self.coauthor_rows.addWidget(row)
            QWidget.setTabOrder(previous, remove)
            previous = remove
        QWidget.setTabOrder(previous, self.add_coauthor_button)
        self.add_coauthor_button.setEnabled(self._pick_person is not None
                                            and "coauthors" not in self._kept)
        self.share_zaps.blockSignals(True)
        self.share_zaps.setChecked(bool(details.zaps))
        self.share_zaps.blockSignals(False)
        # Disabled as a whole: the box and its words.
        unzappable = self._coauthors.unzappable(details.coauthors)
        self._share_row.setEnabled((len(unzappable) < len(details.coauthors)
                                    or bool(details.zaps)) and "zap_split" not in self._kept)
        foreign = bool(details.zaps) and details.zaps != self._coauthors.equal_zaps(
            details.coauthors)
        if foreign:
            note = _("Zaps are shared the way another app set them.")
        elif unzappable:
            note = ngettext(
                "{names} can’t receive zaps: their profile has no lightning address. "
                "They get no share.",
                "{names} can’t receive zaps: their profiles have no lightning address. "
                "They get no share.", len(unzappable)).format(
                names=", ".join(self._name_of(pubkey) for pubkey in unzappable))
        else:
            note = ""
        self.zaps_note.setText(note)
        self.zaps_note.setVisible(bool(note))
        # Last: an answer at once shows the co-authors again.
        self._coauthors.ask(details.coauthors, self._zappable_answered)

    def _zappable_answered(self, pubkey: str, able: Optional[bool]) -> None:
        """A co-author who can't receive zaps loses their equal share; one
        whose profile couldn't be read keeps it and says nothing."""
        details = self._coauthors.learned(self._shown, pubkey, able)
        if details is None:
            return
        if details != self._shown:
            self._hand_back(details)
        else:
            self._show_people(details)

    def _coauthor_row(self, pubkey: str) -> Tuple[QWidget, QPushButton]:
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
        return row, remove

    def _ask_coauthor(self) -> None:
        if self._pick_person is not None:
            self._pick_person(self.add_coauthor_button, self._add_coauthor)

    def _add_coauthor(self, person) -> None:
        details = self._coauthors.added(self._shown, person)
        if details is not None:        # None: already there, or the author
            self._hand_back(details)

    def _remove_coauthor(self, pubkey: str) -> None:
        self._hand_back(self._coauthors.removed(self._shown, pubkey))

    def _share_zaps_toggled(self, shared: bool) -> None:
        self._hand_back(self._coauthors.shared(self._shown, shared))

    def _hand_back(self, details: ArticleDetails) -> None:
        self._shown = details
        # Handed back before it is shown: showing it may learn at once that a
        # co-author can't receive zaps, and hand back what follows from that.
        self.changed.emit(details)
        self._show_people(details)

    # -- editing ----------------------------------------------------------------

    def details(self) -> ArticleDetails:
        """What the fields hold, on the details shown."""
        return self._shown.with_changes(
            title=self.title_edit.text().strip(),
            summary=self.summary_edit.toPlainText().strip(),
            image=self.cover.text().strip(),
            hashtags=self.hashtags.tags(),
            identifier=self._identifier())

    def _identifier(self) -> str:
        """The link name in its field, as it is published: the article's
        own exactly as it is, one typed in one form (link_name)."""
        return link_name(self.identifier_edit.text(), self._kept_identifier)

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
                               slug_problem(self._identifier()) or "")
        if not self.identifier_problem.isHidden():
            self.advanced_toggle.setChecked(True)

    def _suggest_identifier(self) -> None:
        """An empty address shows the one Publish as Article would start
        with, as a placeholder: a file's article the one made of the file's
        name; any other the one made of the title, following it."""
        title = self.title_edit.text().strip()
        self.identifier_edit.setPlaceholderText(
            self._made_identifier or (slugify(title) if title else _("my-article")))

    def _say_cover_problem(self, reason: str) -> None:
        announce.field_problem(self.cover.url_edit, self.cover_problem, reason)

    def _show_advanced(self, shown: bool) -> None:
        self.advanced.setVisible(shown)
        self.advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
