# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Modal dialog for publishing a NIP-23 long-form article (kind 30023).

The layout is built around the *writing*, not the metadata: a large,
prominent title; a quieter inline summary; the full-width Markdown body;
and the technical fields (slug, cover image, hashtags) tucked into a
collapsible **Advanced** section that stays out of the way until needed.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence

from PySide6.QtCore import QLocale, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import word_count
from alerts import CANCEL, DEFAULT, NORMAL, Button, ask
from i18n import _, language, ngettext

from ..article_details import ArticleDetails
from ..first_publications import FirstPublications
from ..avatar_store import AvatarStore
from ..bech32 import encode_naddr
from ..blossom.store import MediaFile, MediaStore
from ..bunker import BunkerSessionPool, humanize_failure
from ..known_people import KnownPeople
from ..outbox import RelayDirectory, relays_from
from ..profiles import Profile, ProfileStore
from ..publisher import (
    FOUND, NEVER, FirstPublication, PublishJob, PublishResult, build_article,
    find_first_publication, slugify,
)
from ..relay import RelayPool
from ..search import Nip50SearchClient
from .avatar import (
    AVATAR_SIZE,
    CHIP_TOTAL_WIDTH,
    compose_chip_icon,
    pixmap_for_profile,
)
from ..media.media_visibility import MediaVisibility
from ..media.private_library import PrivateLibrary
from ..media.publish_copy import PublicCopyMaker
from .media_library_dialog import MediaLibraryDialog
from .publish_copy_dialog import resolve_pick
from .mention_chips import MentionChipRow
from .thumbnail_loader import ThumbnailLoader




# --------------------------------------------------------------------------- #
# Stylesheets: palette pulled from widgets.py / editor.py                    #
# --------------------------------------------------------------------------- #

_DARK_CSS = """
QDialog { background: #1E1E1E; }
QLabel { color: #D4D4D4; font-size: 12px; }
QLabel#article_field_label {
    color: #858585;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}
QLabel#article_status { color: #FFB347; }
QLabel#article_meta { color: #858585; font-size: 11px; }

QLineEdit#article_title {
    background: transparent;
    border: none;
    color: #FFFFFF;
    font-size: 22px;
    font-weight: 600;
    padding: 6px 0;
}
QLineEdit#article_title:focus { border-bottom: 1px solid #3C3C3C; }

QLineEdit#article_summary {
    background: transparent;
    border: none;
    color: #B5B5B5;
    font-size: 14px;
    padding: 2px 0 8px 0;
}
QLineEdit#article_summary:focus { border-bottom: 1px solid #3C3C3C; }

QLineEdit#article_advanced_field {
    background: #252526;
    color: #D4D4D4;
    border: 1px solid #3C3C3C;
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: #264F78;
    font-size: 12px;
}

QTextEdit#article_body {
    background: #252526;
    color: #D4D4D4;
    border: 1px solid #3C3C3C;
    border-radius: 4px;
    padding: 10px 12px;
    font-family: "Menlo", "Consolas", "Noto Sans Mono", monospace;
    font-size: 12px;
    selection-background-color: #264F78;
}

QFrame#article_divider { background: #2D2D30; max-height: 1px; min-height: 1px; }

QToolButton#advanced_toggle {
    background: transparent;
    border: none;
    color: #858585;
    text-align: left;
    padding: 4px 0;
    font-size: 11px;
}
QToolButton#advanced_toggle:hover { color: #D4D4D4; }

QPushButton {
    background: #2D2D30;
    color: #D4D4D4;
    border: 1px solid #3C3C3C;
    padding: 6px 14px;
    border-radius: 4px;
}
QPushButton:hover { background: #3C3C3C; }
QPushButton:pressed { background: #1E1E1E; }
QPushButton:disabled { background: #252526; color: #6A6A6A; border-color: #2D2D2D; }

QToolButton#profile_switch {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
    color: #CCCCCC;
}
QToolButton#profile_switch:hover { background: #3C3C3C; }
QToolButton#profile_switch::menu-indicator { image: none; width: 0; }

QLabel#article_cover_preview {
    background: #252526;
    border: 1px solid #3C3C3C;
    border-radius: 4px;
    color: #6A6A6A;
    font-size: 10px;
}
QLabel#article_field_hint {
    color: #6A6A6A;
    font-size: 10px;
}
"""

_LIGHT_CSS = """
QDialog { background: #FFFFFF; }
QLabel { color: #333333; font-size: 12px; }
QLabel#article_field_label {
    color: #999999;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.6px;
}
QLabel#article_status { color: #A05000; }
QLabel#article_meta { color: #999999; font-size: 11px; }

QLineEdit#article_title {
    background: transparent;
    border: none;
    color: #1A1A1A;
    font-size: 22px;
    font-weight: 600;
    padding: 6px 0;
}
QLineEdit#article_title:focus { border-bottom: 1px solid #E1E1E1; }

QLineEdit#article_summary {
    background: transparent;
    border: none;
    color: #555555;
    font-size: 14px;
    padding: 2px 0 8px 0;
}
QLineEdit#article_summary:focus { border-bottom: 1px solid #E1E1E1; }

QLineEdit#article_advanced_field {
    background: #FFFFFF;
    color: #333333;
    border: 1px solid #E1E1E1;
    border-radius: 4px;
    padding: 6px 8px;
    selection-background-color: #0078D4;
    font-size: 12px;
}

QTextEdit#article_body {
    background: #FFFFFF;
    color: #333333;
    border: 1px solid #E1E1E1;
    border-radius: 4px;
    padding: 10px 12px;
    font-family: "Menlo", "Consolas", "Noto Sans Mono", monospace;
    font-size: 12px;
    selection-background-color: #0078D4;
}

QFrame#article_divider { background: #ECECEC; max-height: 1px; min-height: 1px; }

QToolButton#advanced_toggle {
    background: transparent;
    border: none;
    color: #777777;
    text-align: left;
    padding: 4px 0;
    font-size: 11px;
}
QToolButton#advanced_toggle:hover { color: #333333; }

QPushButton {
    background: #ECECEC;
    color: #333333;
    border: 1px solid #CCCCCC;
    padding: 6px 14px;
    border-radius: 4px;
}
QPushButton:hover { background: #E1E1E1; }
QPushButton:pressed { background: #D0D0D0; }
QPushButton:disabled { background: #F8F8F8; color: #BBBBBB; border-color: #EBEBEB; }

QToolButton#profile_switch {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 3px 6px;
    color: #555555;
}
QToolButton#profile_switch:hover { background: #E1E1E1; }
QToolButton#profile_switch::menu-indicator { image: none; width: 0; }

QLabel#article_cover_preview {
    background: #F8F8F8;
    border: 1px solid #E1E1E1;
    border-radius: 4px;
    color: #999999;
    font-size: 10px;
}
QLabel#article_field_hint {
    color: #999999;
    font-size: 10px;
}
"""

_DARK_MENU_CSS = """
QMenu { background: #252526; color: #CCCCCC; border: 1px solid #3C3C3C; padding: 4px; }
QMenu::item { padding: 4px 20px 4px 30px; }
QMenu::item:selected { background: #1E1E1E; color: #FFFFFF; }
QMenu::separator { height: 1px; background: #3C3C3C; margin: 4px 0px; }
"""

_LIGHT_MENU_CSS = """
QMenu { background: #F8F8F8; color: #333333; border: 1px solid #E1E1E1; padding: 4px; }
QMenu::item { padding: 4px 20px 4px 30px; }
QMenu::item:selected { background: #F3F3F3; color: #000000; }
QMenu::separator { height: 1px; background: #E1E1E1; margin: 4px 0px; }
"""


# --------------------------------------------------------------------------- #
# Tiny helper widgets                                                         #
# --------------------------------------------------------------------------- #

def _make_divider(parent: Optional[QWidget] = None) -> QFrame:
    line = QFrame(parent)
    line.setObjectName("article_divider")
    line.setFrameShape(QFrame.NoFrame)
    return line


def _make_field(label_text: str, edit: QLineEdit) -> QWidget:
    """Compact label-above-input column, used inside the Advanced panel."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    label = QLabel(label_text)
    label.setObjectName("article_field_label")
    edit.setObjectName("article_advanced_field")
    layout.addWidget(label)
    layout.addWidget(edit)
    return container


# --------------------------------------------------------------------------- #
# Dialog                                                                       #
# --------------------------------------------------------------------------- #

class PublishArticleDialog(QDialog):
    """Compose the metadata + body for a kind 30023 and publish it.

    ``published(naddr_str, results)`` is emitted on success. The naddr is
    suitable for pasting into another client. The dialog closes itself
    after a successful publish; failures are shown inline so the user can
    edit and retry.
    """

    # Args: (naddr_string, list[PublishResult])
    published = Signal(str, list)

    def __init__(
        self,
        *,
        body_markdown: str,
        active_profile: Profile,
        store: ProfileStore,
        relay_pool: RelayPool,
        relay_directory: RelayDirectory,
        session_pool: BunkerSessionPool,
        entitled_relays: Optional[Callable[[], Sequence[str]]] = None,
        known_people: KnownPeople,
        search_client: Nip50SearchClient,
        avatars: AvatarStore,
        media_store: Optional[MediaStore] = None,
        media_visibility: Optional[MediaVisibility] = None,
        copy_maker: Optional[PublicCopyMaker] = None,
        private_library: Optional[PrivateLibrary] = None,
        default_title: str = "",
        default_slug: str = "",
        first_published: Optional[int] = None,
        details: Optional[ArticleDetails] = None,
        first_publications: Optional[FirstPublications] = None,
        published_at_lookup: Optional[
            Callable[[str, str, Callable[[FirstPublication], None]], None]] = None,
        parent=None,
        is_dark: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Publish as Article"))
        self.setModal(True)
        self.resize(760, 680)
        self.setMinimumSize(640, 520)

        self._store = store
        self._relay_pool = relay_pool
        self._relay_directory = relay_directory
        self._session_pool = session_pool
        # Relays this account has standing on beyond its own list, resolved
        # when the publish actually happens rather than at dialog open.
        self._entitled_relays = entitled_relays
        self._known_people = known_people
        self._search_client = search_client
        self._avatars = avatars
        self._media_store = media_store
        # A cover image is the most public thing in an article: it is the
        # picture every reader sees before they read a word. So the same
        # gate the editor uses runs here, and a picker with no visibility
        # wired simply sees every file as public, which is what it saw
        # before this existed.
        self._media_visibility = media_visibility or MediaVisibility()
        self._copy_maker = copy_maker
        # Held so the cover picker can start the library reading. Nothing
        # is read on the way into this dialog: the signer prompts that
        # cost belong to the moment someone goes looking for a picture,
        # and most articles are published without one.
        self._private_library = private_library
        self._is_dark = is_dark
        self._current_profile = active_profile
        self._job: Optional[PublishJob] = None
        self._signed_event_id: Optional[str] = None
        # NIP-23: ``published_at`` is when the article first went out, and
        # an edit keeps it. Known when the tab came from a draft that
        # carries it (for that draft's identifier) or when this computer
        # published the article before; otherwise asked of the relays at
        # publish time. "Now" only for a first publication; when nobody
        # can tell, the person decides (never a guess).
        self._first_published = (
            {default_slug: first_published} if default_slug and first_published else {})
        self._first_publications = first_publications
        self._published_at_lookup = published_at_lookup or self._look_up_published_at
        self._looking_up = False
        self._published_at_used: Optional[int] = None
        # What the article's draft holds besides its text (an imported
        # article's summary, cover, hashtags and source): offered here and
        # published with it.
        self._details = details or ArticleDetails()
        # Tracks whether the user has manually edited the slug. As long as
        # they haven't, slug stays in sync with title. An article that has
        # an identifier already (its draft's) keeps it: another one would
        # publish another article.
        self._slug_is_auto = not self._details.identifier
        self._advanced_open = False
        # Cover-image preview state.
        self._cover_thumb_hash: str = ""
        self._cover_loader: Optional[ThumbnailLoader] = (
            ThumbnailLoader(parent=self) if media_store is not None else None
        )
        if self._cover_loader is not None:
            self._cover_loader.ready.connect(self._on_cover_thumb_ready)

        self._build_ui(
            body_markdown=body_markdown,
            default_title=default_title,
            default_slug=default_slug,
        )
        self._summary_edit.setText(self._details.summary)
        self._image_edit.setText(self._details.image)
        self._tags_edit.setText(", ".join(self._details.hashtags))
        self._apply_theme()
        self._refresh_profile_chip()
        self._refresh_meta()
        self._refresh_publish_enabled()

        # Repaint the profile-switcher button if the active profile's
        # avatar lands while the dialog is open.
        self._avatars.avatar_added.connect(self._on_avatar_added)

    def _on_avatar_added(self, pubkey_hex: str, _pixmap) -> None:
        if pubkey_hex == self._current_profile.user_pubkey:
            self._refresh_profile_chip()

    # -- UI ----------------------------------------------------------------

    def _build_ui(
        self,
        *,
        body_markdown: str,
        default_title: str,
        default_slug: str,
    ) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 16)
        root.setSpacing(8)

        # ---- Title (large, prominent) -----------------------------------
        self._title_edit = QLineEdit()
        self._title_edit.setObjectName("article_title")
        self._title_edit.setPlaceholderText(_("Article title"))
        self._title_edit.setText(default_title)
        self._title_edit.textChanged.connect(self._on_title_changed)
        root.addWidget(self._title_edit)

        # ---- Summary (quieter, italic-feel via lower contrast) ----------
        self._summary_edit = QLineEdit()
        self._summary_edit.setObjectName("article_summary")
        self._summary_edit.setPlaceholderText(_("Short summary (one sentence)"))
        root.addWidget(self._summary_edit)

        root.addWidget(_make_divider(self))
        root.addSpacing(6)

        # ---- Body (full-width Markdown) ---------------------------------
        self._body_edit = QTextEdit()
        self._body_edit.setObjectName("article_body")
        self._body_edit.setAcceptRichText(False)
        self._body_edit.setPlainText(body_markdown)
        self._body_edit.setPlaceholderText(_("Write your article in Markdown…"))
        self._body_edit.textChanged.connect(self._on_body_changed)
        root.addWidget(self._body_edit, 1)

        # ---- Meta strip (word count / read time) ------------------------
        self._meta_label = QLabel("")
        self._meta_label.setObjectName("article_meta")
        self._meta_label.setAlignment(Qt.AlignRight)
        root.addWidget(self._meta_label)

        # ---- Mention chip row -------------------------------------------
        self._mention_row = MentionChipRow(
            self._known_people,
            self._search_client,
            avatars=self._avatars,
            parent=self,
            is_dark=self._is_dark,
        )
        root.addWidget(self._mention_row)

        # ---- Advanced toggle + panel ------------------------------------
        self._advanced_toggle = QToolButton()
        self._advanced_toggle.setObjectName("advanced_toggle")
        self._advanced_toggle.setCursor(Qt.PointingHandCursor)
        self._advanced_toggle.clicked.connect(self._toggle_advanced)
        root.addWidget(self._advanced_toggle)

        # The Advanced panel stacks two rows so the cover-image block,
        # which is taller than a single-line field, gets its own
        # horizontal slot instead of stretching the siblings around it.
        #   Row 1:  Slug · Hashtags          (equal-weight compact fields)
        #   Row 2:  Cover image              (URL + button, then thumb)
        self._advanced_panel = QWidget()
        adv = QVBoxLayout(self._advanced_panel)
        adv.setContentsMargins(0, 4, 0, 4)
        adv.setSpacing(12)

        self._slug_edit = QLineEdit()
        self._slug_edit.setPlaceholderText(_("article-slug"))
        seed_slug = default_slug or (slugify(default_title) if default_title else "")
        self._slug_edit.setText(seed_slug)
        self._slug_edit.textEdited.connect(self._on_slug_edited)

        self._image_edit = QLineEdit()
        self._image_edit.setObjectName("article_advanced_field")
        self._image_edit.setPlaceholderText("https://example.com/cover.png")
        self._image_edit.setClearButtonEnabled(True)
        self._image_edit.textChanged.connect(self._on_cover_url_changed)

        self._tags_edit = QLineEdit()
        self._tags_edit.setPlaceholderText(_("comma, separated, hashtags"))

        compact_row = QHBoxLayout()
        compact_row.setContentsMargins(0, 0, 0, 0)
        compact_row.setSpacing(12)
        compact_row.addWidget(_make_field(_("Slug (article ID)"), self._slug_edit), 1)
        compact_row.addWidget(_make_field(_("Hashtags"), self._tags_edit), 1)
        adv.addLayout(compact_row)

        adv.addWidget(self._build_cover_field())

        self._advanced_panel.setVisible(False)
        root.addWidget(self._advanced_panel)

        root.addSpacing(4)
        root.addWidget(_make_divider(self))
        root.addSpacing(4)

        # ---- Status: a full-width line above the buttons, hidden while
        # empty, so a long message never squeezes the footer -------------
        self._status = QLabel("")
        self._status.setObjectName("article_status")
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        root.addWidget(self._status)

        # ---- Footer: Publishing-as + buttons ----------------------------
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)

        footer.addWidget(QLabel(_("Publishing as")))

        self._profile_switch = QToolButton()
        self._profile_switch.setObjectName("profile_switch")
        self._profile_switch.setPopupMode(QToolButton.InstantPopup)
        self._profile_switch.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._profile_switch.setIconSize(QSize(CHIP_TOTAL_WIDTH, AVATAR_SIZE))
        self._profile_switch.setCursor(Qt.PointingHandCursor)
        self._switch_menu = QMenu(self._profile_switch)
        self._profile_switch.setMenu(self._switch_menu)
        footer.addWidget(self._profile_switch)
        footer.addStretch(1)

        buttons = QDialogButtonBox()
        self._cancel_btn = buttons.addButton(QDialogButtonBox.Cancel)
        self._publish_btn = buttons.addButton(_("Publish"), QDialogButtonBox.AcceptRole)
        self._publish_btn.setDefault(True)
        self._cancel_btn.clicked.connect(self._on_cancel)
        self._publish_btn.clicked.connect(self._on_publish)
        footer.addWidget(buttons)

        root.addLayout(footer)

        self._refresh_advanced_toggle_label()

    def _apply_theme(self) -> None:
        self.setStyleSheet(_DARK_CSS if self._is_dark else _LIGHT_CSS)
        self._switch_menu.setStyleSheet(
            _DARK_MENU_CSS if self._is_dark else _LIGHT_MENU_CSS
        )

    # -- title ↔ slug coupling --------------------------------------------

    def _on_title_changed(self, text: str) -> None:
        if self._slug_is_auto:
            self._slug_edit.blockSignals(True)
            self._slug_edit.setText(slugify(text))
            self._slug_edit.blockSignals(False)
        self._refresh_publish_enabled()

    def _on_slug_edited(self, _text: str) -> None:
        # The user took manual control of the slug; stop tracking the title.
        self._slug_is_auto = False
        self._refresh_publish_enabled()

    def _on_body_changed(self) -> None:
        self._refresh_meta()
        self._refresh_publish_enabled()

    # -- cover image -------------------------------------------------------

    # Compact landscape thumb, large enough to read at a glance, small
    # enough that the cover block sits at the same visual weight as the
    # slug + hashtags row above it.
    _COVER_THUMB_WIDTH = 144
    _COVER_THUMB_HEIGHT = 84

    def _build_cover_field(self) -> QWidget:
        """Compose the Cover-image field as one tidy horizontal block:

            [thumbnail]   COVER IMAGE
                          [URL field________________] [Choose or upload…]

        The thumbnail is on the left so the user's eye lands on the
        actual image first; the controls cluster on the right. Alt
        text is intentionally absent: NIP-23's ``image`` tag has no
        alt sibling.
        """
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        self._cover_preview = QLabel(_("No cover\nselected"))
        self._cover_preview.setObjectName("article_cover_preview")
        self._cover_preview.setFixedSize(
            self._COVER_THUMB_WIDTH, self._COVER_THUMB_HEIGHT
        )
        self._cover_preview.setAlignment(Qt.AlignCenter)
        row.addWidget(self._cover_preview, 0, Qt.AlignTop)

        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)

        label = QLabel(_("Cover image"))
        label.setObjectName("article_field_label")
        controls.addWidget(label)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)
        input_row.addWidget(self._image_edit, 1)
        if self._media_store is not None:
            self._image_pick_btn = QPushButton(_("Choose or upload…"))
            self._image_pick_btn.setCursor(Qt.PointingHandCursor)
            self._image_pick_btn.clicked.connect(self._on_choose_cover_image)
            input_row.addWidget(self._image_pick_btn)
        else:
            self._image_pick_btn = None
        controls.addLayout(input_row)
        # Subtle help line under the input, since Notion / Medium do something
        # similar so users know where the asset comes from without us
        # having to write docs.
        hint = QLabel(
            _("Picked images upload to your Blossom servers automatically.")
            if self._media_store is not None
            else _("Paste an image URL hosted anywhere on the public web.")
        )
        hint.setObjectName("article_field_hint")
        controls.addWidget(hint)
        controls.addStretch(1)

        row.addLayout(controls, 1)
        return container

    def _on_choose_cover_image(self) -> None:
        """Open the Media Library picker scoped to images, with the alt
        row hidden (cover URL has no alt sibling on Nostr)."""
        if self._media_store is None:
            return
        # Reading the private library is what lets this picker tell a
        # private file from a public one. Without it every file reads as
        # unchecked and no cover can be chosen at all, which is the
        # honest failure but a useless one, so the read starts here.
        if self._private_library is not None:
            self._private_library.bind_profile(self._current_profile)
        picker = MediaLibraryDialog(
            store=self._media_store,
            is_dark=self._is_dark,
            pick_mode=True,
            pick_alt_text=False,
            visibility=self._media_visibility,
            parent=self,
        )
        if self._private_library is not None:
            picker.bind_private_library(self._private_library)
        picker.setWindowTitle(_("Choose hero image"))
        # Pre-filter to images: videos / audio can't be a NIP-23 cover.
        picker._filter_combo.setCurrentIndex(1)
        picker.file_picked.connect(self._on_cover_image_picked)
        picker.exec()

    def _on_cover_image_picked(self, media: MediaFile, _alt: str) -> None:
        # A private pick cannot become a cover as it stands: the bytes at
        # that address are ciphertext, so a reader would get a broken
        # image and the user would have advertised a file they meant to
        # keep. The gate turns it into a public copy first, or leaves the
        # field alone.
        picked = resolve_pick(
            media,
            visibility=self._media_visibility,
            maker=self._copy_maker,
            is_dark=self._is_dark,
            parent=self,
        )
        if not picked.ok:
            if picked.reason:
                self._set_status(picked.reason, error=True)
            return
        # Setting .text() triggers ``_on_cover_url_changed``, which
        # clears any prior thumbnail. We then kick off the load by hash
        # so the preview reflects the new pick.
        self._image_edit.setText(picked.url)
        self._cover_thumb_hash = picked.sha256
        if self._cover_loader is not None and (picked.mime or "").startswith("image/"):
            self._cover_loader.load(picked.sha256, picked.url)

    def _on_cover_url_changed(self, text: str) -> None:
        """Reset the preview whenever the URL field changes. A typed URL
        we don't have a hash for stays as text-only: the user already
        committed to it by typing the URL; downloading + previewing
        arbitrary remote URLs from a publish dialog would be a surprise."""
        if not text.strip():
            self._clear_cover_preview(_("No cover\nselected"))
        elif not self._cover_thumb_hash:
            # Manual entry, so we have no hash, so no thumbnail. Make the
            # preview state honest rather than misleading.
            self._clear_cover_preview(_("Preview shown\nfor library picks"))

    def _clear_cover_preview(self, placeholder: str) -> None:
        self._cover_thumb_hash = ""
        self._cover_preview.setPixmap(QPixmap())
        # Wrap text onto two lines so a short caption sits centered in
        # the thumbnail box without overflowing its fixed width.
        self._cover_preview.setText(placeholder)

    def _on_cover_thumb_ready(self, sha: str, _path: str, pix: QPixmap) -> None:
        if sha != self._cover_thumb_hash or pix.isNull():
            return
        scaled = pix.scaled(
            self._COVER_THUMB_WIDTH - 2,
            self._COVER_THUMB_HEIGHT - 2,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._cover_preview.setPixmap(scaled)
        self._cover_preview.setText("")

    # -- advanced panel ----------------------------------------------------

    def _toggle_advanced(self) -> None:
        self._advanced_open = not self._advanced_open
        self._advanced_panel.setVisible(self._advanced_open)
        self._refresh_advanced_toggle_label()

    def _refresh_advanced_toggle_label(self) -> None:
        if self._advanced_open:
            self._advanced_toggle.setText(_("▾  Advanced"))
        else:
            self._advanced_toggle.setText(_("▸  Advanced  ·  slug, cover image, hashtags"))

    # -- meta strip --------------------------------------------------------

    def _refresh_meta(self) -> None:
        words = word_count.count_markdown_words(self._body_edit.toPlainText())
        if words == 0:
            self._meta_label.setText("")
            return
        minutes = word_count.reading_minutes(words)
        self._meta_label.setText(ngettext(
            "{words} word · ~{minutes} min read", "{words} words · ~{minutes} min read", words,
        ).format(words=QLocale(language()).toString(words), minutes=minutes))

    # -- profile switcher --------------------------------------------------

    def _refresh_profile_chip(self) -> None:
        profile = self._current_profile
        avatar_pix = self._avatars.get(profile.user_pubkey)
        avatar = pixmap_for_profile(
            profile.display_name, profile.user_pubkey, avatar_pix, size=AVATAR_SIZE
        )
        self._profile_switch.setIcon(compose_chip_icon(avatar, self._chevron_color()))
        self._profile_switch.setIconSize(QSize(CHIP_TOTAL_WIDTH, AVATAR_SIZE))
        label = profile.display_name or profile.npub_short()
        self._profile_switch.setText(f"  {label}")
        self._profile_switch.setToolTip(profile.npub_short())
        self._rebuild_switch_menu()

    def _chevron_color(self) -> QColor:
        return QColor("#CCCCCC") if self._is_dark else QColor("#555555")

    def _rebuild_switch_menu(self) -> None:
        menu = self._switch_menu
        menu.clear()
        profiles = self._store.list()
        if not profiles:
            act = menu.addAction(_("(no profiles)"))
            act.setEnabled(False)
            return
        for profile in profiles:
            label = profile.display_name or profile.npub_short()
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(profile.user_pubkey == self._current_profile.user_pubkey)
            act.triggered.connect(
                lambda _checked=False, p=profile: self._on_profile_switched(p)
            )

    def _on_profile_switched(self, profile: Profile) -> None:
        if profile.user_pubkey == self._current_profile.user_pubkey:
            return
        self._current_profile = profile
        self._store.set_default(profile.user_pubkey)
        self._refresh_profile_chip()

    # -- helpers / state ---------------------------------------------------

    def _hashtag_list(self) -> List[str]:
        return [t.strip() for t in self._tags_edit.text().split(",") if t.strip()]

    def _refresh_publish_enabled(self) -> None:
        slug = self._slug_edit.text().strip()
        body = self._body_edit.toPlainText().strip()
        # NIP-23 requires the d-tag; body without anything to say isn't useful.
        self._publish_btn.setEnabled(self._job is None and not self._looking_up
                                     and bool(slug) and bool(body))

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))
        if error:
            color = "#FF6B6B" if self._is_dark else "#C0392B"
        else:
            color = "#FFB347" if self._is_dark else "#A05000"
        self._status.setStyleSheet(f"color: {color};")

    def _set_busy(self, busy: bool) -> None:
        for w in (
            self._title_edit,
            self._summary_edit,
            self._slug_edit,
            self._image_edit,
            self._tags_edit,
            self._body_edit,
        ):
            w.setReadOnly(busy)
        self._profile_switch.setEnabled(not busy)
        self._advanced_toggle.setEnabled(not busy)
        self._refresh_publish_enabled()
        if busy:
            self._publish_btn.setEnabled(False)

    # -- publish flow ------------------------------------------------------

    def _look_up_published_at(self, author: str, slug: str,
                              on_done: Callable[[FirstPublication], None]) -> None:
        find_first_publication(self._relay_pool, self._relay_directory, author, slug,
                               on_done, parent=self)

    def _on_publish(self) -> None:
        slug = self._slug_edit.text().strip()
        body = self._body_edit.toPlainText().strip()
        if not slug or not body or self._job is not None or self._looking_up:
            return
        known = self._first_published.get(slug) or (
            self._first_publications.get(self._current_profile.user_pubkey, slug)
            if self._first_publications is not None else None)
        if known:
            self._publish(slug, body, known)
            return
        # An article published before keeps its date: find it first.
        self._looking_up = True
        self._set_busy(True)
        self._set_status(_("Checking whether this article was published before…"))
        author = self._current_profile.user_pubkey
        self._published_at_lookup(
            author, slug, lambda when, s=slug, a=author: self._on_published_at(s, a, when))

    def _on_published_at(self, slug: str, author: str, found: FirstPublication) -> None:
        if not self._looking_up:
            return                                     # the dialog was closed meanwhile
        self._looking_up = False
        if (author != self._current_profile.user_pubkey
                or slug != self._slug_edit.text().strip()):
            # Changed while asking (another account, another identifier).
            self._set_busy(False)
            self._set_status("")
            return
        body = self._body_edit.toPlainText().strip()
        self._set_busy(False)
        if found.state == FOUND:
            self._first_published[slug] = found.published_at
            self._publish(slug, body, found.published_at)
        elif found.state == NEVER:
            self._publish(slug, body, int(time.time()))
        else:
            self._set_status("")
            self._ask_when_nobody_can_tell(slug, body)

    def _ask_when_nobody_can_tell(self, slug: str, body: str) -> None:
        """None of the relays the author publishes to answered: whether the
        article went out before cannot be told, and a guess would either
        re-date an edit or back-date a new article. The person decides."""
        choice = ask(
            self,
            title=_("Couldn't check whether this article was published before"),
            message=_("None of the relays you publish to answered. If the article went "
                      "out before, publishing it as new gives it today's date, and "
                      "readers see it as a new article."),
            buttons=(Button(_("Publish as New"), "new", NORMAL),
                     Button(_("Cancel"), "cancel", CANCEL),
                     Button(_("Try Again"), "again", DEFAULT)),
            is_dark=self._is_dark)
        if choice == "again":
            self._on_publish()
        elif choice == "new":
            self._publish(slug, body, int(time.time()))

    def _publish(self, slug: str, body: str, published_at: int) -> None:
        try:
            unsigned = build_article(
                content=body,
                pubkey_hex=self._current_profile.user_pubkey,
                slug=slug,
                title=self._title_edit.text(),
                summary=self._summary_edit.text(),
                image=self._image_edit.text(),
                published_at=published_at,
                hashtags=self._hashtag_list(),
                mentions=self._mention_row.mentions(),
                extra_tags=self._details.carried_tags(body) or None,
            )
        except ValueError as exc:
            # Make sure the Advanced panel is open so the slug field is visible
            # when we flag it as the offender.
            if not self._advanced_open:
                self._toggle_advanced()
            self._set_status(_("Cannot build article: {reason}").format(reason=exc), error=True)
            return

        self._set_busy(True)
        self._set_status("")
        self._published_at_used = published_at

        self._job = PublishJob(
            relay_pool=self._relay_pool,
            relay_directory=self._relay_directory,
            session_pool=self._session_pool,
            profile=self._current_profile,
            entitled_relays=relays_from(self._entitled_relays),
            unsigned_event=unsigned,
            parent=self,
        )
        self._job.status_changed.connect(self._set_status)
        self._job.signed.connect(self._on_signed)
        self._job.completed.connect(self._on_completed)
        self._job.failed.connect(self._on_failed)
        self._job.start()

    def _on_signed(self, event_id_hex: str) -> None:
        self._signed_event_id = event_id_hex

    def _on_completed(self, results: List[PublishResult]) -> None:
        self._job = None
        accepted = sum(1 for _url, ok, _message in results if ok)
        if accepted == 0:
            self._set_status(
                _("No relay accepted the article. See log for details."), error=True
            )
            self._set_busy(False)
            return

        if self._first_publications is not None and self._published_at_used:
            # This computer remembers it, for when the relays cannot tell.
            self._first_publications.remember(self._current_profile.user_pubkey,
                                              self._slug_edit.text().strip(),
                                              self._published_at_used)
        hint_relays = [url for url, ok, _message in results if ok][:2]
        naddr = encode_naddr(
            identifier=self._slug_edit.text().strip(),
            author_pubkey_hex=self._current_profile.user_pubkey,
            kind=30023,
            relays=hint_relays,
        )
        self.published.emit(naddr, results)
        self.accept()

    def _on_failed(self, reason: str) -> None:
        self._job = None
        self._set_status(_("Publish failed: {reason}").format(reason=humanize_failure(reason)),
                         error=True)
        self._set_busy(False)

    def _on_cancel(self) -> None:
        self.reject()

    def reject(self) -> None:
        # Cancel, Escape and the window's close button all end here. A
        # signer request already sent can't be revoked, but the job stops:
        # a signature that arrives later is not published.
        if self._job is not None:
            self._job.cancel()
            self._job = None
        self._looking_up = False
        super().reject()
