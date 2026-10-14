#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later


import bisect
import hashlib
import itertools
import logging
import os
import platform
import re
import sys
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import List, Optional, Tuple

from send2trash import send2trash
import shiboken6
from PySide6.QtCore import (
    QBuffer, QByteArray, QDateTime, QEvent, QIODevice, QObject, Qt, QLocale, QMarginsF, QTimer,
    QUrl, QFileSystemWatcher,
)
from PySide6.QtNetwork import QLocalServer
from PySide6.QtGui import (
    QAction, QActionGroup, QKeySequence, QTextCursor, QTextDocument, QTextCharFormat, QColor,
    QImage, QPageLayout, QPageSize, QPixmap, QGuiApplication, QDesktopServices,
    QTextImageFormat
)
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewDialog
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QInputDialog, QLineEdit, QMenu, QWidget,
    QVBoxLayout, QPlainTextEdit, QTextEdit, QTabWidget, QToolButton, QHBoxLayout, QStatusBar,
    QPushButton, QTabBar, QLabel, QDialog, QSplitter,
    QProgressDialog, QToolTip
)

from constants import (
    DARK_BG, DARK_FG, LIGHT_BG, LIGHT_FG, DARK_SELECTION, LIGHT_SELECTION,
    DARK_MENU_BG, DARK_MENU_FG, LIGHT_MENU_BG, LIGHT_MENU_FG,
    DARK_BORDER, LIGHT_BORDER, APP_DISPLAY_NAME, APP_VERSION, APP_URL, TEXT_COLORS,
    DARK_MUTED_FG, LIGHT_MUTED_FG, IPC_SERVER_NAME,
)
from widgets import ElidedLabel, FindBar, LineNumberGutter, FileChangedBar, UpdateBar
from format_toolbar import FormatToolbar
from atomic_file import (
    read_json, read_text_document, save_document, save_text_document, write_json,
)
import diagnostics
import file_paths
import i18n
from i18n import N_, _, ngettext, pgettext
from commands import (
    EDIT, FILE, FORMAT, HELP, INSERT, NOSTR, SEARCH, VIEW, WINDOW, Command, CommandRegistry,
    all_key_texts, platform_keys, redo_words, undo_words,
)
import front_matter
import media_files
import slash_menu
from slash_menu import SlashItem
from table_picker import TablePicker
import spelling
import preview_pane
from preview_pane import (
    ARTICLE, DOCUMENT, NOTE, PREVIEW, SPLIT, SPLIT_MIN_WIDTH, WRITE, PreviewContent, PreviewPane,
    ViewSwitch,
)
from nostr.preview import Article as PreviewArticle
from doc_walk import iter_blocks, iter_image_names, serialize_plain_with_images
from markdown_writer import (
    document_to, document_to_markdown, has_local_only_formatting, holds_faithfully,
    image_markdown, read_markdown,
)
from editor import HtmlEditor
import link_url
import paste
import rich_text
import image_safety
import url_safety
from highlighter import (
    LANGUAGE_DISPLAY_NAMES, RichTextLook, SyntaxHighlighter, canonical_language, detect_language,
    detect_language_from_content,
)
from settings import load_settings, save_setting, save_settings
from welcome import DEFAULT_KEYS as WELCOME_KEYS, is_pristine_welcome, welcome_html
from update_check import UpdateChecker
from updater import (
    UpdateInstaller, detect_install_kind, select_asset, supports_in_app_update,
    sweep_stale_downloads,
)
from alerts import (
    CANCEL, DEFAULT, DESTRUCTIVE, NORMAL, Button, ask, ask_with_checkbox,
    confirm_destructive, inform,
)
from update_dialog import UpdateDialog, WhatsNewDialog
from settings_window import SettingsWindow
from update_flow import AUTOMATIC, guide_url, plan_for
import theme
from export_html import (
    document_to_html, image_data_uri, normalize_after_set_html, sniff_image_ext)
from find_replace import find_all, replace_all, replace_match, visible_matches
from word_count import count_words, reading_minutes
from export_pdf import export_pdf, load_page_setup
from page_setup_dialog import PageSetupDialog
import printing
from pdf_viewer import PdfViewerTab
from rmarkdown import KnitRunner, MediaLink, derive_title, document_to_rmd, media_dir_for
from rmd_setup_dialog import RmdSetupDialog
import rmd_toolchain

# These extensions are loaded as rendered documents, not plain-text source code.
_RICH_DOC_EXTS = ('.html', '.htm', '.md', '.markdown')

# Extensions accepted for drag-and-drop and file open.
# .rmd opens as SOURCE (plain text + markdown highlighting), like RStudio.
# .pdf opens read-only in the built-in PDF viewer, not in an editor.
_SUPPORTED_EXTS = {'.md', '.html', '.htm', '.txt', '.rmd', '.pdf'}

# Image extensions accepted on drag-and-drop. These never overlap with
# _SUPPORTED_EXTS so the dispatcher stays simple. SVG is absent: it is
# not decoded anywhere in this process, so a dropped .svg gets the
# ordinary "unsupported" bar message instead of a silent nothing.
_IMAGE_EXTS = image_safety.IMAGE_FILE_SUFFIXES


# Persisted answer to the paste-upload prompt: "ask" | "always" | "never".
_PASTE_UPLOAD_SETTING = "upload_pasted_images"

# Extensions the Save As dialog can produce; used to decide whether the
# typed filename already carries one.
_SAVE_EXTS = ('.txt', '.html', '.htm', '.md', '.rtf', '.pdf', '.rmd')


def _extension_from_filter(selected_filter: str) -> Optional[str]:
    """Extension from a save-filter's glob pattern, e.g. ".Rmd (*.Rmd)" -> ".Rmd".

    Extracting from the pattern keeps the filter's canonical casing and
    stays correct as filters are added (the old substring matching was
    case-sensitive and silently failed for ".Rmd").
    """
    m = re.search(r'\(\*(\.[0-9A-Za-z]+)', selected_filter)
    return m.group(1) if m else None
from recovery import (
    KEPT, TOO_LARGE, EditorBackup, classify_backup, find_all_backups, is_restorable,
    load_backup_content,
)
import workspace
import workspace_restore
from workspace import Workspace
from recent_files import load_recent, add_recent, clear_recent

from nostr.avatar_store import AvatarBatchLoader, AvatarStore
from nostr.bech32 import encode_nprofile, encode_npub
from nostr.nip05_check import Nip05Check
from nostr.ui.mention_picker import MentionPicker
from nostr.ui import media_card
from nostr.ui.reference_popover import ReferencePopover
from nostr.ui.article_details_panel import ArticleDetailsPanel, DEFAULT_WIDTH as ARTICLE_DETAILS_WIDTH
from nostr import refs
from nostr.blossom.errors import friendly_message
from nostr.blossom.names import MediaNames
from nostr.blossom.hashes import hash_from_url
from nostr.imports.workers import run_for_size
from nostr.media_server_list import publish_server_list, server_list_tags
from nostr.outbox import writer as outbox_writer
from nostr.profile_editing import ProfileEditing
from nostr.relay import NOT_CHOSEN
from nostr.relay_auth import DECLINED, AccountSignIn
from nostr.state import NostrState
from nostr.ui.profile_window import ProfileWindow
from nostr.blossom import BLOSSOM_MAX_FILE_SIZE
from nostr.blossom.plan import get_effective_max_file
from nostr.blossom.server_list import UserServerList
from nostr.blossom.store import MediaFile, MediaStore
from nostr.bunker import BunkerSessionPool
from nostr.contacts import ContactListFetcher
from nostr.draft_store import DraftState, DraftStore
from nostr.draft_deletions import DraftDeletions
from nostr.draft_sync import DraftSync
from nostr.imports.favicons import FaviconCache
from nostr.ui.imports_badge import COMMAND_TITLE as IMPORTS_BADGE_TITLE
from nostr.ui.imports_badge import SETTINGS_KEY as IMPORTS_BADGE_KEY
from nostr.ui.imports_badge import AppIconBadge
from nostr.imports_controller import ImportsController
from nostr.ui.imports_activity import activity_line
from nostr.ui.imports_window import SETTINGS_KEY as IMPORTS_SETTINGS_KEY
from nostr.ui.imports_window import ImportsWindow
from nostr.imports.constants import IDENTIFIER_PREFIX as IMPORT_IDENTIFIER_PREFIX
from nostr.imports.constants import SOURCE_TAG as IMPORT_SOURCE_TAG
from nostr.article_details import (
    ArticleDetails, from_front_matter, front_matter_with, kept_in_file)
from nostr.first_publications import FirstPublications
from nostr.drafts import (
    INNER_KIND_LONG_FORM,
    INNER_KIND_SHORT_NOTE,
    MAX_INNER_PAYLOAD_BYTES,
    SUPPORTED_INNER_KINDS,
    build_inner_event,
    serialize_inner_event,
)
from nostr.known_people import KnownPeople, Person
from nostr.media.assets import ASSET_SCHEME, AssetState, asset_key, parse_asset_key
from nostr.imports.sources.nostr import RelayQueryAdapter
from nostr.media.manager import AssetManager
from nostr.media.media_visibility import MediaVisibility
from nostr.media.private_library import PrivateLibrary
from nostr.media.publish_copy import PublicCopyMaker
from nostr.media.visibility import PublicLedger
from nostr.metadata import AvatarLoader, ProfileMetadataFetcher, can_receive_zaps
from nostr.outbox.directory import RelayDirectory
from nostr.profiles import Profile, ProfileStore, short_npub
from nostr.publisher import (
    DraftBulkDeleteJob,
    DraftPublishJob,
    PublishedMedia,
    library_audio_video_in,
)
from nostr.relay import RelayPool
from nostr.search import Nip50SearchClient
from nostr.ui.connect_dialog import ConnectDialog
from nostr.ui.profile_chip import ProfileChip
from nostr.ui.draft_conflict_banner import DraftConflictBanner
from nostr.ui.drafts_panel import DEFAULT_PANEL_WIDTH, DraftsPanel
from nostr.draft_relays import PrivateDraftRelays
from nostr.published import PublishedList
from nostr.ui.draft_relays_sheet import DraftRelaysSheet
from nostr.ui.published_view import PANEL_VIEW as PUBLISHED_VIEW
from nostr.ui.published_view import PublishedView, add_to_panel as add_published_view
from nostr.membership_controller import MembershipController
from nostr.key_vault import KeyVault
from nostr.account_controller import AccountController
from nostr.ui.media_library_dialog import MediaLibraryDialog, format_size
from nostr.ui.media_saver import MediaSaver, save_failure, saves_failure
from nostr.ui.media_servers_dialog import MediaServersDialog
from nostr.ui.publish_article_dialog import PublishArticleDialog
from nostr.ui.publish_copy_dialog import resolve_pick
from nostr.ui.publish_note_dialog import PublishNoteDialog
from nostr.ui.save_destination_dialog import SaveDestination, SaveDestinationDialog
from nostr.ui.stash_kind_dialog import StashChoice, StashKind, StashKindDialog
from nostr.ui.thumbnail_loader import ThumbnailLoader

# Monotonic counter for clipboard / drop upload job names. Pairs with
# the dialog-side helper but lives here too because main_window also
# originates paste-to-upload jobs (editor Ctrl+V).
_paste_job_counter = itertools.count(1)

# Second line of every "Couldn't save" alert: what to do next.
_SAVE_FAILED_HINT = _("Your document is still open. Use Save As to choose another place.")


def _number(n: int) -> str:
    """``n`` with the digit grouping of the reader's language (70,000 in
    English, 70.000 in German)."""
    return i18n.display_locale().toString(n)


# --------------------------------------------------------------------------- #
# Per-tab Nostr-draft binding                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class DraftBinding:
    """Tracks a tab's link to a NIP-37 draft.

    Set on an editor whenever the user stashes a tab as a draft or
    opens an existing draft into a new tab. Used by the tab-title
    decoration (lock glyph + draft title), by the stash flow to
    preserve the addressable ``d``-tag across subsequent saves, and
    by the profile-mismatch guard to spot a binding whose signing
    identity no longer matches the active profile.
    """

    identifier: str            # the draft's d-tag
    inner_kind: int            # INNER_KIND_SHORT_NOTE or INNER_KIND_LONG_FORM
    event_id: str = ""         # newest wrap event id we've observed for this draft
    created_at: int = 0        # ``created_at`` of that wrap (last stash time)
    title: str = ""            # cached display title for the tab
    # pubkey of the profile this draft was last signed by. Used to
    # detect "tab from a different identity" after profile switches.
    profile_pubkey: str = ""


def _ask_save_changes(parent, title: str, *, save_label: str = "",
                      message: str = "") -> str:
    """Save, Don't Save or Cancel, in Apple's standard wording.

    Returns "save", "discard" or "cancel". Don't Save is destructive (red,
    and placed apart from the other two on macOS); Save is the default.
    """
    message = message or _("Your changes will be lost if you don’t save them.")
    return ask(parent, title=title, message=message, buttons=(
        Button(_("Don’t Save"), "discard", DESTRUCTIVE),
        Button(_("Cancel"), "cancel", CANCEL),
        Button(save_label or _("Save"), "save", DEFAULT),
    ))


# How long quitting waits for unsent changes to the list of sources.
QUIT_SYNC_WAIT_MS = 5_000


def _text_field(widget):
    """``widget`` when it is a text field other than a document (a find
    field, a sheet's or a dialog's field, the Imports window's article):
    while it has the focus, the Edit menu acts on it. Else None."""
    if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)) and not isinstance(
            widget, HtmlEditor):
        return widget
    return None


def _field_history(field) -> tuple:
    """Whether ``field`` has something to undo, and to redo."""
    if isinstance(field, QLineEdit):
        return field.isUndoAvailable(), field.isRedoAvailable()
    if field.isReadOnly():
        return False, False
    return field.document().isUndoAvailable(), field.document().isRedoAvailable()


def _field_signals(field) -> list:
    """What changes in a text field that the Edit menu shows: its text
    (what Undo undoes), its selection (what Cut and Copy act on)."""
    signals = [field.textChanged, field.selectionChanged]
    if not isinstance(field, QLineEdit):
        signals += [field.undoAvailable, field.redoAvailable]
    return signals

class _MessageTip(QObject):
    """A tooltip for one status bar message: shown over the message alone
    (not the word count or a button beside it), and gone with it."""

    def __init__(self, bar) -> None:
        super().__init__(bar)
        self._bar = bar
        self._message = self._tip = ""
        bar.installEventFilter(self)
        bar.messageChanged.connect(self._changed)

    def set(self, message: str, tip: str) -> None:
        """``tip`` for ``message``, which the bar shows now."""
        self._message, self._tip = message, tip

    def _changed(self, message: str) -> None:
        if message != self._message:
            self._message = self._tip = ""

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.Type.ToolTip or not self._tip:
            return False
        beside = [child.geometry().left() for child in self._bar.findChildren(
                      QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)
                  if child.isVisible() and child.width() > 0]
        if event.pos().x() < min(beside, default=self._bar.width()):
            QToolTip.showText(event.globalPos(), self._tip, self._bar)
            return True
        return False


class _WidthWatcher(QObject):
    """Calls ``changed()`` when a widget's width changes (a tab's Split
    needs room for two readable columns)."""

    def __init__(self, widget, changed) -> None:
        super().__init__(widget)
        self._changed = changed
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.Resize and event.size().width() != event.oldSize().width():
            self._changed()
        return False



def spelling_language_names(tags) -> list:
    """(tag, name) of the languages a spell checker offers, by name: the
    language as its speakers write it, with its country where several
    are offered ("Deutsch (Deutschland)", "English (United States)")."""
    named = []
    for tag in tags:
        locale = QLocale(tag.replace("-", "_"))
        name = locale.nativeLanguageName() or tag
        name = name[:1].upper() + name[1:]
        if "-" in tag and locale.nativeTerritoryName():
            name = f"{name} ({locale.nativeTerritoryName()})"
        named.append((tag, name))
    return sorted(named, key=lambda pair: pair[1].casefold())

class MainWindow(QMainWindow):
    def __init__(self, initial_path: str | None = None):
        super().__init__()
        self.setWindowTitle("")
        self.resize(1100, 720)

        theme_pref = load_settings().get("theme")
        if theme_pref == "dark":
            self.is_dark_theme = True
        elif theme_pref == "light":
            self.is_dark_theme = False
        else:
            self.is_dark_theme = self._detect_os_dark_theme()
        # While True (View > Appearance > Automatic), the app follows the
        # system's light or dark appearance live; Light or Dark sticks.
        self._follow_os_theme = theme_pref is None
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_os_color_scheme_changed)

        self.show_line_numbers = False
        # Off by default, like line numbers above it. This is a writing
        # app first, and colouring prose that merely looks like code is
        # a distraction the user has to go and switch off.
        self.syntax_highlighting = False

        _s = load_settings()
        self.editor_background = _s.get("editor_background", "none")
        self.highlight_current_line = _s.get("highlight_current_line", False)
        self.paper_mode = _s.get("paper_mode", False)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._saving_paths: set[str] = set()

        self.setAcceptDrops(True)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(False)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.setMovable(True)
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._on_tab_context_menu)

        self.plus_btn = QToolButton()
        self.plus_btn.setText("+")
        self.plus_btn.setAutoRaise(True)
        self.plus_btn.setToolTip(_("New Tab"))
        self.plus_btn.setAccessibleName(_("New Tab"))
        self.plus_btn.clicked.connect(self.new_tab)
        # The account in use, at the end of the tab row (where browsers
        # show theirs); there only while Nostr is in use.
        self.profile_chip = ProfileChip()
        corner = QWidget()
        corner_row = QHBoxLayout(corner)
        corner_row.setContentsMargins(0, 0, 4, 0)
        corner_row.setSpacing(6)
        corner_row.addWidget(self.plus_btn)
        corner_row.addWidget(self.profile_chip)
        self.tabs.setCornerWidget(corner, Qt.TopRightCorner)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        # Silent while backups succeed; when the document's backup cannot be
        # kept, it says so here until it is kept again (decision Q-J).
        self._backup_problem = ElidedLabel()
        self._backup_problem.setObjectName("BackupProblem")
        self._backup_retry = QToolButton()
        self._backup_retry.setObjectName("BackupRetry")
        self._backup_retry.setText(_("Try Again"))
        self._backup_retry.setAccessibleName(_("Try Backing Up Again"))
        self._backup_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        self._backup_retry.clicked.connect(self._retry_backup)
        for widget in (self._backup_problem, self._backup_retry):
            widget.setVisible(False)
            self.status.addPermanentWidget(widget)
        # Files added in the Media Library that didn't upload once its
        # window was closed (name -> reason): counted here, as a backup
        # problem is, until the library, which shows each with Retry, is
        # opened.
        self._library_failures: dict = {}
        self._library_problem = ElidedLabel()
        self._library_problem.setObjectName("LibraryProblem")
        self._library_open = QToolButton()
        self._library_open.setObjectName("LibraryOpen")
        self._library_open.setText(_("Open Media Library"))
        self._library_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._library_open.clicked.connect(lambda: self._on_nostr_media_library())
        for widget in (self._library_problem, self._library_open):
            widget.setVisible(False)
            self.status.addPermanentWidget(widget)
        # How long the document is, quietly: words and reading time, or
        # the words of the selection while there is one.
        self._words_label = QLabel()
        self._words_label.setObjectName("WordCount")
        self._words_label.setContentsMargins(0, 0, 12, 0)
        self.status.addPermanentWidget(self._words_label)
        # A message keeps its room in a narrow window: the word count gives
        # way first, then the library failure line shortens, and steps
        # aside while not even a few letters of it fit.
        self.status.messageChanged.connect(lambda _message: self._fit_status_bar())
        _WidthWatcher(self.status, self._fit_status_bar)
        _WidthWatcher(self._words_label, self._fit_status_bar)
        self._word_count_timer = QTimer(self)
        self._word_count_timer.setSingleShot(True)
        self._word_count_timer.setInterval(300)
        self._word_count_timer.timeout.connect(self._update_word_count)
        self._selection_words_timer = QTimer(self)
        self._selection_words_timer.setSingleShot(True)
        self._selection_words_timer.setInterval(150)
        self._selection_words_timer.timeout.connect(
            lambda: self._show_word_count(self.current_editor()))
        self._document_words = 0
        self._line_label = QLabel()
        self._line_label.setContentsMargins(0, 0, 8, 0)
        self.status.addPermanentWidget(self._line_label)
        # The mention picker at the caret, made on its first use.
        self._mention_picker = None
        # Article Details' co-author picker, and who it answers.
        self._coauthor_picker = None
        self._coauthor_chosen = None

        self._apply_theme()

        # Nostr publishing infrastructure. All pieces are process-wide
        # singletons living on the window; they are cheap to create and stay
        # alive for the lifetime of the editor.
        self._relay_pool = RelayPool(parent=self)
        self._profile_store = ProfileStore()
        # Whether Nostr is in use (an account is active): the one signal the
        # editor's Nostr features follow (nostr/state.py).
        self.nostr_state = NostrState(lambda: self._profile_store.default(), parent=self)
        self.profile_chip.setVisible(self.nostr_state.active)
        self.nostr_state.changed.connect(self.profile_chip.setVisible)
        # The preview shows what will be published only while Nostr is in use.
        self.nostr_state.changed.connect(lambda _active: self._redraw_preview())
        # Where everyone reads and writes (NIP-65): verified, cached, and the
        # user's own lists remembered across launches. Every job and panel
        # that touches relays asks it where to go.
        self._relay_directory = RelayDirectory(
            self._relay_pool,
            own_pubkeys=lambda: [p.user_pubkey for p in self._profile_store.list()],
            parent=self)
        # Keys kept on this computer, for accounts that chose that over a
        # signer app (Create Account, Restore Account).
        self._key_vault = KeyVault()
        self._session_pool = BunkerSessionPool(self._relay_pool, parent=self,
                                               vault=self._key_vault)
        # A relay of the account's own that asks for a sign-in (NIP-42)
        # gets one, signed by the account's signer; no other relay does.
        self._relay_pool.sign_in = AccountSignIn(
            profile=lambda: self._profile_store.default(),
            session_pool=self._session_pool, directory=self._relay_directory,
            entitled=self._entitled_relays)
        self._relay_pool.signing_in.connect(self._on_relay_signing_in)
        self._relay_pool.sign_in_failed.connect(self._on_relay_sign_in_failed)
        # Creating, restoring, backing up and signing out of accounts, and
        # telling the network about a new one (nostr/account_controller.py).
        self._accounts = AccountController(
            relay_pool=self._relay_pool, directory=self._relay_directory,
            vault=self._key_vault, session_pool=self._session_pool,
            store=self._profile_store, window=self,
            is_dark=lambda: self.is_dark_theme, parent=self)
        self._accounts.activate.connect(self._on_nostr_profile_connected)
        self._accounts.profile_changed.connect(self._on_metadata_updated)
        self._accounts.status.connect(
            lambda text, ms: self.status.showMessage(text, ms))
        self._accounts.link_activated.connect(self._open_external)
        self._metadata_fetcher = ProfileMetadataFetcher(
            self._relay_pool, self._profile_store, parent=self,
            relay_directory=self._relay_directory,
        )
        self._metadata_fetcher.updated.connect(self._on_metadata_updated)
        self._avatar_loader = AvatarLoader(parent=self)
        # Throttled batcher feeds AvatarStore; widgets get repaint hints via
        # AvatarStore.avatar_added so newly-arrived pixmaps appear live.
        self._avatar_batcher = AvatarBatchLoader(self._avatar_loader, parent=self)
        self._avatars = AvatarStore(parent=self)
        self._avatar_batcher.ready.connect(self._avatars.put)
        # The own-profile chip refresh is still triggered explicitly so we can
        # also flip the menu if the active profile's avatar landed.
        self._avatars.avatar_added.connect(self._on_avatar_added)

        # Mentions infrastructure: cached known people, NIP-50 search,
        # background contact-list fetcher. All process-wide singletons.
        self._known_people = KnownPeople()
        # A person's Nostr address shows only once their domain confirmed it.
        self._address_check = Nip05Check(parent=self)
        self._search_client = Nip50SearchClient(
            self._relay_pool, self._known_people, parent=self
        )
        self._search_client.results.connect(self._on_search_results)
        self._contact_fetcher = ContactListFetcher(
            self._relay_pool, self._known_people, parent=self,
            relay_directory=self._relay_directory,
        )
        self._contact_fetcher.person_updated.connect(self._on_person_updated)

        # NIP-37 draft infrastructure. The store is profile-scoped and
        # rebinds when the active profile changes; the sync orchestrator
        # owns the subscription + decryption pipeline; the panel is the
        # user-facing surface. All three stay alive for the session.
        self._draft_store = DraftStore(parent=self)
        self._draft_store.record_changed.connect(self._on_draft_record_changed)
        # When the articles published from here first went out, for when
        # the relays cannot tell (an edit keeps that date).
        self._first_publications = FirstPublications()
        self._draft_sync = DraftSync(
            relay_pool=self._relay_pool,
            relay_directory=self._relay_directory,
            session_pool=self._session_pool,
            store=self._draft_store,
            entitled_relays=self._entitled_relays,
            parent=self,
        )
        self._draft_sync.status_changed.connect(self._on_draft_sync_status)
        self._draft_sync.bunker_error.connect(self._on_draft_sync_bunker_error)
        self._draft_sync.signer_unreachable.connect(
            self._on_draft_sync_signer_unreachable
        )
        # Deletions other apps announce as NIP-09 requests (STANDUP does),
        # which the wraps alone would not show (nostr/draft_deletions.py).
        self._draft_deletions = DraftDeletions(
            relay_pool=self._relay_pool,
            relay_directory=self._relay_directory,
            store=self._draft_store,
            entitled_relays=self._entitled_relays,
            parent=self,
        )
        # Imports: the sources shared with STANDUP, the inbox, the checks
        # while the app runs, the import jobs and the Imports window, for
        # the account in use (nostr/imports_controller.py). It binds only
        # while Nostr is in use (see _update_account_actions).
        self._imports = ImportsController(
            relay_pool=self._relay_pool,
            relay_directory=self._relay_directory,
            session_pool=self._session_pool,
            draft_store=self._draft_store,
            entitled_relays=self._entitled_relays,
            blossom_primary=lambda: self._media_store.settings.primary,
            window_factory=self._make_imports_window,
            # The sources' site icons, found once and kept (C12).
            icons=FaviconCache(parent=self),
            parent=self,
        )
        # The Inbox count on the app icon, when the person wants it (Q-7).
        self._imports_badge = AppIconBadge(
            self._imports, load=lambda: load_settings().get(IMPORTS_BADGE_KEY),
            save=lambda value: save_setting(IMPORTS_BADGE_KEY, value), parent=self)
        # Created lazily inside ``_build_findbar`` so its parent is the
        # central widget rather than ``self`` - keeps Qt's geometry
        # reasoning straightforward.
        self._drafts_panel: Optional[DraftsPanel] = None
        # Maps each open editor → its conflict banner widget so we can
        # avoid stacking duplicate banners on the same tab.
        self._tab_conflict_banners: dict = {}

        # Content-addressed byte cache. Doubles as the asset layer's blob
        # store: one cache for thumbnails and document images means an
        # image the library already downloaded needs no second copy.
        self._media_image_loader = ThumbnailLoader(parent=self)
        # Blossom media library - orchestrates uploads / list / delete
        # against the user's configured Blossom servers, signing each
        # auth event through the existing bunker pool. It seeds the same
        # cache on the way out, so an upload never has to be downloaded
        # back to be shown.
        self._media_library_dialog = None
        # The Insert Image picker while it is open (it is modal).
        self._media_picker = None
        # Save As from either: it goes on when the window that asked
        # closes, and is said in the status bar then.
        self._media_saver = MediaSaver(self._media_image_loader, parent=self)
        # What this bar last said about a save ("Saving “x”…" to be followed
        # by its end), and the message its tooltip of reasons belongs to.
        self._media_save_said = ""
        self._message_tip = _MessageTip(self.status)
        self._media_saver.batch_finished.connect(self._on_media_saves_finished)
        self._media_saver.started.connect(self._on_media_save_started)
        self._media_saver.finished.connect(self._on_media_save_finished)
        # The Media Servers sheet while it is open (it is modal).
        self._media_servers_sheet = None
        # Association membership, which grants a relay and a media server.
        # Resolved in the background; until it answers the account simply
        # has no benefits, which is the same state as not being a member.
        # The controller owns the roster, its refresh, the membership
        # window and its client; this window only hands its providers on
        # (see _entitled_relays and the three below it).
        self._membership = MembershipController(
            profile_provider=lambda: self._profile_store.default(),
            relay_pool=self._relay_pool,
            session_pool=self._session_pool,
            relay_directory=self._relay_directory,
            is_dark=lambda: self.is_dark_theme,
            open_link=self._open_external,
            connect_signer=self._on_nostr_connect,
            show_status=self.status.showMessage,
            parent=self,
        )
        self._membership.benefits_changed.connect(self._on_membership_benefits_changed)
        self._membership.profile_published.connect(self._on_own_profile_published)
        self._media_store = MediaStore(
            session_pool=self._session_pool,
            profile_provider=lambda: self._profile_store.default(),
            blob_cache=self._media_image_loader,
            entitled_servers=self._entitled_blossom_servers,
            server_quota=self._entitled_quota,
            names=MediaNames(),
            parent=self,
        )
        # An account that was already signed in when the app opened never
        # passes through the connect flow, so it is resolved here too, and
        # the roster is refreshed while the app is open. Started once the
        # store its answer may concern exists.
        self._membership.start()
        # The media servers the account publishes (its kind 10063 list):
        # adopted only when nothing is configured here yet, otherwise
        # offered in the Media Library, and always tried when an image's
        # stored address stops answering. It shares the store's settings,
        # so what it adopts is what the store uploads to.
        self._server_list = UserServerList(
            self._relay_pool, relay_directory=self._relay_directory,
            settings=self._media_store.settings, parent=self)
        self._server_list.adopted.connect(self._on_media_servers_adopted)
        self._server_list.suggestions_changed.connect(self._on_media_server_suggestions)
        # The one object the editor side talks to about images. Every
        # boundary it crosses is injected here, so nothing below this
        # line knows anything about Blossom.
        self._asset_manager = AssetManager(
            blob_store=self._media_image_loader,
            uploader=self._media_store,
            profile_provider=lambda: self._profile_store.default(),
            decoder=image_safety.decode_image_bytes,
            recovery_provider=self._server_list.recovery_servers,
            parent=self,
        )
        self._asset_manager.asset_changed.connect(self._refresh_asset_in_documents)
        self._asset_manager.asset_changed.connect(self._on_media_uploaded)
        self._asset_manager.asset_upload_failed.connect(self._on_asset_upload_failed)
        # How far each audio or video file's upload has got (sha256 ->
        # percent), shown on its card in the text.
        self._media_progress: dict = {}
        self._media_store.upload_progress.connect(self._on_media_upload_progress)
        # A file added in the Media Library is the store's job, not the
        # window's: a failure after that window closed is said here.
        self._media_store.upload_failed.connect(self._on_library_upload_failed)

        # Private media. The ledger is the record of everything this app
        # has deliberately made public and is the only way to revoke any
        # of it, so it is loaded at startup rather than on demand. The
        # library is not: reading it costs one signer round-trip per
        # file, and an account that never opens the media library must
        # not pay for a prompt it did not ask for. It is bound lazily,
        # where the user actually goes looking for their pictures.
        self._public_ledger = PublicLedger()
        self._private_library = PrivateLibrary(
            session_pool=self._session_pool,
            relay_directory=self._relay_directory,
            query=RelayQueryAdapter(self._relay_pool, parent=self),
            entitled_relays=self._entitled_relays,
            parent=self,
        )
        self._media_visibility = MediaVisibility(
            library=self._private_library, ledger=self._public_ledger,
        )
        # The one object in the app that turns a private file into a
        # public one. It fetches through the same guarded downloader as
        # every other blob and uploads through the same store, so a
        # public copy gets the same auth, verification and mirroring as
        # anything else this app sends.
        self._copy_maker = PublicCopyMaker(
            ledger=self._public_ledger,
            fetcher=self._media_image_loader,
            uploader=self._media_store,
            parent=self,
        )

        self._update_profile_chip()
        self._refresh_profile_chip_menu()
        # If a profile exists from a previous session, refresh its metadata
        # in the background and prime the mentions cache from the NIP-02
        # contact list.
        active = self._profile_store.default()
        if active is not None:
            self._metadata_fetcher.fetch(active)
            self._contact_fetcher.fetch(active.user_pubkey)
            self._server_list.refresh(active)
            # Start the draft sync in the background. It's idempotent -
            # if the panel is never opened, this still keeps the store
            # warm so opening the panel later is instant.
            self._draft_sync.start_for(active)
            self._draft_deletions.start_for(active)
            # An account created here whose setup was left for later is
            # finished now, once the window is up.
            QTimer.singleShot(0, lambda: self._accounts.profile_activated(
                self._profile_store.default()))

        self._build_actions()
        self._build_menu()
        # The app icon setting as kept (not a choice made now), and as
        # changed where Imports offers it.
        self._on_imports_badge_changed(self._imports_badge.on)
        self._imports_badge.changed.connect(self._on_imports_badge_changed)
        self._build_findbar()
        self.nostr_state.changed.connect(self._on_nostr_in_use_changed)

        # The find bar and the account chip construct themselves in dark
        # mode; bring them in line with a light theme detected above.
        if not self.is_dark_theme:
            self._set_theme(self.is_dark_theme)

        # Startup order: the tabs an update restart wrote down come back
        # first, in their own order; then the file named on the command
        # line; then any crash leftovers. The crash sweep skips only the
        # backups the update restore took over. A file tab's backup is
        # named after its path, so skipping "every open tab's backup" would
        # also skip the crash backup of the very file named on the command
        # line, and the clean tab would later overwrite it.
        resumed, adopted = self._resume_workspace()
        if initial_path and os.path.isfile(initial_path):
            self.open_path(initial_path)

        restored = self._restore_backups(skip=adopted)
        session = (self._restore_session()
                   if not initial_path and not restored and resumed is None else False)
        if not initial_path and not restored and not session and not self.tabs.count():
            if "welcome_shown" not in load_settings():
                self.show_welcome_tab()
                save_setting("welcome_shown", True)
            else:
                self.new_tab()

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._update_undo_redo_buttons()
        # Edit > Undo follows the window in front (the Imports window has
        # its own Undo Skip), and the document's own commands are dimmed
        # while another window is in front.
        QApplication.instance().focusChanged.connect(self._update_undo_redo_buttons)
        # Paste follows what is on the clipboard, as it follows the focus.
        QApplication.clipboard().dataChanged.connect(self._update_clipboard_commands)
        QApplication.instance().focusChanged.connect(self._update_window_commands)
        self._update_status_bar()
        self._start_ipc_server()
        QTimer.singleShot(0, lambda ws=resumed: self._announce_version_change(ws))
        QTimer.singleShot(3000, self._maybe_auto_check_for_updates)
        # Downloads an earlier update could not delete (the Windows
        # installer runs from its folder after MyEditor has quit).
        QTimer.singleShot(5000, sweep_stale_downloads)


    # ----------------------------------------------------------------------
    # IPC - SINGLE INSTANCE
    # ----------------------------------------------------------------------
    def _start_ipc_server(self):
        """Start a local socket server so that a second launch can forward a
        file path here instead of opening a new window."""
        QLocalServer.removeServer(IPC_SERVER_NAME)  # remove stale socket if any
        self._ipc_server = QLocalServer(self)
        self._ipc_server.newConnection.connect(self._on_ipc_connection)
        self._ipc_server.listen(IPC_SERVER_NAME)

    def _on_ipc_connection(self):
        conn = self._ipc_server.nextPendingConnection()
        conn.waitForReadyRead(300)
        # An absolute path: the second launch made it so in its own folder.
        path = conn.readAll().data().decode("utf-8").strip()
        conn.deleteLater()
        if path and os.path.isfile(path):
            self.open_path(path)
        self.raise_()
        self.activateWindow()

    # ----------------------------------------------------------------------
    # THEME APPLICATION
    # ----------------------------------------------------------------------
    @staticmethod
    def _detect_os_dark_theme() -> bool:
        """Best-effort read of the OS color scheme. Dark is the fallback
        default when the platform does not report a scheme."""
        scheme = QGuiApplication.styleHints().colorScheme()
        return scheme != Qt.ColorScheme.Light

    def _apply_theme(self):
        # Sync the application palette first so pop-ups the window stylesheet
        # never touches (message boxes, input dialogs, the tab scroller)
        # follow the theme. This is the single call site for both startup
        # and every later toggle, since _set_theme routes through here.
        theme.apply_app_theme(self.is_dark_theme)

        bg = DARK_BG if self.is_dark_theme else LIGHT_BG
        fg = DARK_FG if self.is_dark_theme else LIGHT_FG
        menu_bg = DARK_MENU_BG if self.is_dark_theme else LIGHT_MENU_BG
        menu_fg = DARK_MENU_FG if self.is_dark_theme else LIGHT_MENU_FG
        border = DARK_BORDER if self.is_dark_theme else LIGHT_BORDER
        muted = DARK_MUTED_FG if self.is_dark_theme else LIGHT_MUTED_FG
        # A dimmed menu item is still read (it says what a command needs, as
        # the Nostr menu's do without an account): dimmer than the rest, as
        # in the editor's context menu, not the border color it vanished in.
        dimmed = "#6A6A6A" if self.is_dark_theme else "#999999"

        self.setStyleSheet(f"""
            QMainWindow {{ background: {bg}; }}
            QTabWidget::pane {{
                border-top: 1px solid {border};
                background: {bg};
            }}
            QTabBar {{
                background: {menu_bg};
            }}
            QTabBar::tab {{
                background: {menu_bg};
                color: {menu_fg};
                padding: 8px 16px;
                border: 1px solid {border};
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {bg};
                border-bottom: 1px solid {bg};
            }}
            QTabBar::tab:hover {{ background: {menu_bg}; }}
            QTabBar QToolButton {{
                background: {menu_bg};
                color: {menu_fg};
                border: 1px solid {border};
            }}
            QTabBar QToolButton:hover {{ background: {bg}; }}
            QPushButton#TabClose {{
                color: {muted}; background: transparent; border: none;
                border-radius: 4px; font-size: 18px; padding: 0px;
            }}
            QPushButton#TabClose:hover {{ color: {fg}; background: {border}; }}
            QStatusBar {{
                background: {menu_bg};
                color: {menu_fg};
                border-top: 1px solid {border};
            }}
            QMenuBar {{
                background: {menu_bg};
                color: {menu_fg};
                border-bottom: 1px solid {border};
            }}
            QMenuBar::item:selected {{ background: {bg}; }}
            QMenu {{
                background: {menu_bg};
                color: {menu_fg};
                border: 1px solid {border};
            }}
            QMenu::item:selected {{ background: {bg}; }}
            QMenu::item:disabled {{ color: {dimmed}; }}
            QScrollBar:vertical {{
                background: {menu_bg};
                width: 12px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {border};
                min-height: 20px;
                border-radius: 6px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {fg}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{
                background: {menu_bg};
                height: 12px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {border};
                min-width: 20px;
                border-radius: 6px;
                margin: 2px;
            }}
            QScrollBar::handle:horizontal:hover {{ background: {fg}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        """)

        if self.is_dark_theme:
            self.plus_btn.setStyleSheet("""
                QToolButton {
                    background: #2D2D30;
                    color: #CCCCCC;
                    border: 1px solid #3C3C3C;
                    border-radius: 4px;
                    font-size: 14px;
                    font-weight: bold;
                    min-width: 20px;
                    min-height: 20px;
                }
                QToolButton:hover { background: #3C3C3C; }
            """)
        else:
            self.plus_btn.setStyleSheet("""
                QToolButton {
                    background: #DCDCDC;
                    color: #222222;
                    border: 1px solid #BBBBBB;
                    border-radius: 4px;
                    font-size: 14px;
                    font-weight: bold;
                    min-width: 20px;
                    min-height: 20px;
                }
                QToolButton:hover { background: #C8C8C8; }
            """)

        label_color = DARK_MENU_FG if self.is_dark_theme else LIGHT_FG
        self._line_label.setStyleSheet(f"color: {label_color}; font-size: 12px;")
        muted = DARK_MUTED_FG if self.is_dark_theme else LIGHT_MUTED_FG
        self._words_label.setStyleSheet(f"color: {muted}; font-size: 12px;")
        self._show_backup_status()
        self._show_library_failures()
        for picker in (self._mention_picker, getattr(self, "_coauthor_picker", None)):
            if picker is not None:
                picker.set_dark(self.is_dark_theme)

        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed:
                self._update_editor_theme(ed)


    def _auto_detect_language(self, editor):
        """Called via debounce timer; detects language from content for untitled tabs."""
        if getattr(editor, '_language', None):
            return  # already known
        lang = detect_language_from_content(editor.toPlainText())
        if lang and self.syntax_highlighting:
            editor._language = lang
            self._drop_highlighter(editor)
            editor._highlighter = SyntaxHighlighter(
                editor.document(), lang, self.is_dark_theme
            )
            self._update_editor_font(editor)     # code: the monospace font
            self._attach_spelling(editor)        # code is not checked
            self._update_status_bar()

    @staticmethod
    def _drop_highlighter(editor) -> None:
        if hasattr(editor, '_highlighter'):
            editor._highlighter.setDocument(None)
            del editor._highlighter

    def _attach_highlighter(self, editor, path: str | None):
        """Detect language from path and attach / replace the highlighter:
        syntax colors for a code file (while Syntax Highlighting is on),
        the look of inline code and code blocks for a document with
        Markdown structure. One per document: two would undo each other."""
        lang = detect_language(path)
        editor._language = lang
        self._drop_highlighter(editor)
        # Only highlight plain-text source files, not rendered rich documents
        if lang and path and not path.lower().endswith(_RICH_DOC_EXTS) and self.syntax_highlighting:
            editor._highlighter = SyntaxHighlighter(
                editor.document(), lang, self.is_dark_theme
            )
        elif self._editor_kind(editor) == "rich":
            editor._highlighter = RichTextLook(editor.document(), self.is_dark_theme)
        self._update_editor_font(editor)
        self._attach_spelling(editor)

    # -- spelling (Edit > Spelling; the system's own checker, spelling/) ---------

    def _spell_checker(self):
        """The app's one link to the system spell checker, made at first
        use (it reaches the system checker only when it checks)."""
        if getattr(self, "_checker", None) is None:
            self._checker = spelling.SpellChecker(parent=self)
            self._checker.availabilityChanged.connect(self._spelling_available)
        return self._checker

    def _spell_checked(self, ed) -> bool:
        """Whether a tab's text is checked: prose (a document with Markdown
        structure, Markdown or plain text, not code), editable, not the
        welcome page (the app's own words, names like Nostr among them),
        while Check Spelling While Typing is on."""
        if (ed is None or not self.check_spelling or ed.isReadOnly()
                or getattr(ed, "_is_welcome", False)):
            return False
        kind = self._editor_kind(ed)
        return kind == "rich" or (kind == "source"
                                  and getattr(ed, "_language", None) in (None, "markdown"))

    def _attach_spelling(self, ed) -> None:
        """Start or stop checking a tab's spelling, as it should be now."""
        if ed is None or not hasattr(ed, "set_spelling"):
            return
        current = ed.spelling()
        if self._spell_checked(ed):
            if current is None:
                ed.set_spelling(spelling.DocumentSpelling(
                    self._spell_checker(), ed.document(),
                    language=self._usable_spelling_language()))
        elif current is not None:
            current.close()
            ed.set_spelling(None)

    def _all_editors(self) -> list:
        found = []
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed is not None:
                found.append(ed)
        return found

    def _usable_spelling_language(self):
        """The language chosen in Edit > Spelling > Language while the
        system has its dictionary, else None (the system's language): a
        choice carried to a computer without it never stops the checking."""
        chosen = self.spelling_language or None
        if chosen is None:
            return None
        checker = self._spell_checker()
        return chosen if chosen in checker.languages() else None

    def _set_check_spelling(self, on: bool) -> None:
        """Edit > Spelling > Check Spelling While Typing, remembered."""
        self.check_spelling = bool(on)
        save_setting("check_spelling", self.check_spelling)
        for ed in self._all_editors():
            self._attach_spelling(ed)

    def _spelling_available(self, available: bool) -> None:
        self.act_check_spelling.setEnabled(available)

    def _update_spelling_menu(self) -> None:
        """Edit > Spelling as it opens: dimmed where the system has no
        checker; on Windows and Linux, the languages it can check."""
        checker = self._spell_checker()
        available = checker.is_available()
        self.act_check_spelling.setEnabled(available)
        menu = getattr(self, "m_spelling_language", None)
        if menu is None:
            return
        menu.clear()
        menu.setEnabled(available and self.check_spelling)
        group = QActionGroup(menu)
        chosen = self._usable_spelling_language() or ""
        for tag, name in [("", _("System Language"))] + spelling_language_names(
                checker.languages()):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(tag == chosen)
            action.triggered.connect(lambda _c=False, t=tag: self._set_spelling_language(t))
            group.addAction(action)
            if not tag:
                menu.addSeparator()

    def _set_spelling_language(self, tag: str) -> None:
        """Edit > Spelling > Language (Windows and Linux), remembered: every
        document is checked in it ("" for the system's language)."""
        self.spelling_language = tag
        save_setting("spelling_language", tag)
        for ed in self._all_editors():
            current = ed.spelling()
            if current is not None:
                current.set_language(tag or None)

    def _spelling_context(self, menu, editor, pos) -> None:
        """A misspelled word under the pointer: what it might be, and
        Ignore Spelling and Learn Spelling (Ignore All and Add to
        Dictionary on Windows and Linux, as Word and LibreOffice say)."""
        checks = editor.spelling() if hasattr(editor, "spelling") else None
        if checks is None or editor.textCursor().hasSelection():
            return
        position = editor.cursorForPosition(pos).position()
        word = checks.misspelling_at(position)
        mac = sys.platform == "darwin"
        if word is None:
            # A word learned (by mistake, perhaps): it can be unlearned.
            under = editor.cursorForPosition(pos)
            under.select(QTextCursor.SelectionType.WordUnderCursor)
            learned = under.selectedText().strip()
            checker = self._spell_checker()
            if learned and checker.has_learned(learned, checks.language()):
                menu.addAction(_("Unlearn Spelling") if mac else _("Remove from Dictionary"),
                               lambda: checker.unlearn(learned, checks.language()))
                menu.addSeparator()
            return
        block = editor.document().findBlock(position)
        checker = self._spell_checker()
        guesses = checker.suggestions(word.word, word.language)[:5]
        bold = menu.font()
        bold.setBold(True)
        for guess in guesses:
            action = menu.addAction(guess)
            action.setFont(bold)
            action.triggered.connect(
                lambda _c=False, g=guess, b=block.position(): editor.replace_misspelling(word, b, g))
        if not guesses:
            menu.addAction(_("No Guesses Found")).setEnabled(False)
        menu.addSeparator()
        menu.addAction(_("Ignore Spelling") if mac else _("Ignore All"),
                       lambda: checker.ignore(word.word, word.language))
        menu.addAction(_("Learn Spelling") if mac else _("Add to Dictionary"),
                       lambda: checker.learn(word.word, word.language))
        menu.addSeparator()

    def _update_editor_font(self, editor) -> None:
        """Writing in the system's text font, code and plain text in the
        monospace font (View > Use Monospace Font for Writing sets all
        writing in monospace too)."""
        writing = (not self.monospace_writing
                   and (self._editor_kind(editor) == "rich"
                        or getattr(editor, "_markdown_source", False)))
        editor.set_writing_font(writing)

    def _update_editor_theme(self, editor):
        pane = getattr(editor, "_preview_pane", None)
        if pane is not None:
            pane.set_dark(self.is_dark_theme)
        bg = DARK_BG if self.is_dark_theme else LIGHT_BG
        fg = DARK_FG if self.is_dark_theme else LIGHT_FG
        selection = DARK_SELECTION if self.is_dark_theme else LIGHT_SELECTION
        menu_bg = DARK_MENU_BG if self.is_dark_theme else LIGHT_MENU_BG
        border = DARK_BORDER if self.is_dark_theme else LIGHT_BORDER

        editor.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {fg};
                border: none;
                selection-background-color: {selection};
                selection-color: {fg};
                padding: 8px;
            }}
            QScrollBar:vertical {{
                background: {menu_bg};
                width: 12px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {border};
                min-height: 20px;
                border-radius: 6px;
                margin: 2px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {fg}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QScrollBar:horizontal {{
                background: {menu_bg};
                height: 12px;
                border: none;
            }}
            QScrollBar::handle:horizontal {{
                background: {border};
                min-width: 20px;
                border-radius: 6px;
                margin: 2px;
            }}
            QScrollBar::handle:horizontal:hover {{ background: {fg}; }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
        """)

        editor._theme_colors = {'bg': bg, 'fg': fg}

        if hasattr(editor, '_highlighter'):
            editor._highlighter.set_theme(self.is_dark_theme)

        if hasattr(editor, '_line_gutter'):
            editor._line_gutter._update_theme()

    def _apply_view_prefs_to_editor(self, editor):
        """Push the current View-menu preferences onto one editor. Called for
        every editor created (new_tab, open_path) so all tabs stay in sync."""
        editor.set_background_pattern(self.editor_background)
        editor.set_highlight_current_line(self.highlight_current_line)
        editor.set_paper_mode(self.paper_mode)


    def _new_wired_editor(self) -> HtmlEditor:
        """A fully wired editor: signals, theme, view prefs, media entry.

        Every tab is built through here. The three construction sites
        used to duplicate this by hand, and a tab wired without the
        resource resolver silently loses image rendering, which is
        exactly what the crash-restored tab (the one nobody opens by
        hand) used to do.
        """
        ed = HtmlEditor()
        ed.document().contentsChanged.connect(self._update_tab_title)
        # Saved or not, the window's title shows it (a Mac: the close button).
        ed.document().modificationChanged.connect(self._update_window_title)
        ed.document().undoAvailable.connect(self._update_undo_redo_buttons)
        ed.document().redoAvailable.connect(self._update_undo_redo_buttons)
        # What Undo and Redo are called changes with every step.
        ed.document().contentsChanged.connect(self._update_undo_redo_buttons)
        ed.undo_named.connect(self._update_undo_redo_buttons)
        ed.cursorPositionChanged.connect(self._update_status_bar)
        ed.document().contentsChanged.connect(
            lambda e=ed: self._on_document_changed_for_find(e))
        ed.document().contentsChanged.connect(
            lambda e=ed: self._on_document_changed_for_words(e))
        ed.selectionChanged.connect(lambda e=ed: self._on_selection_changed_for_words(e))
        ed.currentCharFormatChanged.connect(self._schedule_format_buttons)
        ed.selectionChanged.connect(self._schedule_format_buttons)
        ed.selectionChanged.connect(self._update_clipboard_commands)
        self._update_editor_theme(ed)
        self._apply_view_prefs_to_editor(ed)
        ed.set_resource_resolver(self._resolve_asset_image, ASSET_SCHEME)
        ed.set_object_lookup(self._outside_of)
        ed.set_local_image_resolver(lambda name, e=ed: self._resolve_local_image(e, name))
        ed.image_pasted.connect(lambda img, e=ed: self._handle_pasted_image(e, img))
        ed.notice.connect(lambda text: self.status.showMessage(text, 8000))
        ed.visible_area_changed.connect(lambda e=ed: self._on_visible_area_changed(e))
        ed.urls_dropped.connect(self._handle_dropped_urls)
        ed.set_context_menu_filler(self._fill_editor_context_menu)
        ed.set_structure_check(lambda e=ed: self._editor_kind(e) == "rich")
        ed.set_local_formats_check(lambda e=ed: self._keeps_underline(e))
        ed.set_paste_guard(lambda text, e=ed: self._guard_paste(e, text))
        ed.mention_requested.connect(lambda at, e=ed: self._ask_mention(e, at))
        # Mentions read back from Markdown get their names (rich_text.link_references).
        ed._mention_label = self._mention_label
        ed.set_markdown_shortcuts_check(lambda: self.markdown_shortcuts)
        ed.set_slash_items(self._slash_items)
        ed.set_link_opener(self._open_link)
        ed.set_nostr_check(lambda: self.nostr_state.active)
        return ed

    def _resolve_local_image(self, editor, name: str):
        """QImage for an image named relative to the editor's own file.

        A .md saved offline points at "<stem>_media/<sha>.png" beside
        itself, so this is the path that decides whether a reopened
        document shows its pictures. Reading it here rather than
        letting Qt fall back to the working directory also keeps the
        bytes inside the decode allowlist.
        """
        path = getattr(editor, "_file_path", "") or ""
        if not path:
            return None
        root = os.path.dirname(os.path.abspath(path))
        data = image_safety.ImageRootPolicy((root,)).read(name)
        if not data:
            return None
        return image_safety.decode_image_bytes(data)

    # ----------------------------------------------------------------------
    # EDITOR / TAB HELPERS
    # ----------------------------------------------------------------------
    def current_editor(self) -> HtmlEditor | None:
        return self._editor_from_widget(self.tabs.currentWidget())

    def current_path(self) -> str | None:
        ed = self.current_editor()
        return getattr(ed, "_file_path", None) if ed else None

    def set_current_path(self, path: str | None):
        ed = self.current_editor()
        if ed is not None:
            ed._file_path = path
            self._update_tab_title()

    def _update_tab_title(self):
        ed = self.current_editor()
        if not ed:
            return
        index = self.tabs.currentIndex()
        self.tabs.setTabText(index, self._compose_tab_title(ed))
        # Where the file is, for whoever wonders: on the tab, where it does
        # not take room from the text.
        self.tabs.setTabToolTip(index, getattr(ed, "_file_path", None) or "")
        # A screen reader announces the text area by its document's name.
        ed.setAccessibleName(self._document_name(ed))
        self._update_window_title()
        self._update_status_bar()

    def _compose_tab_title(self, ed) -> str:
        """Compute the display title for a tab.

        Precedence:
          - "Welcome" for the welcome tab as MyEditor opened it (never
            saved and not edited; see welcome.is_pristine_welcome).
          - File path basename if the tab is backed by a local file.
          - Draft title if the tab is purely a draft (opened from the
            drafts panel without a local file).
          - "Untitled" as the final fallback for fresh blank tabs.

        Tabs with a draft binding get a leading lock glyph so the user
        can tell at a glance that contents are encrypted at rest on
        the relays.
        """
        if is_pristine_welcome(ed):
            return _("Welcome")
        binding = getattr(ed, "_draft_binding", None)
        dirty = "*" if ed.document().isModified() else ""
        prefix = "⚿ " if binding is not None else ""
        return f"{prefix}{self._document_name(ed)}{dirty}"

    @staticmethod
    def _document_name(ed) -> str:
        """The document's own name: the file's, the draft's title, or
        Untitled."""
        if is_pristine_welcome(ed):
            return _("Welcome")
        path = getattr(ed, "_file_path", None)
        binding = getattr(ed, "_draft_binding", None)
        recovered = getattr(ed, "_recovered_title", "")
        if recovered:
            return recovered
        if path:
            return os.path.basename(path)
        if binding and binding.title:
            return binding.title
        published = getattr(ed, "_published_article", None)
        if published is not None and published.title:
            return published.title
        if binding:
            return _("Untitled draft")
        return _("Untitled")

    def _update_status_bar(self):
        """The status bar's lasting part: the words (beside, see
        _show_word_count) and, in code and plain text, where lines are
        lines, the language and the line the caret is on. Its message
        area is left to what just happened (saved, published): the file's
        place is the tab's tooltip, and a PDF's page is in its own
        toolbar, so neither is repeated here."""
        self._update_editor_commands()
        ed = self.current_editor()
        if self.current_pdf_viewer() is not None or not ed:
            self._line_label.setText("")
            if not ed:
                self._words_label.setText("")
            self._update_format_buttons()
            return
        parts = []
        if self._editor_kind(ed) == "source":
            lang = getattr(ed, "_language", None)
            if lang and LANGUAGE_DISPLAY_NAMES.get(lang):
                parts.append(LANGUAGE_DISPLAY_NAMES[lang])
            parts.append(_("Ln {line} / {total}").format(
                line=ed.textCursor().blockNumber() + 1, total=ed.document().blockCount()))
        self._line_label.setText(" \u00b7 ".join(parts))
        self._schedule_format_buttons()

    def _update_window_title(self, *_args):
        """The document in front names the window, as in every document
        app: the title bar, the Window and Dock menus on a Mac, the
        taskbar and Alt+Tab elsewhere, and what a screen reader says
        when the window comes to the front. Windows and Linux add the
        app's name after it; a Mac shows unsaved changes in the close
        button ([*] is Qt's placeholder for them)."""
        ed = self.current_editor()
        viewer = self.current_pdf_viewer()
        if ed is not None:
            name, modified = self._document_name(ed), ed.document().isModified()
        elif viewer is not None:
            name, modified = os.path.basename(viewer._file_path), False
        else:
            self.setWindowModified(False)
            self.setWindowTitle(APP_DISPLAY_NAME)
            return
        if sys.platform == "darwin":
            self.setWindowTitle(f"{name}[*]")
        else:
            self.setWindowTitle(_("{document}[*] - {app}").format(document=name,
                                                                   app=APP_DISPLAY_NAME))
        self.setWindowModified(modified)

    def _on_tab_changed(self, index=None):
        self._show_backup_status()
        self._fill_article_details_panel()
        self._apply_view(self.current_editor())
        self._update_window_title()
        self._update_word_count()
        self._update_undo_redo_buttons()
        self._update_status_bar()  # also calls _update_format_buttons
        self._update_knit_actions()
        self._update_print_actions()
        # The global find bar searches editors only; a PDF tab carries
        # its own find bar, so retire the editor one when switching over.
        if self.findbar.isVisible() and self.current_pdf_viewer() is not None:
            self.findbar.setVisible(False)
            self.findbar.set_match_info("")
            self._clear_search_highlights()
        if self.findbar.isVisible():
            # Clear stale highlights on every non-active tab
            for i in range(self.tabs.count()):
                if i != index:
                    ed = self._editor_from_widget(self.tabs.widget(i))
                    if ed:
                        ed.set_highlights("find", [])
            self._search_matches = []
            self._current_match_index = -1
            self._last_search_text = ""
            self._on_search_text_changed()

    # -- words --------------------------------------------------------------

    def _on_document_changed_for_words(self, ed) -> None:
        if ed is self.current_editor():
            self._word_count_timer.start()

    def _on_selection_changed_for_words(self, ed) -> None:
        if ed is self.current_editor():
            # Counted once the selection settles, not at every step of it.
            self._selection_words_timer.start()

    def _update_word_count(self) -> None:
        """Count the current document's words again (after it changed, or
        another tab came to the front)."""
        ed = self.current_editor()
        self._document_words = count_words(ed.toPlainText()) if ed is not None else 0
        self._show_word_count(ed)

    def _show_word_count(self, ed) -> None:
        """"1,234 words · 6 min read", or "12 of 1,234 words" while words
        are selected; nothing for an empty document or a PDF."""
        total = self._document_words
        if ed is None or total == 0:
            self._words_label.setText("")
            return
        selected = ed.textCursor().selectedText() if ed.textCursor().hasSelection() else ""
        if selected:
            self._words_label.setText(ngettext(
                "{selected} of {count} word", "{selected} of {count} words", total).format(
                selected=_number(count_words(selected)), count=_number(total)))
            return
        minutes = reading_minutes(total)
        self._words_label.setText(ngettext(
            "{count} word", "{count} words", total).format(count=_number(total))
            + " · " + ngettext("{minutes} min read", "{minutes} min read", minutes).format(
                minutes=_number(minutes)))

    def _update_undo_redo_buttons(self, *_args):
        """Undo and Redo for what has the focus: the Imports window when it
        is in front (Undo Skip there), a text field (the find field, a
        dialog's field), or the document, unless only its preview shows
        (nothing edits the hidden text)."""
        if not hasattr(self, "act_undo"):
            return
        imports = self._imports_in_front()
        field = _text_field(QApplication.focusWidget())
        if imports is not None:
            # What Undo undoes in the window in front (Undo Skip there).
            self.act_undo.setText(imports.undo_text())
            self.act_redo.setText(redo_words())
            self.act_undo.setEnabled(imports.can_undo())
            self.act_redo.setEnabled(field is not None and _field_history(field)[1])
            return
        undo_name = redo_name = ""
        if field is not None:
            # Never the document behind (T1 of the CP3 fixes review: Undo
            # with a dialog's About field focused undid the document).
            can_undo, can_redo = _field_history(field)
        else:
            ed = self.current_editor()
            editing = ed is not None and not self._shows_preview_only(ed)
            can_undo = editing and ed.document().isUndoAvailable()
            can_redo = editing and ed.document().isRedoAvailable()
            if editing:
                # What it takes back, named where the platform names it
                # ("Undo Typing", "Undo Paste").
                undo_name, redo_name = ed.undo_name(), ed.redo_name()
        self.act_undo.setText(undo_words(undo_name if can_undo else ""))
        self.act_redo.setText(redo_words(redo_name if can_redo else ""))
        self.act_undo.setEnabled(can_undo)
        self.act_redo.setEnabled(can_redo)

    def _update_clipboard_commands(self, *_args) -> None:
        """Cut, Copy, Paste and Delete for whatever has the focus, dimmed
        where they cannot act (as the HIG asks): a text field (the find
        field, a sheet's or a dialog's), the Imports window, the PDF reader
        (Copy) or the document. Cut, Copy and Delete need a selection,
        Paste a place that takes text and something on the clipboard (T2
        of the CP3 fixes review: it followed the kind of tab, not the
        focus). Paste and Match Style needs words on the clipboard."""
        if not hasattr(self, "act_cut"):
            return
        field = _text_field(QApplication.focusWidget())
        imports = self._imports_in_front()
        clipboard = QApplication.clipboard().mimeData()
        has_words = clipboard is not None and (clipboard.hasText() or clipboard.hasHtml())
        if field is not None:
            selected = (field.hasSelectedText() if isinstance(field, QLineEdit)
                        else field.textCursor().hasSelection())
            editable = not field.isReadOnly()
            pastes = editable and (clipboard is not None and clipboard.hasText()
                                   if isinstance(field, QLineEdit) else field.canPaste())
            plain = pastes
        elif imports is not None:
            # Never the document behind (N2 of the CP3 fixes review): Copy
            # copies the posts' links and Delete skips; nothing takes a
            # paste, and nothing acts while a sheet is in front.
            self.act_copy.setEnabled(imports.can_edit("copy"))
            self.act_cut.setEnabled(imports.can_edit("cut"))
            self.act_delete.setEnabled(imports.can_edit("delete"))
            self.act_paste.setEnabled(False)
            self.act_paste_plain.setEnabled(False)
            return
        elif self.current_pdf_viewer() is not None:
            selected, editable, pastes, plain = True, False, False, False   # Copy only
        else:
            ed = self.current_editor()
            selected = ed is not None and ed.textCursor().hasSelection()
            editable = ed is not None and not ed.isReadOnly()
            # What the editor takes, a picture included; Paste and Match
            # Style pastes words only.
            pastes = editable and ed.canPaste()
            plain = editable and has_words
        self.act_copy.setEnabled(selected)
        self.act_cut.setEnabled(selected and editable)
        self.act_delete.setEnabled(selected and editable)
        self.act_paste.setEnabled(pastes)
        self.act_paste_plain.setEnabled(plain)

    def _on_focus_changed(self, _old, new) -> None:
        tabs = getattr(self, "tabs", None)
        if tabs is None or not shiboken6.isValid(tabs):
            return                                   # the window is closing
        self._note_side_in_use(new)
        self._follow_field(new)
        self._on_field_changed()

    def _follow_field(self, widget) -> None:
        """Cut, Copy, Delete, Undo and Redo follow the text field with the
        focus as it changes, as they follow the document: a find field, a
        sheet's field, the Imports window's article (S4 of the CP3 fixes
        review). The one place for every field; the document has its own
        connections."""
        old = getattr(self, "_followed_field", None)
        if old is not None and shiboken6.isValid(old):
            for signal in _field_signals(old):
                signal.disconnect(self._on_field_changed)
        self._followed_field = None
        if _text_field(widget) is not None:
            for signal in _field_signals(widget):
                signal.connect(self._on_field_changed)
            self._followed_field = widget

    def _on_field_changed(self, *_args) -> None:
        self._update_clipboard_commands()
        self._update_undo_redo_buttons()

    def _toggle_format(self, fmt: str):
        ed = self.current_editor()
        if ed:
            getattr(ed, f'toggle_{fmt}')()
            self._update_format_buttons()

    def _schedule_format_buttons(self):
        """Show the style at the caret once the event loop is free: moving
        the caret or extending a selection reports several changes at
        once, and they need one update, not three."""
        timer = getattr(self, "_format_buttons_timer", None)
        if timer is None:
            timer = self._format_buttons_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(0)
            timer.timeout.connect(self._update_format_buttons)
        timer.start()

    def _update_format_buttons(self):
        """The format commands show the style at the caret (or of the whole
        selection): in the Format menu and on the format toolbar alike."""
        if not hasattr(self, "act_bold"):
            return
        ed = self.current_editor()
        styles = {self.act_bold: rich_text.BOLD, self.act_italic: rich_text.ITALIC,
                  self.act_underline: rich_text.UNDERLINE, self.act_strike: rich_text.STRIKE,
                  self.act_code: rich_text.CODE}
        cursor = ed.textCursor() if ed else None
        if cursor is None or cursor.isNull():
            state = {}
        elif cursor.hasSelection():
            # One bounded pass over the selection for all five.
            state = rich_text.selection_state(cursor, tuple(styles.values()))
        else:
            fmt = ed.currentCharFormat()
            state = {style: rich_text.has_style(fmt, style) for style in styles.values()}
        for action, style in styles.items():
            action.setChecked(state.get(style, False))
        self._update_style_checks(ed)
        if self._caret_context() != getattr(self, "_caret_context_shown", (False, False)):
            # The caret went into a code block or a table, or out of one.
            self._update_editor_commands()

    def _update_style_checks(self, ed) -> None:
        """The Style menu checks the style of the paragraph at the caret
        (none for a selection of mixed styles, or Heading 4 and lower,
        which come from Markdown files)."""
        if not hasattr(self, "act_styles"):
            return
        cursor = ed.textCursor() if ed else None
        paragraphs = (rich_text.paragraph_state(cursor) if cursor is not None
                      else rich_text.ParagraphState())
        level = paragraphs.heading if paragraphs.code is None else -1
        for index, action in enumerate(self.act_styles):
            action.setChecked(index == level)
        for action in self.act_code_languages:
            action.setChecked(paragraphs.code is not None and action.data() == (
                canonical_language(paragraphs.code) or paragraphs.code))
        if getattr(self, "format_toolbar", None) is not None:
            if paragraphs.code is not None:
                name = self._code_style_name(paragraphs.code)
            elif 0 <= level < len(self.act_styles):
                name = self.act_styles[level].text()
            elif level > 0:
                name = _("Heading {level}").format(level=level)
            else:
                name = pgettext("paragraph style", "Mixed")
            self.format_toolbar.set_style_name(name)
        # A checklist is shown as one, not as a bulleted list as well.
        self.act_bullets.setChecked(paragraphs.list_kind == rich_text.BULLET
                                    and not paragraphs.tasks)
        self.act_numbers.setChecked(paragraphs.list_kind == rich_text.NUMBER)
        self.act_checklist.setChecked(paragraphs.tasks)
        rich = cursor is not None and self._editor_kind(ed) == "rich"
        tasks = rich_text.task_blocks(cursor) if rich else []
        self.act_tick.setEnabled(bool(tasks) and not self._shows_preview_only(ed))
        self.act_tick.setText(_("Mark as Unchecked") if tasks and all(
            b.blockFormat().marker() == rich_text.TICKED for b in tasks)
            else _("Mark as Checked"))
        target = rich_text.footnote_jump(cursor) if rich else None
        self.act_footnote_jump.setEnabled(target is not None)
        self.act_footnote_jump.setText(
            _("Go to Mark") if target is not None and target < cursor.position()
            else _("Go to Note"))
        self.act_quote.setChecked(paragraphs.quoted)
        in_link = (cursor is not None and self._editor_kind(ed) == "rich"
                   and rich_text.link_range(cursor) is not None)
        self.act_link.setText(_("Edit Link\u2026") if in_link else _("Add Link\u2026"))

    # ----------------------------------------------------------------------
    # ACTIONS / MENU
    # ----------------------------------------------------------------------
    def _build_actions(self):
        """Every command of the window, through the one command list
        (commands.py), which also feeds the Keyboard Shortcuts window."""
        self.commands = CommandRegistry(self)
        add = self.commands.add

        self.act_new = add(Command("file.new", _("New"), FILE, QKeySequence.StandardKey.New,
                                   listed_as=_("New tab"), keywords=("tab", "document")),
                           triggered=self.new_tab)
        self.act_open = add(Command("file.open", _("Open…"), FILE,
                                    QKeySequence.StandardKey.Open, listed_as=_("Open file")),
                            triggered=self.open_dialog)
        self.act_save = add(Command("file.save", _("Save"), FILE, QKeySequence.StandardKey.Save),
                            triggered=self.save)
        # Contextual: behaves as classic Save As when no Nostr profile
        # is connected; otherwise asks where to save (local file vs.
        # encrypted Nostr draft) and remembers the per-tab choice. See
        # ``_on_save_as_pressed`` for the full decision tree.
        self.act_save_as = add(Command("file.save_as", _("Save As…"), FILE, "Ctrl+Shift+S"),
                               triggered=self._on_save_as_pressed)
        self.act_page_setup = add(Command("file.page_setup", _("Page Setup…"), FILE),
                                  triggered=self._on_page_setup)
        # Print prints the current tab as formatted pages (see printing.py);
        # both actions dim when the current tab has nothing to print.
        self.act_print = add(Command("file.print", _("Print\u2026"), FILE,
                                     QKeySequence.StandardKey.Print),
                             triggered=self._on_print)
        self.act_print_preview = add(Command("file.print_preview", _("Print Preview\u2026"),
                                             FILE),
                                     triggered=self._on_print_preview)
        # Knitting renders the on-disk .Rmd through the R toolchain, so the
        # actions only light up for .Rmd tabs (see _update_knit_actions).
        self.act_knit_html = add(Command("file.knit_html", _("Knit to HTML"), FILE,
                                         "Ctrl+Shift+K", listed_as=_("Knit R Markdown to HTML")),
                                 triggered=lambda: self._on_knit("html"), enabled=False)
        self.act_knit_pdf = add(Command("file.knit_pdf", _("Knit to PDF"), FILE),
                                triggered=lambda: self._on_knit("pdf"), enabled=False)
        self.act_rmd_toolchain = add(Command("file.rmd_toolchain", _("R Markdown Toolchain…"),
                                             FILE),
                                     triggered=self._on_rmd_toolchain)
        # Ctrl+W (Command-W) first, as Notepad, Word and every Mac app show
        # it; the system's other Close keys (Ctrl+F4 on Windows) answer too.
        close_keys = ("Ctrl+W",) + tuple(
            keys for keys in all_key_texts(QKeySequence.StandardKey.Close) if keys != "Ctrl+W")
        self.act_close_tab = add(Command("file.close_tab", _("Close Tab"), FILE, close_keys,
                                         listed_as=_("Close tab")),
                                 triggered=self._close_current_tab)
        self.act_quit = add(Command("file.quit", _("Quit"), FILE, "Ctrl+Q",
                                    role=QAction.MenuRole.QuitRole),
                            triggered=self._quit_application)

        # Edit, as in every Mac app. Cut, Copy, Paste and Select All act on
        # whatever has the keyboard focus: the document, the find field,
        # the PDF reader (see _edit_focused).
        self.act_undo = add(Command("edit.undo", _("Undo"), EDIT, QKeySequence.StandardKey.Undo),
                            triggered=self._undo)
        self.act_redo = add(Command("edit.redo", _("Redo"), EDIT, QKeySequence.StandardKey.Redo),
                            triggered=self._redo)
        self.act_cut = add(Command("edit.cut", _("Cut"), EDIT, QKeySequence.StandardKey.Cut),
                           triggered=lambda: self._edit_focused("cut"))
        self.act_copy = add(Command("edit.copy", _("Copy"), EDIT, QKeySequence.StandardKey.Copy),
                            triggered=lambda: self._edit_focused("copy"))
        self.act_paste = add(Command("edit.paste", _("Paste"), EDIT,
                                     QKeySequence.StandardKey.Paste),
                             triggered=lambda: self._edit_focused("paste"))
        # Paste as plain text, in the style of the text around it.
        self.act_paste_plain = add(
            Command("edit.paste_plain", _("Paste and Match Style"), EDIT,
                    platform_keys("Ctrl+Alt+Shift+V", "Ctrl+Shift+V"),
                    keywords=(_("plain text"),)),
            triggered=self._paste_plain)
        self.act_delete = add(Command("edit.delete", _("Delete"), EDIT),
                              triggered=lambda: self._edit_focused("delete"))
        self.act_select_all = add(Command("edit.select_all", _("Select All"), EDIT,
                                          QKeySequence.StandardKey.SelectAll),
                                  triggered=lambda: self._edit_focused("select_all"))
        # Settings: in the application menu on a Mac (Command-comma), at the
        # end of the Edit menu elsewhere (Ctrl+comma, as GNOME and most apps).
        self.act_settings = add(Command("app.settings", _("Settings\u2026"), EDIT, "Ctrl+,",
                                        listed_as=_("Settings"),
                                        keywords=("preferences", "options"),
                                        role=QAction.MenuRole.PreferencesRole),
                                triggered=self._show_settings)

        # Formatting
        self.act_bold = add(Command("format.bold", _("Bold"), FORMAT, "Ctrl+B",
                                    checkable=True),
                            triggered=self._fmt_bold)
        self.act_italic = add(Command("format.italic", _("Italic"), FORMAT, "Ctrl+I",
                                    checkable=True),
                              triggered=self._fmt_italic)
        self.act_underline = add(Command("format.underline", _("Underline"), FORMAT, "Ctrl+U",
                                    checkable=True),
                                 triggered=self._fmt_underline)
        # Underline has no Markdown, so it is offered only where the file
        # keeps it: HTML documents (owner decision Q-L). Elsewhere it is
        # dimmed, and it has no toolbar button.
        self.act_underline.setToolTip(_("Underlining is kept in HTML documents only."))
        # Shift-Command-X on a Mac, Alt+Shift+5 elsewhere, as in Google Docs:
        # Notes, Pages and TextEdit have no key for it, nor Word or
        # LibreOffice (the one exception to Apple's keys on a Mac, kept
        # because writers know it from Google Docs).
        self.act_strike = add(Command("format.strike", _("Strikethrough"), FORMAT,
                                      platform_keys("Ctrl+Shift+X", "Alt+Shift+5"),
                                      checkable=True, keywords=(_("cross out"),)),
                              triggered=lambda: self._toggle_style(rich_text.STRIKE))
        self.act_code = add(Command("format.code", _("Inline Code"), FORMAT,
                                    checkable=True, keywords=(_("monospace"),)),
                            triggered=lambda: self._toggle_style(rich_text.CODE))
        # Command-\ and Ctrl+\, Google Docs' keys for it (Apple's apps have
        # none).
        self.act_reset_format = add(Command("format.reset", _("Clear Formatting"), FORMAT,
                                            "Ctrl+\\", keywords=(_("plain"),)),
                                    triggered=self._reset_format)
        # Paragraph styles. Apple Notes' keys on a Mac: Body Shift-Command-B,
        # Title, Heading and Subheading Shift-Command-T, H and J for
        # Heading 1 to 3. Ctrl+0 to 3 elsewhere, as in LibreOffice and
        # Google Docs' numbers, because Ctrl+Alt is AltGr there and types
        # characters (² and ³ on a German keyboard).
        mac_style_keys = {0: "Ctrl+Shift+B", 1: "Ctrl+Shift+T", 2: "Ctrl+Shift+H",
                          3: "Ctrl+Shift+J"}
        self._style_group = QActionGroup(self)
        self._style_group.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)
        self.act_styles = []
        for level, title, words in (
                (0, _("Body"), (_("paragraph"), _("text"))),
                (1, _("Heading 1"), (_("title"), "h1")),
                (2, _("Heading 2"), (_("subtitle"), "h2")),
                (3, _("Heading 3"), ("h3",))):
            action = add(Command(f"format.style.{'body' if not level else f'h{level}'}", title,
                                 FORMAT, platform_keys(mac_style_keys[level], f"Ctrl+{level}"),
                                 checkable=True, keywords=words,
                                 slash_section=slash_menu.TEXT),
                         triggered=lambda n=level: self._set_heading(n))
            self._style_group.addAction(action)
            self.act_styles.append(action)
        # Lists: Apple Notes' keys on a Mac (Shift-Command-7 and 9), Google
        # Docs' elsewhere (Ctrl+Shift+8 and 7).
        self.act_bullets = add(Command("format.list.bullet", _("Bulleted List"), FORMAT,
                                       platform_keys("Ctrl+Shift+7", "Ctrl+Shift+8"),
                                       checkable=True, keywords=(_("bullets"), _("list")),
                                       slash_section=slash_menu.LISTS),
                               triggered=lambda: self._toggle_list(rich_text.BULLET))
        self.act_numbers = add(Command("format.list.number", _("Numbered List"), FORMAT,
                                       platform_keys("Ctrl+Shift+9", "Ctrl+Shift+7"),
                                       checkable=True,
                                       keywords=(_("numbers"), _("list"), _("ordered")),
                                       slash_section=slash_menu.LISTS),
                               triggered=lambda: self._toggle_list(rich_text.NUMBER))
        # Checklist: Apple Notes' key on a Mac (Shift-Command-L), Google
        # Docs' elsewhere (Ctrl+Shift+9).
        self.act_checklist = add(Command("format.list.task", _("Checklist"), FORMAT,
                                         platform_keys("Ctrl+Shift+L", "Ctrl+Shift+9"),
                                         checkable=True,
                                         keywords=(_("todo"), _("checkbox"), _("task")),
                                         slash_section=slash_menu.LISTS),
                                 triggered=lambda: self._editor_call("toggle_checklist"))
        # Mark as Checked: Notes' key on a Mac; none elsewhere (Ctrl+Shift+U
        # is the Unicode input of Linux desktops).
        self.act_tick = add(Command("format.list.tick", _("Mark as Checked"), FORMAT,
                                    platform_keys("Ctrl+Shift+U", None),
                                    keywords=(_("tick"), _("done"), _("task"))),
                            triggered=self._toggle_ticks)
        # Go to Note and back (footnotes): the keyboard's way to Command-click.
        self.act_footnote_jump = add(Command("edit.footnote_jump", _("Go to Note"), EDIT,
                                             keywords=(_("footnote"),)),
                                     triggered=self._footnote_jump)
        self.act_indent = add(Command("format.indent", _("Increase Indent"), FORMAT, "Ctrl+]",
                                      keywords=(_("nest"),)),
                              triggered=lambda: self._change_indent(+1))
        self.act_outdent = add(Command("format.outdent", _("Decrease Indent"), FORMAT,
                                       "Ctrl+["),
                               triggered=lambda: self._change_indent(-1))
        # Apple Notes' Block Quote key on a Mac; Word, Google Docs and
        # LibreOffice have none.
        self.act_quote = add(Command("format.quote", _("Quote"), FORMAT,
                                     platform_keys("Ctrl+'", None),
                                     checkable=True, keywords=(_("citation"), _("blockquote")),
                                     slash_section=slash_menu.BLOCKS),
                             triggered=lambda: self._editor_call("toggle_quote"))

        # Insert. Command-K, as in Pages, Mail and Notes; the title says
        # Edit Link… while the caret is in a link.
        self.act_link = add(Command("insert.link", _("Add Link\u2026"), INSERT, "Ctrl+K",
                                    keywords=(_("web address"), "url", _("hyperlink")),
                                    slash_section=slash_menu.INSERT),
                            triggered=lambda: self._editor_call("show_link_popover"))
        self.act_divider = add(Command("insert.divider", _("Divider"), INSERT,
                                       keywords=(_("horizontal rule"), _("line"),
                                                 _("separator")),
                                       slash_section=slash_menu.BLOCKS),
                               triggered=lambda: self._editor_call("insert_divider"))
        # Code Block: no key (Option-Command-C is Copy Style on a Mac). The
        # language is asked right after, in a short list at the caret.
        self.act_code_block = add(Command("insert.code_block", _("Code Block"), INSERT,
                                          keywords=(_("code"), _("snippet"), _("program"),
                                                    "fence"),
                                          slash_section=slash_menu.BLOCKS),
                                  triggered=self._insert_code_block)
        # The language of the code block at the caret: Format > Code
        # Language, one choice of a group.
        self._code_language_group = QActionGroup(self)
        self.act_code_languages = []
        for language, name in [("", _("Plain Text"))] + sorted(
                ((key, title) for key, title in LANGUAGE_DISPLAY_NAMES.items()
                 if key != "markdown"), key=lambda pair: pair[1].lower()):
            action = add(Command(f"format.code_language.{language or 'plain'}", name, FORMAT,
                                 checkable=True, keywords=(_("code"), _("language"))),
                         triggered=lambda lang=language: self._set_code_language(lang))
            action.setData(language)
            self._code_language_group.addAction(action)
            self.act_code_languages.append(action)
        # Footnote: its mark at the caret, its note at the end of the
        # document, where the caret goes (literal Markdown, rich_text.py).
        self.act_footnote = add(Command("insert.footnote", _("Footnote"), INSERT,
                                        keywords=(_("note"), _("source"), _("reference"),
                                                  _("citation")),
                                        slash_section=slash_menu.INSERT),
                                triggered=lambda: self._editor_call("insert_footnote"))
        # Table...: its size is chosen on a grid at the caret (table_picker).
        self.act_table = add(Command("insert.table", _("Table\u2026"), INSERT,
                                     keywords=(_("grid"), _("rows"), _("columns")),
                                     slash_section=slash_menu.BLOCKS),
                             triggered=self._insert_table)
        # A table's rows and columns: Format > Table, and the context menu in
        # a table. No keys: Pages' Option-arrow keys for rows and columns
        # move by word and paragraph in text, and no convention has others.
        self.act_table_commands = [
            add(Command(f"format.table.{name}", title, FORMAT,
                        keywords=(_("table"), _("row") if "row" in name else _("column"))),
                triggered=lambda n=name: self._editor_table_command(n))
            for name, title in (
                ("add_row_above", _("Add Row Above")), ("add_row_below", _("Add Row Below")),
                ("add_column_before", _("Add Column Before")),
                ("add_column_after", _("Add Column After")),
                ("delete_row", _("Delete Row")), ("delete_column", _("Delete Column")),
                ("delete_table", _("Delete Table")))]
        # Text colors stay in the document and in local files; Markdown
        # has none, so they never reach Nostr.
        self.act_colors = []
        for name, color in TEXT_COLORS.items():
            self.act_colors.append(add(
                Command(f"format.color.{name.lower()}", _(name), FORMAT,
                        keywords=(_("color"),)),
                triggered=lambda c=color: self._apply_color(c)))
        self.act_remove_color = add(Command("format.color.none", _("Remove Color"), FORMAT),
                                    triggered=lambda: self._apply_color(None))

        # Find, in the Edit menu. Find Next and Find Previous use each
        # platform's own keys (Command-G on a Mac, F3 on Windows).
        self.act_find = add(Command("search.find", _("Find\u2026"), SEARCH, "Ctrl+F",
                                    listed_as=_("Find")),
                            triggered=lambda: self._show_find(replace=False))
        # Option-Command-F on a Mac (TextEdit, Pages); Ctrl+H elsewhere
        # (Word, Google Docs, LibreOffice). Never QKeySequence.Replace,
        # which is Command-H, Hide, on a Mac.
        self.act_replace = add(Command("search.replace", _("Find and Replace\u2026"), SEARCH,
                                       platform_keys("Ctrl+Alt+F", "Ctrl+H"),
                                       listed_as=_("Find and replace")),
                               triggered=lambda: self._show_find(replace=True))
        self.act_find_next = add(Command("search.next", _("Find Next"), SEARCH,
                                         QKeySequence.StandardKey.FindNext,
                                         listed_as=_("Find next")),
                                 triggered=self._find_next)
        self.act_find_prev = add(Command("search.previous", _("Find Previous"), SEARCH,
                                         QKeySequence.StandardKey.FindPrevious,
                                         listed_as=_("Find previous")),
                                 triggered=self._find_prev)
        # Command-E on a Mac: the selection becomes what Find Next looks for.
        self.act_use_selection = add(
            Command("search.use_selection", _("Use Selection for Find"), SEARCH,
                    platform_keys("Ctrl+E", None)),
            triggered=self._use_selection_for_find)

        # Disabled where there is no editor (a PDF tab), so the shortcuts
        # the PDF reader has for itself stay its own.
        self._editor_actions = [
            self.act_undo, self.act_redo, self.act_cut, self.act_paste, self.act_paste_plain,
            self.act_delete, self.act_select_all, self.act_use_selection, self.act_replace,
            self.act_indent, self.act_outdent,
            self.act_bold, self.act_italic, self.act_underline, self.act_reset_format,
            *self.act_colors, self.act_remove_color,
        ]
        # Structure that only a document holding Markdown can carry
        # (headings, lists, links): off in plain-text tabs as well.
        self._rich_actions = [self.act_strike, self.act_code, *self.act_styles,
                              self.act_bullets, self.act_numbers, self.act_checklist,
                              self.act_quote, self.act_divider, self.act_link,
                              self.act_code_block, self.act_table, self.act_footnote]
        # Paragraph structure, which a table cell (one line of words in
        # Markdown) cannot hold: off in a table.
        self._block_actions = [*self.act_styles, self.act_bullets, self.act_numbers,
                               self.act_checklist, self.act_quote, self.act_divider,
                               self.act_code_block, self.act_table, self.act_indent,
                               self.act_outdent]
        # Styles of words, which code does not have: off in a code block.
        self._word_actions = [self.act_bold, self.act_italic, self.act_underline,
                              self.act_strike, self.act_code, self.act_link,
                              *self.act_colors, self.act_remove_color, self.act_reset_format,
                              self.act_footnote]

        self._search_matches = []
        self._current_match_index = -1
        self._last_search_text = ""

        # Appearance, line numbers and syntax highlighting: menu items
        # without keys, since no platform convention gives them one (their
        # old keys were this app's own; Shift-Command-L is Notes' Checklist).
        # Appearance is the system's (followed as it changes) unless Light
        # or Dark is chosen; Automatic goes back to the system's.
        self._appearance_group = QActionGroup(self)
        self._appearance_group.setExclusive(True)
        self.act_appearance = {}
        for mode, title in (("system", pgettext("appearance", "Automatic")),
                            ("light", pgettext("appearance", "Light")),
                            ("dark", pgettext("appearance", "Dark"))):
            action = add(Command(f"view.appearance.{mode}", title, VIEW, checkable=True,
                                 keywords=(_("appearance"), _("theme"), "dark", "light")),
                         triggered=lambda m=mode: self.set_appearance(m),
                         checked=self.appearance == mode)
            self._appearance_group.addAction(action)
            self.act_appearance[mode] = action
        self.act_toggle_line_numbers = add(
            Command("view.line_numbers", _("Show Line Numbers"), VIEW,
                    checkable=True, listed_as=_("Toggle line numbers")),
            triggered=self._toggle_line_numbers)
        self.act_toggle_syntax_hl = add(
            Command("view.syntax_highlighting", _("Syntax Highlighting"), VIEW,
                    checkable=True, listed_as=_("Toggle syntax highlighting")),
            triggered=self._toggle_syntax_highlighting, checked=False)

        # View menu: background viewing aids. All four toggles are independent
        # and composable except the background pattern, which is a radio pick.
        self.act_paper_mode = add(Command("view.paper_mode", _("Paper Mode"), VIEW,
                                          checkable=True),
                                  toggled=self._toggle_paper_mode, checked=self.paper_mode)

        self._bg_pattern_group = QActionGroup(self)
        self._bg_pattern_group.setExclusive(True)
        backgrounds = {}
        # The pattern's name in the settings, and in the menu.
        self._background_titles = {
            "none": pgettext("background", "None"),
            "lines": pgettext("background", "Lines"),
            "dashed": pgettext("background", "Dashed"),
            "dots": pgettext("background", "Dots"),
            "grid": pgettext("background", "Grid"),
        }
        for name, title in self._background_titles.items():
            action = add(Command(f"view.background.{name}", title, VIEW, checkable=True,
                                 keywords=("background", "pattern")),
                         triggered=lambda n=name: self._set_background_pattern(n),
                         checked=self.editor_background == name)
            self._bg_pattern_group.addAction(action)
            backgrounds[name] = action
        self.act_bg_none = backgrounds["none"]
        self.act_bg_lines = backgrounds["lines"]
        self.act_bg_dashed = backgrounds["dashed"]
        self.act_bg_dots = backgrounds["dots"]
        self.act_bg_grid = backgrounds["grid"]

        # The language MyEditor speaks. Applied at the next start: many
        # texts are read once, when their module loads (see i18n.py).
        self._language_group = QActionGroup(self)
        self._language_group.setExclusive(True)
        chosen = i18n.chosen_language()
        self.act_languages = []
        self._language_codes = [i18n.SYSTEM] + i18n.available()
        for code in self._language_codes:
            title = (_("System Language") if code == i18n.SYSTEM
                     else i18n.LANGUAGE_NAMES.get(code, code))
            action = add(Command(f"view.language.{code}", title, VIEW, checkable=True,
                                 keywords=("language", "sprache")),
                         triggered=lambda c=code: self._choose_language(c),
                         checked=chosen == code)
            self._language_group.addAction(action)
            self.act_languages.append(action)

        # Option-Command-T on a Mac, as every Mac app's Show Toolbar; no key
        # elsewhere, where none is common (Ctrl+Alt+T opens a terminal).
        self.show_toolbar = bool(load_settings().get("show_toolbar", True))
        # Typed Markdown turned into what it means ("# " a heading, "**x**"
        # bold), in Edit > Substitutions as Apple's apps keep such
        # substitutions; off until the person turns it on.
        # Spelling, with the system's own checker (decision Q-O): on, as in
        # Apple's apps; dimmed where the system has no checker.
        self.check_spelling = bool(load_settings().get("check_spelling", True))
        self.spelling_language = str(load_settings().get("spelling_language", "") or "")
        self.act_check_spelling = add(
            Command("edit.check_spelling", _("Check Spelling While Typing"), EDIT,
                    checkable=True, keywords=(_("spelling"), _("spellcheck"))),
            toggled=self._set_check_spelling, checked=self.check_spelling)
        self.markdown_shortcuts = bool(load_settings().get("markdown_shortcuts", False))
        self.act_markdown_shortcuts = add(
            Command("edit.markdown_shortcuts", _("Markdown Shortcuts"), EDIT, checkable=True,
                    keywords=(_("autoformat"), _("substitutions"), "markdown")),
            toggled=self._set_markdown_shortcuts, checked=self.markdown_shortcuts)
        # Write, Preview and Split: the text, its preview, or both side by
        # side, for every document that holds Markdown (app-wide, like
        # standup's). No key: no platform has one for it.
        mode = load_settings().get("view_mode", WRITE)
        self.view_mode = mode if mode in preview_pane.MODES else WRITE
        self._view_group = QActionGroup(self)
        self.act_view_write = add(Command("view.write", _("Write"), VIEW, checkable=True,
                                          keywords=(_("editor"), _("text"))),
                                  triggered=lambda: self._choose_view(WRITE),
                                  checked=self.view_mode == WRITE)
        self.act_view_preview = add(Command("view.preview", _("Preview"), VIEW, checkable=True,
                                            keywords=(_("reading"), _("read"))),
                                    triggered=lambda: self._choose_view(PREVIEW),
                                    checked=self.view_mode == PREVIEW)
        self.act_view_split = add(Command("view.split", _("Split"), VIEW, checkable=True,
                                          keywords=(_("side by side"),)),
                                  triggered=lambda: self._choose_view(SPLIT),
                                  checked=self.view_mode == SPLIT)
        for action in (self.act_view_write, self.act_view_preview, self.act_view_split):
            self._view_group.addAction(action)
        # Writing in the monospace font, for those who prefer it (Q-K).
        self.monospace_writing = bool(load_settings().get("monospace_writing", False))
        self.act_monospace_writing = add(
            Command("view.monospace_writing", _("Use Monospace Font for Writing"), VIEW,
                    checkable=True, keywords=(_("font"), _("typewriter"))),
            toggled=self._set_monospace_writing, checked=self.monospace_writing)
        self.act_show_toolbar = add(Command("view.toolbar", _("Show Toolbar"), VIEW,
                                            platform_keys("Ctrl+Alt+T", None),
                                            checkable=True),
                                    toggled=self._set_toolbar_shown, checked=self.show_toolbar)
        self.act_highlight_line = add(Command("view.highlight_line",
                                              _("Highlight Current Line"), VIEW,
                                              checkable=True),
                                      toggled=self._toggle_highlight_line,
                                      checked=self.highlight_current_line)
        # Distraction-free reading and writing: the whole window can go
        # full screen. QKeySequence.FullScreen is F11 on Windows/Linux
        # and Ctrl+Cmd+F on macOS, matching each platform's convention.
        # Named for what it does next, as Notes, Pages and Finder name it
        # (changeEvent keeps the name in step with the window).
        self.act_fullscreen = add(Command("view.fullscreen", _("Enter Full Screen"), VIEW,
                                          QKeySequence.StandardKey.FullScreen,
                                          listed_as=_("Full screen")),
                                  triggered=self._toggle_fullscreen)
        # View > Show Sidebar, for the window in front that has one (the
        # Imports window); dimmed while this window is in front. Its own
        # key (Control-Command-S) belongs to that window.
        self.act_window_sidebar = add(Command("view.sidebar", _("Show Sidebar"), VIEW,
                                              listed_as=_("Show or hide the sidebar")),
                                      triggered=self._toggle_window_sidebar, enabled=False)

        # Nostr. Only the ones that need an account are marked: Create
        # Account, Connect Signer, Restore and the membership window are
        # how a person starts using Nostr. No keys: no platform has a
        # convention for them (Shift-Command-P is Page Setup in Apple's
        # apps), and keys are not invented (decision Q-A).
        self.act_nostr_publish_note = add(
            Command("nostr.publish_note", _("Publish as Note…"), NOSTR,
                    nostr=True, listed_as=_("Publish as note"), keywords=("post", "kind 1")),
            triggered=self._on_nostr_publish_note)
        self.act_nostr_publish_article = add(
            Command("nostr.publish_article", _("Publish as Article…"), NOSTR,
                    nostr=True, listed_as=_("Publish as article"), keywords=("long", "blog")),
            triggered=self._on_nostr_publish_article)
        # Media (Blossom) - browse the user's uploaded blobs, upload new
        # ones, or insert one into the current document at the cursor.
        self.act_nostr_media = add(
            Command("nostr.media_library", _("Media Library…"), NOSTR,
                    nostr=True, listed_as=_("Media library"), keywords=("images", "upload")),
            triggered=self._on_nostr_media_library)
        self.act_nostr_insert_image = add(
            Command("nostr.insert_image", _("Insert Image…"), NOSTR,
                    nostr=True, listed_as=_("Insert image"),
                    keywords=(_("picture"), _("photo")), slash_section=slash_menu.INSERT),
            triggered=self._on_nostr_insert_image)
        # Mention: "@" at the start of a word does the same while typing.
        self.act_insert_mention = add(
            Command("nostr.insert_mention", _("Insert Mention…"), NOSTR, nostr=True,
                    keywords=(_("person"), _("people"), _("tag"), "@"),
                    slash_section=slash_menu.INSERT),
            triggered=lambda: self._ask_mention(self.current_editor(), -1))
        # A note or an article quoted in the text, as a paragraph of its own.
        self.act_insert_reference = add(
            Command("nostr.insert_reference", _("Insert Note or Article…"), NOSTR, nostr=True,
                    keywords=(_("quote"), _("embed"), _("note"), _("article")),
                    slash_section=slash_menu.INSERT),
            triggered=lambda: self._ask_reference(self.current_editor()))
        # Article Details: the inspector beside the document, as in Pages
        # (View > Inspector, Option-Command-I there; no convention elsewhere).
        self.act_article_details = add(
            Command("nostr.article_details", _("Article Details"), NOSTR,
                    platform_keys("Ctrl+Alt+I", None), checkable=True, nostr=True,
                    listed_as=_("Show or hide Article Details"),
                    keywords=(_("inspector"), _("title"), _("summary"), _("cover"),
                              _("hashtags"))),
            toggled=self._show_article_details_panel)
        # Audio or video, uploaded to the media servers and put in the text
        # as its address, where readers show a player.
        self.act_insert_audio_video = add(
            Command("nostr.insert_audio_video", _("Insert Audio or Video…"), NOSTR,
                    nostr=True, keywords=(_("audio"), _("video"), _("podcast"), _("music"),
                                          _("film")), slash_section=slash_menu.INSERT),
            triggered=self._ask_audio_or_video)
        # Drafts surface - the side-docked panel, toggled from the Nostr
        # menu (no key, as above).
        self.act_nostr_drafts = add(
            Command("nostr.drafts", _("Drafts"), NOSTR, checkable=True,
                    nostr=True, listed_as=_("Toggle Drafts panel")),
            triggered=self._on_toggle_drafts_panel)
        # The Drafts panel's view reads again (its title follows the view
        # shown). In the menu bar, where every command of the app is listed,
        # and in the panel's action menu; each platform's own key for it.
        self.act_refresh_drafts = add(
            Command("nostr.refresh_drafts", _("Refresh Drafts"), NOSTR,
                    QKeySequence.StandardKey.Refresh, nostr=True,
                    listed_as=_("Refresh the Drafts panel"), keywords=("reload", "sync")),
            triggered=self._refresh_drafts_panel, enabled=False)
        # Where the account keeps its drafts: a sheet on this window. In the
        # menu bar, where every command of the app is listed, and in the
        # Drafts panel's action menu.
        self.act_draft_relays = add(
            Command("nostr.draft_relays", _("Draft Relays…"), NOSTR, nostr=True,
                    keywords=("drafts", "private", "relays")),
            triggered=self._open_draft_relays)
        # Imports: the window of sources, their posts and the open one. No
        # shortcut: no platform has a convention for it.
        self.act_nostr_imports = add(
            Command("nostr.imports", _("Imports\u2026"), NOSTR, nostr=True,
                    keywords=("rss", "feed", "import", "sources")),
            triggered=self._open_imports_window)
        # Q-7: off until turned on here, or where Imports offers it once.
        self.act_imports_badge = add(
            Command("nostr.imports_badge", IMPORTS_BADGE_TITLE, NOSTR, checkable=True,
                    nostr=True, keywords=("dock", "badge", "taskbar", "new posts")),
            toggled=self._on_imports_badge_toggled)
        self.act_membership = add(
            Command("nostr.membership", _("EINUNDZWANZIG Membership\u2026"), NOSTR,
                    keywords=("einundzwanzig", "21", "join")),
            triggered=self._open_membership_window)
        self.act_create_account = add(
            Command("nostr.create_account", _("Create Account\u2026"), NOSTR,
                    keywords=("new", "key", "sign up")),
            triggered=self._on_create_account)
        self.act_nostr_connect = add(
            Command("nostr.connect", _("Connect Signer App\u2026"), NOSTR,
                    keywords=("login", "bunker", "signer")),
            triggered=self._on_nostr_connect)
        self.act_restore_account = add(
            Command("nostr.restore_account", _("Restore Account\u2026"), NOSTR,
                    keywords=("backup", "import")),
            triggered=self._on_restore_account)
        self._act_backup_account = add(
            Command("nostr.backup_account", _("Back Up Account\u2026"), NOSTR, nostr=True,
                    keywords=("export", "key")),
            triggered=self._on_backup_account)
        self.act_move_to_signer = add(
            Command("nostr.move_to_signer", _("Move Key to Signer App\u2026"), NOSTR,
                    nostr=True, keywords=("amber", "phone", "key")),
            triggered=self._on_move_to_signer)
        self.act_edit_profile = add(
            Command("nostr.edit_profile", _("Edit Profile\u2026"), NOSTR, nostr=True,
                    keywords=("name", "picture", "about", "lightning address")),
            triggered=self._on_edit_profile)
        self.act_nostr_sign_out = add(
            Command("nostr.sign_out", _("Sign Out"), NOSTR, nostr=True,
                    keywords=("log out",)),
            triggered=self._on_nostr_sign_out)

        # The Window menu every Mac app has, where Command-M minimizes the
        # window in front (Qt makes none). Windows and Linux keep these in
        # the title bar.
        if sys.platform == "darwin":
            self.act_minimize = add(Command("window.minimize", _("Minimize"), WINDOW,
                                            "Ctrl+M"),
                                    triggered=lambda: self._front_window().showMinimized())
            self.act_zoom = add(Command("window.zoom", _("Zoom"), WINDOW),
                                triggered=self._zoom_front_window)
            self.act_bring_all = add(Command("window.bring_all", _("Bring All to Front"),
                                             WINDOW),
                                     triggered=self._bring_all_to_front)

        # Help
        self.act_welcome = add(Command("help.welcome", _("Welcome"), HELP),
                               triggered=self.show_welcome_tab)
        self.act_shortcuts = add(Command("help.shortcuts", _("Keyboard Shortcuts"), HELP,
                                         keywords=("keys", "cheat sheet")),
                                 triggered=self._show_shortcuts)
        self.act_install_help = add(Command("help.install", _("Installation Help"), HELP),
                                    triggered=self._open_install_guide)
        self.act_check_updates = add(Command("help.check_updates", _("Check for Updates\u2026"),
                                             HELP, keywords=("upgrade", "version")),
                                     triggered=self._check_for_updates_manual)
        self.act_show_logs = add(Command("help.show_logs", _("Show Log Files"), HELP,
                                         keywords=("diagnostics", "bug report", "crash")),
                                 triggered=self._show_log_files)
        self.act_about = add(Command("help.about",
                                     _("About {app}").format(app=APP_DISPLAY_NAME), HELP,
                                     role=QAction.MenuRole.AboutRole),
                             triggered=self._show_about)

    def _build_menu(self):
        # The chevron a narrow window shows for the menus it has no room
        # for: Qt gives it no name, so a screen reader would say "button".
        more = self.menuBar().findChild(QToolButton, "qt_menubar_ext_button")
        if more is not None:
            more.setAccessibleName(_("More Menus"))
        m_file = self.menuBar().addMenu(_("&File"))
        m_file.addAction(self.act_new)
        m_file.addAction(self.act_open)
        m_file.addSeparator()
        self.m_recent = m_file.addMenu(_("Recent Files"))
        self._populate_recent_menu()
        m_file.addSeparator()
        m_file.addAction(self.act_save)
        m_file.addAction(self.act_save_as)
        m_file.addSeparator()
        m_file.addAction(self.act_page_setup)
        if printing.OFFERS_PRINT_PREVIEW:
            m_file.addAction(self.act_print_preview)
        m_file.addAction(self.act_print)
        m_file.addSeparator()
        # R Markdown is for the few who write it: one item in the File menu,
        # its commands (and Knit's key) inside.
        self.m_rmarkdown = m_file.addMenu(_("R Markdown"))
        self.m_rmarkdown.addAction(self.act_knit_html)
        self.m_rmarkdown.addAction(self.act_knit_pdf)
        self.m_rmarkdown.addSeparator()
        self.m_rmarkdown.addAction(self.act_rmd_toolchain)
        m_file.addSeparator()
        m_file.addAction(self.act_close_tab)
        m_file.addAction(self.act_quit)

        m_edit = self.menuBar().addMenu(_("&Edit"))
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)
        m_edit.addSeparator()
        m_edit.addAction(self.act_cut)
        m_edit.addAction(self.act_copy)
        m_edit.addAction(self.act_paste)
        m_edit.addAction(self.act_paste_plain)
        m_edit.addAction(self.act_delete)
        m_edit.addAction(self.act_select_all)
        m_edit.addSeparator()
        self.m_find = m_edit.addMenu(pgettext("menu", "Find"))
        self.m_find.addAction(self.act_find)
        self.m_find.addAction(self.act_replace)
        self.m_find.addAction(self.act_find_next)
        self.m_find.addAction(self.act_find_prev)
        self.m_find.addAction(self.act_use_selection)
        m_edit.addAction(self.act_footnote_jump)
        # Spelling, then Substitutions, in the order of Apple's Edit menus.
        self.m_spelling = m_edit.addMenu(_("Spelling"))
        self.m_spelling.addAction(self.act_check_spelling)
        if sys.platform != "darwin":
            # macOS tells the language of each paragraph by itself;
            # elsewhere the person says which one (the system's at first).
            self.m_spelling_language = self.m_spelling.addMenu(_("Language"))
        self.m_spelling.aboutToShow.connect(self._update_spelling_menu)
        self.m_substitutions = m_edit.addMenu(_("Substitutions"))
        self.m_substitutions.addAction(self.act_markdown_shortcuts)
        # On a Mac Settings moves to the application menu; a separator left
        # here would end the Edit menu with a line.
        if sys.platform != "darwin":
            m_edit.addSeparator()
        m_edit.addAction(self.act_settings)
        self.m_edit = m_edit

        # Insert, between Edit and Format, as in Pages.
        self.m_insert = self.menuBar().addMenu(_("&Insert"))
        self.m_insert.addAction(self.act_link)
        self.m_insert.addSeparator()
        self.m_insert.addAction(self.act_table)
        self.m_insert.addAction(self.act_divider)
        self.m_insert.addAction(self.act_code_block)
        self.m_insert.addAction(self.act_footnote)
        # Nostr's own insertions, in the Insert menu only while a Nostr
        # account is in use (decision D-1); always in the Nostr menu.
        self._insert_nostr_separator = self.m_insert.addSeparator()
        self._insert_nostr_actions = [self.act_insert_mention, self.act_insert_reference,
                                      self.act_nostr_insert_image, self.act_insert_audio_video]
        self.m_insert.aboutToShow.connect(self._fill_insert_menu)

        m_format = self.menuBar().addMenu(_("F&ormat"))
        self.m_style = m_format.addMenu(_("Style"))
        for action in self.act_styles:
            self.m_style.addAction(action)
        m_format.addSeparator()
        m_format.addAction(self.act_bold)
        m_format.addAction(self.act_italic)
        m_format.addAction(self.act_underline)
        m_format.addAction(self.act_strike)
        m_format.addAction(self.act_code)
        m_format.addSeparator()
        m_format.addAction(self.act_bullets)
        m_format.addAction(self.act_numbers)
        m_format.addAction(self.act_checklist)
        m_format.addAction(self.act_tick)
        m_format.addAction(self.act_quote)
        m_format.addSeparator()
        m_format.addAction(self.act_indent)
        m_format.addAction(self.act_outdent)
        m_format.addSeparator()
        self.m_color = m_format.addMenu(_("Color"))
        for action in self.act_colors:
            self.m_color.addAction(action)
        self.m_color.addSeparator()
        self.m_color.addAction(self.act_remove_color)
        self.m_code_language = m_format.addMenu(_("Code Language"))
        for action in self.act_code_languages:
            self.m_code_language.addAction(action)
            if not action.data():
                self.m_code_language.addSeparator()         # after Plain Text
        self.m_table = m_format.addMenu(_("Table"))
        for index, action in enumerate(self.act_table_commands):
            if index in (2, 4, 6):
                self.m_table.addSeparator()     # rows | columns | delete | the table
            self.m_table.addAction(action)
        m_format.addSeparator()
        m_format.addAction(self.act_reset_format)
        self.m_format = m_format

        m_view = self.menuBar().addMenu(_("&View"))
        self.m_view = m_view
        # Full screen is named for the window in front as the menu opens.
        m_view.aboutToShow.connect(self._name_full_screen)
        m_view.addAction(self.act_view_write)
        m_view.addAction(self.act_view_preview)
        m_view.addAction(self.act_view_split)
        m_view.addSeparator()
        m_view.addAction(self.act_show_toolbar)
        m_view.addSeparator()
        m_view.addAction(self.act_monospace_writing)
        m_view.addSeparator()
        self.m_appearance = m_view.addMenu(_("Appearance"))
        for action in self.act_appearance.values():
            self.m_appearance.addAction(action)
        m_view.addAction(self.act_toggle_line_numbers)
        m_view.addAction(self.act_toggle_syntax_hl)
        m_view.addSeparator()
        m_view.addAction(self.act_paper_mode)
        m_view.addSeparator()
        m_background = m_view.addMenu(_("Background"))
        m_background.addAction(self.act_bg_none)
        m_background.addAction(self.act_bg_lines)
        m_background.addAction(self.act_bg_dashed)
        m_background.addAction(self.act_bg_dots)
        m_background.addAction(self.act_bg_grid)
        m_view.addAction(self.act_highlight_line)
        m_view.addSeparator()
        m_language = m_view.addMenu(_("Language"))
        for action in self.act_languages:
            m_language.addAction(action)
        m_view.addSeparator()
        m_view.addAction(self.act_window_sidebar)
        # Article Details, while a Nostr account is in use (D-1).
        self._view_nostr_separator = m_view.addSeparator()
        m_view.addAction(self.act_article_details)
        m_view.aboutToShow.connect(self._fill_view_menu)
        m_view.addAction(self.act_fullscreen)
        self._m_background = m_background

        m_nostr = self.menuBar().addMenu("&Nostr")
        m_nostr.addAction(self.act_nostr_publish_note)
        m_nostr.addAction(self.act_nostr_publish_article)
        m_nostr.addSeparator()
        m_nostr.addAction(self.act_nostr_media)
        m_nostr.addAction(self.act_nostr_insert_image)
        m_nostr.addAction(self.act_insert_mention)
        m_nostr.addAction(self.act_insert_reference)
        m_nostr.addAction(self.act_insert_audio_video)
        m_nostr.addAction(self.act_article_details)
        m_nostr.addSeparator()
        m_nostr.addAction(self.act_nostr_drafts)
        m_nostr.addAction(self.act_refresh_drafts)
        m_nostr.addAction(self.act_draft_relays)
        m_nostr.addAction(self.act_nostr_imports)
        m_nostr.addAction(self.act_imports_badge)
        m_nostr.addSeparator()
        m_nostr.addAction(self.act_membership)
        # The ways to an account, then the account in use.
        m_nostr.addSeparator()
        m_nostr.addAction(self.act_create_account)
        m_nostr.addAction(self.act_nostr_connect)
        m_nostr.addAction(self.act_restore_account)
        m_nostr.addSeparator()
        m_nostr.addAction(self.act_edit_profile)
        m_nostr.addAction(self._act_backup_account)
        m_nostr.addAction(self.act_move_to_signer)
        m_nostr.addAction(self.act_nostr_sign_out)
        self._update_account_actions()

        if sys.platform == "darwin":
            self.m_window = self.menuBar().addMenu(_("Window"))
            self.m_window.addAction(self.act_minimize)
            self.m_window.addAction(self.act_zoom)
            self.m_window.addSeparator()
            self.m_window.addAction(self.act_bring_all)

        help_menu = self.menuBar().addMenu(_("&Help"))
        help_menu.addAction(self.act_welcome)
        help_menu.addAction(self.act_shortcuts)
        help_menu.addSeparator()
        help_menu.addAction(self.act_install_help)
        help_menu.addAction(self.act_check_updates)
        help_menu.addAction(self.act_show_logs)
        help_menu.addSeparator()
        help_menu.addAction(self.act_about)

    def _populate_recent_menu(self):
        """The files opened last, then Clear Recent Files: an ordinary
        menu item (reached by the keyboard like any other, in the menu's
        own color, since clearing the list deletes no file), dimmed while
        the list is empty, as a Mac's Open Recent menu does."""
        # The menu owns its items, so clear() deletes them (owned by the
        # window, every refill used to leave the old ones behind).
        self.m_recent.clear()
        self.m_recent.setToolTipsVisible(True)
        entries = [p for p in load_recent() if os.path.exists(p)]
        for path in entries:
            action = QAction(os.path.basename(path), self.m_recent)
            action.setToolTip(path)
            action.triggered.connect(lambda checked, p=path: self.open_path(p))
            self.m_recent.addAction(action)
        if entries:
            self.m_recent.addSeparator()
        clear = QAction(_("Clear Recent Files"), self.m_recent)
        clear.setEnabled(bool(entries))
        clear.triggered.connect(self._clear_recent_files)
        self.m_recent.addAction(clear)

    def _clear_recent_files(self):
        clear_recent()
        self._populate_recent_menu()

    def _build_findbar(self):
        self.findbar = FindBar(self._find_next, self._find_prev, self._close_findbar, self,
                               replace=True, options=True)
        self.findbar.setVisible(False)
        self.findbar.edit.textChanged.connect(self._on_search_text_changed)
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self.findbar.options_changed.connect(self._on_find_options_changed)
        self.findbar.replace_requested.connect(self._replace_current)
        self.findbar.replace_all_requested.connect(self._replace_all)
        # Matches are found again shortly after the document changes, so
        # the count and the highlights never describe text that is gone.
        self._find_refresh = QTimer(self)
        self._find_refresh.setSingleShot(True)
        self._find_refresh.setInterval(250)
        self._find_refresh.timeout.connect(self._refresh_matches)

        self.update_bar = UpdateBar()
        self.update_bar.update_theme(self.is_dark_theme)
        self.update_bar.update_requested.connect(self._on_update_bar_clicked)
        self.update_bar.whats_new_requested.connect(self._on_whats_new_requested)

        # The editor area (header + findbar + tabs) lives on the left
        # of a horizontal splitter; the drafts panel docks on the right.
        # We always create the panel - keeping it always-present (just
        # hidden) preserves the layout's geometry across show/hide and
        # avoids re-parenting issues. When no Nostr profile is connected,
        # the panel itself renders the "Connect a Nostr profile" empty
        # state without taking visual space beyond a thin border.
        editor_side = QWidget()
        v = QVBoxLayout(editor_side)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.format_toolbar = FormatToolbar(
            {"bold": self.act_bold, "italic": self.act_italic, "strike": self.act_strike,
             "link": self.act_link, "bullets": self.act_bullets, "numbers": self.act_numbers,
             "checklist": self.act_checklist, "quote": self.act_quote},
            self.m_style, dark=self.is_dark_theme)
        self.view_switch = ViewSwitch(
            (self.act_view_write, self.act_view_preview, self.act_view_split),
            dark=self.is_dark_theme)
        # The switch sits right of the toolbar, outside it, so a narrow
        # window never hides it in the toolbar's chevron.
        self._view_switch_holder = self.format_toolbar.add_trailing(self.view_switch)
        self.format_bar = QWidget()
        bar_row = QHBoxLayout(self.format_bar)
        bar_row.setContentsMargins(0, 0, 0, 0)
        bar_row.setSpacing(0)
        bar_row.addWidget(self.format_toolbar, 1)
        bar_row.addWidget(self._view_switch_holder, 0)
        # Tabs made before the switch existed show their view now.
        QTimer.singleShot(0, lambda: self._apply_view(self.current_editor()))
        v.addWidget(self.format_bar, 0)
        v.addWidget(self.findbar, 0)
        v.addWidget(self.update_bar, 0)
        v.addWidget(self.tabs, 1)

        self._drafts_panel = DraftsPanel(is_dark=self.is_dark_theme)
        self._drafts_panel.bind_store(self._draft_store)
        # The hover preview's hero images go through the loader the app
        # already owns, so there is one cache, one URL policy and one
        # place where an image request can be made.
        self._drafts_panel.set_preview_image_loader(self._media_image_loader)
        self._drafts_panel.set_active_profile(self._profile_store.default())
        # The panel's outbound actions all route back through the host.
        self._drafts_panel.open_draft.connect(self._on_panel_open_draft)
        self._drafts_panel.publish_draft.connect(self._on_panel_publish_draft)
        self._drafts_panel.delete_drafts.connect(self._on_panel_delete_drafts)
        self._drafts_panel.retry_decrypt.connect(self._on_panel_retry_decrypt)
        self._drafts_panel.retry_signer.connect(self._draft_sync.retry_signer)
        self._drafts_panel.copy_event_id.connect(self._on_panel_copy_event_id)
        self._drafts_panel.refresh_requested.connect(self._draft_sync.refresh_by_hand)
        # The deletions other apps announce come with the drafts (L13).
        self._drafts_panel.refresh_requested.connect(self._draft_deletions.refresh)
        self._drafts_panel.close_requested.connect(self._hide_drafts_panel)
        self._drafts_panel.draft_relays_requested.connect(self._open_draft_relays)
        # The second view: what the account published, to edit, open,
        # share or delete. It reads the relays once it is shown
        # (_follow_account_in_published), not when MyEditor starts.
        self._published_view = PublishedView(
            PublishedList(relay_pool=self._relay_pool, relay_directory=self._relay_directory,
                          session_pool=self._session_pool,
                          entitled_relays=self._entitled_relays, parent=self),
            is_dark=self.is_dark_theme)
        add_published_view(self._drafts_panel, self._published_view)
        self._drafts_panel.view_changed.connect(self._follow_account_in_published)
        self._drafts_panel.view_changed.connect(self._update_refresh_command)
        self._published_view.open_requested.connect(self._on_published_open)
        self._published_view.status_message.connect(self._on_published_status)
        # The panel's one row about imports (D-2): new posts, how an
        # import goes, and the Imports window.
        self._drafts_panel.open_imports.connect(self._open_imports_window)
        self._drafts_panel.pause_import.connect(self._imports.pause_import)
        self._drafts_panel.resume_import.connect(self._resume_import)
        for signal in (self._imports.bound_changed, self._imports.inbox_count_changed,
                       self._imports.activity_changed):
            signal.connect(self._update_imports_row)
        self._update_imports_row()

        self._central_splitter = QSplitter(Qt.Horizontal)
        self._central_splitter.setObjectName("central_splitter")
        self._central_splitter.setHandleWidth(1)
        self._central_splitter.setChildrenCollapsible(False)
        self._central_splitter.addWidget(editor_side)
        # The inspector beside the document, the drafts panel at the edge.
        self._article_details_panel = ArticleDetailsPanel(
            media_store=self._media_store, media_visibility=self._media_visibility,
            copy_maker=self._copy_maker, private_library=self._private_library,
            profile=self._profile_store.default, pick_person=self._pick_coauthor,
            name_of=self._person_name, can_receive_zaps=self._ask_can_receive_zaps,
            relay_hint=self._relay_directory.relay_hint, is_dark=self.is_dark_theme)
        self._article_details_panel.changed.connect(
            lambda details: self._set_article_details(self.current_editor(), details))
        self._article_details_panel.close_requested.connect(
            lambda: self.act_article_details.setChecked(False))
        self._central_splitter.addWidget(self._article_details_panel)
        self._central_splitter.addWidget(self._drafts_panel)
        # Editor takes all stretch; the panels start hidden.
        self._central_splitter.setStretchFactor(0, 1)
        self._central_splitter.setStretchFactor(1, 0)
        self._central_splitter.setStretchFactor(2, 0)
        self._article_details_panel.hide()
        self._drafts_panel.hide()

        self.setCentralWidget(self._central_splitter)


    # ----------------------------------------------------------------------
    # CRASH RECOVERY
    # ----------------------------------------------------------------------
    def _restore_backups(self, skip=frozenset()) -> bool:
        """Silently restore any backup files left over from a previous crash.

        ``skip`` holds the backup files an update restart just reopened its
        tabs from, or that those tabs write to, so none opens twice.
        Returns True if at least one backup was restored.
        """
        restored = 0
        for backup in find_all_backups():
            if os.path.abspath(backup.get("_backup_file", "")) in skip:
                continue
            try:
                if self._restore_one_backup(backup) is not None:
                    restored += 1
            except Exception:
                # One unreadable record must never cost the user the
                # other tabs it was found beside.
                continue
        return restored > 0

    def _restore_one_backup(self, backup: dict) -> Optional[HtmlEditor]:
        """Open one backup record as a recovered tab; returns its editor,
        or None for a record this build must leave alone."""
        if not is_restorable(backup):
            # Written by a newer build. Leave it on disk untouched rather
            # than guess at a format this build does not know.
            return None

        # Possibly written by an older version, in another spelling.
        original_path = file_paths.normalize(backup.get("original_path") or "") or None
        backup_file = backup["_backup_file"]
        freshness = classify_backup(backup)

        ed = self._new_wired_editor()
        # A stale backup keeps no path: Ctrl+S then has to go through
        # Save As, so a recovered copy can never overwrite newer work.
        # Set before the content: image names beside the original file
        # resolve against it.
        ed._file_path = None if freshness == "stale" else original_path
        load_backup_content(ed, backup, modified=True)
        # The draft it was bound to and the article it edited come back
        # too, so saving or publishing it goes on under its identifier.
        workspace_restore.restore_identity(
            ed, backup.get("draft"), backup.get("article"), backup.get("article_author"),
            backup.get("details"), backup.get("front_matter"), draft_type=DraftBinding)
        self._attach_highlighter(ed, ed._file_path)

        container, bar = self._editor_container(ed)

        base_name = os.path.basename(original_path) if original_path else _("Untitled")
        if freshness == "stale":
            tab_title = _("{name} (recovered copy)").format(name=base_name)
        else:
            tab_title = _("{name} (recovered)").format(name=base_name)
        # The tab says so until the work is saved (_compose_tab_title).
        ed._recovered_title = tab_title
        idx = self.tabs.addTab(container, tab_title + "*")
        self._attach_close_button(idx, container)

        if freshness == "stale" and original_path:
            bar.show_notice(
                _("Recovered a copy. The file on disk is newer: {path}").format(
                    path=original_path)
            )
        elif ed._file_path:
            self._watcher.addPath(ed._file_path)

        ed._backup = self._new_backup(ed, ed._file_path)
        ed._backup.take_over(backup_file)
        return ed

    # ----------------------------------------------------------------------
    # FILE I/O
    # ----------------------------------------------------------------------
    def new_tab(self) -> HtmlEditor:
        """Open an untitled tab, make it current, and return its editor."""
        ed = self._new_wired_editor()

        container, bar = self._editor_container(ed)

        idx = self.tabs.addTab(container, _("Untitled") + "*")
        self.tabs.setCurrentIndex(idx)

        self._attach_close_button(idx, container)

        ed._file_path = None
        ed._language = None

        # Debounce timer: detect language from pasted/typed content
        _timer = QTimer(ed)
        _timer.setSingleShot(True)
        _timer.setInterval(600)
        _timer.timeout.connect(lambda: self._auto_detect_language(ed))
        ed.document().contentsChanged.connect(_timer.start)
        ed._lang_detect_timer = _timer

        ed.setHtml("<div></div>")
        self._attach_highlighter(ed, None)
        ed._backup = self._new_backup(ed, None)
        ed.setFocus()
        self._update_window_title()
        return ed

    def _welcome_keys(self) -> dict:
        """The keys the welcome page names, as this platform writes them."""
        keys = {}
        for command in WELCOME_KEYS:
            shortcut = self.commands.action(command).shortcut()
            text = shortcut.toString(QKeySequence.SequenceFormat.NativeText)
            if text:
                keys[command] = text
        return keys

    def show_welcome_tab(self) -> HtmlEditor:
        """Open a friendly first-run tab introducing the app; returns its editor."""
        ed = self.new_tab()
        ed.setHtml(welcome_html(self._welcome_keys()))
        ed.document().setModified(False)
        ed._is_welcome = True   # so an update restart reopens it as the welcome tab
        self._attach_spelling(ed)
        self.tabs.setTabText(self.tabs.currentIndex(), _("Welcome"))
        self._update_status_bar()
        return ed

    def _on_tab_context_menu(self, pos):
        idx = self.tabs.tabBar().tabAt(pos)
        if idx < 0:
            return
        ed = self._editor_from_widget(self.tabs.widget(idx))
        has_path = bool(getattr(ed, '_file_path', None))

        menu = QMenu(self)
        rename_action = menu.addAction(_("Rename"))
        rename_action.setEnabled(has_path)
        if not has_path:
            rename_action.setToolTip(_("Save the file first before renaming"))

        menu.addSeparator()

        delete_action = menu.addAction(_("Delete File..."))
        delete_action.setEnabled(has_path)
        if not has_path:
            delete_action.setToolTip(_("No file on disk to delete"))

        action = menu.exec(self.tabs.tabBar().mapToGlobal(pos))
        if action == rename_action:
            self._rename_tab(idx, ed)
        elif action == delete_action:
            self._delete_tab_file(idx, ed)

    def _delete_tab_file(self, idx: int, ed):
        file_path = ed._file_path
        file_name = os.path.basename(file_path)

        # Windows calls it the Recycle Bin; macOS and Linux desktops, the Trash.
        if sys.platform == "win32":
            title = _("Move \u201c{name}\u201d to the Recycle Bin?")
            message = _("You can restore it from the Recycle Bin until you empty it.")
            action = _("Move to Recycle Bin")
            failed = _("Couldn't move \u201c{name}\u201d to the Recycle Bin")
        else:
            title = _("Move \u201c{name}\u201d to the Trash?")
            message = _("You can restore it from the Trash until you empty it.")
            action = _("Move to Trash")
            failed = _("Couldn't move \u201c{name}\u201d to the Trash")
        if not confirm_destructive(
                self,
                title=title.format(name=file_name),
                message=message,
                action=action,
                is_dark=self.is_dark_theme):
            return

        try:
            send2trash(file_path)
        except Exception as e:
            inform(self, title=failed.format(name=file_name), message=str(e))
            return

        # Close tab without prompting to save - file is gone
        if hasattr(ed, '_backup'):
            ed._backup.delete()
        self._watcher.removePath(file_path)
        self.tabs.removeTab(idx)

    def _rename_tab(self, idx: int, ed):
        old_path = ed._file_path
        old_name = os.path.basename(old_path)
        directory = os.path.dirname(old_path)

        new_name, ok = QInputDialog.getText(self, _("Rename File"), _("New filename:"),
                                            text=old_name)
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return

        new_name = new_name.strip()
        new_path = file_paths.normalize(os.path.join(directory, new_name))

        # Any entry with that name is a clash, a link (even a broken one)
        # included: renaming onto it would replace it. Only changing the
        # case of the name, where the disk ignores case (Windows, a Mac),
        # finds the file itself, and that is a rename.
        if os.path.lexists(new_path) and not file_paths.is_case_change(old_path, new_path):
            inform(self, title=_("\u201c{name}\u201d already exists").format(name=new_name),
                   message=_("Choose a different name."))
            return

        try:
            os.rename(old_path, new_path)
        except OSError as e:
            inform(self, title=_("Couldn't rename the file"), message=str(e))
            return

        self._watcher.removePath(old_path)
        self._watcher.addPath(new_path)
        ed._file_path = new_path
        if hasattr(ed, '_backup'):
            ed._backup.update_file_path(new_path)
        self.tabs.setTabText(idx, new_name)
        ed.setAccessibleName(self._document_name(ed))
        self._update_window_title()

    def _attach_close_button(self, idx: int, container: QWidget):
        # Closing a tab is not destructive (unsaved changes are asked
        # about), so the button is the tab's own quiet color, not red; the
        # window's stylesheet (TabClose) gives it its colors per theme.
        close_btn = QPushButton("×")
        close_btn.setObjectName("TabClose")
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip(_("Close Tab"))
        close_btn.setAccessibleName(_("Close Tab"))
        close_btn.clicked.connect(lambda: self.close_tab(self.tabs.indexOf(container)))
        self.tabs.tabBar().setTabButton(idx, QTabBar.RightSide, close_btn)

    def close_tab(self, index: int):
        w = self.tabs.widget(index)

        viewer = self._pdf_viewer_from_widget(w)
        if viewer is not None:
            viewer.save_view_state()
            self._watcher.removePath(viewer._file_path)
            self.tabs.removeTab(index)
            return

        editor = self._editor_from_widget(w)

        uploading = self._uploading(editor) if editor else []
        if editor and not editor.document().isModified() \
                and not self._may_leave_uploads(uploading, quitting=False):
            return
        if editor and editor.document().isModified():
            file_name = os.path.basename(editor._file_path) if getattr(editor, '_file_path', None) else _("Untitled")
            r = _ask_save_changes(
                self, _("Do you want to save the changes you made to \u201c{name}\u201d?").format(
                    name=file_name),
                message=self._save_question_words(uploading, quitting=False))
            if r == "cancel":
                return
            if r == "save":
                self.tabs.setCurrentIndex(index)
                if not self.save():
                    return
        if editor:
            self._drop_media_reads(editor)
        if editor and hasattr(editor, '_backup'):
            editor._backup.delete()
        if editor:
            path = getattr(editor, '_file_path', None)
            if path:
                self._watcher.removePath(path)
            # A draft tab might own a conflict banner - drop the dict
            # entry so we don't accumulate references to dead editors
            # across the session.
            banner = self._tab_conflict_banners.pop(editor, None)
            if banner is not None:
                banner.setParent(None)
                banner.deleteLater()
            # Cancel any in-flight stash so its signal callbacks don't
            # fire against a destroyed editor. The signer round-trip
            # itself can't be recalled, but ``cancel()`` flips a flag
            # that suppresses all future signal emissions.
            active_job = getattr(editor, "_active_stash_job", None)
            if active_job is not None:
                active_job.cancel()
                editor._active_stash_job = None
        self.tabs.removeTab(index)

    def _close_current_tab(self):
        # Command-W closes the window in front (Settings, Keyboard
        # Shortcuts, Imports...), also from the menu: on a Mac the menu
        # bar takes the key before the window in front sees it. In this
        # window it closes the tab. A sheet is answered with its buttons:
        # neither it nor the window under it is closed (CP3 review M2).
        front = self._front_window()
        if front is not self:
            imports = self._imports_in_front()
            if (front.windowModality() == Qt.WindowModality.NonModal
                    and not (imports is not None and imports.sheet_in_front())):
                front.close()
            return
        idx = self.tabs.currentIndex()
        if idx >= 0:
            self.close_tab(idx)

    def _quit_application(self):
        # closeEvent owns the unsaved-work question, so quitting from the
        # menu and closing the window ask it once, the same way. (It used
        # to be asked here and then again, differently, by closeEvent.)
        self.close()

    def _resolve_unsaved_before_closing(self) -> bool:
        """Save, discard or keep unsaved documents. True means close."""
        editors = [ed for i in range(self.tabs.count())
                   if (ed := self._editor_from_widget(self.tabs.widget(i))) is not None]
        # Quitting stops every upload, the documents' and the Media
        # Library's alike, and every Save As still downloading.
        uploading = self._uploading(*editors) + self._library_uploads()
        saving = self._media_saves()
        unsaved = [i for i in range(self.tabs.count())
                   if (ed := self._editor_from_widget(self.tabs.widget(i)))
                   and ed.document().isModified()]
        if not unsaved:
            # Nothing to save: what is still uploading or saving is the question.
            return self._may_leave_uploads(uploading, quitting=True, saving=saving)
        words = self._save_question_words(uploading, quitting=True, saving=saving)
        if len(unsaved) == 1:
            ed = self._editor_from_widget(self.tabs.widget(unsaved[0]))
            name = os.path.basename(ed._file_path) if getattr(ed, "_file_path", None) else _("Untitled")
            answer = _ask_save_changes(
                self, _("Do you want to save the changes you made to \u201c{name}\u201d?").format(
                    name=name), message=words)
        else:
            count = len(unsaved)
            answer = _ask_save_changes(
                self, ngettext("Do you want to save the changes to {count} document?",
                               "Do you want to save the changes to {count} documents?",
                               count).format(count=count),
                save_label=_("Save All"), message=words)
        if answer == "cancel":
            return False
        if answer == "save":
            for i in unsaved:
                self.tabs.setCurrentIndex(i)
                if not self.save():
                    return False
        return True

    def _editor_from_widget(self, w) -> HtmlEditor | None:
        if isinstance(w, HtmlEditor):
            return w
        if isinstance(w, QWidget):
            return w.findChild(HtmlEditor)
        return None

    def _pdf_viewer_from_widget(self, w) -> PdfViewerTab | None:
        return w if isinstance(w, PdfViewerTab) else None

    def current_pdf_viewer(self) -> PdfViewerTab | None:
        return self._pdf_viewer_from_widget(self.tabs.currentWidget())

    def _tab_file_path(self, w) -> str | None:
        """The file backing a tab, regardless of tab kind (editor / PDF)."""
        ed = self._editor_from_widget(w)
        if ed is not None:
            return getattr(ed, '_file_path', None)
        viewer = self._pdf_viewer_from_widget(w)
        return viewer._file_path if viewer is not None else None

    def _bar_from_widget(self, w) -> FileChangedBar | None:
        if isinstance(w, QWidget):
            return w.findChild(FileChangedBar)
        return None

    # ----------------------------------------------------------------------
    # DRAG AND DROP
    # ----------------------------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            self._handle_dropped_urls(event.mimeData().urls())
            event.acceptProposedAction()
        elif event.mimeData().hasText():
            self._handle_dropped_text(event.mimeData().text())
            event.acceptProposedAction()

    def _handle_dropped_urls(self, urls):
        unsupported = []
        image_paths = []
        for url in urls:
            if not url.isLocalFile():
                # A tile dragged out of the Media Library: its address,
                # inserted as Insert Image inserts it.
                media = self._library_file_at(url.toString())
                if media is not None and self.current_editor() is not None:
                    self._insert_media_at_cursor(media, self.current_editor())
                continue
            # A URL's path has forward slashes, also on Windows.
            path = file_paths.normalize(url.toLocalFile())
            ext = os.path.splitext(path)[1].lower()
            if ext in _SUPPORTED_EXTS:
                self.open_path(path)
            elif ext in _IMAGE_EXTS:
                image_paths.append(path)
            elif media_files.is_audio_or_video(path) and self._may_insert_audio_video(
                    self.current_editor()):
                self._upload_into(self.current_editor(), path)
            else:
                unsupported.append(os.path.basename(path))

        if image_paths:
            self._handle_dropped_images(image_paths)

        if unsupported:
            bar = self._bar_from_widget(self.tabs.currentWidget())
            if bar:
                bar.show_unsupported(", ".join(unsupported))

    def _library_file_at(self, url: str):
        """The Media Library's file at this address, or None."""
        sha = hash_from_url(url)
        store = getattr(self, "_media_store", None)
        return store.files.get(sha) if sha and store is not None else None

    def _handle_dropped_images(self, paths):
        """Images were dropped on the window: adopt and insert them now.

        The insert never waits for a signer or a server. Uploading is
        offered afterwards, and declining costs nothing: the image is
        already in the document either way.
        """
        ed = self.current_editor()
        if ed is None:
            self.new_tab()
            ed = self.current_editor()
        if ed is None:
            return

        adopted = []
        refused = []
        for path in paths:
            alt = os.path.splitext(os.path.basename(path))[0] or "image"
            asset = self._asset_manager.adopt_file(path, alt=alt)
            if asset is None:
                refused.append(os.path.basename(path))
                continue
            self._insert_asset(ed, asset, alt=alt)
            adopted.append(asset)

        if refused:
            self.status.showMessage(
                _("Could not add: {names}").format(names=", ".join(refused)), 5000
            )
        if not adopted:
            return

        pending = [a for a in adopted if not a.is_uploaded]
        if not pending:
            return
        if self._profile_store.default() is None:
            return          # a local image; nothing of Nostr without an account

        count = len(pending)
        upload = ask(
            self,
            title=ngettext("Upload {count} image to your media servers?",
                           "Upload {count} images to your media servers?",
                           count).format(count=count),
            message=_("Anyone with the link can view them. They’re already in your "
                      "document and stay there either way."),
            buttons=(Button(_("Keep Local"), False, CANCEL), Button(_("Upload"), True, DEFAULT)))
        if not upload:
            return
        for asset in pending:
            self._asset_manager.request_upload(asset.sha256)

    def _handle_dropped_text(self, text: str):
        ed = self.current_editor()
        if ed is None:
            self.new_tab()
            ed = self.current_editor()
        cursor = ed.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontWeight(400)
        fmt.setFontItalic(False)
        fmt.setFontUnderline(False)
        fmt.clearForeground()
        cursor.insertText(text, fmt)
        ed.setTextCursor(cursor)

    def open_dialog(self):
        # Both .Rmd casings listed: some non-native dialogs glob case-sensitively.
        notes = "*.md *.html *.htm *.txt *.Rmd *.rmd"
        filters = ";;".join((
            _("Supported files ({patterns})").format(patterns=notes + " *.pdf"),
            _("Note files ({patterns})").format(patterns=notes),
            _("PDF documents ({patterns})").format(patterns="*.pdf"),
            _("All files ({patterns})").format(patterns="*.*"),
        ))
        path, _filter = QFileDialog.getOpenFileName(self, _("Open"), "", filters)
        if path:
            self.open_path(path)

    def _set_editor_content(self, ed, path: str, content: str) -> None:
        """Load file content into an editor with per-format handling.

        Shared by open_path and _reload_from_disk so both agree on the
        dispatch and on post-load normalization.
        """
        ext = path.lower()
        if ext.endswith(('.html', '.htm')):
            ed.setHtml(content)
            # Lists stay real lists (the editor edits them as such); what
            # the reader leaves behind is tidied.
            normalize_after_set_html(ed.document())
            ed.document().clearUndoRedoStacks()
        elif ext.endswith('.md'):
            # The article's details are the file's front matter (Q-F): shown
            # in Article Details, never in the text, never published as it.
            front, body = front_matter.split(content)
            ed._front_matter = front
            details = from_front_matter(front)
            ed._article_details = details if details != ArticleDetails() else None
            self._load_markdown(ed, body)
        elif ext.endswith('.rmd'):
            # R Markdown is edited as source, the way RStudio does it.
            ed.setPlainText(content)
            ed._loaded_as_rmd_source = True
        else:
            ed.setPlainText(content)

    def open_path(self, path: str):
        """Open ``path`` in a new tab and make it current.

        Returns the new tab's editor, or its PDF viewer. Returns None when
        no tab was opened: the file is open already (its tab becomes
        current) or it could not be read (the person is told why).

        Every way of opening a file ends here (the Open dialog, the recent
        files, the last session, the system's file manager, a second
        launch), so the path takes the app's one spelling here, and a file
        already open under another spelling is found.
        """
        path = file_paths.normalize(path)
        for i in range(self.tabs.count()):
            if file_paths.same_file(self._tab_file_path(self.tabs.widget(i)), path):
                self.tabs.setCurrentIndex(i)
                bar = self._bar_from_widget(self.tabs.widget(i))
                if bar:
                    bar.show_already_open()
                return None

        if path.lower().endswith('.pdf'):
            return self._open_pdf_tab(path)

        try:
            content, newline = read_text_document(path)
        except (OSError, ValueError) as e:
            inform(self, title=_("Couldn't open \u201c{name}\u201d").format(
                       name=os.path.basename(path)),
                   message=str(e))
            return None

        ed = self._new_wired_editor()
        # The path first: image names in the content resolve against the
        # document's own directory, and Qt caches whatever the first
        # lookup answered.
        ed._file_path = path
        # Saved back with the line endings it came with.
        ed._newline = newline
        self._set_editor_content(ed, path, content)

        ed.document().setModified(False)
        self._attach_highlighter(ed, path)

        container, bar = self._editor_container(ed)

        idx = self.tabs.addTab(container, os.path.basename(path))
        self.tabs.setCurrentIndex(idx)
        self._attach_close_button(idx, container)
        ed._backup = self._new_backup(ed, path)
        self._watcher.addPath(path)
        add_recent(path)
        self._populate_recent_menu()
        ed.setFocus()
        self._update_window_title()
        return ed

    def _open_pdf_tab(self, path: str) -> Optional[PdfViewerTab]:
        """Open ``path`` read-only in the built-in PDF viewer; returns the
        viewer, or None when the file could not be read."""
        viewer = PdfViewerTab(path, is_dark=self.is_dark_theme)
        if not viewer.load_ok:
            inform(self, title=_("Couldn't open \u201c{name}\u201d").format(
                       name=os.path.basename(path)),
                   message=str(viewer.load_error))
            viewer.deleteLater()
            return None
        viewer.page_changed.connect(self._on_pdf_page_changed)

        idx = self.tabs.addTab(viewer, os.path.basename(path))
        self.tabs.setTabToolTip(idx, path)
        self.tabs.setCurrentIndex(idx)
        self._attach_close_button(idx, viewer)
        # Watch the file so a regenerated PDF (LaTeX build, re-export)
        # refreshes in place at the same reading position.
        self._watcher.addPath(path)
        add_recent(path)
        self._populate_recent_menu()
        viewer.setFocus()
        self._update_window_title()
        self._update_status_bar()
        return viewer

    def _on_pdf_page_changed(self):
        if self.sender() is self.tabs.currentWidget():
            self._update_status_bar()

    def _has_formatting(self, ed: HtmlEditor) -> bool:
        """Return True if the document contains any bold, italic, underline, or color
        formatting, or structure plain text cannot keep (lists, headings)."""
        block = ed.document().begin()
        while block.isValid():
            if (block.textList() is not None or block.blockFormat().headingLevel()
                    or rich_text.quote_depth(block) or rich_text.is_divider(block)):
                return True
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    if (fmt.fontWeight() > 400
                            or fmt.fontItalic()
                            or fmt.fontUnderline()
                            or fmt.hasProperty(QTextCharFormat.ForegroundBrush)):
                        return True
                it += 1
            block = block.next()
        return False

    def _has_images(self, ed: HtmlEditor) -> bool:
        """Whether the document holds any image at all.

        Kept apart from _has_formatting on purpose: .md now preserves
        images, so folding images into the formatting check would warn
        about a loss that no longer happens.
        """
        return any(True for _ in iter_image_names(ed.document()))

    def _unpublishable_images(self, doc) -> list:
        """Image names that would break for a reader of a published event.

        An asset with no upload has no address anyone else can fetch,
        and a bare local path means even less to a stranger. A foreign
        http(s) or data: image is already readable and passes.
        """
        blocked = []
        for name in dict.fromkeys(iter_image_names(doc)):
            sha = parse_asset_key(name)
            if sha is None:
                if not image_safety.is_portable_image_source(name):
                    blocked.append(name)
                continue
            asset = self._asset_manager.get(sha)
            if asset is None or not asset.is_uploaded:
                blocked.append(name)
        return blocked

    def _confirm_images_uploaded(self, ed) -> bool:
        """Gate before anything is signed. False means do not publish.

        Publishing is irreversible in a way that inserting is not: an
        event with an unreachable image cannot be recalled, so this is
        the one place where waiting is the right answer.
        """
        if ed is None:
            return True
        waiting = self._media_not_uploaded(ed.document())
        if waiting:
            # Not there yet: its upload goes on (or starts again after a
            # failure or a restart), and publishing waits for it.
            for asset in waiting:
                self._asset_manager.request_upload(asset.sha256)
            inform(self, title=_("Audio or video is still uploading"),
                   message=_("Publish or save it as a draft once the upload is done; it "
                             "goes on uploading meanwhile."))
            return False
        blocked = self._unpublishable_images(ed.document())
        if not blocked:
            return True

        count = len(blocked)
        upload = ask(
            self,
            title=ngettext("Upload {count} image before publishing?",
                           "Upload {count} images before publishing?", count).format(count=count),
            message=ngettext(
                "{count} image in this document is not on your media servers yet. "
                "Publishing now would break it for readers.",
                "{count} images in this document are not on your media servers yet. "
                "Publishing now would break them for readers.", count).format(count=count),
            buttons=(Button(_("Cancel"), False, CANCEL), Button(_("Upload Now"), True, DEFAULT)))
        if upload:
            for name in blocked:
                sha = parse_asset_key(name)
                if sha is not None:
                    self._asset_manager.request_upload(sha)
        return False

    def _publish_payload(self, ed, flavor: str) -> tuple[str, list]:
        """Document text for a signed event, plus what its media is.

        toPlainText() emits U+FFFC for every image, so a published note
        used to carry an invisible placeholder where the picture was.

        The media half is collected here because this walk is the only
        place that knows which URL was written for which image, and what
        this app measured about the bytes behind it. That is why NIP-92
        imeta is built from these records rather than from a scan of the
        finished content: a scan would also find third-party addresses
        nobody here fetched and URLs the user typed as prose.

        Foreign images stay foreign. One exception, and it is narrow: an
        image this app uploaded and then saved to ``.md`` comes back as a
        plain URL, and ``AssetManager.find_by_url`` recognises it only
        when the address is one an upload actually produced. Nothing is
        rewritten either way; the only effect is whether the image gets
        described.
        """
        records: dict = {}

        def remember(asset, url: str, fmt) -> None:
            if not url or url in records:
                return
            # Audio and video have no alternative text: their name is a name.
            alt = "" if asset.is_media else (
                str(fmt.property(QTextImageFormat.ImageAltText) or "") or asset.alt)
            records[url] = PublishedMedia(
                url=url,
                sha256=asset.sha256,
                mime=asset.mime,
                width=asset.width,
                height=asset.height,
                alt=alt,
                size=asset.size,
                servers=tuple(asset.servers),
            )

        def destination(fmt) -> Optional[str]:
            name = fmt.name()
            sha = parse_asset_key(name)
            if sha is None:
                if not image_safety.is_portable_image_source(name):
                    return None
                asset = self._asset_manager.find_by_url(name)
                if asset is not None:
                    remember(asset, name, fmt)
                return name
            asset = self._asset_manager.get(sha)
            if asset is None or not asset.is_uploaded:
                return None
            url = _published_address(asset)
            remember(asset, url, fmt)
            return url

        def as_markdown(fmt) -> Optional[str]:
            url = destination(fmt)
            if not url:
                return None
            if _is_media(self._asset_manager, fmt.name()):
                return url                     # where readers show a player
            return image_markdown(str(fmt.property(QTextImageFormat.ImageAltText) or ""), url)

        def as_note(fmt) -> Optional[str]:
            url = destination(fmt)
            if url and _is_media(self._asset_manager, fmt.name()):
                return url                     # always a line of its own
            # A bare URL welded to the surrounding words is unreadable
            # and unlinkable, so it always keeps its own whitespace.
            return f" {url} " if url else None

        # One writer for every flavor: an article (and its draft) is the
        # Markdown a .md file would hold; a note is the same walk as plain
        # text, which is what apps show for a note.
        target = as_markdown if flavor == "markdown" else as_note
        if getattr(ed, "_markdown_source", False):
            # Opened as its Markdown text: published exactly as written.
            content = serialize_plain_with_images(ed.document(), target).rstrip("\n")
        else:
            content = document_to(ed.document(), flavor, target).rstrip("\n")
        # Audio and video of the account's media library, described from
        # what its media servers confirmed.
        store = getattr(self, "_media_store", None)
        if store is not None:
            for media in library_audio_video_in(store.files, content):
                records.setdefault(media.url, media)
        return content, list(records.values())

    def _publish_text(self, ed, flavor: str) -> str:
        """The content half of :meth:`_publish_payload`."""
        return self._publish_payload(ed, flavor)[0]

    def _loses_content_on_save(self, ed, path: str) -> bool:
        """Whether saving to ``path`` would drop something in the document.

        .md and .Rmd keep images now (remote URL or sidecar), so only
        formatting counts there. .rtf keeps formatting but carries no image.
        Everything else is a plain-text destination, and losing an image
        silently is the worst outcome, so the check applies by default and
        formats opt out rather than in.
        """
        lowered = path.lower()
        if lowered.endswith(('.html', '.htm', '.pdf')):
            return False
        if lowered.endswith('.rtf'):
            return self._has_images(ed)
        if lowered.endswith('.md'):
            # Markdown carries bold, italic, lists, headings and links;
            # only underline and colors stay behind.
            return has_local_only_formatting(ed.document())
        if lowered.endswith('.rmd'):
            return self._has_formatting(ed)
        return self._has_formatting(ed) or self._has_images(ed)

    def _warn_formatting_loss(self, ext_label: str) -> str:
        """Ask before saving to a format that drops content.
        Returns 'anyway', 'html', 'rtf', or 'cancel'."""
        buttons = [
            Button(_("Save as {ext} Anyway").format(ext=ext_label), "anyway", DESTRUCTIVE,
                   _("Formatting, colors and images will be permanently removed "
                     "from the saved file.")),
            Button(_("Save as {ext}").format(ext=".html"), "html", NORMAL,
                   _("Saves all colors, bold, italic and formatting.\n"
                     "Best choice for editing in this editor.")),
        ]
        # Offering .rtf as the escape from an .rtf save would be a loop.
        if ext_label.lower() != ".rtf":
            buttons.append(Button(
                _("Save as {ext}").format(ext=".rtf"), "rtf", NORMAL,
                _("Saves all colors, bold, italic and formatting.\n"
                  "Compatible with Word, LibreOffice and other apps.")))
        buttons.append(Button(_("Cancel"), "cancel", DEFAULT))
        return ask(
            self, title=_("Save as {ext} without formatting?").format(ext=ext_label),
            message=_("This document has colors, text formatting or images "
                      "that {ext} can't store.").format(ext=ext_label),
            buttons=buttons, caution=True)

    def save(self) -> bool:
        if self.current_pdf_viewer() is not None:
            self.status.showMessage(_("PDFs open read-only; there is nothing to save."), 3000)
            return True
        path = self.current_path()
        if not path:
            # No local file backs this tab. If it's a draft tab (opened
            # from the drafts panel, or stashed and never disk-saved),
            # the user pressing Ctrl+S means "save what I'm working on"
            # - i.e. re-stash with the same kind + d-tag, no dialogs.
            # Otherwise fall through to the standard "Save As" prompt.
            ed = self.current_editor()
            binding = getattr(ed, "_draft_binding", None) if ed else None
            if binding is not None and self._has_active_nostr_profile():
                if self._is_stash_in_flight_for(ed):
                    self._stash_already_running_message()
                    return True
                # On Ctrl+S we never silently fork: if the binding
                # belongs to a different profile, the user has to make
                # an intentional choice. We don't offer "save a copy"
                # here because Ctrl+S is meant to mean "save what I
                # have" - silently changing identity violates that.
                mismatch_pk = self._draft_binding_profile_mismatch(ed)
                if mismatch_pk is not None:
                    outcome = self._resolve_mismatch(ed, mismatch_pk, allow_fork=False)
                    if outcome == "cancel":
                        return False
                    if outcome == "switch":
                        if not self._switch_active_profile_to(mismatch_pk):
                            inform(
                                self, title=_("That account isn\u2019t signed in anymore"),
                                message=_("Connect it again with Nostr > Connect Signer "
                                          "App\u2026 to save here."))
                            return False
                self._restash_with_binding(ed, binding)
                return True
            return self.save_as()
        ed = self.current_editor()
        if ed and self._loses_content_on_save(ed, path):
            result = self._warn_formatting_loss(os.path.splitext(path)[1])
            if result == 'cancel':
                return False
            elif result == 'html':
                return self.save_as(self._suggest_path(path, '.html'), ".html (*.html)")
            elif result == 'rtf':
                return self.save_as(self._suggest_path(path, '.rtf'), ".rtf (*.rtf)")
        return self._save_to(path)

    def _suggest_path(self, current_path: str, new_ext: str) -> str:
        """Return current_path with its extension replaced by new_ext."""
        return os.path.splitext(current_path)[0] + new_ext

    def save_as(self, initial_path: str = "", initial_filter: str = "") -> bool:
        path, selected_filter = QFileDialog.getSaveFileName(
            self, _("Save As"), initial_path,
            ".txt (*.txt);; .html (*.html);; .pdf (*.pdf);; .md (*.md);; .rtf (*.rtf);; .Rmd (*.Rmd)",
            initial_filter
        )
        if not path:
            return False
        # Qt's dialog answers with forward slashes, also on Windows.
        path = file_paths.normalize(path)

        # Auto-add extension if the user didn't type one
        if not any(path.lower().endswith(e) for e in _SAVE_EXTS):
            ext = _extension_from_filter(selected_filter)
            if ext:
                path += ext

        # Warn if saving to a format that loses formatting
        ed = self.current_editor()
        if ed and self._loses_content_on_save(ed, path):
            result = self._warn_formatting_loss(os.path.splitext(path)[1])
            if result == 'cancel':
                return False
            elif result == 'html':
                return self.save_as(self._suggest_path(path, '.html'), ".html (*.html)")
            elif result == 'rtf':
                return self.save_as(self._suggest_path(path, '.rtf'), ".rtf (*.rtf)")

        old_path = self.current_path()
        ok = self._save_to(path)
        if ok:
            if old_path and old_path != path:
                self._watcher.removePath(old_path)
            self._watcher.addPath(path)
            self.set_current_path(path)
            ed = self.current_editor()
            if ed:
                self._attach_highlighter(ed, path)
                self._update_status_bar()
            if ed and hasattr(ed, '_backup'):
                ed._backup.update_file_path(path)
            add_recent(path)
            self._populate_recent_menu()
            self._update_knit_actions()
        return ok

    def _save_to(self, path: str) -> bool:
        ed = self.current_editor()
        if not ed:
            return False

        # Suppress the watcher trigger caused by our own write
        self._saving_paths.add(path)
        QTimer.singleShot(500, lambda: self._saving_paths.discard(path))

        ext = path.lower()
        try:
            if ext.endswith('.pdf'):
                return self._save_as_pdf(ed, path)
            elif ext.endswith('.rtf'):
                return self._save_as_rtf(ed, path)

            if ext.endswith(('.html', '.htm')):
                content = document_to_html(
                    ed.document(),
                    title=self._export_title_for(path),
                    image_roots=self._image_roots_for(path),
                    asset_resolver=self._asset_manager.export_view,
                )
            elif ext.endswith('.rmd'):
                content = self._to_rmd_content(ed, path)
            elif ext.endswith('.md'):
                # The same Markdown an article publishes, whether the tab
                # came from a .md file or was typed here.
                target = self._image_target_for_file_save(ed.document(), path)
                reference = self._markdown_reference_for(target)
                if getattr(ed, "_markdown_source", False):
                    content = serialize_plain_with_images(ed.document(), reference)
                else:
                    content = document_to_markdown(ed.document(), reference)
                content = self._with_front_matter(ed, content)
            else:
                # .txt and anything unknown: images cannot be carried, so
                # they are omitted rather than left as the raw U+FFFC
                # placeholder the old toPlainText call wrote out.
                content = serialize_plain_with_images(ed.document(), lambda fmt: None)

            save_text_document(path, content, newline=getattr(ed, "_newline", None))
            ed._recovered_title = ""
            ed.document().setModified(False)
            self._update_tab_title()
            self._say_saved(ed, path)
            return True

        except Exception as e:
            inform(self, title=_("Couldn't save \u201c{name}\u201d").format(
                       name=os.path.basename(path)),
                   message=f"{e}\n\n{_SAVE_FAILED_HINT}", caution=True)
            return False

    def _say_saved(self, ed, path: str) -> None:
        """Say a document was saved, and whether audio or video in it is
        still to go in. A Markdown, HTML or PDF file holds such a file once
        it is on a media server (its address, or a link to it): until then the save writes what it can, and the
        document is edited again when the upload is done
        (_on_media_uploaded), as Pages does when an inserted item finishes
        loading after a save."""
        waiting = (self._media_not_uploaded(ed.document())
                   if path.lower().endswith((".md", ".html", ".htm", ".pdf")) else [])
        ed._media_left_out = {asset.sha256 for asset in waiting}
        if not waiting:
            self.status.showMessage(_("Saved: {path}").format(path=path))
            return
        self.status.showMessage(ngettext(
            "Saved. “{name}” goes into the file once it is uploaded.",
            "Saved. {count} files go into the file once they are uploaded.",
            len(waiting)).format(name=waiting[0].alt, count=len(waiting)))

    def _on_media_uploaded(self, sha: str) -> None:
        """Audio or video a save had to leave out is uploaded: the documents
        saved without it are edited again, so Close and Quit ask."""
        asset = self._asset_manager.get(sha)
        if asset is None or not asset.is_media or not asset.is_uploaded:
            return
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            left_out = getattr(ed, "_media_left_out", set()) if ed is not None else set()
            if sha in left_out:
                left_out.discard(sha)
                ed.document().setModified(True)
                self.status.showMessage(
                    _("“{name}” is uploaded. Save the document to put it in.").format(
                        name=asset.alt))

    def _with_front_matter(self, ed, body: str) -> str:
        """A Markdown file's text: ``body`` under its front matter (Q-F), as
        written but for the details changed here; none when there are no
        details and it had none."""
        front = front_matter_with(self._article_details_of(ed),
                                  getattr(ed, "_front_matter", None))
        ed._front_matter = front
        return front_matter.join(front, body)

    def _on_file_changed(self, path: str):
        if path in self._saving_paths:
            return

        # Re-add path: some OS implementations remove it after a change event
        if os.path.exists(path):
            self._watcher.addPath(path)

        # A regenerated PDF reloads in place: the viewer holds no user
        # edits, so there is nothing to protect behind a prompt.
        for i in range(self.tabs.count()):
            viewer = self._pdf_viewer_from_widget(self.tabs.widget(i))
            if viewer is not None and viewer._file_path == path:
                viewer.schedule_reload()
                return

        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            ed = self._editor_from_widget(w)
            if ed and getattr(ed, '_file_path', None) == path:
                bar = self._bar_from_widget(w)
                if bar:
                    if os.path.exists(path):
                        bar.show_changed(ed.document().isModified())
                    else:
                        bar.show_deleted()
                break

    def _reload_from_disk(self, ed, bar: FileChangedBar):
        path = getattr(ed, '_file_path', None)
        if not path or not os.path.exists(path):
            return
        try:
            content, newline = read_text_document(path)
        except (OSError, ValueError) as e:
            inform(self, title=_("Couldn't reload \u201c{name}\u201d").format(
                       name=os.path.basename(path)),
                   message=str(e))
            return
        ed._newline = newline

        self._set_editor_content(ed, path, content)
        self._attach_highlighter(ed, path)
        ed._recovered_title = ""

        ed.document().setModified(False)
        bar.hide()
        self._update_tab_title()

    def _open_external(self, url: str) -> None:
        """Hand a URL to the desktop browser, if it is one.

        Release metadata arrives as JSON from the network, so the check
        belongs on this side of it rather than at each call site.
        """
        if url_safety.is_safe_external_url(url):
            QDesktopServices.openUrl(QUrl(url))
        else:
            self.status.showMessage(_("That link cannot be opened."), 5000)

    def _export_title_for(self, path: str) -> str:
        """Document title for export metadata: the filename without extension."""
        return os.path.splitext(os.path.basename(path))[0] or _("Untitled")

    def _blob_cache_root(self) -> str:
        """Directory holding the content-addressed image cache."""
        return str(self._media_image_loader.cache_path("x").parent)

    def _image_roots_for(self, save_path: str) -> tuple:
        """Directories an exporter may read local image bytes from.

        The document's own directory, because sidecar media beside a
        file is this app's own pattern, plus the blob cache, because a
        document written by an older build names its images by their
        absolute cache path.
        """
        doc_dir = os.path.dirname(os.path.abspath(save_path))
        return (doc_dir, self._blob_cache_root())

    def _markdown_reference_for(self, target):
        """Wrap a destination function so plain text gets ![alt](dest)."""
        def reference(fmt):
            destination = target(fmt)
            if not destination:
                return None
            if _is_media(self._asset_manager, fmt.name()):
                return destination             # audio or video: its address
            return image_markdown(str(fmt.property(QTextImageFormat.ImageAltText) or ""),
                                  destination)

        return reference

    def _image_target_for_file_save(self, doc, save_path: str):
        """Where each image should point once written to ``save_path``.

        An uploaded asset serializes to its validated remote URL. An
        asset that is not uploaded is copied into a sidecar folder
        beside the document, so a .md file carries its own pictures
        without the app running. Media this app did not create is left
        exactly as it was found.
        """
        media_dir = media_dir_for(save_path)
        written: dict = {}

        def sidecar(sha: str, data: bytes, ext: str) -> Optional[str]:
            # Hex names only: a markdown destination containing a space
            # is destroyed by Qt's own parser when the file is reopened.
            name = f"{sha}{ext}"
            if name in written:
                return written[name]
            try:
                os.makedirs(media_dir, exist_ok=True)
                save_document(os.path.join(media_dir, name), data)
            except OSError:
                return None
            written[name] = f"{os.path.basename(media_dir)}/{name}"
            return written[name]

        def target(fmt) -> Optional[str]:
            name = fmt.name()
            sha = parse_asset_key(name)
            if sha is None:
                return self._foreign_image_target(name, sidecar)

            asset = self._asset_manager.get(sha)
            if asset is not None and asset.is_uploaded:
                return _published_address(asset)
            if asset is not None and asset.is_media:
                # Not on a media server yet: nothing to write (never a
                # placeholder); saved again once it is, it is there.
                return None

            data = self._asset_manager.resolve_bytes(name)
            if data:
                ext = sniff_image_ext(data) or ".png"
                written_path = sidecar(sha, data, ext)
                if written_path:
                    return written_path
            else:
                # The cache lost the bytes but the open document still
                # holds the decoded image; re-encoding it is lossy for
                # nothing else but keeps the picture.
                rescued = self._encode_document_image(doc, name)
                if rescued is not None:
                    written_path = sidecar(sha, rescued, ".png")
                    if written_path:
                        return written_path
            if asset is not None and asset.remote_url:
                return asset.remote_url
            # An honest dangling relative reference: repairable by hand,
            # and never a machine-local cache path or an internal key.
            ext = ".png"
            return f"{os.path.basename(media_dir)}/{sha}{ext}"

        return target

    def _foreign_image_target(self, name: str, sidecar):
        """Destination for an image this app did not create.

        Everything passes through verbatim: a URL, and equally the
        author's own local layout, because another editor's
        "pics/dog.png" is the document's arrangement and rewriting it on
        a text-only save would duplicate the file and destroy the
        reference (AD-4, I3).

        The one exception is a name pointing into this app's own blob
        cache, which older builds wrote into documents. That path is
        machine-local, so it cannot travel with the file and its bytes
        move into the sidecar folder beside the document instead.
        """
        if image_safety.is_portable_image_source(name):
            return name
        if not (os.path.isabs(name) or name.lower().startswith("file:")):
            # Only an absolute name can be shown to point at the cache;
            # a relative one would be resolved against it by guesswork.
            return name
        policy = image_safety.ImageRootPolicy((self._blob_cache_root(),))
        data = policy.read(name)
        if not data:
            return name
        ext = sniff_image_ext(data)
        if ext is None:
            return name
        return sidecar(hashlib.sha256(data).hexdigest(), data, ext) or name

    def _encode_document_image(self, doc, name: str) -> Optional[bytes]:
        """PNG bytes for an image the document still holds in memory."""
        resource = doc.resource(QTextDocument.ImageResource, QUrl(name))
        if isinstance(resource, QByteArray):
            return bytes(resource)
        if isinstance(resource, QPixmap):
            resource = resource.toImage()
        if not isinstance(resource, QImage) or resource.isNull():
            return None
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        if not resource.save(buf, "PNG"):
            return None
        return bytes(buf.data())

    def _to_rmd_content(self, ed, path: str) -> str:
        """Content for a .Rmd save: source passthrough or rich conversion.

        A tab that came from an .Rmd file (including one restored by crash
        recovery, which loses the flag but keeps the path) is already
        source. A plain tab whose text already starts with a YAML fence is
        treated as source too, so raw Rmd typed into a new tab is not
        double-wrapped. Everything else is converted with frontmatter.
        """
        origin = (getattr(ed, "_file_path", "") or "").lower()
        # R Markdown is markdown, so a pasted image serializes to real
        # ![alt](sidecar) syntax here. Reading the source out as plain text
        # would drop it, leaving a bare U+FFFC for pandoc to choke on.
        target = self._image_target_for_file_save(ed.document(), path)
        picture = self._markdown_reference_for(target)

        def reference(fmt):
            media = _media_asset(self._asset_manager, fmt.name())
            if media is None:
                return picture(fmt)
            link = self._media_file_link(media, path)
            return link.markdown() if link is not None else None

        source = serialize_plain_with_images(ed.document(), reference)
        if getattr(ed, "_loaded_as_rmd_source", False) or origin.endswith(".rmd"):
            return source
        if source.lstrip().startswith("---") and not self._has_formatting(ed):
            return source
        title = derive_title(ed.document(), self._export_title_for(path))
        return document_to_rmd(ed.document(), title,
                               copy_image=self._make_rmd_image_copier(path))

    def _make_rmd_image_copier(self, rmd_path: str):
        """Copy images into the .Rmd's sidecar media folder.

        Returns a callback mapping a local cache path to a relative
        markdown reference like "notes_media/<sha>.png". rmarkdown's
        html_document embeds these into the knitted output, so the
        rendered artifact stays a single shareable file.
        """
        media_dir = media_dir_for(rmd_path)
        policy = image_safety.ImageRootPolicy(self._image_roots_for(rmd_path))

        def copy(name: str):
            media = _media_asset(self._asset_manager, name)
            if media is not None:
                return self._media_file_link(media, rmd_path)
            sha = parse_asset_key(name)
            if sha is not None:
                data = self._asset_manager.resolve_bytes(name)
                if not data:
                    return None
                base = sha
            elif image_safety.is_portable_image_source(name):
                return name  # foreign media travels as it arrived
            else:
                # A document names the files it embeds and a document
                # can be hostile, so these bytes come through the same
                # trusted roots the other exporters read from. A name
                # outside them is dropped rather than passed along:
                # knitting with self_contained would hand the very read
                # this refused to pandoc instead.
                data = policy.read(name)
                if not data:
                    return None
                base = os.path.basename(name)
            ext = sniff_image_ext(data)
            if ext is None:
                return None
            filename = base + ext
            os.makedirs(media_dir, exist_ok=True)
            target = os.path.join(media_dir, filename)
            try:
                save_document(target, data)
            except OSError:
                return None
            return f"{os.path.basename(media_dir)}/{filename}"

        return copy

    def _media_file_link(self, asset, save_path: str) -> Optional[MediaLink]:
        """An audio or video file as a link from the document saved at
        ``save_path``: to its address once uploaded, else to a copy of it
        in the document's media folder, as the document's pictures are."""
        label = asset.alt or f"{asset.sha256}{media_files.extension_for(asset.mime)}"
        if asset.is_uploaded:
            return MediaLink(label, _published_address(asset))
        data = self._asset_manager.resolve_bytes(asset.key)
        if not data:
            return None
        media_dir = media_dir_for(save_path)
        name = asset.sha256 + media_files.extension_for(asset.mime)
        try:
            os.makedirs(media_dir, exist_ok=True)
            save_document(os.path.join(media_dir, name), data)
        except OSError:
            return None
        return MediaLink(label, f"{os.path.basename(media_dir)}/{name}")

    def _save_as_rtf(self, editor, path: str) -> bool:
        try:
            content = self._to_rtf(editor)
            save_text_document(path, content, encoding="ascii",
                               newline=getattr(editor, "_newline", None))
            editor._recovered_title = ""
            editor.document().setModified(False)
            self._update_tab_title()
            self.status.showMessage(_("Saved: {path}").format(path=path))
            return True
        except Exception as e:
            inform(self, title=_("Couldn't save \u201c{name}\u201d").format(
                       name=os.path.basename(path)),
                   message=f"{e}\n\n{_SAVE_FAILED_HINT}", caution=True)
            return False

    def _to_rtf(self, editor) -> str:
        """Convert QTextDocument to an RTF string.

        Walks the document block by block, fragment by fragment.
        Each fragment becomes an RTF group carrying its formatting codes.
        Non-ASCII characters are escaped as \\uN? (RTF Unicode escape).
        """
        doc = editor.document()

        # --- Pass 1: collect all unique foreground colors used in the document ---
        colors: list[tuple[int, int, int]] = []
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fmt = it.fragment().charFormat()
                if fmt.hasProperty(QTextCharFormat.ForegroundBrush):
                    c = fmt.foreground().color()
                    rgb = (c.red(), c.green(), c.blue())
                    if rgb not in colors:
                        colors.append(rgb)
                it += 1
            block = block.next()

        # --- RTF header ---
        # \colortbl: entry 0 is implicit default; custom colors start at index 1.
        color_table = "{\\colortbl;" + "".join(
            f"\\red{r}\\green{g}\\blue{b};" for r, g, b in colors
        ) + "}"

        parts = [
            "{\\rtf1\\ansi\\deff0\n",
            "{\\fonttbl{\\f0\\fmodern\\fcharset0 Courier New;}}\n",
            color_table + "\n",
            "\\f0\\fs28\n",  # Courier New 14 pt  (RTF uses half-points)
        ]

        # --- Pass 2: emit content ---
        block = doc.begin()
        first_block = True
        while block.isValid():
            if not first_block:
                parts.append("\\par\n")
            first_block = False

            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if fmt.isImageFormat():
                    # RTF image support is out of scope; emitting the
                    # fragment text would write an escaped U+FFFC into
                    # the file, which is visible junk in every reader.
                    it += 1
                    continue

                # Escape RTF special chars and non-ASCII
                escaped = []
                for ch in frag.text():
                    if ch == "\\":
                        escaped.append("\\\\")
                    elif ch == "{":
                        escaped.append("\\{")
                    elif ch == "}":
                        escaped.append("\\}")
                    elif ord(ch) > 127:
                        escaped.append(f"\\u{ord(ch)}?")
                    else:
                        escaped.append(ch)
                text = "".join(escaped)

                # Build format prefix (all codes go inside a single RTF group
                # so they reset automatically at the closing brace)
                codes = ""
                if fmt.fontWeight() > 400:
                    codes += "\\b "
                if fmt.fontItalic():
                    codes += "\\i "
                if fmt.fontUnderline():
                    codes += "\\ul "
                if fmt.hasProperty(QTextCharFormat.ForegroundBrush):
                    c = fmt.foreground().color()
                    idx = colors.index((c.red(), c.green(), c.blue())) + 1
                    codes += f"\\cf{idx} "

                parts.append(f"{{{codes}{text}}}" if codes else text)
                it += 1

            block = block.next()

        parts.append("\n}")
        return "".join(parts)

    def _save_as_pdf(self, editor, path: str) -> bool:
        """Export via the native Qt PDF pipeline (export_pdf.py): document
        metadata, persisted page setup, page-number footer, images capped
        to the printable width."""
        try:
            export_pdf(editor.document(), path,
                       title=self._export_title_for(path),
                       image_roots=self._image_roots_for(path),
                       asset_resolver=self._asset_manager.export_view)
            editor._recovered_title = ""
            editor.document().setModified(False)
            self._update_tab_title()
            self._say_saved(editor, path)
            return True
        except Exception as e:
            inform(self, title=_("Couldn't save \u201c{name}\u201d").format(
                       name=os.path.basename(path)),
                   message=f"{e}\n\n{_SAVE_FAILED_HINT}", caution=True)
            return False

    # ----------------------------------------------------------------------
    # PAGE SETUP + R MARKDOWN KNITTING
    # ----------------------------------------------------------------------
    def _on_page_setup(self):
        PageSetupDialog(is_dark=self.is_dark_theme, parent=self).exec()

    # ----------------------------------------------------------------------
    # PRINT
    # ----------------------------------------------------------------------
    def _update_print_actions(self):
        """Dim Print when the current tab has nothing to print."""
        can_print = self._print_job() is not None
        self.act_print.setEnabled(can_print)
        self.act_print_preview.setEnabled(can_print)

    def _printer(self):
        """One printer for the window's lifetime, so the chosen printer and
        its options carry over to the next print."""
        if getattr(self, "_print_device", None) is None:
            self._print_device = printing.make_printer()
        return self._print_device

    def _print_job(self):
        """What Print prints for the current tab: ``(title, paint)``, where
        ``paint(printer)`` prints and returns the page count. None when
        the tab has nothing to print."""
        viewer = self.current_pdf_viewer()
        if viewer is not None:
            return viewer.document_title(), lambda printer: printing.print_pdf(
                viewer.document, printer)
        ed = self.current_editor()
        if ed is None:
            return None
        path = getattr(ed, "_file_path", None)
        title = self._export_title_for(path) if path else _("Untitled")
        roots = self._image_roots_for(path) if path else (self._blob_cache_root(),)
        return title, lambda printer: printing.print_document(
            ed.document(), printer, image_roots=roots,
            asset_resolver=self._asset_manager.export_view)

    def _on_print(self):
        job = self._print_job()
        if job is None:
            return
        title, paint = job
        printer = self._printer()
        printing.prepare(printer, title, load_page_setup())
        # The platform's own dialog: printer, pages, copies, and on macOS
        # the preview.
        if QPrintDialog(printer, self).exec() != QDialog.Accepted:
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pages = paint(printer)
        except printing.PrintError as exc:
            QGuiApplication.restoreOverrideCursor()
            inform(self, title=_("Couldn't print"),
                   message=_("{error} Check that it's turned on and connected, then try "
                             "again.").format(error=exc))
            return
        QGuiApplication.restoreOverrideCursor()
        if pages == 0:
            self.status.showMessage(
                _("Nothing printed: those pages aren't in the document."), 6000)
            return
        where = printer.outputFileName() or printer.printerName()
        if where:
            sent = ngettext("Sent {count} page to {where}.", "Sent {count} pages to {where}.",
                            pages).format(count=pages, where=where)
        else:
            sent = ngettext("Sent {count} page to the printer.",
                            "Sent {count} pages to the printer.", pages).format(count=pages)
        self.status.showMessage(sent, 6000)

    def _on_print_preview(self):
        """File > Print Preview (Windows and Linux; macOS previews in its
        print panel). The preview's own Print button prints for real."""
        job = self._print_job()
        if job is None:
            return
        title, paint = job
        printer = self._printer()
        printing.prepare(printer, title, load_page_setup())
        preview = QPrintPreviewDialog(printer, self)
        preview.setWindowTitle(_("Print Preview"))
        preview.paintRequested.connect(paint)
        preview.exec()

    def _on_rmd_toolchain(self):
        RmdSetupDialog(is_dark=self.is_dark_theme, parent=self).exec()

    def _current_rmd_path(self) -> Optional[str]:
        ed = self.current_editor()
        path = getattr(ed, '_file_path', None) if ed else None
        if path and path.lower().endswith('.rmd'):
            return path
        return None

    def _update_knit_actions(self):
        if not hasattr(self, 'act_knit_html'):
            return
        busy = hasattr(self, '_knit_runner') and self._knit_runner.busy
        available = self._current_rmd_path() is not None and not busy
        self.act_knit_html.setEnabled(available)
        self.act_knit_pdf.setEnabled(available)

    def _ensure_knit_runner(self) -> KnitRunner:
        if not hasattr(self, '_knit_runner'):
            self._knit_runner = KnitRunner(self)
            self._knit_runner.started.connect(self._on_knit_started)
            self._knit_runner.finished.connect(self._on_knit_done)
            self._knit_runner.failed.connect(self._on_knit_failed)
        return self._knit_runner

    def _on_knit(self, fmt: str):
        ed = self.current_editor()
        if ed is None:
            return
        path = self._current_rmd_path()
        if path is None:
            # Knitting renders the file on disk, so the tab must become an
            # .Rmd file first.
            if not self.save_as("", ".Rmd (*.Rmd)"):
                return
            path = self._current_rmd_path()
            if path is None:
                return
        elif ed.document().isModified():
            # Auto-save before knitting, RStudio-style. It goes through the
            # same guard as an explicit save, so an automatic step can never
            # discard more than a deliberate one would.
            if self._loses_content_on_save(ed, path):
                if self._warn_formatting_loss(os.path.splitext(path)[1]) == 'cancel':
                    return
            if not self._save_to(path):
                return
        runner = self._ensure_knit_runner()
        if runner.busy:
            return
        if not rmd_toolchain.ready_to_knit(to_pdf=(fmt == "pdf")):
            dlg = RmdSetupDialog(is_dark=self.is_dark_theme, parent=self,
                                 want_pdf=(fmt == "pdf"))
            if dlg.exec() != QDialog.Accepted or not dlg.succeeded:
                return
        runner.knit(path, fmt)

    def _on_knit_started(self):
        self.status.showMessage(_("Knitting…"))
        self._update_knit_actions()

    def _on_knit_done(self, output_path: str):
        self.status.showMessage(_("Knit complete: {path}").format(path=output_path), 8000)
        # Not gated: this path is the output of our own knit run, not a
        # name that came from a document or the network.
        QDesktopServices.openUrl(QUrl.fromLocalFile(output_path))
        self._update_knit_actions()

    def _on_knit_failed(self, kind: str, detail: str):
        self._update_knit_actions()
        installable = {"missing-r", "missing-rmarkdown", "missing-pandoc",
                       "missing-latex"}
        if kind in installable:
            # Route straight into the setup dialog, which shows what is
            # missing and can install it from the official source.
            dlg = RmdSetupDialog(is_dark=self.is_dark_theme, parent=self,
                                 want_pdf=(kind == "missing-latex"))
            dlg.exec()
            return
        inform(self, title=_("Couldn't knit the document"),
               message=_("R Markdown stopped with an error. The details show what it "
                         "reported."),
               details=detail)
        self.status.showMessage(_("Knit failed."), 5000)

    # ----------------------------------------------------------------------
    # SEARCH
    # ----------------------------------------------------------------------
    def _show_find(self, replace: bool = False):
        """Find… or Find and Replace…: the find bar, its field focused
        (Find and Replace adds the Replace row). In a PDF tab, the PDF
        reader's own find bar. With the Imports window in front, its
        search field."""
        imports = self._imports_in_front()
        if imports is not None:
            imports.focus_search()
            return
        viewer = self.current_pdf_viewer()
        if viewer is not None:
            if viewer.findbar.isVisible():
                viewer.findbar.focusIn()
            else:
                viewer.toggle_findbar()
            return
        if self.current_editor() is None:
            return
        self.findbar.show_replace(replace or (self.findbar.isVisible()
                                              and self.findbar.replace_shown()))
        self.findbar.setVisible(True)
        self.findbar.focusIn()
        self._last_search_text = None
        self._on_search_text_changed()

    def _close_findbar(self):
        """Done or Escape: the bar goes, the highlights with it, and the
        writing goes on where the match was."""
        self.findbar.setVisible(False)
        self.findbar.set_match_info("")
        self._clear_search_highlights()
        ed = self.current_editor()
        if ed:
            cursor = ed.textCursor()
            cursor.setPosition(cursor.selectionStart())
            ed.setTextCursor(cursor)
            ed.setFocus()

    def _on_find_options_changed(self):
        self._last_search_text = None
        self._on_search_text_changed()

    def _on_document_changed_for_find(self, ed):
        if ed is self.current_editor() and self.findbar.isVisible():
            self._find_refresh.start()

    def _refresh_matches(self):
        if not self.findbar.isVisible():
            return
        self._last_search_text = None
        self._on_search_text_changed()

    def _replace_current(self):
        """Replace: the match that is selected becomes the replacement (in
        its style), and the next match is selected. With no match
        selected, the next one is found first."""
        ed = self.current_editor()
        needle = self.findbar.text()
        if ed is None or not needle:
            return
        self._update_search_matches(needle)
        self._last_search_text = needle
        cursor = ed.textCursor()
        selected = (cursor.selectionStart(), cursor.selectionEnd())
        if selected in self._search_matches:
            # No highlight cursors for the document to move while it changes.
            ed.set_highlights("find", [])
            with ed.undo_step(N_("Replace")):
                _start, end = replace_match(ed.document(), selected,
                                            self.findbar.replacement())
            cursor.setPosition(end)
            ed.setTextCursor(cursor)
            self._find_refresh.stop()          # found again right here
            self._update_search_matches(needle)
            self._last_search_text = needle
            if not self._search_matches:
                self._highlight_all_matches()
                self._update_match_display()
                return
        # The next match after the caret (after the replacement).
        self._find_once(True)

    def _replace_all(self):
        """Replace All: every match, as one step to undo."""
        ed = self.current_editor()
        needle = self.findbar.text()
        if ed is None or not needle:
            return
        # Every highlight holds a cursor the document would move on each of
        # the replacements: take them away first (thousands of matches made
        # Replace All take minutes).
        ed.set_highlights("find", [])
        with ed.undo_step(N_("Replace All")):
            count = replace_all(ed.document(), needle, self.findbar.replacement(),
                                self.findbar.options())
        self._find_refresh.stop()              # found again right here
        self._update_search_matches(needle)
        self._last_search_text = needle
        self._highlight_all_matches()
        if count:
            self.findbar.set_match_info(
                ngettext("{count} replaced", "{count} replaced", count).format(
                    count=_number(count)))
        else:
            self.findbar.set_match_info(_("No matches"))

    def _choose_language(self, code: str, tell: bool = True) -> None:
        """Remember the language; it is used from the next start on. View >
        Language says so in an alert; Settings (``tell`` False) says it
        under its choice."""
        save_setting(i18n.SETTING, code)
        for known, action in zip(self._language_codes, self.act_languages):
            action.setChecked(known == code)
        target = i18n.resolve(code)
        if target == i18n.language() or not tell:
            return
        name = i18n.LANGUAGE_NAMES.get(target, target)
        inform(self, title=_("Restart MyEditor to Change the Language"),
               message=_("MyEditor will be in {name} the next time you open it.").format(
                   name=name),
               is_dark=self.is_dark_theme)

    # -- a Mac's Window menu ----------------------------------------------------------

    def _front_window(self):
        """The window in front: this one, Settings, the Imports window…"""
        return QApplication.activeWindow() or self

    def _zoom_front_window(self) -> None:
        window = self._front_window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    def _bring_all_to_front(self) -> None:
        """Every open window of MyEditor in front of other apps' windows,
        the one in front staying in front."""
        front = QApplication.activeWindow()
        for window in QApplication.topLevelWidgets():
            if window.isVisible() and not window.isMinimized() and window is not front:
                window.raise_()
        if front is not None:
            front.raise_()

    def _show_settings(self) -> None:
        """The Settings window: one, brought to the front when it is open."""
        window = getattr(self, "_settings_window", None)
        if window is None:
            window = SettingsWindow(
                appearance=self.act_appearance,
                languages=list(zip(self._language_codes, self.act_languages)),
                choose_language=lambda code: self._choose_language(code, tell=False),
                monospace=self.act_monospace_writing,
                paste_choice=self._paste_choice,
                choose_paste=self._store_paste_choice,
                nostr_in_use=lambda: self.nostr_state.active,
                is_dark=self.is_dark_theme, parent=self)
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            window.destroyed.connect(lambda _obj=None: setattr(self, "_settings_window", None))
            self._settings_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _show_shortcuts(self):
        """Help > Keyboard Shortcuts: a reference window beside the
        document, not modal, so a key can be tried while it is open; one
        window, brought to the front when it is already open."""
        window = getattr(self, "_shortcuts_window", None)
        if window is None:
            from shortcuts_dialog import ShortcutsDialog
            window = ShortcutsDialog(self._shortcut_groups(), is_dark=self.is_dark_theme,
                                     parent=self)
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            window.destroyed.connect(lambda _obj=None: setattr(self, "_shortcuts_window", None))
            self._shortcuts_window = window
        window.show()
        window.raise_()
        window.activateWindow()

    def _shortcut_groups(self):
        """The keys Help > Keyboard Shortcuts lists. Nostr shortcuts are
        listed while an account is in use; before that the editor shows
        nothing of it beyond the Nostr menu."""
        from shortcuts_dialog import OTHER_KEYS, groups_from
        return groups_from(self.commands.shortcut_groups(
            OTHER_KEYS, nostr=self.nostr_state.active))

    def _show_about(self):
        answer = ask(
            self, title=APP_DISPLAY_NAME,
            message=_("Version {version}\nA minimal distraction-free text editor.\n"
                      "Built by rinbal.").format(version=APP_VERSION),
            buttons=(Button(_("Source Code"), "source", NORMAL),
                     Button(_("OK"), "ok", DEFAULT)))
        if answer == "source":
            self._open_external(APP_URL)

    # ----------------------------------------------------------------------
    # UPDATE CHECK
    # ----------------------------------------------------------------------
    _UPDATE_CHECK_INTERVAL = 24 * 60 * 60  # seconds

    def _maybe_auto_check_for_updates(self):
        """Silent startup check, throttled to once every 24 hours."""
        settings = load_settings()
        last_check = settings.get("last_update_check", 0)
        if time.time() - last_check < self._UPDATE_CHECK_INTERVAL:
            return
        self._auto_update_checker = UpdateChecker(self)
        self._auto_update_checker.update_available.connect(self._on_auto_update_available)
        self._auto_update_checker.check()
        save_setting("last_update_check", time.time())

    def _on_auto_update_available(self, info):
        if load_settings().get("skipped_version") == info.version:
            return
        self._pending_release = info
        self.update_bar.show_update(info.version)

    def _on_update_bar_clicked(self):
        info = getattr(self, "_pending_release", None)
        if info is not None:
            self._show_update_dialog(info)

    def _show_update_dialog(self, info):
        """Software Update: the same steps as the install guide, for this install."""
        self._pending_release = info
        kind = detect_install_kind()
        asset = select_asset(kind, info.assets)
        # Only a file GitHub published a hash for is installed automatically;
        # anything else goes through the guide, where the person downloads it.
        can_self_update = (supports_in_app_update(kind) and asset is not None
                           and bool(getattr(asset, "sha256", "")))
        plan = plan_for(
            kind, info.version, release_url=info.page_url, asset=asset,
            can_self_update=can_self_update)
        installer = UpdateInstaller(kind, self) if plan.mode == AUTOMATIC else None
        dlg = UpdateDialog(
            info.version, APP_VERSION, plan,
            release_url=info.page_url, asset=asset, installer=installer,
            before_restart=self._prepare_workspace_for_update,
            release_notes=getattr(info, "notes", ""),
            is_dark=self.is_dark_theme, parent=self)
        if installer is not None:
            installer.setParent(dlg)   # lives and goes with its dialog
        dlg.skip_requested.connect(self._skip_update_version)
        dlg.link_activated.connect(self._open_external)
        dlg.restart_ready.connect(self._close_for_update)
        dlg.restart_failed.connect(self._discard_update_workspace)
        dlg.finished.connect(dlg.deleteLater)
        self._update_dialog = dlg
        dlg.open()

    def _skip_update_version(self, version):
        save_setting("skipped_version", version)
        self.update_bar.hide()

    def _close_for_update(self):
        # The installer (Windows) or the swap or relaunch helper (AppImage,
        # macOS, .deb) is now running and will open MyEditor again; close so
        # it can replace our files.
        self._closing_for_update = True
        self.close()

    def _show_log_files(self):
        """Help > Show Log Files: the folder with the log, for a bug report."""
        folder = diagnostics.log_folder()
        os.makedirs(folder, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            self.statusBar().showMessage(
                _("The log files are in {folder}").format(folder=folder), 8000)

    def _open_install_guide(self):
        """Help > Installation Help: the web guide, opened on this system."""
        self._open_external(guide_url(detect_install_kind(), machine=platform.machine()))

    # -- keeping the workspace across an update restart ---------------------

    def _prepare_workspace_for_update(self) -> bool:
        """Write down every open tab so the restart brings them back.

        Unsaved and untitled work is kept in its crash-recovery backup,
        written now, so nothing needs to be asked. Only a tab whose backup
        cannot be written (too large, or a disk error) is asked about, by
        name, the way closing it would be: saved, it comes back as its file;
        not saved, as its file on disk, or not at all if it has none. If the
        record itself cannot be written, the restart brings back nothing
        unsaved, so every unsaved document is asked about. Returns False
        when the person cancels.
        """
        capture = workspace_restore.capture_tabs(self)
        for ed in capture.unprotected:
            answer = self._ask_save_before_update(ed)
            if answer == "cancel":
                return False
            if answer == "save" and not self._save_tab_of(ed):
                return False
            capture.settle(ed, saved=answer == "save")
        info = getattr(self, "_pending_release", None)
        tabs, active = capture.tabs()
        ws = Workspace(
            tabs=tabs,
            active=active,
            from_version=APP_VERSION,
            to_version=getattr(info, "version", ""),
            release_notes=getattr(info, "notes", ""),
            release_url=getattr(info, "page_url", ""),
        )
        if not workspace.write_workspace(ws):
            self._update_workspace = None
            return self._confirm_save_before_update()
        self._update_workspace = ws
        return True

    def _discard_update_workspace(self) -> None:
        """The restart did not happen: the record must not reopen anything later."""
        workspace.discard_workspace()
        self._update_workspace = None

    def _ask_save_before_update(self, ed) -> str:
        """Save, Don't Save or Cancel for one document the update restart
        can't bring back by itself; the same question closing its tab asks."""
        path = getattr(ed, "_file_path", None)
        name = os.path.basename(path) if path else _("Untitled")
        return _ask_save_changes(
            self, _("Do you want to save the changes you made to “{name}” "
                    "before updating?").format(name=name),
            message=_("MyEditor couldn’t keep a copy of this document for the restart. "
                      "Your changes will be lost if you don’t save them."))

    def _save_tab_of(self, ed) -> bool:
        """Save the document in ``ed``'s tab, the way Save does."""
        index = self._editor_widget_index(ed)
        if index < 0:
            return False
        self.tabs.setCurrentIndex(index)
        return self.save()

    def _confirm_save_before_update(self):
        """The record of the tabs could not be written, so the restart brings
        back only saved files: ask about every unsaved document. Returns
        True if it is safe to proceed, False if the user cancelled."""
        has_unsaved = False
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed and ed.document().isModified():
                has_unsaved = True
                break
        if not has_unsaved:
            return True
        r = _ask_save_changes(
            self, _("Save your changes before updating?"),
            message=_("MyEditor closes to install the update. Changes you don’t save will be "
                      "lost."))
        if r == "cancel":
            return False
        if r == "save":
            for i in range(self.tabs.count()):
                ed = self._editor_from_widget(self.tabs.widget(i))
                if ed and ed.document().isModified():
                    self.tabs.setCurrentIndex(i)
                    if not self.save():
                        return False
        return True

    def _check_for_updates_manual(self):
        """Help > Check for Updates: always checks, then shows Software Update."""
        self._manual_update_checker = UpdateChecker(self)
        self._manual_update_checker.update_available.connect(self._on_manual_update_available)
        self._manual_update_checker.up_to_date.connect(self._on_manual_up_to_date)
        self._manual_update_checker.failed.connect(self._on_manual_update_failed)
        self._manual_update_checker.check()
        save_setting("last_update_check", time.time())

    def _on_manual_update_available(self, info):
        self._show_update_dialog(info)

    def _on_manual_up_to_date(self):
        inform(
            self, title=_("You're up to date"),
            message=_("{app} {version} is the newest version.").format(
                app=APP_DISPLAY_NAME, version=APP_VERSION),
            is_dark=self.is_dark_theme)

    def _on_manual_update_failed(self, error: str):
        inform(
            self, title=_("Can't check for updates"),
            message=_("{app} couldn't reach GitHub. Check your "
                      "internet connection, then try again.").format(app=APP_DISPLAY_NAME),
            is_dark=self.is_dark_theme)

    def _on_search_text_changed(self):
        needle = self.findbar.text()
        if needle != self._last_search_text:
            self._update_search_matches(needle)
            self._last_search_text = needle
        if needle:
            self._highlight_all_matches()
            self._update_match_display()
        else:
            self._clear_search_highlights()
            self.findbar.set_match_info("")

    def _update_search_matches(self, needle):
        ed = self.current_editor()
        if not ed:
            return
        self._search_matches = find_all(ed.document(), needle, self.findbar.options())
        # The match that is selected stays the current one when the matches
        # are found again (after typing, after a replacement).
        cursor = ed.textCursor()
        selected = (cursor.selectionStart(), cursor.selectionEnd())
        self._current_match_index = (self._search_matches.index(selected)
                                     if selected in self._search_matches else -1)

    def _highlight_all_matches(self):
        """Paint the matches on screen (all of them are counted, but only
        those are painted: each painted one holds a cursor the document
        moves on every edit), the current one in the selection color."""
        ed = self.current_editor()
        if not ed:
            return
        if not self._search_matches:
            ed.set_highlights("find", [])
            return
        match_fmt = QTextCharFormat()
        match_fmt.setBackground(QColor("#FFD700"))
        match_fmt.setForeground(QColor("#000000"))
        current_fmt = QTextCharFormat()
        current_fmt.setBackground(QColor("#264F78" if self.is_dark_theme else "#0078D4"))
        current_fmt.setForeground(QColor("#FFFFFF"))
        first, last = ed.visible_range()
        selections = []
        for index, (start, end) in visible_matches(self._search_matches, first, last):
            cursor = QTextCursor(ed.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = current_fmt if index == self._current_match_index else match_fmt
            selections.append(selection)
        ed.set_highlights("find", selections)

    def _on_visible_area_changed(self, ed) -> None:
        """Scrolled or resized: the matches now on screen get painted."""
        if ed is self.current_editor() and self.findbar.isVisible() and self._search_matches:
            self._highlight_all_matches()

    def _clear_search_highlights(self):
        ed = self.current_editor()
        if ed:
            ed.set_highlights("find", [])

    def _find_once(self, forward=True):
        ed = self.current_editor()
        if not ed:
            return
        needle = self.findbar.text()
        if not needle:
            return
        if needle != self._last_search_text:
            self._update_search_matches(needle)
            self._last_search_text = needle
        if not self._search_matches:
            self.findbar.set_match_info(_("No matches"))
            return

        matches = self._search_matches
        total = len(matches)
        cursor = ed.textCursor()
        selected = (cursor.selectionStart(), cursor.selectionEnd())
        index = self._current_match_index
        if 0 <= index < total and matches[index] == selected:
            # On a match: the one after it (before it, going back).
            index = index + 1 if forward else index - 1
        elif forward:
            # Anywhere else: from the caret, the way every editor searches.
            index = bisect.bisect_left(matches, selected[1], key=lambda match: match[0])
        else:
            index = bisect.bisect_right(matches, selected[0], key=lambda match: match[1]) - 1
        wrapped = not 0 <= index < total
        self._current_match_index = index % total

        if 0 <= self._current_match_index < len(self._search_matches):
            start_pos, end_pos = self._search_matches[self._current_match_index]
            cursor = QTextCursor(ed.document())
            cursor.setPosition(start_pos)
            cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
            ed.setTextCursor(cursor)
            ed.ensureCursorVisible()

        self._highlight_all_matches()
        self._update_match_display()
        if wrapped:
            self.findbar.set_match_info(
                _("Wrapped · {matches}").format(matches=self.findbar.match_info.text()))
            QTimer.singleShot(1200, self._update_match_display)

    def _update_match_display(self):
        if not self._search_matches:
            self.findbar.set_match_info(_("No matches"))
            return
        total = len(self._search_matches)
        if self._current_match_index < 0:
            self.findbar.set_match_info(
                ngettext("{count} match", "{count} matches", total).format(count=total))
            return
        current = self._current_match_index + 1
        self.findbar.set_match_info(_("{current} of {total}").format(
            current=current, total=total))

    def _find_next(self):
        viewer = self.current_pdf_viewer()
        if viewer is not None:
            viewer.find_next()
            return
        self._find_once(True)

    def _find_prev(self):
        viewer = self.current_pdf_viewer()
        if viewer is not None:
            viewer.find_prev()
            return
        self._find_once(False)


    # ----------------------------------------------------------------------
    # FORMAT HANDLERS
    # ----------------------------------------------------------------------
    def _fmt_bold(self):
        self._toggle_format('bold')

    def _fmt_italic(self):
        self._toggle_format('italic')

    def _fmt_underline(self):
        self._toggle_format('underline')

    def _reset_format(self):
        ed = self.current_editor()
        if ed:
            ed.reset_to_default()
            self._update_format_buttons()

    # Commands that act on this window's document: dimmed while another
    # window is in front, as a Mac app dims what the key window cannot do.
    _DOCUMENT_COMMANDS = frozenset({
        "file.save", "file.save_as", "file.page_setup", "file.print", "file.print_preview",
        "file.knit_html", "file.knit_pdf", "search.replace", "search.next",
        "search.previous", "search.use_selection",
        "view.line_numbers", "view.syntax_highlighting", "view.paper_mode", "view.toolbar",
        "view.highlight_line", "view.write", "view.preview", "view.split",
        "edit.footnote_jump", "nostr.publish_note", "nostr.publish_article",
        "nostr.insert_image", "nostr.insert_mention", "nostr.insert_reference",
        "nostr.insert_audio_video"})
    _DOCUMENT_PREFIXES = ("format.", "insert.")

    def _is_document_command(self, command_id: str) -> bool:
        return (command_id in self._DOCUMENT_COMMANDS
                or command_id.startswith(self._DOCUMENT_PREFIXES))

    def _update_window_commands(self, *_args) -> None:
        """Dim the document's commands while the Imports window is in front
        (and View > Show Sidebar works for it); give them back after."""
        if not hasattr(self, "commands"):
            return
        imports = self._imports_in_front()
        in_front = imports is not None
        if in_front != getattr(self, "_document_commands_dimmed", False):
            self._document_commands_dimmed = in_front
            kept = self.__dict__.setdefault("_dimmed_were_enabled", {})
            for command in self.commands.commands():
                if not self._is_document_command(command.id):
                    continue
                action = self.commands.action(command.id)
                if in_front:
                    kept[command.id] = action.isEnabled()
                    action.setEnabled(False)
                else:
                    action.setEnabled(kept.pop(command.id, True))
            if hasattr(self, "_m_background"):
                self._m_background.setEnabled(not in_front)
            if not in_front:
                # What changed meanwhile is asked again.
                self._update_knit_actions()
                self._update_format_buttons()
        self.act_window_sidebar.setEnabled(in_front)
        # File > Close names what it closes (HIG; Mail's Close Window): the
        # Imports window has no tabs. A sheet blocks its window: it is not
        # closed under it.
        self.act_close_tab.setText(_("Close Window") if in_front else _("Close Tab"))
        self.act_close_tab.setEnabled(not (in_front and imports.sheet_in_front()))
        # Enter or Exit Full Screen, for the window now in front.
        self._name_full_screen()
        if in_front:
            shown = imports.act_sidebar.isChecked()
            self.act_window_sidebar.setText(_("Hide Sidebar") if shown else _("Show Sidebar"))
        else:
            self.act_window_sidebar.setText(_("Show Sidebar"))

    def _toggle_window_sidebar(self) -> None:
        imports = self._imports_in_front()
        if imports is not None:
            imports.act_sidebar.trigger()
            self._update_window_commands()

    def _imports_in_front(self):
        """The Imports window while it is the window in front, or one of its
        sheets or alerts is (CP2 review L-a: with a sheet up, Edit > Undo
        undid text in the document behind). It has no menu bar of its
        own: the app's Edit commands act on it then, as a Mac app's menu
        bar acts on its key window."""
        imports = getattr(self, "_imports", None)
        window = imports.window() if imports is not None else None
        if window is None:
            return None
        active = QApplication.activeWindow()
        while active is not None:
            if active is window:
                return window
            active = active.parentWidget()
        return None

    def _undo(self):
        """Edit > Undo, for what has the focus: the Imports window when it
        is in front (Undo Skip there), a text field (the find field, a
        dialog's About field: each takes its own, as its keys do), or the
        document."""
        imports = self._imports_in_front()
        if imports is not None:
            imports.undo()
            return
        field = _text_field(QApplication.focusWidget())
        if field is not None:
            field.undo()
            return
        ed = self.current_editor()
        if ed and not self._shows_preview_only(ed):
            ed.undo()

    def _redo(self):
        field = _text_field(QApplication.focusWidget())
        if field is not None:
            field.redo()
            return
        ed = self.current_editor()
        if ed and self._imports_in_front() is None and not self._shows_preview_only(ed):
            ed.redo()

    def _editor_call(self, name: str) -> None:
        """Run an editing command on the current tab's editor."""
        ed = self.current_editor()
        if ed:
            getattr(ed, name)()
            self._update_format_buttons()

    def _open_link(self, href: str) -> None:
        """Open a link from the document: a web page in the browser, a
        Nostr link through njump.me, an email in the mail app (with only
        its address, subject, body, cc and bcc). Links reach the document
        from files and drafts without passing the Add Link checks, so
        everything else is refused here. A place in the document itself
        (#name) has nothing to open yet."""
        if href.startswith("#"):
            return
        web = link_url.web_address_for(href)
        mail = link_url.mail_address_for(href)
        if web:
            self._open_external(web)
        elif mail:
            QDesktopServices.openUrl(QUrl(mail))
        else:
            self.status.showMessage(_("That link cannot be opened."), 5000)

    @staticmethod
    def _copy_link(href: str) -> None:
        """The link's address on the clipboard (an email without mailto:)."""
        QGuiApplication.clipboard().setText(
            href[len("mailto:"):] if href.lower().startswith("mailto:") else href)

    def _toggle_list(self, kind: str) -> None:
        ed = self.current_editor()
        if ed:
            ed.toggle_list(kind)
            self._update_format_buttons()

    def _change_indent(self, delta: int) -> None:
        ed = self.current_editor()
        if ed:
            ed.change_indent(delta)
            self._update_format_buttons()

    def _set_heading(self, level: int) -> None:
        ed = self.current_editor()
        if ed:
            ed.set_heading(level)
            self._update_format_buttons()

    def _caret_context(self) -> tuple:
        """(in a code block, in a table): where the caret (or the selection)
        is, in a document with structure that is being edited."""
        ed = self.current_editor()
        editing = (ed is not None and MainWindow._editor_kind(self, ed) == "rich"
                   and not MainWindow._shows_preview_only(self, ed))
        if not editing:
            return False, False
        return (MainWindow._caret_in_code(ed),
                ed.textCursor().currentTable() is not None)

    @staticmethod
    def _caret_in_code(ed) -> bool:
        """Whether the caret, or either end of the selection, is in a code
        block."""
        if ed is None:
            return False
        cursor = ed.textCursor()
        doc = ed.document()
        return (rich_text.is_code_block(doc.findBlock(cursor.selectionStart()))
                or rich_text.is_code_block(doc.findBlock(cursor.selectionEnd())))

    @staticmethod
    def _code_style_name(language: str) -> str:
        """What the Style pop-up says in a code block: "Code: Python"."""
        if language == "mixed":
            return pgettext("paragraph style", "Mixed")
        canonical = canonical_language(language)
        if canonical is not None:
            return _("Code: {language}").format(language=LANGUAGE_DISPLAY_NAMES[canonical])
        if language:
            return _("Code: {language}").format(language=language)
        return _("Code")

    def _insert_table(self) -> None:
        """Insert > Table...: its size chosen on a grid at the caret, then
        the table, the caret in its first cell."""
        ed = self.current_editor()
        if ed is None:
            return
        picker = TablePicker(self, dark=self.is_dark_theme)
        picker.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        picker.picked.connect(lambda rows, columns, e=ed: self._table_picked(e, rows, columns))
        self._table_picker = picker
        picker.open_at(ed.cursorRect(), ed.viewport())

    def _table_picked(self, ed, rows: int, columns: int) -> None:
        if not shiboken6.isValid(ed):
            return
        # Back to the text, in this window, whatever the panel left active.
        self.activateWindow()
        ed.setFocus()
        ed.insert_table(rows, columns)
        self._update_format_buttons()

    def _editor_table_command(self, name: str) -> None:
        ed = self.current_editor()
        if ed is not None:
            ed.table_command(name)
            self._update_format_buttons()

    def _insert_code_block(self) -> None:
        """Insert > Code Block: the paragraph at the caret (or those
        selected) become a code block, and its language is asked in a
        short list at the caret (Escape keeps it plain text)."""
        ed = self.current_editor()
        if ed is None:
            return
        ed.make_code_block()
        self._update_editor_commands()
        self._update_format_buttons()
        caret = ed.cursorRect()
        self.m_code_language.popup(ed.viewport().mapToGlobal(caret.bottomLeft()))

    def _set_code_language(self, language: str) -> None:
        ed = self.current_editor()
        if ed is not None:
            ed.set_code_language(language)
            self._update_format_buttons()

    def _slash_items(self, query: str) -> list:
        """What the slash menu offers for ``query``: the window's commands,
        ranked (CommandRegistry.rank), Nostr ones while Nostr is in use."""
        return [SlashItem(command.slash_section, self.commands.action(command.id))
                for command in self.commands.rank(query, nostr=self.nostr_state.active)]

    def _set_markdown_shortcuts(self, on: bool) -> None:
        """Edit > Substitutions > Markdown Shortcuts, remembered."""
        self.markdown_shortcuts = bool(on)
        save_setting("markdown_shortcuts", self.markdown_shortcuts)

    def _set_monospace_writing(self, on: bool) -> None:
        """View > Use Monospace Font for Writing, remembered."""
        self.monospace_writing = bool(on)
        save_setting("monospace_writing", self.monospace_writing)
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed is not None:
                self._update_editor_font(ed)

    def _set_toolbar_shown(self, shown: bool) -> None:
        """View > Show Toolbar, remembered."""
        self.show_toolbar = bool(shown)
        save_setting("show_toolbar", self.show_toolbar)
        self._update_editor_commands()

    def _toggle_style(self, style: str) -> None:
        ed = self.current_editor()
        if ed:
            ed.toggle_style(style)
            self._update_format_buttons()

    def _apply_color(self, color) -> None:
        ed = self.current_editor()
        if ed:
            ed.apply_color(color)

    def _edit_focused(self, name: str) -> None:
        """Cut, Copy, Paste, Delete or Select All, for whatever has the
        keyboard focus, the way a Mac app sends them to its first
        responder: a text field (the find field), the PDF reader, or the
        document."""
        focus = _text_field(QApplication.focusWidget())
        if focus is not None:
            if name == "delete":
                if isinstance(focus, QLineEdit):
                    focus.del_()
                elif not focus.isReadOnly():
                    focus.textCursor().removeSelectedText()
                return
            {"cut": focus.cut, "copy": focus.copy, "paste": focus.paste,
             "select_all": focus.selectAll}[name]()
            return
        imports = self._imports_in_front()
        if imports is not None:
            imports.edit(name)
            return
        viewer = self.current_pdf_viewer()
        if viewer is not None:
            if name == "copy":
                viewer.view.copy_selection()
            return
        ed = self.current_editor()
        if ed is None:
            return
        if name == "cut":
            ed.cut()
        elif name == "copy":
            ed.copy()
        elif name == "paste":
            ed.paste_from_clipboard()
        elif name == "select_all":
            ed.selectAll()
        elif name == "delete":
            cursor = ed.textCursor()
            if cursor.hasSelection():
                cursor.beginEditBlock()
                cursor.removeSelectedText()
                cursor.endEditBlock()
                ed.setTextCursor(cursor)
        ed.setFocus()

    def _paste_plain(self) -> None:
        field = _text_field(QApplication.focusWidget())
        if isinstance(field, QLineEdit):
            field.paste()
        elif field is not None:
            if not field.isReadOnly():
                field.insertPlainText(QApplication.clipboard().text())
        elif self._imports_in_front() is None:
            ed = self.current_editor()
            if ed:
                ed.paste_normalized()

    def _use_selection_for_find(self) -> None:
        """The selected words become what Find looks for (Command-E): Find
        Next then goes to their next occurrence, with the find bar open or
        not."""
        ed = self.current_editor()
        if ed is None:
            return
        text = ed.textCursor().selectedText().replace("\u2029", " ").replace("\u2028", " ")
        if text.strip():
            self.findbar.edit.setText(text)

    def _editor_kind(self, ed) -> str:
        """What a tab's editor holds: "rich" (Markdown and HTML documents,
        and untitled ones: headings, lists and links go to Markdown), or
        "source" (text written as it is: .txt, R Markdown, code, and
        Markdown opened as its text). "" for no editor."""
        if ed is None:
            return ""
        if getattr(ed, "_markdown_source", False) or getattr(ed, "_loaded_as_rmd_source", False):
            return "source"
        path = getattr(ed, "_file_path", None)
        if path:
            return "rich" if path.lower().endswith(_RICH_DOC_EXTS) else "source"
        language = getattr(ed, "_language", None)
        return "rich" if language in (None, "markdown", "html") else "source"

    def _update_editor_commands(self) -> None:
        """Editing commands follow the current tab: none on a PDF tab, and
        no Markdown structure in a plain-text one."""
        if not hasattr(self, "_editor_actions"):
            return
        kind = self._editor_kind(self.current_editor())
        reading = self._shows_preview_only(self.current_editor())
        in_code, in_table = self._caret_context_shown = self._caret_context() or (False, False)
        if getattr(self, "format_toolbar", None) is not None:
            # A PDF has nothing to format; plain text has no paragraph
            # styles, and neither has a table cell.
            self.format_toolbar.setVisible(self.show_toolbar and bool(kind))
            if getattr(self, "format_bar", None) is not None:
                self.format_bar.setVisible(self.show_toolbar and bool(kind))
            self.format_toolbar.style_button.setEnabled(kind == "rich" and not reading
                                                        and not in_table)
        # Only the preview shows: nothing to edit there (Copy and Select
        # All act on the preview itself, through the focus).
        for action in self._editor_actions:
            action.setEnabled(bool(kind) and not reading)
        for action in self._rich_actions:
            action.setEnabled(kind == "rich" and not reading)
        # In a code block, words have no styles: the text is code.
        if in_code:
            for action in self._word_actions:
                action.setEnabled(False)
        for action in self.act_code_languages:
            action.setEnabled(in_code)
        # A table cell holds words, no paragraph structure.
        if in_table:
            for action in self._block_actions:
                action.setEnabled(False)
        for action in self.act_table_commands:
            action.setEnabled(in_table)
        self.act_select_all.setEnabled(bool(kind))
        # Code files only: a document's code blocks are always colored.
        self.act_toggle_syntax_hl.setEnabled(kind != "rich")
        for action in (self.act_find, self.act_replace, self.act_find_next, self.act_find_prev):
            action.setEnabled(not reading)
        self.act_underline.setEnabled(bool(self._keeps_underline(self.current_editor()))
                                      and not reading and not in_code)
        self._update_clipboard_commands()
        if kind:
            self._update_undo_redo_buttons()

    @staticmethod
    def _keeps_underline(ed) -> bool:
        """Whether the document's file keeps underlining: an HTML document.
        Markdown (files, new documents, drafts, articles) and plain text
        have no underline."""
        path = (getattr(ed, "_file_path", None) or "").lower() if ed is not None else ""
        return path.endswith((".html", ".htm")) and not getattr(ed, "_markdown_source", False)

    def _fill_editor_context_menu(self, menu, editor, pos) -> None:
        """The editor's context menu: the window's own commands, so it
        offers what the menus offer, under the same names. On a link, the
        link's own commands come first."""
        clicked = editor.cursorForPosition(pos)
        if not editor.textCursor().hasSelection() and (
                editor.anchorAt(pos) or (self._editor_kind(editor) == "rich"
                                         and rich_text.footnote_jump(clicked) is not None)):
            # A link or a footnote right-clicked: its commands, there.
            editor.setTextCursor(clicked)
            self._update_format_buttons()
        self._spelling_context(menu, editor, pos)
        link = editor.link_at_caret() if self._editor_kind(editor) == "rich" else None
        if link is not None:
            # A link's own commands, here only: they mean something only
            # where a link is.
            href = link[2]
            if not href.startswith("#"):
                # A place in the document itself opens nothing yet.
                menu.addAction(_("Open Link"), lambda: self._open_link(href))
            menu.addAction(self.act_link)
            menu.addAction(_("Copy Link"), lambda: self._copy_link(href))
            menu.addAction(_("Remove Link"), editor.remove_link)
            menu.addSeparator()
        if self._editor_kind(editor) == "rich":
            if rich_text.footnote_jump(editor.textCursor()) is not None:
                # A footnote's mark or note: the way to the other (also in
                # the Edit menu; Command-click is the pointer's way).
                menu.addAction(self.act_footnote_jump)
                menu.addSeparator()
        for action in (self.act_cut, self.act_copy, self.act_paste, self.act_paste_plain):
            menu.addAction(action)
        menu.addSeparator()
        if self._editor_kind(editor) == "rich" and editor.textCursor().currentTable() is not None:
            # In a table: its rows and columns.
            menu.addMenu(self.m_table)
            menu.addSeparator()
        for action in (self.act_bold, self.act_italic, self.act_underline, self.act_strike,
                       self.act_code):
            menu.addAction(action)
        menu.addMenu(self.m_color)
        menu.addSeparator()
        menu.addMenu(self.m_style)
        menu.addAction(self.act_reset_format)


    # ----------------------------------------------------------------------
    # APPEARANCE
    # ----------------------------------------------------------------------
    @property
    def appearance(self) -> str:
        """"system" while the system's appearance is followed, else "light"
        or "dark"."""
        if self._follow_os_theme:
            return "system"
        return "dark" if self.is_dark_theme else "light"

    def set_appearance(self, mode: str) -> None:
        """Automatic ("system": the system's light or dark, followed as it
        changes), Light or Dark; remembered across launches. View >
        Appearance and Settings both choose through here."""
        settings = load_settings()
        if mode == "system":
            settings.pop("theme", None)
            is_dark = self._detect_os_dark_theme()
        else:
            settings["theme"] = mode
            is_dark = mode == "dark"
        save_settings(settings)
        self._follow_os_theme = mode == "system"
        if is_dark != self.is_dark_theme:
            self._set_theme(is_dark)
        self.act_appearance[mode].setChecked(True)

    def _set_theme(self, is_dark: bool):
        """Apply is_dark to every theme-aware widget in the window.

        Shared by View > Appearance and the path that follows the system's
        appearance, so the fan-out logic lives in one place instead of
        being duplicated. The change itself is the feedback; nothing is
        said about it.
        """
        self.is_dark_theme = is_dark
        self._apply_theme()
        if hasattr(self, 'findbar'):
            self.findbar.is_dark = self.is_dark_theme
            self.findbar._update_theme()
        self.profile_chip.set_dark_theme(self.is_dark_theme)
        if getattr(self, 'format_toolbar', None) is not None:
            self.format_toolbar.set_dark(self.is_dark_theme)
        if getattr(self, 'view_switch', None) is not None:
            self.view_switch.set_dark(self.is_dark_theme)
        if hasattr(self, 'update_bar'):
            self.update_bar.update_theme(self.is_dark_theme)
        for i in range(self.tabs.count()):
            bar = self._bar_from_widget(self.tabs.widget(i))
            if bar:
                bar.update_theme(self.is_dark_theme)
            viewer = self._pdf_viewer_from_widget(self.tabs.widget(i))
            if viewer is not None:
                viewer.update_theme(self.is_dark_theme)
        # The drafts panel and any mounted conflict banners each carry
        # their own dark/light stylesheet pair. Update them in lockstep
        # with the rest of the window so a theme switch doesn't leave
        # half the editor on the old palette.
        if getattr(self, "_drafts_panel", None) is not None:
            self._drafts_panel.apply_theme(self.is_dark_theme)
            self._published_view.apply_theme(self.is_dark_theme)
        if getattr(self, "_article_details_panel", None) is not None:
            self._article_details_panel.set_dark(self.is_dark_theme)
        self._redraw_media_cards()
        for banner in getattr(self, "_tab_conflict_banners", {}).values():
            banner.apply_theme(self.is_dark_theme)
        imports_window = getattr(self, "_imports", None) and self._imports.window()
        if imports_window is not None:
            imports_window.apply_theme(self.is_dark_theme)
        for name in ("_settings_window", "_shortcuts_window"):
            window = getattr(self, name, None)
            if window is not None:
                window.apply_theme(self.is_dark_theme)

    def _on_os_color_scheme_changed(self, scheme):
        if not self._follow_os_theme:
            return
        self._set_theme(scheme != Qt.ColorScheme.Light)

    def _toggle_fullscreen(self, *_args):
        """The window in front goes full screen, as View > Enter Full Screen
        does in every Mac app (with the Imports window in front, that one:
        CP2 review L-a), or back to the size it had (a maximized window
        comes back maximized)."""
        window = self._imports_in_front() or self
        window.setWindowState(window.windowState() ^ Qt.WindowState.WindowFullScreen)
        self._name_full_screen()

    def _name_full_screen(self, *_args) -> None:
        """Enter Full Screen or Exit Full Screen, for the window in front."""
        if hasattr(self, "act_fullscreen"):
            window = self._imports_in_front() or self
            self.act_fullscreen.setText(
                _("Exit Full Screen") if window.isFullScreen() else _("Enter Full Screen"))

    def changeEvent(self, event):  # noqa: N802, Qt's name
        super().changeEvent(event)
        # However the window got there: the menu, its key, or the window's
        # own full-screen button on a Mac. (A state set while the window is
        # being built comes before its commands.)
        if event.type() == QEvent.Type.WindowStateChange:
            self._name_full_screen()


    # ----------------------------------------------------------------------
    # LINE NUMBERS
    # ----------------------------------------------------------------------
    # -- backups -------------------------------------------------------------

    def _new_backup(self, ed, path) -> EditorBackup:
        """The document's crash backup, which reports here when it cannot
        be kept."""
        backup = EditorBackup(ed, path, externalize=self._asset_manager.adopt_data_uri)
        backup.set_status_listener(lambda _problem, e=ed: self._on_backup_status(e))
        return backup

    def _on_backup_status(self, ed) -> None:
        if ed is self.current_editor():
            self._show_backup_status()

    # -- Write, Preview, Split ------------------------------------------------

    def _editor_container(self, ed):
        """A tab's content and its file-changed bar: the bar over the text
        (with its line numbers), the preview beside it when it shows."""
        container = QWidget()
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        bar = FileChangedBar()
        bar.update_theme(self.is_dark_theme)
        bar.reload_requested.connect(lambda b=bar, e=ed: self._reload_from_disk(e, b))
        vbox.addWidget(bar)
        editor_area = QWidget()
        layout = QHBoxLayout(editor_area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        if self.show_line_numbers:
            gutter = LineNumberGutter(ed)
            layout.addWidget(gutter)
            ed._line_gutter = gutter
        layout.addWidget(ed)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(editor_area)
        splitter.splitterMoved.connect(lambda *_args, s=splitter: self._remember_split(s))
        _WidthWatcher(splitter, lambda e=ed: self._width_changed(e))
        vbox.addWidget(splitter)
        ed._view_splitter = splitter
        ed._view_side = WRITE           # the side used last, for a narrow Split
        ed._preview_pane = None
        ed._split_fits = None
        return container, bar

    def _previewable(self, ed) -> bool:
        """Whether a tab has a preview: a document with Markdown structure,
        or Markdown opened as its text (published as it is written, so a
        reader draws it the same way). Plain text, code and R Markdown
        have none."""
        return (ed is not None and getattr(ed, "_view_splitter", None) is not None
                and (self._editor_kind(ed) == "rich" or getattr(ed, "_markdown_source", False)))

    def _shown_view(self, ed) -> str:
        """What the tab shows: WRITE, PREVIEW or SPLIT. Split needs room for
        two readable columns; narrower, the side used last shows (Q-N)."""
        if not self._previewable(ed):
            return WRITE
        if self.view_mode == SPLIT and not self._split_fits(ed):
            return ed._view_side if ed._view_side in (WRITE, PREVIEW) else WRITE
        return self.view_mode

    @staticmethod
    def _split_fits(ed) -> bool:
        return ed._view_splitter.width() >= SPLIT_MIN_WIDTH

    def _shows_preview_only(self, ed) -> bool:
        return ed is not None and self._shown_view(ed) == PREVIEW

    def _choose_view(self, mode: str) -> None:
        """Write, Preview or Split, chosen. In a tab too narrow for Split
        while Split is the choice, Write and Preview flip the side shown,
        and Split comes back when there is room."""
        ed = self.current_editor()
        if (mode != SPLIT and self.view_mode == SPLIT and ed is not None
                and getattr(ed, "_view_splitter", None) is not None
                and not self._split_fits(ed)):
            ed._view_side = mode
        else:
            self.view_mode = mode
            save_setting("view_mode", mode)
            if ed is not None and mode != SPLIT:
                ed._view_side = mode
        self._apply_view(ed)

    def _apply_view(self, ed) -> None:
        """Show the tab's text, preview or both, as chosen."""
        if getattr(self, "view_switch", None) is None:
            return
        splitter = getattr(ed, "_view_splitter", None) if ed is not None else None
        shown = self._shown_view(ed)
        if splitter is not None:
            ed._split_fits = self._split_fits(ed)
            editor_area = splitter.widget(0)
            pane = ed._preview_pane
            if shown != WRITE:
                pane = self._preview_pane_for(ed)
            if shown == PREVIEW:
                had_focus = ed.hasFocus()
                pane.show()
                editor_area.hide()
                if had_focus:
                    pane.view.setFocus()
            else:
                editor_area.show()
                if pane is not None:
                    pane.setVisible(shown == SPLIT)
                if shown == SPLIT:
                    ratio = float(load_settings().get("split_ratio", 0.5))
                    ratio = min(0.8, max(0.2, ratio))
                    width = splitter.width()
                    splitter.setSizes([round(width * ratio), width - round(width * ratio)])
                if pane is not None and pane.view.hasFocus():
                    ed.setFocus()
        self._sync_view_switch(ed, shown)
        self._update_editor_commands()
        if shown == PREVIEW and self.findbar.isVisible():
            self._close_findbar()

    def _sync_view_switch(self, ed, shown: str) -> None:
        rich = self._previewable(ed)
        self._view_switch_holder.setVisible(rich)
        for action in (self.act_view_write, self.act_view_preview, self.act_view_split):
            action.setEnabled(rich)
        if not rich:
            return
        fits = self._split_fits(ed)
        self.act_view_split.setEnabled(fits)
        self.act_view_split.setToolTip("" if fits else
                                       _("Make the window wider to show both"))
        {WRITE: self.act_view_write, PREVIEW: self.act_view_preview,
         SPLIT: self.act_view_split}[shown].setChecked(True)

    def _width_changed(self, ed) -> None:
        """A tab got wider or narrower: Split may fit now, or not any more.
        Looked at once the layout has settled, never in the middle of it."""
        if ed is self.current_editor() and self.view_mode == SPLIT:
            QTimer.singleShot(0, lambda e=ed: self._width_settled(e))

    def _width_settled(self, ed) -> None:
        if (shiboken6.isValid(ed) and ed is self.current_editor() and self.view_mode == SPLIT
                and ed._split_fits is not None and ed._split_fits != self._split_fits(ed)):
            self._apply_view(ed)

    def _remember_split(self, splitter) -> None:
        sizes = splitter.sizes()
        if len(sizes) == 2 and sum(sizes) > 0 and all(sizes):
            save_setting("split_ratio", round(sizes[0] / sum(sizes), 3))

    def _note_side_in_use(self, widget) -> None:
        """The side of a tab that has the focus is the one a narrow Split
        shows."""
        while widget is not None:
            if isinstance(widget, HtmlEditor):
                if getattr(widget, "_view_splitter", None) is not None:
                    widget._view_side = WRITE
                return
            if isinstance(widget, PreviewPane):
                ed = self._editor_from_widget(widget.parentWidget())
                if ed is not None:
                    ed._view_side = PREVIEW
                return
            widget = widget.parentWidget()

    def _preview_pane_for(self, ed) -> PreviewPane:
        """The tab's preview, made the first time it shows."""
        if ed._preview_pane is None:
            binding = getattr(ed, "_draft_binding", None)
            pane = PreviewPane(
                lambda kind, e=ed: self._preview_content(e, kind),
                images=lambda name, e=ed: self._preview_image(e, name),
                media_names=self._media_name,
                dark=self.is_dark_theme,
                note=binding is not None and binding.inner_kind == INNER_KIND_SHORT_NOTE)
            pane.setVisible(False)
            ed._view_splitter.addWidget(pane)
            ed.document().contentsChanged.connect(pane.schedule)
            pane.view.selectionChanged.connect(self._update_clipboard_commands)
            ed._preview_pane = pane
        return ed._preview_pane

    def _preview_content(self, ed, kind: str) -> PreviewContent:
        """What the preview of ``ed`` draws: the document as it reads, or,
        while a Nostr account is in use, what will be published (as an
        ARTICLE or a NOTE) and what of it readers will not see."""
        names = self._preview_name
        if not self.nostr_state.active:
            def pictures(fmt):
                media = _media_asset(self._asset_manager, fmt.name())
                if media is not None:
                    # Audio or video: where readers show its player, once
                    # it is on a media server.
                    return _published_address(media) if media.is_uploaded else None
                return image_markdown(str(fmt.property(QTextImageFormat.ImageAltText) or ""),
                                      fmt.name())
            if getattr(ed, "_markdown_source", False):
                # Markdown opened as its text: the text is the Markdown.
                markdown = serialize_plain_with_images(ed.document(), pictures)
            else:
                markdown = document_to_markdown(ed.document(), pictures)
            return PreviewContent(DOCUMENT, markdown.rstrip("\n"), names=names)
        profile = self._profile_store.default()
        author = (profile.display_name or profile.npub_short()) if profile is not None else ""
        checks = []
        if self._media_not_uploaded(ed.document()):
            checks.append(_("Audio or video is still uploading: it shows here, and is "
                            "published, once it is on your media servers."))
        missing = self._pictures_not_uploaded(ed)
        if missing:
            checks.append(ngettext(
                "A picture is not uploaded yet: readers will not see it.",
                "{count} pictures are not uploaded yet: readers will not see them.",
                missing).format(count=missing))
        if kind == NOTE:
            text = self._publish_payload(ed, "note")[0]
            return PreviewContent(NOTE, text, author=author, names=names, checks=tuple(checks))
        markdown = self._publish_payload(ed, "markdown")[0]
        if rich_text.footnote_marks(ed.document()):
            checks.append(_("Some apps (Amethyst, Coracle, Damus) show footnotes as typed: "
                            "the marks in brackets and the notes at the end."))
        if rich_text.orphan_notes(ed.document()):
            checks.append(_("A note no mark refers to: most apps leave it out."))
        details = self._article_details_of(ed)
        title = details.title or next(
            (line.lstrip("# ").strip() for line in markdown.splitlines() if line.strip()), "")
        first = next((line for line in markdown.splitlines() if line.strip()), "")
        if details.title and first.startswith("# ") and first[2:].strip() == details.title:
            # Said only for a title set on purpose: without one, the title
            # is that first heading, and nothing better can be done yet.
            checks.append(_("The first heading repeats the title, so readers see it twice."))
        article = PreviewArticle(title=title, summary=details.summary, image=details.image,
                                 tags=tuple(details.hashtags), author=author,
                                 published_at=details.published_at or 0)
        return PreviewContent(ARTICLE, markdown, article, author, names, tuple(checks))

    def _redraw_preview(self) -> None:
        ed = self.current_editor()
        pane = getattr(ed, "_preview_pane", None) if ed is not None else None
        if pane is not None and pane.isVisible():
            pane.render()

    def _preview_name(self, pubkey: str):
        person = self._known_people.get(pubkey)
        return person.display_name if person is not None and person.display_name else None

    def _pictures_not_uploaded(self, ed) -> int:
        """Pictures in the document that would not be published: kept on
        this computer only."""
        count = 0
        for name in iter_image_names(ed.document()):
            sha = parse_asset_key(name)
            if sha is not None:
                asset = self._asset_manager.get(sha)
                if asset is None or not asset.is_uploaded:
                    count += 1
            elif not name.lower().startswith(("http://", "https://")):
                count += 1
        return count

    def _preview_image(self, ed, name: str):
        """A picture the preview names: from this app's own media (also by
        its published address), or beside the document's file. Never from
        the network."""
        if parse_asset_key(name) is not None:
            return self._asset_manager.resolve_image(name)
        if name.lower().startswith(("http://", "https://")):
            asset = self._asset_manager.find_by_url(name)
            if asset is not None:
                return self._asset_manager.resolve_image(f"{ASSET_SCHEME}:{asset.sha256}")
            return None
        return self._resolve_local_image(ed, name)

    def _show_backup_status(self) -> None:
        """Say, for the current document only, that its backup is not kept,
        and offer to try again where trying again can help."""
        if not hasattr(self, "_backup_problem"):
            return
        ed = self.current_editor()
        backup = getattr(ed, "_backup", None) if ed is not None else None
        problem = backup.problem if backup is not None else KEPT
        if problem == TOO_LARGE:
            text = _("This document is too large to keep a backup of.")
        elif problem:
            text = _("Couldn't keep a backup of this document.")
        else:
            text = ""
        self._backup_problem.setText(text)
        self._backup_problem.setStyleSheet(
            f"color: {theme.error_text_color(self.is_dark_theme)}; padding-right: 4px;")
        # A link, so it reads as something to click next to the status texts.
        link = theme.dialog_link_color(self.is_dark_theme)
        self._backup_retry.setStyleSheet(
            f"#BackupRetry {{ color: {link}; border: none; background: transparent; "
            f"padding: 0 4px; }} #BackupRetry:hover {{ text-decoration: underline; }}")
        self._backup_problem.setVisible(bool(text))
        self._backup_retry.setVisible(bool(problem) and problem != TOO_LARGE)
        self._backup_retry.setAccessibleDescription(text)

    def _retry_backup(self) -> None:
        ed = self.current_editor()
        backup = getattr(ed, "_backup", None) if ed is not None else None
        if backup is not None:
            backup.write_now()
        self._show_backup_status()

    def _toggle_line_numbers(self):
        self.show_line_numbers = not self.show_line_numbers
        self.act_toggle_line_numbers.setChecked(self.show_line_numbers)

        for i in range(self.tabs.count()):
            container = self.tabs.widget(i)
            if isinstance(container, QWidget):
                editor = self._editor_from_widget(container)
                if editor:
                    if self.show_line_numbers and not hasattr(editor, '_line_gutter'):
                        gutter = LineNumberGutter(editor)
                        editor.parent().layout().insertWidget(0, gutter)
                        editor._line_gutter = gutter
                    elif not self.show_line_numbers and hasattr(editor, '_line_gutter'):
                        editor.parent().layout().removeWidget(editor._line_gutter)
                        editor._line_gutter.deleteLater()
                        delattr(editor, '_line_gutter')

        self.status.showMessage(_("Line numbers enabled") if self.show_line_numbers
                                else _("Line numbers disabled"), 2000)


    # ----------------------------------------------------------------------
    # SYNTAX HIGHLIGHTING
    # ----------------------------------------------------------------------
    def _toggle_syntax_highlighting(self):
        self.syntax_highlighting = not self.syntax_highlighting
        self.act_toggle_syntax_hl.setChecked(self.syntax_highlighting)

        for i in range(self.tabs.count()):
            container = self.tabs.widget(i)
            if isinstance(container, QWidget):
                editor = self._editor_from_widget(container)
                if editor:
                    self._attach_highlighter(editor, getattr(editor, '_file_path', None))

        self.status.showMessage(_("Syntax highlighting enabled") if self.syntax_highlighting
                                else _("Syntax highlighting disabled"), 2000)


    # ----------------------------------------------------------------------
    # VIEW MENU (background pattern, current-line highlight, paper mode)
    # ----------------------------------------------------------------------
    def _set_background_pattern(self, name):
        self.editor_background = name
        save_setting("editor_background", name)
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed:
                ed.set_background_pattern(name)
        title = self._background_titles.get(name, name)
        self.status.showMessage(_("Background: {pattern}").format(pattern=title), 2000)

    def _toggle_paper_mode(self, on):
        on = bool(on)
        self.paper_mode = on
        save_setting("paper_mode", on)
        if hasattr(self, "act_paper_mode") and self.act_paper_mode.isChecked() != on:
            self.act_paper_mode.blockSignals(True)
            self.act_paper_mode.setChecked(on)
            self.act_paper_mode.blockSignals(False)
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed:
                ed.set_paper_mode(on)
        self.status.showMessage(_("Paper mode on") if on else _("Paper mode off"), 2000)

    def _toggle_highlight_line(self, on):
        on = bool(on)
        self.highlight_current_line = on
        save_setting("highlight_current_line", on)
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed:
                ed.set_highlight_current_line(on)
        self.status.showMessage(_("Highlight current line on") if on
                                else _("Highlight current line off"), 2000)


    # ----------------------------------------------------------------------
    # CLOSE EVENT
    # ----------------------------------------------------------------------
    _SESSION_FILE = os.path.join(os.path.expanduser("~"), ".cache", "my_editor", "session.json")

    def _save_session(self):
        paths = []
        for i in range(self.tabs.count()):
            path = self._tab_file_path(self.tabs.widget(i))
            if path:
                paths.append(path)
        if not paths:
            return
        data = {"paths": paths, "active": self.tabs.currentIndex()}
        write_json(self._SESSION_FILE, data)

    def _restore_session(self) -> bool:
        if not os.path.isfile(self._SESSION_FILE):
            return False
        data = read_json(self._SESSION_FILE, dict)
        # Restored at most once, and a damaged record never restores.
        try:
            os.remove(self._SESSION_FILE)
        except OSError:
            pass
        listed = data.get("paths")
        listed = listed if isinstance(listed, list) else []
        paths = [p for p in listed if isinstance(p, str) and os.path.isfile(p)]
        if not paths:
            return False
        missing = len(listed) - len(paths)
        for path in paths:
            self.open_path(path)
        active = data.get("active", 0)
        if isinstance(active, int) and 0 <= active < self.tabs.count():
            self.tabs.setCurrentIndex(active)
        if missing:
            self.status.showMessage(
                ngettext("{count} file(s) from last session could not be found.",
                         "{count} file(s) from last session could not be found.",
                         missing).format(count=missing), 5000)
        return True

    # -- reopening the workspace after an update restart ---------------------

    def _resume_workspace(self) -> Tuple[Optional[Workspace], frozenset]:
        """Reopen the tabs an update restart wrote down, if it wrote any.

        Returns the record (None when there was none) and the backup files
        the reopened tabs took over (see workspace_restore.resume).
        """
        ws = workspace.take_workspace()
        if ws is None:
            return None, frozenset()
        return ws, workspace_restore.resume(self, ws, draft_type=DraftBinding)

    # -- after a version change ------------------------------------------------

    def _announce_version_change(self, ws: Optional[Workspace]) -> None:
        """Say so when this launch is a new version, or when an update that
        was meant to happen did not (workspace_restore.version_news decides)."""
        last_run = load_settings().get("last_run_version")
        save_setting("last_run_version", APP_VERSION)
        news = workspace_restore.version_news(
            ws, last_run, APP_VERSION,
            release_page=f"{APP_URL}/releases/tag/v{APP_VERSION}")
        if news is None:
            return
        if not news.updated:
            self._report_unfinished_update(news.version)
            return
        self._whats_new = (news.notes, news.release_url)
        self.update_bar.show_updated(APP_VERSION)

    def _on_whats_new_requested(self) -> None:
        notes, release_url = getattr(self, "_whats_new", ("", ""))
        self.update_bar.hide()
        if not notes:
            self._open_external(release_url)
            return
        dlg = WhatsNewDialog(APP_VERSION, notes, release_url=release_url,
                             is_dark=self.is_dark_theme, parent=self)
        dlg.link_activated.connect(self._open_external)
        dlg.finished.connect(dlg.deleteLater)
        dlg.open()

    def _report_unfinished_update(self, version: str) -> None:
        inform(self, title=_("The update wasn’t installed"),
               message=_("{app} {version} couldn’t replace this version, "
                         "so you’re still using {current}. Your documents are open "
                         "as you left them. To try again, choose Help > Check for "
                         "Updates.").format(app=APP_DISPLAY_NAME, version=version,
                                            current=APP_VERSION),
               is_dark=self.is_dark_theme)

    def closeEvent(self, event):
        # An update relaunch already handled unsaved work, so skip the prompt.
        closing_for_update = getattr(self, "_closing_for_update", False)
        if not closing_for_update and not self._resolve_unsaved_before_closing():
            event.ignore()
            return
        # Quitting from here: a Save As still running stops, and its part
        # file goes; nothing still on its way goes into a document.
        self._media_saver.cancel()
        for i in range(self.tabs.count()):
            self._drop_media_reads(self._editor_from_widget(self.tabs.widget(i)))
        if hasattr(self, "_knit_runner"):
            self._knit_runner.kill()
        self._asset_manager.flush()
        update_ws = getattr(self, "_update_workspace", None) if closing_for_update else None
        if update_ws is None:
            # The workspace record, when there is one, supersedes the session.
            self._save_session()
            workspace.discard_workspace()
        # Backups the restart will restore from must outlive this window.
        claimed = update_ws.claimed_backups() if update_ws is not None else set()
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed and hasattr(ed, '_backup'):
                if os.path.abspath(ed._backup.path) in claimed:
                    ed._backup.release()
                else:
                    ed._backup.delete()
            viewer = self._pdf_viewer_from_widget(self.tabs.widget(i))
            if viewer is not None:
                viewer.save_view_state()
        # Publish any pending changes to the list of sources before the
        # signer and the relay sockets go away, waiting a few seconds at
        # most: a key kept here signs on a later turn of the event loop,
        # and what does not arrive in time is sent on the next launch.
        imports = getattr(self, "_imports", None)
        if imports is not None:
            imports.flush()
            if imports.subscriptions.is_busy:
                # The documents are settled and their backups gone: nothing
                # typed now could be kept, so the window takes no more
                # input while it says what it finishes (engine review M2).
                self.setEnabled(False)
                self.status.showMessage(_("Saving your list of sources…"))
                imports.subscriptions.wait_until_settled(QUIT_SYNC_WAIT_MS)
            # The inbox closes and its lock goes, so the next start (or a
            # second MyEditor) finds it free.
            imports.shutdown()
        # Close any warm relay sockets and bunker channels so the WebSocket
        # layer can flush close frames before the QApplication tears down.
        if hasattr(self, "_session_pool"):
            self._session_pool.close_all()
        if hasattr(self, "_relay_pool"):
            self._relay_pool.close_all()
        event.accept()

    # ----------------------------------------------------------------------
    # NOSTR - profile chip menu + connect flow
    # ----------------------------------------------------------------------

    def _update_profile_chip(self):
        """Re-render the chip icon from the current default profile.

        If we have a cached avatar pixmap for the active profile, it's
        passed in; otherwise the chip falls back to initials on a
        deterministic color disc.
        """
        chip = self.profile_chip
        default = self._profile_store.default()
        if default is None:
            chip.set_disconnected()
            return
        chip.set_profile(
            display_name=default.display_name,
            user_pubkey_hex=default.user_pubkey,
            avatar_pixmap=self._avatars.get(default.user_pubkey),
        )

    def _refresh_profile_chip_menu(self):
        """Rebuild the chip's dropdown - fast and idempotent."""
        menu = self.profile_chip.menu()
        menu.clear()
        self._update_account_actions()

        profiles = self._profile_store.list()
        if not profiles:
            act = menu.addAction(_("Create Account\u2026"))
            act.triggered.connect(self._on_create_account)
            act = menu.addAction(_("Connect Signer App\u2026"))
            act.triggered.connect(self._on_nostr_connect)
            act = menu.addAction(_("Restore Account\u2026"))
            act.triggered.connect(self._on_restore_account)
            return

        active = self._profile_store.default()
        for profile in profiles:
            label = profile.display_name or profile.npub_short()
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(profile is active)
            act.triggered.connect(
                lambda _checked=False, p=profile: self._on_nostr_select_profile(p)
            )

        menu.addSeparator()
        act_membership = menu.addAction(_("EINUNDZWANZIG Membership\u2026"))
        act_membership.triggered.connect(self._open_membership_window)
        menu.addSeparator()
        if active is not None:
            act_edit = menu.addAction(_("Edit Profile\u2026"))
            act_edit.triggered.connect(self._on_edit_profile)
        if active is not None and active.is_local:
            act_backup = menu.addAction(_("Back Up Account\u2026"))
            act_backup.triggered.connect(self._on_backup_account)
            act_move = menu.addAction(_("Move Key to Signer App\u2026"))
            act_move.triggered.connect(self._on_move_to_signer)
        # Connecting offers creating and restoring an account too.
        act_add = menu.addAction(_("Add Account\u2026"))
        act_add.triggered.connect(self._on_nostr_connect)
        menu.addSeparator()
        act_signout = menu.addAction(_("Sign Out"))
        act_signout.triggered.connect(self._on_nostr_sign_out)

    def _update_account_actions(self) -> None:
        """Back Up Account and Move Key to Signer App are for an account
        whose key is kept here. The menu bar is built after the first chip
        refresh, so both call this.

        Every change of the active account passes through here (connect,
        switch, sign out, create, restore), so this is also where the one
        "Nostr in use" signal is brought up to date."""
        pool = getattr(self, "_relay_pool", None)
        if pool is not None:
            # One connection never carries two accounts (NIP-42): those
            # signed in as anyone but the account in use now open anew.
            active = self._profile_store.default()
            pool.end_sign_ins(keep=active.user_pubkey.lower() if active else "")
        state = getattr(self, "nostr_state", None)
        if state is not None:
            state.refresh()
            imports = getattr(self, "_imports", None)
            if imports is not None:
                try:
                    imports.account_changed(self._profile_store.default() if state.active
                                            else None)
                except Exception:  # noqa: BLE001, imports never stop the editor
                    logging.getLogger(__name__).exception(
                        "imports could not follow the account")
        self._update_nostr_commands()

    # The account's own commands that need its key kept on this computer.
    _LOCAL_KEY_COMMANDS = frozenset({"nostr.backup_account", "nostr.move_to_signer"})

    def _update_nostr_commands(self) -> None:
        """The Nostr menu's commands that act for an account are dimmed while
        none is in use, as the HIG asks of a command that cannot act; the
        ways to an account (create, connect, restore) and the membership
        stay available next to them. Back Up and Move Key also need the
        key on this computer. A document command dimmed while the Imports
        window is in front gets its new state when that window goes."""
        if not hasattr(self, "commands"):
            return
        current = self._profile_store.default()
        active = self.nostr_state.active
        dimmed = getattr(self, "_document_commands_dimmed", False)
        kept = self.__dict__.setdefault("_dimmed_were_enabled", {})
        for command in self.commands.commands(NOSTR):
            if not command.nostr:
                continue
            enabled = active
            if command.id in self._LOCAL_KEY_COMMANDS:
                enabled = active and current is not None and current.is_local
            if dimmed and self._is_document_command(command.id):
                kept[command.id] = enabled
            else:
                self.commands.action(command.id).setEnabled(enabled)
        # Refresh Drafts also needs the panel shown.
        self._update_refresh_command()

    def _on_nostr_in_use_changed(self, active: bool) -> None:
        """Nothing of Nostr stays on screen once no account is in use."""
        if not active:
            self._hide_drafts_panel()
            details = getattr(self, "act_article_details", None)
            if details is not None:
                details.setChecked(False)
        if getattr(self, "_settings_window", None) is not None:
            self._settings_window.refresh()
        if getattr(self, "_shortcuts_window", None) is not None:
            self._shortcuts_window.set_groups(self._shortcut_groups())

    # -- creating, restoring and backing up accounts ---------------------------
    # The windows and the network work behind them are the account
    # controller's (nostr/account_controller.py); it makes an account active
    # through ``_on_nostr_profile_connected``.

    def _on_create_account(self) -> None:
        self._accounts.create_account()

    def _offer_publishing_account(self) -> None:
        self._offer_account(
            title=_("Publishing needs a Nostr account"),
            message=_("Notes and articles are published from your Nostr account."))

    def _offer_media_account(self) -> None:
        self._offer_account(
            title=_("The media library needs a Nostr account"),
            message=_("Images you upload are kept for your Nostr account."))

    def _offer_account(self, *, title: str, message: str) -> None:
        """A Nostr command without an account in use: say what it needs and
        offer the ways in, the Nostr menu's own first two commands (Restore
        Account is there too). Creating an account is the default: it needs
        nothing but this app."""
        choice = ask(self, title=title, message=message, is_dark=self.is_dark_theme,
                     buttons=(Button(_("Create Account\u2026"), "create", DEFAULT),
                              Button(_("Connect Signer App\u2026"), "connect", NORMAL),
                              Button(_("Cancel"), None, CANCEL)))
        if choice == "create":
            self._on_create_account()
        elif choice == "connect":
            self._on_nostr_connect()

    def _on_restore_account(self) -> None:
        self._accounts.restore_account()

    def _on_backup_account(self) -> None:
        self._accounts.backup_account()

    def _on_move_to_signer(self) -> None:
        self._accounts.move_to_signer()

    def _on_edit_profile(self) -> None:
        """Edit Profile for the active account. The window reads the
        profile fresh before anything can be changed (nostr/ui/profile_window.py)."""
        active = self._profile_store.default()
        if active is None:
            self._offer_account(
                title=_("Your profile needs a Nostr account"),
                message=_("The name and picture people see belong to your Nostr account."))
            return
        editing = ProfileEditing(profile=active, pool=self._relay_pool,
                                 session_pool=self._session_pool,
                                 directory=self._relay_directory, parent=self)
        window = ProfileWindow(read=editing.read, save=editing.save,
                               signs_locally=active.is_local, is_dark=self.is_dark_theme,
                               parent=self)
        # Closing the window ends what is still running for it.
        editing.setParent(window)
        window.setWindowFlag(Qt.Window, True)
        window.setAttribute(Qt.WA_DeleteOnClose, True)

        def saved() -> None:
            self.status.showMessage(_("Profile saved."), 5000)
            # Read back what was published, so the chip and menus show it.
            self._metadata_fetcher.fetch(active)

        window.saved.connect(saved)
        window.show()

    def _on_nostr_connect(self):
        dialog = ConnectDialog(
            self._relay_pool,
            self._profile_store,
            parent=self,
            is_dark=self.is_dark_theme,
        )
        dialog.profile_connected.connect(self._on_nostr_profile_connected)
        # A key still kept here for the account is offered for deletion, and
        # an account connected from a signer app may have no relay list yet.
        dialog.profile_connected.connect(
            lambda profile: QTimer.singleShot(0, lambda: self._accounts.signer_paired(profile)))
        # No signer app yet: the dialog offers the other two ways in.
        dialog.create_requested.connect(lambda: QTimer.singleShot(0, self._on_create_account))
        dialog.restore_requested.connect(lambda: QTimer.singleShot(0, self._on_restore_account))
        dialog.exec()

    # -- relays that ask for a sign-in (NIP-42) -------------------------------

    def _on_relay_signing_in(self, relay: str) -> None:
        self.status.showMessage(_("Signing in to {relay}…").format(relay=relay), 5000)

    def _on_relay_sign_in_failed(self, relay: str, reason: str, answered: bool) -> None:
        if reason == NOT_CHOSEN:
            return
        if answered:
            text = _("{relay} didn’t accept your sign-in.")
        elif reason == DECLINED:
            text = _("{relay} asks you to sign in, and your signer app declined.")
        else:
            text = _("Couldn’t sign in to {relay}.")
        self.status.showMessage(text.format(relay=relay), 8000)

    # -- association membership --------------------------------------------

    def _entitled_relays(self) -> list:
        """The relays the active account's membership adds."""
        return self._membership.entitled_relays()

    def _entitled_blossom_servers(self) -> list:
        """The media servers the active account's membership adds."""
        return self._membership.entitled_blossom_servers()

    def _media_server_label(self, origin: str):
        """The Media Library's name for a server, when it has a better one
        than its host: the members' server is the association's."""
        return self._membership.server_label(origin)

    def _on_media_servers_adopted(self, _servers: list) -> None:
        """Nothing was configured here, so the servers the account
        publishes became its upload servers: a background change, shown
        in Media Servers and nowhere else."""
        if self._visible_media_library() is not None:
            self._media_store.refetch_if_targets_changed()

    def _media_server_suggestions(self) -> list:
        """Servers the active account publishes and this app does not
        upload to. Another account's list is never offered."""
        active = self._profile_store.default()
        if active is None or (self._server_list.discovered_pubkey.lower()
                              != active.user_pubkey.lower()):
            return []
        return self._server_list.suggestions

    def _on_media_server_suggestions(self, _servers: list) -> None:
        sheet = self._media_servers_sheet
        if sheet is not None and shiboken6.isValid(sheet):
            sheet.offer(self._media_server_suggestions())

    def _open_media_servers(self) -> None:
        """Media Servers…, from the Media Library: which servers keep the
        person's files, in which order, and sharing that list. A sheet on
        the library window."""
        parent = self._visible_media_library() or self
        sheet = MediaServersDialog(
            settings=self._media_store.settings,
            entitled=self._entitled_blossom_servers(),
            suggestions=self._media_server_suggestions(),
            server_label=self._media_server_label,
            is_dark=self.is_dark_theme, parent=parent)
        sheet.changed.connect(self._media_store.refetch_if_targets_changed)
        sheet.share_requested.connect(lambda s=sheet: self._share_media_server_list(s))
        sheet.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        sheet.destroyed.connect(lambda _obj=None: setattr(self, "_media_servers_sheet", None))
        self._media_servers_sheet = sheet
        # A sheet on the library window, as Draft Relays is on this one.
        sheet.open()

    def _share_media_server_list(self, parent=None) -> None:
        """Publish the servers uploads go to as the account's media server
        list (kind 10063), after asking: it replaces the list other apps
        read now, if the account published one. The servers named are the
        ones the list will hold (a plain-http one, or more than ten, is
        left out of it)."""
        active = self._profile_store.default()
        if active is None:
            return
        servers = [url_safety.origin_of(s) or s for s in self._media_store.target_servers()]
        hosts = ", ".join(url_safety.host_of(tag[1]) or tag[1]
                          for tag in server_list_tags(servers, None))
        parent = parent or self._visible_media_library() or self
        choice = ask(parent, title=_("Tell Other Apps Where Your Media Is?"),
                     message=_("Other Nostr apps then look on {hosts} for your pictures, "
                               "in this order. This replaces the list they use now, if "
                               "you shared one before.").format(hosts=hosts),
                     buttons=(Button(_("Cancel"), False, CANCEL),
                              Button(_("Share List"), True, DEFAULT)),
                     is_dark=self.is_dark_theme)
        if choice is not True:
            return
        try:
            writer = publish_server_list(
                servers=servers, pool=self._relay_pool, directory=self._relay_directory,
                session_pool=self._session_pool, profile=active, parent=self)
        except ValueError:
            self.status.showMessage(_("There is no media server to share."), 5000)
            return
        messages = {
            outbox_writer.WRITTEN: _("Your media server list is shared."),
            outbox_writer.UNCHANGED: _("Other apps already have this list."),
            outbox_writer.UNKNOWN_BASE: _("Your current list couldn’t be read, so nothing "
                                          "was changed. Try again later."),
        }

        def finished(outcome) -> None:
            self.status.showMessage(messages.get(outcome.status, _(
                "The list wasn’t shared. Try again in a moment.")), 6000)
            if outcome.status == outbox_writer.WRITTEN:
                self._server_list.refresh(active, force=True)
            writer.deleteLater()

        writer.finished.connect(finished)
        writer.start()

    def _entitled_quota(self, origin: str):
        """The space a membership gives on its media server, in bytes."""
        return self._membership.quota(origin)

    def _on_membership_benefits_changed(self) -> None:
        """The active account's benefits changed (resolved, lapsed,
        confirmed). Nothing is written to the user's configuration: the
        relay and media layers read the providers above on each call."""
        # An open Media Library lists the members' server as soon as it
        # applies. The store walks again only when its servers changed,
        # so a check that changed nothing costs no signer prompt.
        if self._visible_media_library() is not None:
            self._media_store.refetch_if_targets_changed()
        # Drafts are written to the members' relay as well, so they are
        # read there too (a no-op when the set of relays did not change),
        # and the deletions other apps announce for them with them.
        self._draft_sync.reroute()
        self._draft_deletions.reroute()

    def _visible_media_library(self):
        """The Media Library dialog while it is open, else None.

        It deletes itself on close, so the reference can outlive the
        dialog; a deleted one is forgotten here rather than asked
        anything, which would raise.
        """
        dialog = self._media_library_dialog
        if dialog is not None and not shiboken6.isValid(dialog):
            dialog = self._media_library_dialog = None
        return dialog if dialog is not None and dialog.isVisible() else None

    def _forget_media_library(self, dialog) -> None:
        if self._media_library_dialog is dialog:
            self._media_library_dialog = None

    def _media_window_open(self) -> bool:
        """Whether a Media Library window (the library or the picker) is
        open, to say how a Save As goes itself."""
        return self._visible_media_library() is not None or self._media_picker is not None

    def _say_media_save_running(self) -> None:
        """A library window closed: a Save As it started goes on, said here."""
        if self._media_saver.current:
            self._on_media_save_started(self._media_saver.current)

    def _on_media_save_started(self, target: str) -> None:
        if not self._media_window_open():
            self._media_save_said = _("Saving “{name}”…").format(name=os.path.basename(target))
            self.status.showMessage(self._media_save_said, 0)

    def _on_media_save_finished(self, target: str, why: str) -> None:
        """How a Save As went, when no library window is open to say it,
        or when this bar still says it is saving (a library window opened
        meanwhile): kept until the next message, as a saved document's
        is."""
        announced = self._media_save_announced()
        self._media_save_said = ""
        if why == "cancelled":
            if announced:
                self.status.clearMessage()
            return
        if announced or not self._media_window_open():
            self._media_save_said = (save_failure(why, target) if why
                                     else _("Saved: {path}").format(path=target))
            self.status.showMessage(self._media_save_said, 0)

    def _on_media_saves_finished(self, total: int, failures: list) -> None:
        """A Save to Folder ended without every file: said at its end, where
        its saves were said, with every reason in the tooltip."""
        if not failures or not (self._media_save_announced() or not self._media_window_open()):
            return
        self._media_save_said, tip = saves_failure(total, failures)
        self.status.showMessage(self._media_save_said, 0)
        self._message_tip.set(self._media_save_said, tip)

    def _media_save_announced(self) -> bool:
        """Whether the status bar still shows what it last said about a save."""
        return bool(self._media_save_said) and \
            self.status.currentMessage() == self._media_save_said

    def _open_membership_window(self) -> None:
        """Nostr > EINUNDZWANZIG Membership."""
        self._membership.open_window()

    def _open_imports_window(self) -> None:
        """Nostr > Imports: sources, their posts, and the open one. Without
        an account, or when imports cannot be kept here, it says so; after
        such a problem it tries once more first, so whatever the message
        asked to fix (a folder, a full disk, another program) counts."""
        imports = self._imports
        if not imports.bound and imports.problem:
            imports.retry()
        if imports.bound:
            imports.open_window()
        elif imports.problem:
            inform(self, title=_("Imports aren't available"), message=imports.problem,
                   is_dark=self.is_dark_theme)
        else:
            self._offer_account(
                title=_("Imports need a Nostr account"),
                message=_("Imported posts become drafts in your Nostr account."))

    def _update_imports_row(self, *_args) -> None:
        """What the Drafts panel's Imports row says."""
        imports = self._imports
        job = imports.activity()
        action = ""
        if job is not None and not imports.read_only:
            if job.status == "running":
                action = "pause"
            elif job.status == "paused":
                action = "resume"
            elif job.status == "partial":
                action = "retry"
        self._drafts_panel.set_imports_state(
            available=imports.bound, new_posts=imports.counts().inbox,
            activity=activity_line(job), action=action)

    def _resume_import(self) -> None:
        job = self._imports.activity()
        if job is not None:
            self._imports.resume_import(job.id)

    def _make_imports_window(self, controller):
        """The Imports window, remembered in settings.json between runs."""
        window = ImportsWindow(
            controller, dark=self.is_dark_theme,
            load_settings=lambda: load_settings().get(IMPORTS_SETTINGS_KEY) or {},
            save_settings=lambda value: save_setting(IMPORTS_SETTINGS_KEY, value),
            open_url=lambda url: self._open_external(url.toString()),
            show_drafts=self._show_drafts_from_imports, badge=self._imports_badge)
        # The Edit menu says what it acts on there (Undo Skip, a sheet's field).
        window.edit_changed.connect(self._update_undo_redo_buttons)
        window.edit_changed.connect(self._update_clipboard_commands)
        return window

    def _on_imports_badge_toggled(self, on: bool) -> None:
        """Nostr > Show New Posts on the App Icon, chosen (Q-7)."""
        self._imports_badge.set_on(on)

    def _on_imports_badge_changed(self, on: bool) -> None:
        """Turned on where Imports offered it: the menu item follows."""
        blocked = self.act_imports_badge.blockSignals(True)
        self.act_imports_badge.setChecked(on)
        self.act_imports_badge.blockSignals(blocked)

    def _show_drafts_from_imports(self) -> None:
        """Show Drafts, after an import: this window and its Drafts panel."""
        self._show_drafts_panel()
        self.raise_()
        self.activateWindow()

    def _on_nostr_profile_connected(self, profile: Profile):
        # New (or re-connected) profile becomes the active one.
        previous = self._profile_store.default()
        bound = self._draft_sync.active_profile
        if previous is not None and (
            previous.user_pubkey.lower() != profile.user_pubkey.lower()
        ):
            # Connecting a second account is a switch, so the first one's
            # drafts and media keys go with it.
            self._release_identity_state()
        elif bound is not None and bound.user_pubkey == profile.user_pubkey and (
            bound.signer != profile.signer
        ):
            # The same account signs another way now (its key restored here,
            # or a signer app paired): whatever holds the old signer lets go.
            self._release_identity_state()
        self._profile_store.set_default(profile.user_pubkey)
        self._membership.account_changed(profile)
        self._update_profile_chip()
        self._refresh_profile_chip_menu()
        self.status.showMessage(
            _("Connected as {name}").format(name=profile.display_name or profile.npub_short()),
            5000
        )
        # Kick off metadata + avatar fetch in the background. The chip will
        # refresh itself when the fetcher signals back.
        self._metadata_fetcher.fetch(profile)
        # Also prime the mentions cache from this profile's NIP-02 contact list.
        self._contact_fetcher.fetch(profile.user_pubkey)
        self._server_list.refresh(profile)
        # Bind the draft pipeline to the new profile so the panel
        # (visible or not) starts collecting wraps from the relays.
        self._draft_sync.start_for(profile)
        self._draft_deletions.start_for(profile)
        if self._drafts_panel is not None:
            self._drafts_panel.set_active_profile(profile)
            self._drafts_panel.set_signer_unsupported(False)
            self._drafts_panel.set_signer_unreachable(False)
            self._follow_account_in_published()
        # An account created here whose setup was left for later is finished.
        self._accounts.profile_activated(profile)

    def _release_identity_state(self) -> None:
        """Drop everything that belonged to the account being left.

        Identity-scoped state arrives one object at a time, and each new
        one has to be remembered at three separate transitions: connect,
        switch, and sign out. Naming the set in one place means the next
        addition has somewhere obvious to go instead of being forgotten
        at two of the three.

        This is not only cache. The private library holds a decryption
        key per file, so an account left behind with its keys still in
        memory is a privacy problem and not merely untidy.
        """
        self._draft_sync.stop()
        self._draft_deletions.stop()
        self._private_library.stop()
        self._server_list.forget_account()
        # The library lists one account's files; the next account must
        # never see them, not even until its own fetch lands.
        self._media_store.clear()
        # The membership window speaks for one identity; never show one
        # account's membership, invoice or name to another.
        self._membership.close_window()

    def _on_nostr_select_profile(self, profile: Profile):
        previous = self._profile_store.default()
        leaving = previous is not None and (
            previous.user_pubkey.lower() != profile.user_pubkey.lower()
        )
        self._profile_store.set_default(profile.user_pubkey)
        self._update_profile_chip()
        self._refresh_profile_chip_menu()
        # Only tear down when the account actually changes. Re-selecting
        # the current one would otherwise discard drafts already
        # decrypted and cost a fresh round of signer prompts.
        if leaving:
            self._release_identity_state()
        # After the teardown, so the membership follows the account that
        # stays rather than updating a window about to close.
        self._membership.account_changed(profile)
        # ``DraftSync.start_for`` is idempotent if the same profile is
        # already active.
        self._draft_sync.start_for(profile)
        self._draft_deletions.start_for(profile)
        if self._drafts_panel is not None:
            self._drafts_panel.set_active_profile(profile)
            self._drafts_panel.set_signer_unsupported(False)
            self._drafts_panel.set_signer_unreachable(False)
            self._follow_account_in_published()
        self._server_list.refresh(profile)
        self._accounts.profile_activated(profile)

    def _on_nostr_sign_out(self):
        active = self._profile_store.default()
        # The controller asks, then forgets every key kept for the account,
        # its signer and its profile.
        if active is None or not self._accounts.sign_out(active):
            return
        self._avatars.pop(active.user_pubkey, None)
        self._update_profile_chip()
        self._refresh_profile_chip_menu()
        # Tear down everything scoped to the account just removed. If
        # another profile remains, the caller (or chip menu) can re-bind
        # to it; otherwise the panel falls back to its "Connect a Nostr
        # profile" empty state.
        self._release_identity_state()
        remaining = self._profile_store.default()
        if self._drafts_panel is not None:
            self._drafts_panel.set_active_profile(remaining)
            self._drafts_panel.set_signer_unsupported(False)
            self._drafts_panel.set_signer_unreachable(False)
            self._follow_account_in_published()
        if remaining is not None:
            self._draft_sync.start_for(remaining)
            self._draft_deletions.start_for(remaining)

    # -- metadata / avatar updates ----------------------------------------

    def _on_own_profile_published(self, pubkey: str):
        """This app just changed the account's profile (its Nostr address):
        read it back, so the chip and the menus show what was published."""
        profile = self._profile_store.get(pubkey)
        if profile is not None:
            self._metadata_fetcher.fetch(profile)

    def _on_metadata_updated(self, profile: Profile):
        """Refreshed display name / picture URL landed - repaint the chip
        and queue the avatar download if one is available."""
        self._update_profile_chip()
        self._refresh_profile_chip_menu()
        if profile.picture:
            self._avatar_batcher.request(profile.user_pubkey, profile.picture)

    def _on_avatar_added(self, pubkey_hex: str, _pixmap: QPixmap):
        """A new pixmap landed in the store. Refresh the header chip only
        if it's for the currently-active profile; the picker/chip-row
        widgets subscribe to ``avatar_added`` themselves and don't need
        this signal."""
        active = self._profile_store.default()
        if active is not None and active.user_pubkey == pubkey_hex:
            self._update_profile_chip()

    def _on_person_updated(self, person: Person):
        """A kind 0 just resolved for someone in the contact list. Queue
        their avatar so the picker has it ready by the time the user
        searches for them."""
        if person.picture:
            self._avatar_batcher.request(person.pubkey, person.picture)

    def _on_search_results(self, _query: str, people: list):
        """NIP-50 search returned matches - queue their avatars too."""
        for person in people:
            if isinstance(person, Person) and person.picture:
                self._avatar_batcher.request(person.pubkey, person.picture)

    # -- publish ----------------------------------------------------------

    def _on_nostr_publish_note(self):
        active = self._profile_store.default()
        if active is None:
            self._offer_publishing_account()
            return

        ed = self.current_editor()
        if not self._confirm_images_uploaded(ed):
            return
        content, media = self._publish_payload(ed, "note") if ed is not None else ("", [])
        content = content.strip()
        if not content:
            inform(self, title=_("Nothing to publish"),
                   message=_("The current document is empty."))
            return

        dialog = PublishNoteDialog(
            content=content,
            active_profile=active,
            store=self._profile_store,
            relay_pool=self._relay_pool,
            relay_directory=self._relay_directory,
            session_pool=self._session_pool,
            entitled_relays=self._entitled_relays,
            known_people=self._known_people,
            search_client=self._search_client,
            avatars=self._avatars,
            media=media,
            parent=self,
            is_dark=self.is_dark_theme,
        )
        dialog.published.connect(self._on_nostr_note_published)
        dialog.exec()

    def _toggle_ticks(self) -> None:
        ed = self.current_editor()
        if ed is not None:
            ed.toggle_ticks()
            self._update_format_buttons()

    def _footnote_jump(self) -> None:
        ed = self.current_editor()
        if ed is not None:
            ed.jump_to_footnote(ed.textCursor())

    def _fill_insert_menu(self) -> None:
        """The Insert menu as it opens: Nostr's insertions only while a
        Nostr account is in use."""
        active = bool(self.nostr_state.active)
        for action in self._insert_nostr_actions:
            self.m_insert.removeAction(action)
        self._insert_nostr_separator.setVisible(active)
        if active:
            self.m_insert.addActions(self._insert_nostr_actions)

    def _fill_view_menu(self) -> None:
        """The View menu as it opens: Article Details only while a Nostr
        account is in use (D-1)."""
        active = bool(self.nostr_state.active)
        self._view_nostr_separator.setVisible(active)
        self.act_article_details.setVisible(active)

    # -- Article Details ----------------------------------------------------------

    def _show_article_details_panel(self, shown: bool) -> None:
        panel = self._article_details_panel
        if shown and self.nostr_state.active:
            self._fill_article_details_panel()
            panel.show()
            self._give_panel_room(panel, ARTICLE_DETAILS_WIDTH)
        else:
            if panel.isAncestorOf(QApplication.focusWidget()):
                ed = self.current_editor()
                if ed is not None:
                    ed.setFocus()
            panel.hide()

    def _fill_article_details_panel(self) -> None:
        """Article Details shows the article in the tab in front: its details
        (_article_details_of), or why there are none."""
        panel = getattr(self, "_article_details_panel", None)
        if panel is None or not panel.isVisible() and not self.act_article_details.isChecked():
            return
        ed = self.current_editor()
        binding = getattr(ed, "_draft_binding", None) if ed is not None else None
        if ed is None:
            panel.show_no_article(_("Article details belong to a document written here."))
        elif binding is not None and binding.inner_kind == INNER_KIND_SHORT_NOTE:
            panel.show_no_article(_("This is a note. Notes have no title, summary or cover; "
                                    "save it as an article to give it some."))
        else:
            fixed = (binding is not None and binding.inner_kind == INNER_KIND_LONG_FORM) or \
                self._published_article_of(ed) is not None
            panel.show_article(self._article_details_of(ed), fixed_identifier=fixed,
                               kept_in_file=kept_in_file(getattr(ed, "_front_matter", None)))

    def _pick_coauthor(self, anchor, chosen) -> None:
        """Who writes the article with its author, asked under ``anchor`` the
        way a mention is: the people you follow first, then a search; their
        Nostr address only once confirmed. ``chosen(person)`` answers."""
        if self._coauthor_picker is None:
            self._coauthor_picker = MentionPicker(
                self._known_people, self._search_client, avatars=self._avatars,
                parent=self, is_dark=self.is_dark_theme, address_check=self._address_check)
            self._coauthor_picker.picked.connect(
                lambda person: self._coauthor_chosen and self._coauthor_chosen(person))
        self._coauthor_chosen = chosen
        self._coauthor_picker.open_at(anchor.rect(), anchor)

    def _person_name(self, pubkey: str) -> str:
        """How a person is called: their name when known, else their short
        Nostr address."""
        person = self._known_people.get(pubkey)
        return (person.display_name if person is not None and person.display_name
                else refs.short_key(encode_npub(pubkey)))

    # -- mentions ----------------------------------------------------------------

    def _mention_label(self, entity: str) -> Optional[str]:
        """The name a mention shows: the person's, when known; None when
        ``entity`` names no person."""
        def name(pubkey: str):
            person = self._known_people.get(pubkey)
            return person.display_name if person is not None else None
        return refs.person_name(entity, name)

    def _ask_mention(self, ed, at: int) -> None:
        """Who to mention, asked at the caret ("@" typed at ``at``, or -1
        from Insert > Mention): the people you follow first, then a search;
        their Nostr address only once confirmed. Escape keeps what was
        typed."""
        if ed is None or not self.nostr_state.active or self._editor_kind(ed) != "rich":
            return
        if self._mention_picker is None:
            self._mention_picker = MentionPicker(
                self._known_people, self._search_client, avatars=self._avatars,
                parent=self, is_dark=self.is_dark_theme, address_check=self._address_check)
            self._mention_picker.picked.connect(self._mention_picked)
        self._mention_target = (ed, at)
        self._mention_picker.open_at(ed.cursorRect(), ed.viewport())

    def _mention_picked(self, person) -> None:
        ed, at = getattr(self, "_mention_target", (None, -1))
        if ed is None or not shiboken6.isValid(ed):
            return
        self.activateWindow()
        ed.setFocus()
        if person.relay_hint:
            href = "nostr:" + encode_nprofile(person.pubkey, [person.relay_hint])
        else:
            href = "nostr:" + encode_npub(person.pubkey)
        ed.insert_mention(at, href, person.display_name or refs.short_key(
            encode_npub(person.pubkey)))

    def _ask_reference(self, ed) -> None:
        """Insert > Note or Article: a link to one asked at the caret, the
        way Add Link asks for an address; it goes in as a paragraph of its
        own (readers show it as a card)."""
        if ed is None or not self.nostr_state.active or self._editor_kind(ed) != "rich":
            return
        popover = ReferencePopover(ed)
        popover.chosen.connect(lambda reference: ed.insert_reference(reference.uri))
        # Escape, Cancel or a click elsewhere: the writing goes on. (A
        # method: Qt drops the connection if the editor goes first.)
        popover.destroyed.connect(ed.take_focus_back)
        popover.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        popover.show_below(ed.cursorRect(), ed.viewport())

    # -- audio and video ----------------------------------------------------------

    def _may_insert_audio_video(self, ed) -> bool:
        return (ed is not None and self.nostr_state.active
                and self._editor_kind(ed) == "rich")

    def _ask_audio_or_video(self) -> None:
        """Insert > Audio or Video: a file chosen, uploaded to the media
        servers, and put in the text as its own paragraph."""
        ed = self.current_editor()
        if not self._may_insert_audio_video(ed):
            return
        patterns = " ".join("*" + ext for ext in media_files.AUDIO_VIDEO_EXTENSIONS)
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Insert Audio or Video"), "",
            _("Audio and video ({patterns})").format(patterns=patterns))
        if path:
            self._upload_into(ed, file_paths.normalize(path))

    def _upload_limit(self) -> int:
        """The largest file one of the media servers takes."""
        servers = self._media_store.target_servers()
        largest = max((get_effective_max_file(server) for server in servers),
                      default=BLOSSOM_MAX_FILE_SIZE)
        return min(largest, BLOSSOM_MAX_FILE_SIZE)

    def _upload_into(self, ed, path: str) -> None:
        """Put the audio or video file ``path`` into ``ed`` as a paragraph of
        its own and send it to the media servers. It is an object of the
        text, as a picture is (an asset, named by its hash): finishing its
        upload changes nothing the person wrote and no step of Undo, and it
        outlives a crash or a restart. A file larger than every media server
        takes is refused at once, saying the limit (nostrdesign: before a
        long wait, not after)."""
        name = os.path.basename(path)
        mime = media_files.type_of(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        if size > self._upload_limit():
            inform(self, title=_("“{name}” is too large").format(name=name),
                   message=_("Your media servers take files up to {size}.").format(
                       size=format_size(self._upload_limit())))
            return
        if media_files.kind_of(mime or "") is None or size <= 0:
            self._say_cannot_add(name)
            return
        # Where it goes: the caret now, followed through what is typed while
        # a large file is read and hashed off the UI thread (staying before
        # what is typed there).
        at = QTextCursor(ed.textCursor())
        at.setKeepPositionOnInsert(True)
        # Dropped when the document closes or the app quits meanwhile
        # (close_tab, closeEvent): nothing is then kept or uploaded.
        read = _MediaRead()
        ed._media_reads = getattr(ed, "_media_reads", []) + [read]
        run_for_size(size, lambda: self._asset_manager.store_media_file(
                         path, read.keeping, read.kept),
                     lambda stored: self._insert_media(ed, at, stored, mime, name, read),
                     lambda _error: self._media_read_failed(ed, name, read), parent=self)

    def _drop_media_reads(self, ed) -> None:
        """The audio and video still being read for ``ed`` go nowhere, and
        leave no copy, at whatever point they are (_MediaRead)."""
        for read in getattr(ed, "_media_reads", []) if ed is not None else []:
            sha = read.drop()
            if sha is not None:
                self._asset_manager.release_stored(sha)

    def _media_read_failed(self, ed, name: str, read: "_MediaRead") -> None:
        """Say a file couldn't be read, while the document it was for is
        still open; once it is gone, the read ends quietly, as an operation
        cancelled does in Mail and the Finder."""
        if read.dropped or self._editor_widget_index(ed) < 0:
            return
        self._say_cannot_add(name)

    def _say_cannot_add(self, name: str) -> None:
        inform(self, title=_("Couldn’t add “{name}”").format(name=name),
               message=_("MyEditor can read audio and video files of these kinds: "
                         "{kinds}.").format(kinds=", ".join(media_files.AUDIO_VIDEO_EXTENSIONS)))

    def _insert_media(self, ed, at, stored, mime: str, name: str,
                      read: "_MediaRead") -> None:
        """The file kept in the local store (``stored``: its sha256 and size)
        as an object of ``ed``'s text, in a paragraph of its own at ``at``,
        and on its way to the media servers."""
        if stored is None:
            return                         # dropped before it was kept
        sha, size = stored
        if self._editor_widget_index(ed) < 0 or not read.take_over():
            # The document was closed, or the app quit, while the file was
            # read: it goes nowhere, nothing is uploaded, nothing is kept.
            dropped = read.drop()
            if dropped is not None:
                self._asset_manager.release_stored(dropped)
            return
        asset = self._asset_manager.adopt_stored_media(sha, size=size, mime=mime, name=name)
        self._asset_manager.release_stored(sha, taken=asset is not None)
        if asset is None:
            self._say_cannot_add(name)
            return
        self._insert_media_card(ed, at, asset)
        self._asset_manager.request_upload(asset.sha256)

    def _insert_media_card(self, ed, at, asset) -> None:
        """Audio or video ``asset`` as an object of ``ed``'s text: its card,
        in a paragraph of its own at ``at``. The one way it goes in, from
        Insert Audio or Video and from the Media Library alike."""
        image = QTextImageFormat()
        image.setName(asset.key)
        image.setProperty(QTextImageFormat.ImageAltText, asset.alt)
        image.setWidth(media_card.WIDTH)
        image.setHeight(media_card.HEIGHT)
        ed.document().addResource(QTextDocument.ImageResource, QUrl(asset.key),
                                  self._media_card(asset))
        caret_there = ed.textCursor().position() == at.position()
        cursor = QTextCursor(ed.document())
        cursor.setPosition(at.position())
        rich_text.insert_own_paragraph(cursor, image=image)
        if caret_there:
            ed.setTextCursor(cursor)

    def _uploading(self, *editors) -> list:
        """The audio and video in ``editors`` still on its way to the media
        servers."""
        return [asset for ed in editors for asset in self._media_not_uploaded(ed.document())
                if asset.upload_state in (AssetState.QUEUED, AssetState.SIGNING,
                                          AssetState.UPLOADING)]

    def _library_uploads(self) -> list:
        """Files added in the Media Library still on their way, as the
        quit question names them (a failed one is not stopped by quitting:
        nothing is moving)."""
        return [SimpleNamespace(alt=added.name, library=True,
                                finishing=added.state == "finishing")
                for added in self._media_store.added_uploads() if added.state != "failed"]

    def _media_saves(self) -> list:
        """The Save As downloads quitting would stop: the one running and
        the ones waiting, by the path each is saved to."""
        return self._media_saver.pending()

    @staticmethod
    def _still_uploading(uploading: list, saving: list = ()) -> str:
        if not saving:
            return ngettext("“{name}” is still uploading.", "{count} files are still uploading.",
                            len(uploading)).format(name=uploading[0].alt, count=len(uploading))
        if not uploading:
            return ngettext("A file is still being saved.", "{count} files are still being saved.",
                            len(saving)).format(count=len(saving))
        count = len(uploading) + len(saving)
        return ngettext("{count} file is still uploading or being saved.",
                        "{count} files are still uploading or being saved.",
                        count).format(count=count)

    @staticmethod
    def _what_leaving_does(uploading: list = (), *, quitting: bool, saving: list = ()) -> str:
        """What closing (or quitting) does to the uploads still running
        (audio and video in a document, files added to the Media Library)
        and, quitting, to the Save As downloads. A library file its first
        server has is in the library already: quitting stops only its
        copies to the other servers. Closing a document leaves a save
        running."""
        if quitting and saving:
            if not uploading:
                return ngettext("Quitting now stops the download, and the file isn’t saved.",
                                "Quitting now stops the downloads, and the files aren’t saved.",
                                len(saving))
            return " ".join((
                MainWindow._what_leaving_does(uploading, quitting=True),
                ngettext("Quitting also stops saving “{name}”.",
                         "Quitting also stops saving {count} files.",
                         len(saving)).format(name=os.path.basename(saving[0]),
                                             count=len(saving))))
        copying = [u for u in uploading if getattr(u, "finishing", False)]
        if quitting and copying and len(copying) == len(uploading):
            return ngettext(
                "Quitting now stops copying it to your other media servers. It’s already in "
                "your Media Library.",
                "Quitting now stops copying them to your other media servers. They’re already "
                "in your Media Library.", len(copying))
        uploading = [u for u in uploading if not getattr(u, "finishing", False)]
        library = sum(1 for u in uploading if getattr(u, "library", False))
        if quitting and library == len(uploading) and library:
            return ngettext(
                "Quitting now stops the upload, and the file isn’t added to your Media Library.",
                "Quitting now stops the uploads, and the files aren’t added to your Media "
                "Library.", library)
        if quitting and library:
            return _("Quitting now stops every upload: the documents won’t hold their files, "
                     "and your Media Library won’t get its new ones.")
        if quitting:
            return _("Quitting now stops the upload, and the document won’t hold the file.")
        return _("If you close the document now, the upload goes on to your media library, "
                 "but the document doesn’t keep it.")

    def _may_leave_uploads(self, uploading: list, *, quitting: bool,
                           saving: list = ()) -> bool:
        """With nothing unsaved, closing or quitting while audio or video
        uploads (or, quitting, while a Save As downloads) asks this alone
        (Cancel is the default). True to go on."""
        saving = saving if quitting else []
        if not uploading and not saving:
            return True
        return confirm_destructive(
            self, title=self._still_uploading(uploading, saving),
            message=self._what_leaving_does(uploading, quitting=quitting, saving=saving),
            action=_("Quit Anyway") if quitting else _("Close Anyway"))

    def _save_question_words(self, uploading: list, *, quitting: bool,
                             saving: list = ()) -> str:
        """The save question's words: with audio or video still uploading
        (or, quitting, a Save As downloading), what leaving does to it
        first, so it is one question, not two."""
        saving = saving if quitting else []
        if not uploading and not saving:
            return ""
        return " ".join((self._still_uploading(uploading, saving),
                         self._what_leaving_does(uploading, quitting=quitting, saving=saving),
                         _("Your changes will be lost if you don’t save them.")))

    def _media_name(self, url: str) -> Optional[str]:
        """The name an audio or video file had, by the address it was
        uploaded to (its hash names it); None when it is not one of this
        app's."""
        sha = hash_from_url(url)
        asset = self._asset_manager.get(sha) if sha else None
        return asset.alt if asset is not None and asset.is_media and asset.alt else None

    def _outside_of(self, name: str) -> Optional[paste.Outside]:
        """How one of this app's objects (an asset) goes to another app on
        the clipboard: by its address once uploaded, else audio or video by
        its name and a picture by its bytes; None for any other object."""
        sha = parse_asset_key(name)
        asset = self._asset_manager.get(sha) if sha else None
        if asset is None:
            return None
        address = _published_address(asset) if asset.is_uploaded else ""
        if asset.is_media:
            return paste.Outside(True, address or asset.alt, address)
        data = None if address else self._asset_manager.resolve_bytes(asset.key)
        return paste.Outside(False, address or asset.alt, address,
                             address or (image_data_uri(data) or "" if data else ""))

    def _media_not_uploaded(self, doc) -> list:
        """The audio and video in ``doc`` not on a media server yet."""
        found = []
        for name in dict.fromkeys(iter_image_names(doc)):
            sha = parse_asset_key(name)
            asset = self._asset_manager.get(sha) if sha else None
            if asset is not None and asset.is_media and not asset.is_uploaded:
                found.append(asset)
        return found

    def _resolve_asset_image(self, name: str):
        """What an object of the text shows: an audio or video file's card,
        a picture's picture."""
        sha = parse_asset_key(name)
        asset = self._asset_manager.get(sha) if sha else None
        if asset is not None and asset.is_media:
            return self._media_card(asset)
        return self._asset_manager.resolve_image(name)

    def _media_card(self, asset):
        """The card of an audio or video file, saying how its upload is."""
        kind = media_files.kind_of(asset.mime)
        title = (_("Audio · {name}") if kind == media_files.AUDIO
                 else _("Video · {name}")).format(name=asset.alt or asset.sha256[:12])
        state = asset.upload_state
        if asset.is_uploaded:
            words = ""
        elif state is AssetState.FAILED:
            words = _("Couldn’t upload. Publishing or saving as a draft tries again.")
        elif state in (AssetState.QUEUED, AssetState.SIGNING, AssetState.UPLOADING):
            percent = self._media_progress.get(asset.sha256)
            words = (_("Uploading… {percent} %").format(percent=_number(percent))
                     if percent is not None else _("Uploading…"))
        else:
            words = _("Not uploaded yet. Publishing or saving as a draft uploads it.")
        return media_card.render(title, words, dark=self.is_dark_theme, mime=asset.mime)

    def _redraw_media_cards(self) -> None:
        """Draw the cards of the audio and video in the open documents
        again, in the appearance now in use."""
        shown = set()
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            for name in iter_image_names(ed.document()) if ed is not None else ():
                media = _media_asset(self._asset_manager, name)
                if media is not None and media.sha256 not in shown:
                    shown.add(media.sha256)
                    self._refresh_asset_in_documents(media.sha256)

    def _on_library_upload_failed(self, name: str, reason: str) -> None:
        """A file added in the Media Library did not upload while no
        library window is open: counted in the status bar until the
        library is opened. The next library window shows it with Retry."""
        if self._media_store.can_retry(name) and self._visible_media_library() is None:
            self._library_failures[name] = reason
            self._show_library_failures()

    def _clear_library_failures(self) -> None:
        """The library is open, and shows each failure itself."""
        self._library_failures.clear()
        self._show_library_failures()

    def _show_library_failures(self) -> None:
        failures = self._library_failures
        text = ngettext("“{name}” wasn’t added to your Media Library.",
                        "{count} files weren’t added to your Media Library.",
                        len(failures)).format(name=next(iter(failures), ""),
                                              count=len(failures)) if failures else ""
        self._library_problem.setText(text)
        self._library_problem.setToolTip("\n".join(
            _("“{name}” wasn’t added to your Media Library. {reason}").format(
                name=name, reason=reason) for name, reason in failures.items()))
        self._library_problem.setStyleSheet(
            f"color: {theme.error_text_color(self.is_dark_theme)}; padding-right: 4px;")
        link = theme.dialog_link_color(self.is_dark_theme)
        self._library_open.setStyleSheet(
            f"#LibraryOpen {{ color: {link}; border: none; background: transparent; "
            f"padding: 0 4px; }} #LibraryOpen:hover {{ text-decoration: underline; }}")
        self._library_open.setAccessibleDescription(text)
        self._library_problem.setVisible(bool(text))
        self._library_open.setVisible(bool(text))
        self._fit_status_bar()

    def _fit_status_bar(self) -> None:
        """A message in the status bar keeps its room in a narrow window.
        What says least steps aside first, while it doesn't fit beside
        the message and what matters more: the word count, then the
        caret's line; then the library failure line shortens, and where
        not even a few letters of it fit, it steps aside (Open Media
        Library too) until the message goes. Without a message, each
        takes all it needs."""
        label = self._library_problem
        no_limit = 16777215                              # Qt's QWIDGETSIZE_MAX
        for widget in (self._words_label, self._line_label, label, self._library_open):
            widget.setMaximumWidth(no_limit)
        message = self.status.currentMessage()
        if not message:
            return
        spacing = self.status.layout().spacing()

        def width_of(widget) -> int:
            return widget.sizeHint().width() + spacing if widget.isVisible() else 0

        needed = self.status.fontMetrics().horizontalAdvance(message) + 40   # its margins
        room = (self.status.width() - needed
                - sum(width_of(w) for w in (self._backup_problem, self._backup_retry,
                                            self._library_open)))
        # The failure line at its full width, then the caret's line, then
        # the word count: each stays while it fits beside those before it.
        kept = width_of(label)
        for widget in (self._line_label, self._words_label):
            if room - kept >= width_of(widget):
                kept += width_of(widget)
            else:
                widget.setMaximumWidth(0)
                kept += spacing
        if label.isVisible():
            fits = room - (kept - width_of(label))
            if fits < label.minimumSizeHint().width():
                fits = 0
                self._library_open.setMaximumWidth(0)
            label.setMaximumWidth(fits)

    def _on_media_upload_progress(self, name: str, sent: int, total: int) -> None:
        sha = parse_asset_key(name)
        asset = self._asset_manager.get(sha) if sha else None
        if asset is None or not asset.is_media or total <= 0:
            return
        percent = min(100, sent * 100 // total)
        if self._media_progress.get(sha) != percent:
            self._media_progress[sha] = percent
            self._refresh_asset_in_documents(sha)

    # -- secret keys: never pasted where Nostr is written ---------------------
    # (nor published: nostr/ui/secret_key_check.py, at the publish dialogs)

    def _guard_paste(self, ed, text: str):
        """A paste with a Nostr secret key in it, into prose while a Nostr
        account is in use, is stopped with a warning (the key would be one
        click from being published); the person may paste the link to the
        key's owner instead. Plain text, code and other people's
        documents without an account paste as they are."""
        if (not refs.secret_keys_in(text) or not self.nostr_state.active
                or not (self._editor_kind(ed) == "rich" or getattr(ed, "_markdown_source", False))):
            return text
        public = refs.without_secret_keys(text)
        buttons = [Button(_("Don't Paste"), None, DEFAULT)]
        if public is not None:
            buttons.insert(0, Button(_("Paste Public Key"), public, NORMAL))
        return ask(self, title=_("This is a secret key"), caution=True, buttons=buttons,
                   message=_("Anyone who has it can publish as its owner and read their "
                             "private messages, and once published it can never be taken "
                             "back. MyEditor did not paste it. Keep it in a password "
                             "manager or a signer app."))

    def _on_nostr_note_published(self, _event_id_hex: str, results):
        self._say_published(results, article=False)

    def _on_nostr_publish_article(self):
        active = self._profile_store.default()
        if active is None:
            self._offer_publishing_account()
            return

        ed = self.current_editor()
        if not self._confirm_images_uploaded(ed):
            return
        body, media = self._publish_payload(ed, "markdown") if ed is not None else ("", [])
        body = body.rstrip()
        if not body:
            inform(self, title=_("Nothing to publish"),
                   message=_("The current document is empty."))
            return

        # Pre-fill metadata. Precedence:
        #   1. Draft binding (so a published article inherits the exact
        #      ``d`` and title from its draft, preserving the addressable
        #      coordinate so re-publishing replaces the draft in place).
        #   2. File path basename for disk-backed tabs.
        #   3. First non-blank line as a heuristic title for free-form tabs.
        # A draft of an article (an import, an edit) brings all its details:
        # summary, cover, hashtags, source, and when it first went out (a
        # new version keeps that date).
        details = self._article_details_of(ed)
        default_title = details.title
        default_slug = details.identifier
        first_published = details.published_at
        if not default_title:
            first_line = next((ln for ln in body.splitlines() if ln.strip()), "")
            default_title = first_line.lstrip("# ").strip()
        if not default_slug:
            path = self.current_path()
            default_slug = (
                os.path.splitext(os.path.basename(path))[0] if path else ""
            )

        dialog = PublishArticleDialog(
            body_markdown=body,
            active_profile=active,
            store=self._profile_store,
            relay_pool=self._relay_pool,
            relay_directory=self._relay_directory,
            session_pool=self._session_pool,
            entitled_relays=self._entitled_relays,
            known_people=self._known_people,
            search_client=self._search_client,
            avatars=self._avatars,
            media_store=self._media_store,
            media_visibility=self._media_visibility,
            copy_maker=self._copy_maker,
            private_library=self._private_library,
            default_title=default_title,
            default_slug=default_slug,
            first_published=first_published,
            details=details,
            first_publications=self._first_publications,
            media=media,
            parent=self,
            is_dark=self.is_dark_theme,
        )
        dialog.published.connect(self._on_nostr_article_published)
        dialog.exec()
        # What was changed there is the article's, as in Article Details.
        if dialog.details_changed():
            self._set_article_details(ed, dialog.details())

    def _on_nostr_article_published(self, _naddr: str, results):
        self._say_published(results, article=True)

    def _say_published(self, results, *, article: bool) -> None:
        """That it went out, in the status bar, and how many relays took it
        when not every one did. No event id, which the person has nothing
        to do with: the Published list and the dialog itself say more."""
        accepted = sum(1 for _relay, ok, _message in results if ok)
        total = len(results)
        if accepted >= total:
            message = (_("Your article is published.") if article
                       else _("Your note is published."))
        elif article:
            message = ngettext("Your article is published; {accepted} of {total} relay took it.",
                               "Your article is published; {accepted} of {total} relays took "
                               "it.", total).format(accepted=accepted, total=total)
        else:
            message = ngettext("Your note is published; {accepted} of {total} relay took it.",
                               "Your note is published; {accepted} of {total} relays took it.",
                               total).format(accepted=accepted, total=total)
        self.status.showMessage(message, 8000)

    # -- media (Blossom) --------------------------------------------------

    def _on_nostr_media_library(self):
        """Open the Media Library dialog. Non-modal so the user can keep
        editing while uploads run in the background."""
        active = self._profile_store.default()
        if active is None:
            self._offer_media_account()
            return
        # Reading the private library is where the signer prompts are, so
        # it happens when the user opens their media and not before.
        # Re-binding the profile already loaded is a no-op.
        self._private_library.bind_profile(active)
        dialog = MediaLibraryDialog(
            store=self._media_store,
            is_dark=self.is_dark_theme,
            pick_mode=False,
            visibility=self._media_visibility,
            saver=self._media_saver,
            parent=self,
        )
        dialog.bind_private_library(self._private_library)
        dialog.servers_requested.connect(self._open_media_servers)
        self._clear_library_failures()
        # The dialog deletes itself on close; forget it then, so nothing
        # later asks a deleted object whether it is visible.
        dialog.destroyed.connect(lambda _obj=None, d=dialog: self._forget_media_library(d))
        # A Save As it started goes on: said here from now on.
        dialog.destroyed.connect(lambda _obj=None: self._say_media_save_running())
        self._media_library_dialog = dialog
        dialog.show()

    def _on_nostr_insert_image(self):
        """Open the Media Library in picker mode, restricted to images.
        On selection, materialize the blob to the local cache and insert
        it at the current cursor in the active editor."""
        active = self._profile_store.default()
        if active is None:
            self._offer_media_account()
            return
        ed = self.current_editor()
        if ed is None:
            self.new_tab()
            ed = self.current_editor()
        if ed is None:
            return
        self._private_library.bind_profile(active)
        dialog = MediaLibraryDialog(
            store=self._media_store,
            is_dark=self.is_dark_theme,
            pick_mode=True,
            visibility=self._media_visibility,
            saver=self._media_saver,
            parent=self,
        )
        dialog.bind_private_library(self._private_library)
        # Pre-select images in the picker - videos / audio can't be
        # inserted as inline document objects.
        dialog._filter_combo.setCurrentIndex(1)
        dialog.file_picked.connect(
            lambda media, alt, e=ed: self._insert_media_at_cursor(media, e, alt)
        )
        self._media_picker = dialog
        try:
            dialog.exec()
        finally:
            self._media_picker = None
        self._say_media_save_running()

    def _insert_media_at_cursor(self, media: MediaFile, editor, alt_text: str = "") -> None:
        """Insert a library pick at the editor's cursor, immediately.

        There is no fetch-then-insert detour: the blob is already hosted,
        so the asset goes in at once and the bytes arrive underneath it
        when they arrive.

        Unless the pick is private, which is the one case that has to
        stop and ask. A document is written to be published, so the
        address that goes in here is the address readers will fetch, and
        for a private file that address serves ciphertext. The gate turns
        it into a public copy the user agreed to, or nothing goes in at
        all.
        """
        picked = resolve_pick(
            media,
            visibility=self._media_visibility,
            maker=self._copy_maker,
            is_dark=self.is_dark_theme,
            parent=self,
        )
        if not picked.ok:
            if picked.reason:
                self.status.showMessage(picked.reason, 8000)
            return

        if media_files.kind_of(picked.mime) is not None \
                and self._may_insert_audio_video(editor):
            # Audio or video already on the servers: the same card as
            # Insert Audio or Video makes, through the same record, with
            # nothing downloaded or sent again.
            asset = self._asset_manager.adopt_library_file(
                sha256=picked.sha256, remote_url=picked.url, mime=picked.mime,
                size=picked.size, alt=alt_text or self._library_name(media))
            if asset is not None:
                self._insert_media_card(editor, QTextCursor(editor.textCursor()), asset)
                return

        if not (picked.mime or "").startswith("image/"):
            self._insert_url_as_text(editor, picked.url)
            return

        asset = self._asset_manager.adopt_library_file(
            sha256=picked.sha256,
            remote_url=picked.url,
            mime=picked.mime,
            size=picked.size,
            alt=alt_text,
        )
        if asset is None:
            self._insert_url_as_text(editor, picked.url)
            return
        self._insert_asset(editor, asset, alt=alt_text or "image")

    def _library_name(self, media) -> str:
        """The name a library file goes by: the one it was added with, a
        private file's own, else the day it was added."""
        name = self._media_store.name_of(media.hash) or self._media_visibility.name_of(media.hash)
        uploaded = int(getattr(media, "uploaded_at_ms", 0) or 0)
        if name or not uploaded:
            return name
        return i18n.format_day(QDateTime.fromMSecsSinceEpoch(uploaded), short_month=True)

    def _insert_url_as_text(self, editor, url: str) -> None:
        """Fallback for media that cannot be an inline image."""
        if not url_safety.is_safe_media_url(url):
            self.status.showMessage(_("That link cannot be inserted."), 5000)
            return
        cursor = editor.textCursor()
        cursor.insertText(url)
        editor.setTextCursor(cursor)

    def _insert_asset(self, editor, asset, *, alt: str) -> None:
        """Put one asset into the document. Local only, cannot fail.

        The same real image format goes into markdown-loaded and rich
        text tabs alike. Inserting the literal text ``![alt](url)`` was
        the old markdown path, and Qt's markdown writer escapes it into
        a link, so the image reopened as text.
        """
        alt = alt or "image"
        data = self._asset_manager.resolve_bytes(asset.key)
        if data:
            # Priming the document means the first paint needs no
            # resolver round trip and no decode on the paint path.
            editor.document().addResource(
                QTextDocument.ImageResource, QUrl(asset.key), QByteArray(data)
            )

        fmt = QTextImageFormat()
        fmt.setName(asset.key)
        # Never leave alt empty: Qt's markdown writer substitutes the
        # word "image" for an empty one, which makes round trips differ
        # from what was inserted.
        fmt.setProperty(QTextImageFormat.ImageAltText, alt)

        cursor = editor.textCursor()
        cursor.insertImage(fmt)
        editor.setTextCursor(cursor)

    def _refresh_asset_in_documents(self, sha: str) -> None:
        """Repaint every open document that shows this asset.

        Registering the resource and marking the range dirty replaces
        the placeholder in place without touching the modified flag, so
        a saved document stays saved and no backup is triggered.
        """
        key = asset_key(sha)
        asset = self._asset_manager.get(sha)
        if asset is not None and asset.is_media:
            payload = self._media_card(asset)
        else:
            data = self._asset_manager.resolve_bytes(key)
            if not data:
                return
            payload = QByteArray(data)
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed is None:
                continue
            doc = ed.document()
            if key not in set(iter_image_names(doc)):
                continue
            doc.addResource(QTextDocument.ImageResource, QUrl(key), payload)
            doc.markContentsDirty(0, doc.characterCount())

    def _on_asset_upload_failed(self, sha: str, code: str) -> None:
        waiting = len(self._asset_manager.failed_assets())
        message = friendly_message(code)
        if waiting > 1:
            message += " " + ngettext("{count} file is waiting.", "{count} files are waiting.",
                                      waiting).format(count=waiting)
        self.status.showMessage(message, 8000)

    # -- Blossom: paste image from clipboard ------------------------------

    def _handle_pasted_image(self, editor, image) -> None:
        """A clipboard image arrived. Insert it, then ask about uploading.

        The insert is unconditional and comes first. A pasted screenshot
        exists nowhere else, so a missing signer, a refused upload or no
        network must never be a reason for it to vanish.
        """
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        if not image.save(buf, "PNG"):
            # Nothing image-shaped survived; the editor already handed
            # us the event, so run its fallback from here.
            editor.paste_normalized()
            return
        body = bytes(buf.data())

        asset = self._asset_manager.adopt_bytes(body, mime="image/png", alt="image")
        if asset is None:
            self.status.showMessage(_("That image could not be added."), 5000)
            return
        self._insert_asset(editor, asset, alt="image")

        if asset.is_uploaded or self._profile_store.default() is None:
            return          # nothing of Nostr without an account

        choice = self._paste_choice()
        if choice == "never":
            return
        if choice != "always" and not self._confirm_paste_upload():
            return
        self._asset_manager.request_upload(asset.sha256)

    def _confirm_paste_upload(self) -> bool:
        """Ask before a pasted image leaves the machine.

        Keep local is the default: a paste is a high-frequency, low
        intent gesture, so the outcome of an accidental one has to be
        that nothing was published.
        """
        # What the person decides is who can see it, which the message
        # says; the servers are their own, listed in Media Servers. A
        # paste is a quick, low-intent gesture, so Keep Local is the
        # default.
        upload, remember = ask_with_checkbox(
            self,
            title=_("Upload this image to your media servers?"),
            message=_("Anyone with the link can view it. The image is already in "
                      "your document and stays there either way."),
            buttons=(Button(_("Upload"), True, NORMAL), Button(_("Keep Local"), False, DEFAULT)),
            checkbox=_("Remember this choice"))
        if remember:
            self._store_paste_choice("always" if upload else "never")
        return upload

    def _paste_choice(self) -> str:
        """What a pasted image does while an account is in use: "ask",
        "always" (upload) or "never"."""
        return load_settings().get(_PASTE_UPLOAD_SETTING, "ask")

    def _store_paste_choice(self, value: str) -> None:
        """The one place the choice is stored, from Settings or from the
        alert's Remember This Choice; an open Settings window shows it."""
        save_setting(_PASTE_UPLOAD_SETTING, value)
        if getattr(self, "_settings_window", None) is not None:
            self._settings_window.refresh()

    # ----------------------------------------------------------------------
    # NOSTR - drafts panel toggling
    # ----------------------------------------------------------------------

    def _on_toggle_drafts_panel(self):
        """Menu action: show/hide the side-docked drafts panel.

        First time we show the panel we also size the splitter so the
        editor doesn't lose more than necessary. Subsequent toggles
        preserve the user's resize. The QSplitter naturally clamps the
        panel between its min and max widths.
        """
        if self._drafts_panel is None:
            return
        if self._drafts_panel.isVisible():
            self._hide_drafts_panel()
        else:
            self._show_drafts_panel()

    def _show_drafts_panel(self) -> None:
        if self._drafts_panel is None:
            return
        self._drafts_panel.show()
        # If the panel currently has zero width (its first appearance),
        # split the central area so the panel gets its default width
        # while the editor keeps the remainder.
        self._give_panel_room(self._drafts_panel, DEFAULT_PANEL_WIDTH)
        self.act_nostr_drafts.setChecked(True)
        self._draft_sync.refresh()
        self._draft_deletions.refresh()
        self._follow_account_in_published()
        self._update_refresh_command()

    def _give_panel_room(self, panel, width: int) -> None:
        """A side panel shown for the first time gets ``width`` from the
        document's side; later, the width the person dragged it to."""
        splitter = self._central_splitter
        index = splitter.indexOf(panel)
        sizes = splitter.sizes()
        if index < 0 or sizes[index] > 0:
            return
        room = min(width, max(sizes[0] - 360, 240))
        sizes[0] -= room
        sizes[index] = room
        splitter.setSizes(sizes)

    def _hide_drafts_panel(self) -> None:
        if self._drafts_panel is None:
            return
        self._drafts_panel.hide()
        self.act_nostr_drafts.setChecked(False)
        self._update_refresh_command()

    def _refresh_drafts_panel(self) -> None:
        """Nostr > Refresh Drafts: the panel's view shown reads again."""
        if self._drafts_panel is not None and self._drafts_panel.isVisible():
            self._drafts_panel.refresh_shown_view()

    def _update_refresh_command(self, *_view) -> None:
        """Refresh is named for the panel's view shown, and offered while
        the panel is shown with an account in use."""
        action = getattr(self, "act_refresh_drafts", None)
        panel = getattr(self, "_drafts_panel", None)
        if action is None or panel is None:
            return
        action.setText(panel.refresh_name())
        action.setEnabled(panel.isVisible() and self._profile_store.default() is not None)

    # ----------------------------------------------------------------------
    # NOSTR - drafts panel signal handlers
    # ----------------------------------------------------------------------

    def _follow_account_in_published(self, *_view) -> None:
        """The Published view shows the account in use. It reads the
        relays only while it is shown: shown, it gets the account now;
        hidden, it lets go of another account's items at once and reads
        when it is shown next."""
        view = getattr(self, "_published_view", None)
        if view is None:
            return
        profile = self._profile_store.default()
        shown = (self._drafts_panel.isVisible()
                 and self._drafts_panel.current_view() == PUBLISHED_VIEW)
        held = view.model.account
        if shown:
            view.set_account(profile)
        elif held is not None and (profile is None or profile.user_pubkey.lower()
                                   != held.user_pubkey.lower()):
            view.set_account(None)

    def _on_published_status(self, text: str) -> None:
        if text:
            self.status.showMessage(text, 8000)
        else:
            self.status.clearMessage()

    def _on_published_open(self, item) -> None:
        """Edit a published article in a tab bound to it: publishing the
        tab replaces the article (same identifier) and keeps the date it
        first went out, with its summary, cover and hashtags; saving it
        as a draft offers the same article. A tab that already holds the
        article, or a draft of it, comes forward instead.

        An article published from here keeps its draft under the same
        identifier, often with changes not published yet: that draft is
        what opens when it holds the published text, and otherwise the
        person chooses (Open Draft is the default), so the unpublished
        words are never hidden behind the published ones."""
        if not item.is_article:
            return
        author = str(item.event.get("pubkey") or "").lower()
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed is not None and self._edits_article(ed, item.identifier, author):
                self.tabs.setCurrentIndex(i)
                return
        draft = self._draft_of_published(item)
        if draft is not None:
            if draft.state is not DraftState.READY:
                # Its words are not known yet (still being decrypted, or
                # the signer app did not answer): nothing to compare, and
                # nothing to open as the draft.
                choice = ask(
                    self, title=_("This article has a draft that can’t be shown yet."),
                    message=_("The draft couldn’t be decrypted. Double-click it in the Drafts "
                              "panel to try again, or open the version that is published now.")
                    if draft.state is DraftState.FAILED else
                    _("The draft is still being decrypted. Choose Edit again once its title "
                      "shows in the Drafts panel, or open the version that is published now."),
                    buttons=(Button(_("Open Published Version"), "published", NORMAL),
                             Button(_("Cancel"), None, DEFAULT)))
            elif draft.content == str(item.event.get("content") or ""):
                choice = "draft"
            else:
                choice = ask(
                    self, title=_("This article has a draft with changes you haven’t "
                                  "published."),
                    message=_("Open the draft to go on with it, or open the version that "
                              "is published."),
                    buttons=(Button(_("Cancel"), None, CANCEL),
                             Button(_("Open Published Version"), "published", NORMAL),
                             Button(_("Open Draft"), "draft", DEFAULT)))
            if choice == "draft":
                self._on_panel_open_draft(item.identifier)
            if choice != "published":
                return
        self.new_tab()
        ed = self.current_editor()
        if ed is None:
            return
        self._load_markdown(ed, str(item.event.get("content") or ""))
        ed.document().setModified(False)
        details = ArticleDetails.from_tags(item.event.get("tags") or [],
                                           signer=str(item.event.get("pubkey") or ""))
        ed._published_article = details.with_changes(
            identifier=item.identifier, title=details.title or item.title,
            published_at=item.first_published)
        ed._published_author = str(item.event.get("pubkey") or "").lower()
        self._update_tab_title()

    def _draft_of_published(self, item):
        """The account's article draft with the published article's
        identifier, or None (drafts are known for the account in use only)."""
        author = str(item.event.get("pubkey") or "").lower()
        if author != (self._draft_store.profile_pubkey or "").lower():
            return None
        record = self._draft_store.get(item.identifier)
        return record if record is not None and record.is_article else None

    def _edits_article(self, ed, identifier: str, author: str) -> bool:
        """Whether a tab holds ``author``'s article ``identifier``: a draft
        of it, or, with no draft, opened from the Published view. A draft
        decides when there is one (saved under another identifier, the tab
        is that other article now), as it does for the tab's details and
        name. Another account's article of the same identifier is another
        article."""
        binding = getattr(ed, "_draft_binding", None)
        if binding is not None:
            return (binding.inner_kind == INNER_KIND_LONG_FORM
                    and binding.identifier == identifier
                    and binding.profile_pubkey.lower() in ("", author))
        article = getattr(ed, "_published_article", None)
        return (article is not None and article.identifier == identifier
                and getattr(ed, "_published_author", "") == author)

    def _open_draft_relays(self) -> None:
        """Draft Relays…: where the account in use keeps its drafts, in a
        sheet on this window."""
        profile = self._profile_store.default()
        if profile is None:
            return
        relays = PrivateDraftRelays(
            profile=profile, pool=self._relay_pool, session_pool=self._session_pool,
            directory=self._relay_directory, entitled_relays=self._entitled_relays)
        sheet = DraftRelaysSheet(relays, is_dark=self.is_dark_theme, parent=self)
        relays.setParent(sheet)                   # it goes with the sheet
        sheet.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        sheet.saved.connect(self._on_draft_relays_saved)
        sheet.open()

    def _on_draft_relays_saved(self, relays: list) -> None:
        if relays:
            text = ngettext("Drafts are now saved only on the relay you chose.",
                            "Drafts are now saved only on the {count} relays you chose.",
                            len(relays)).format(count=len(relays))
        else:
            text = _("Drafts are saved on your usual relays again.")
        self.status.showMessage(text, 8000)

    def _on_panel_copy_event_id(self, event_id: str) -> None:
        if event_id:
            self.status.showMessage(_("Copied event id {id}…").format(id=event_id[:10]), 3000)

    def _on_panel_open_draft(self, identifier: str) -> None:
        """Open a draft into a new editor tab.

        If a tab is already bound to this identifier, just activate it.
        Otherwise spin up a new untitled tab and load the decrypted body.
        """
        record = self._draft_store.get(identifier)
        if record is None or record.state is not DraftState.READY:
            self.status.showMessage(
                _("Draft isn't ready to open yet - still decrypting."), 4000
            )
            return

        # Activate an already-open tab if it's bound to this draft.
        for i in range(self.tabs.count()):
            ed = self._editor_from_widget(self.tabs.widget(i))
            if ed is None:
                continue
            binding = getattr(ed, "_draft_binding", None)
            if binding is not None and binding.identifier == identifier:
                self.tabs.setCurrentIndex(i)
                return

        # Fresh tab. Reuse ``new_tab`` so every per-tab wiring (backup,
        # timers, theming) is consistent with disk-opened tabs.
        self.new_tab()
        ed = self.current_editor()
        if ed is None:
            return
        self._load_draft_content(ed, record)
        ed.document().setModified(False)
        active = self._profile_store.default()
        ed._draft_binding = DraftBinding(
            identifier=record.identifier,
            inner_kind=record.inner_kind,
            event_id=record.event_id,
            created_at=record.created_at,
            title=record.title,
            profile_pubkey=active.user_pubkey.lower() if active else "",
        )
        # Deliberately NOT pre-setting ``_save_destination`` - the
        # destination dialog should still appear on first Ctrl+Shift+S
        # so the user can save the draft locally if they want. Silent
        # re-stashes go through Ctrl+S, which respects the binding via
        # ``_restash_with_binding``.
        self._update_tab_title()

    def _on_panel_publish_draft(self, identifier: str) -> None:
        """Promote a draft to a real (signed, public) Nostr publish.

        Routes through the existing publish dialogs. The simplest path:
        open the draft into a tab (so all current-tab-reading plumbing
        keeps working) then trigger the appropriate publish action.
        """
        record = self._draft_store.get(identifier)
        if record is None or record.state is not DraftState.READY:
            return
        self._on_panel_open_draft(identifier)
        if record.inner_kind == INNER_KIND_LONG_FORM:
            self._on_nostr_publish_article()
        else:
            self._on_nostr_publish_note()

    def _on_panel_retry_decrypt(self, identifier: str) -> None:
        """Re-attempt NIP-44 decryption for a previously-failed draft.

        Common trigger: the user missed the Amber approval prompt in
        time, the bunker request timed out, and the row is now sitting
        in FAILED state. ``DraftSync.retry_decrypt`` uses the
        ciphertext cached on the record, so this is a single fresh
        signer round-trip - no relay re-fetch.
        """
        self._draft_sync.retry_decrypt(identifier)
        self.status.showMessage(_("Trying again. Approve the request in your signer "
                                  "app\u2026"), 6000)

    def _on_panel_delete_drafts(self, identifiers: list) -> None:
        """Delete one draft or twenty, through one question and one run.

        Drafts written by other Nostr clients can use inner kinds this
        editor does not speak (an article draft, kind 30024, is read as
        an article: nostr/drafts.py). Tombstoning one of those from here
        could leave it visible in the client that made it, so they are
        separated out before anything is confirmed and named in the
        confirmation rather than failing partway through a run the user
        already approved.
        """
        profile = self._profile_store.default()
        if profile is None or not identifiers:
            return

        deletable: List[Tuple[str, int]] = []
        skipped: List[str] = []
        for identifier in identifiers:
            record = self._draft_store.get(identifier)
            if record is None:
                continue
            if record.inner_kind in SUPPORTED_INNER_KINDS:
                deletable.append((identifier, record.inner_kind))
            else:
                skipped.append(record.title or _("Untitled"))

        if not deletable:
            self._warn_all_drafts_foreign(skipped)
            return
        if not self._confirm_draft_deletion(deletable, skipped):
            return
        self._run_draft_deletion(profile, deletable)

    def _warn_all_drafts_foreign(self, skipped: List[str]) -> None:
        count = len(skipped)
        inform(
            self,
            title=ngettext("This draft was created by another Nostr client",
                           "These {count} drafts were created by other Nostr clients",
                           count).format(count=count),
            message=_("They use a draft format this editor doesn't recognise, so "
                      "removing them from here might leave them visible in the other "
                      "client.\n\nTo remove them cleanly, open them in the app that "
                      "created them and delete them there."),
            details="\n".join(skipped))

    def _confirm_draft_deletion(
        self, deletable: List[Tuple[str, int]], skipped: List[str],
    ) -> bool:
        count = len(deletable)
        if count == 1:
            record = self._draft_store.get(deletable[0][0])
            if record and record.title:
                heading = _("Delete \u201c{name}\u201d from your Nostr drafts?").format(
                    name=record.title)
            else:
                heading = _("Delete \u201cthis draft\u201d from your Nostr drafts?")
            action = _("Delete")
        else:
            heading = ngettext("Delete {count} draft from your Nostr drafts?",
                               "Delete {count} drafts from your Nostr drafts?",
                               count).format(count=count)
            action = ngettext("Delete {count} Draft", "Delete {count} Drafts",
                              count).format(count=count)

        # A draft whose slug holds a secret key is deleted by a request, not
        # by a replacement (DraftDeleteJob), so the key isn't sent again.
        keyed = sum(1 for identifier, _kind in deletable if refs.secret_keys_in(identifier))
        replaced = count - keyed
        lines = []
        if replaced:
            lines.append(ngettext(
                "A blank-content replacement will be published to your relays. "
                "Other clients (and your other devices) will treat the draft as removed. "
                "This action can't be undone.",
                "A blank-content replacement will be published to your relays. "
                "Other clients (and your other devices) will treat them as removed. "
                "This action can't be undone.", replaced))
        if keyed:
            lines.append(ngettext(
                "A draft whose slug holds a secret key is deleted by a request instead, "
                "which doesn't send the key again. Your other devices, and apps that "
                "follow such requests, will treat it as removed. This action can't be "
                "undone.",
                "Drafts whose slugs hold a secret key are deleted by a request each "
                "instead, which doesn't send the keys again. Your other devices, and apps "
                "that follow such requests, will treat them as removed. This action can't "
                "be undone.", keyed))
        # Said before the first prompt appears rather than discovered at
        # the fourth: each deletion is separately signed, and an imported
        # draft's takes a second request that tells other apps it is gone
        # (engine review M7), so this is a row of approvals on the phone.
        # (A keyed draft's one request is its deletion: no second one.)
        imported = sum(1 for identifier in self._imported_draft_wraps(deletable)
                       if not refs.secret_keys_in(identifier))
        requests = count + imported
        profile = self._profile_store.default()
        signs_here = profile is not None and getattr(profile, "is_local", False)
        if requests > 1 and not signs_here:
            lines.append(ngettext(
                "Your signer app will ask you to approve each one, so expect "
                "{count} request. You can stop partway through.",
                "Your signer app will ask you to approve each one, so expect "
                "{count} requests. You can stop partway through.",
                requests).format(count=requests))
            if imported:
                lines.append(ngettext(
                    "An imported draft takes a second request, which tells other apps "
                    "it is gone.",
                    "Imported drafts take a second request each, which tells other "
                    "apps they are gone.", imported))
        if skipped:
            n = len(skipped)
            lines.append(ngettext(
                "{count} draft from another Nostr client is not included, and will be "
                "left alone.",
                "{count} drafts from another Nostr client are not included, and will be "
                "left alone.", n).format(count=n))
        return confirm_destructive(
            self, title=heading, message="\n\n".join(lines),
            action=action,
            caution=True,
            details=(_("Not included:") + "\n" + "\n".join(skipped)) if skipped else "")

    def _run_draft_deletion(
        self, profile, deletable: List[Tuple[str, int]],
    ) -> None:
        try:
            job = DraftBulkDeleteJob(
                relay_pool=self._relay_pool,
                relay_directory=self._relay_directory,
                session_pool=self._session_pool,
                entitled_relays=self._entitled_relays(),
                profile=profile,
                targets=deletable,
                announce=self._draft_wraps_to_name(deletable),
                parent=self,
            )
        except ValueError as exc:
            inform(self, title=_("Couldn't start the deletion"),
                   message=_("These drafts can't be removed from here.\n\n{error}").format(
                       error=exc))
            return

        total = job.total
        progress = None
        if total > 1:
            # One signer approval each, so this is a wait the user has to
            # be able to see and get out of. A single deletion is fast
            # enough that a dialog would flash.
            progress = QProgressDialog(
                _("Deleting drafts…"), _("Stop"), 0, total, self)
            progress.setWindowTitle(_("Deleting drafts"))
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setValue(0)
            progress.canceled.connect(job.cancel)

        def on_progress(done: int, count: int) -> None:
            if progress is not None and not progress.wasCanceled():
                progress.setLabelText(_("Deleting draft {current} of {total}…").format(
                    current=min(done + 1, count), total=count))
                progress.setValue(done)

        job.progress.connect(on_progress)
        job.tombstoned.connect(self._on_draft_tombstoned)
        job.finished.connect(
            lambda deleted, failures: self._on_draft_deletion_finished(
                deleted, failures, total, progress,
            )
        )
        job.start()

    def _on_draft_tombstoned(self, identifier: str, event_id: str) -> None:
        """A draft deleted here: remembered as deleted, as a request from
        another app would be (engine review L13). Removing it only let an
        older copy a slower relay still sends bring it back until the
        blanked wrap came round; every version up to now is gone, and one
        saved later on another device is a new draft and stays."""
        self._draft_store.apply_deletion(identifier, int(time.time()), event_id,
                                         request=True)

    def _imported_draft_wraps(self, deletable: List[Tuple[str, int]]) -> dict:
        """``{d: wrap id}`` for the imported drafts among ``deletable``.

        Their deletion is announced to apps that read only NIP-09
        requests too (EINUNDZWANZIG STANDUP), so a post deleted here is
        never imported there again."""
        found = {}
        for identifier, _kind in deletable:
            record = self._draft_store.get(identifier)
            if record is None:
                continue
            if identifier.startswith(IMPORT_IDENTIFIER_PREFIX) or any(
                    tag and tag[0] == IMPORT_SOURCE_TAG for tag in record.inner_tags):
                found[identifier] = record.event_id
        return found

    def _draft_wraps_to_name(self, deletable: List[Tuple[str, int]]) -> dict:
        """``{d: wrap id}`` for the drafts whose deletion names their wrap:
        the imported ones, and those whose slug holds a secret key, which
        are deleted by their wrap's id alone (DraftDeleteJob), so that the
        deletion does not send the key again."""
        found = self._imported_draft_wraps(deletable)
        for identifier, _kind in deletable:
            record = self._draft_store.get(identifier)
            if record is not None and refs.secret_keys_in(identifier):
                found[identifier] = record.event_id
        return found

    def _on_draft_deletion_finished(
        self, deleted: int, failures: list, total: int, progress,
    ) -> None:
        if progress is not None:
            progress.close()
        if not failures:
            self.status.showMessage(
                ngettext("Draft deleted.", "{count} drafts deleted.",
                         deleted).format(count=deleted), 5000,
            )
            return

        # A partial result reported as a whole one is how a user comes to
        # believe a draft is gone when it is not, so the count that did
        # not go is the headline and the reasons are one click away.
        if deleted:
            title = ngettext("{deleted} of {total} draft deleted",
                             "{deleted} of {total} drafts deleted",
                             total).format(deleted=deleted, total=total)
        else:
            title = _("No drafts were deleted")
        inform(
            self,
            title=title,
            message=ngettext("{count} could not be removed and is still on your relays. "
                             "You can try again.",
                             "{count} could not be removed and are still on your relays. "
                             "You can try again.", len(failures)).format(count=len(failures)),
            caution=True,
            details="\n".join(
                f"{identifier[:16]}: {reason}" for identifier, reason in failures))

    def _on_draft_sync_status(self, text: str) -> None:
        if self._drafts_panel is not None:
            self._drafts_panel.set_status(text)

    def _on_draft_sync_signer_unreachable(self, unreachable: bool) -> None:
        if self._drafts_panel is not None:
            self._drafts_panel.set_signer_unreachable(unreachable)
        if unreachable:
            # The panel may be closed, and this is the same condition that
            # makes publishing fail, so it belongs in the window too.
            self.status.showMessage(
                _("Your signer app is not responding. Open it and make sure it is "
                  "running."), 8000,
            )

    def _on_draft_sync_bunker_error(self, message: str) -> None:
        if self._drafts_panel is not None:
            self._drafts_panel.set_signer_unsupported(True)
        # Surface in the main window status bar too - the user may not
        # have the panel open.
        self.status.showMessage(message, 8000)

    # ----------------------------------------------------------------------
    # NOSTR - contextual Ctrl+Shift+S: disk vs. draft destination
    # ----------------------------------------------------------------------

    def _has_active_nostr_profile(self) -> bool:
        return self._profile_store.default() is not None

    def _on_save_as_pressed(self) -> None:
        """Handler for ``Ctrl+Shift+S``.

        Three-way decision:
          1. No Nostr profile connected → behave as classic ``Save As``.
          2. Nostr connected + this tab already chose a destination →
             use that destination silently.
          3. Otherwise → open the chooser dialog; optionally remember
             the choice for this tab.

        Two safety guards run before any dialog opens:
          - **Debounce**: if a stash is already in flight for this tab,
            we surface a status message and bail. Prevents rapid-fire
            shortcut presses from stacking up signer prompts.
          - **Profile mismatch**: if the tab is bound to a draft from a
            different identity, the user picks between switching back,
            saving a copy under the active profile, or cancelling.
        """
        ed = self.current_editor()
        if ed is None:
            return
        if self._is_stash_in_flight_for(ed):
            self._stash_already_running_message()
            return
        if not self._has_active_nostr_profile():
            self.save_as()
            return

        # Profile mismatch handling. On the Save-As path we allow
        # forking because the user has explicitly asked "save somewhere"
        # rather than the silent "save what I have" that Ctrl+S means.
        mismatch_pk = self._draft_binding_profile_mismatch(ed)
        if mismatch_pk is not None:
            outcome = self._resolve_mismatch(ed, mismatch_pk, allow_fork=True)
            if outcome == "cancel":
                return
            if outcome == "switch":
                if not self._switch_active_profile_to(mismatch_pk):
                    # Profile was removed between detection and
                    # confirmation - surface and bail rather than fall
                    # through to a save under the wrong identity.
                    inform(
                        self, title=_("That account isn\u2019t signed in anymore"),
                        message=_("Connect it again with Nostr > Connect Signer "
                                  "App\u2026 to save here."))
                    return
                # After the switch, the binding now matches active, so
                # we proceed normally below.
            elif outcome == "fork":
                self._fork_draft_binding(ed)

        remembered = getattr(ed, "_save_destination", None)
        if remembered is SaveDestination.LOCAL:
            self.save_as()
            return
        if remembered is SaveDestination.NOSTR_DRAFT:
            self._stash_current_tab_as_draft()
            return

        # Default the chooser to the option that matches the tab's
        # current identity: if it's a draft tab, pre-select "Save as
        # Nostr draft" - but still show the dialog so the user can
        # change their mind. Otherwise default to the safer disk option.
        is_draft_tab = getattr(ed, "_draft_binding", None) is not None
        default = (
            SaveDestination.NOSTR_DRAFT if is_draft_tab else SaveDestination.LOCAL
        )
        dlg = SaveDestinationDialog(
            default=default,
            is_dark=self.is_dark_theme,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        if dlg.remember:
            ed._save_destination = dlg.destination
        if dlg.destination is SaveDestination.LOCAL:
            self.save_as()
        else:
            self._stash_current_tab_as_draft()

    def _stash_current_tab_as_draft(self) -> None:
        """Open the kind picker and, on accept, fire a ``DraftPublishJob``.

        Per the product spec we ask for the kind on every Save-As stash
        - the prior binding (if any) is pre-selected so the common path
        is two clicks: open dialog → confirm.

        For a silent re-save of an already-bound tab (the ``Ctrl+S``
        path) see ``_restash_with_binding`` instead.
        """
        ed = self.current_editor()
        profile = self._profile_store.default()
        if ed is None or profile is None:
            return

        prior: Optional[DraftBinding] = getattr(ed, "_draft_binding", None)
        published = self._published_article_of(ed)
        default_kind = (
            StashKind.ARTICLE
            if (prior is not None and prior.inner_kind == INNER_KIND_LONG_FORM)
            or (prior is None and published is not None)
            else StashKind.NOTE
        )
        title_hint, slug_hint, summary_hint, existing_note_id = (
            self._stash_defaults_for(ed, prior)
        )

        dlg = StashKindDialog(
            default=default_kind,
            suggested_title=title_hint,
            suggested_slug=slug_hint,
            suggested_summary=summary_hint,
            existing_note_identifier=existing_note_id,
            is_dark=self.is_dark_theme,
            parent=self,
        )
        if dlg.exec() != QDialog.Accepted or dlg.choice is None:
            return

        choice: StashChoice = dlg.choice
        if not self._may_replace_draft(prior, choice.identifier):
            return
        if not self._confirm_images_uploaded(ed):
            return
        flavor = "markdown" if choice.kind is StashKind.ARTICLE else "note"
        inner = self._build_inner_for_choice(
            profile, choice, self._publish_text(ed, flavor),
            details=self._article_details_of(ed),
        )
        if inner is None:
            return
        self._fire_draft_publish_job(ed, profile, choice, inner)

    def _may_replace_draft(self, prior: Optional[DraftBinding], identifier: str) -> bool:
        """Saving as a draft under an identifier another draft has replaces
        that draft on every device: asked first, the way Save As asks before
        it replaces a file (Cancel is the default). A tab bound to that
        draft just saves it again."""
        if prior is not None and prior.identifier == identifier:
            return True
        existing = self._draft_store.get(identifier)
        if existing is None:
            return True
        return confirm_destructive(
            self, title=_("A draft named “{name}” already exists. Do you want to replace "
                          "it?").format(name=existing.title or identifier),
            message=_("Replacing it overwrites its text on all your devices."),
            action=_("Replace"))

    def _restash_with_binding(self, ed, binding: DraftBinding) -> None:
        """Silent re-save of a draft-bound tab - no dialogs.

        Triggered by ``Ctrl+S`` when the tab has a draft binding but no
        local file path. The kind, identifier, and metadata are taken
        from the binding (and from the store for the cached summary),
        so the user gets the standard "save without asking" experience
        familiar from every editor.

        If the user wants to change the kind or move the draft to disk,
        they use ``Ctrl+Shift+S`` (the chooser dialog).
        """
        profile = self._profile_store.default()
        if profile is None:
            return

        # Every detail the draft holds goes into the new version (review
        # F4: an imported draft lost its cover, hashtags, first
        # publication date and source here). The title is the binding's.
        details = self._article_details_of(ed)

        kind = (
            StashKind.ARTICLE
            if binding.inner_kind == INNER_KIND_LONG_FORM
            else StashKind.NOTE
        )
        choice = StashChoice(
            kind=kind,
            identifier=binding.identifier,
            title=details.title or binding.title,
            summary=details.summary,
        )
        if not self._confirm_images_uploaded(ed):
            return
        flavor = "markdown" if kind is StashKind.ARTICLE else "note"
        inner = self._build_inner_for_choice(
            profile, choice, self._publish_text(ed, flavor), details=details
        )
        if inner is None:
            return
        self._fire_draft_publish_job(ed, profile, choice, inner)

    def _fire_draft_publish_job(
        self,
        ed,
        profile: Profile,
        choice: StashChoice,
        inner: dict,
    ) -> None:
        """Validate size, fire ``DraftPublishJob``, wire the callbacks.

        Shared by the explicit-kind-picker path and the silent-restash
        path so both behave identically once the kind + identifier are
        decided.
        """
        # Final debounce check - the entry points (Ctrl+S, Ctrl+Shift+S)
        # also guard, but defense-in-depth here protects any future
        # call site we add (e.g. an auto-save timer).
        if self._is_stash_in_flight_for(ed):
            self._stash_already_running_message()
            return
        # Empty body: short-circuit with a friendly message before we
        # ask the signer to encrypt nothing. Matches the same guard the
        # publish-note and publish-article flows already use.
        if not str(inner.get("content", "")).strip():
            inform(self, title=_("Nothing to save as a draft"),
                   message=_("The current document is empty. Add some content first."))
            return
        # Pre-flight the plaintext cap with a friendly message rather
        # than letting the publish job fail mid-pipeline. The job also
        # checks, but doing it here means no signer round-trip wasted.
        try:
            payload_bytes = len(serialize_inner_event(inner).encode("utf-8"))
        except (KeyError, TypeError, ValueError):
            inform(self, title=_("Couldn't prepare the draft"),
                   message=_("Its contents could not be prepared for encryption."))
            return
        if payload_bytes > MAX_INNER_PAYLOAD_BYTES:
            inform(self, title=_("This draft is too large"),
                   message=_("It has {size} bytes, and encryption allows {limit}. "
                             "Split it into smaller drafts or publish it directly.").format(
                       size=_number(payload_bytes), limit=_number(MAX_INNER_PAYLOAD_BYTES)))
            return

        job = DraftPublishJob(
            relay_pool=self._relay_pool,
            relay_directory=self._relay_directory,
            session_pool=self._session_pool,
            entitled_relays=self._entitled_relays(),
            profile=profile,
            inner_event=inner,
            identifier=choice.identifier,
            parent=self,
        )
        # Pass the editor + inner through the closure so the stashed
        # handler can bind the tab and update the store optimistically.
        job.status_changed.connect(lambda s: self.status.showMessage(s, 5000))
        job.stashed.connect(
            lambda ident, eid, ts, _ed=ed, _inner=inner, _choice=choice:
            self._on_draft_stashed(_ed, _choice, eid, ts, _inner)
        )
        job.failed.connect(
            lambda reason: inform(self, title=_("Couldn't save the draft"), message=reason)
        )
        # Register before starting so a synchronous failure path can't
        # leave the tab thinking nothing is in flight.
        self._attach_active_stash(ed, job)
        job.start()

    def _published_article_of(self, ed) -> Optional[ArticleDetails]:
        """The published article a tab edits (from the Published view),
        while its author is the account in use; None otherwise. Under
        another account the tab is a plain document: publishing it must
        not take the other account's identifier or first date."""
        article = getattr(ed, "_published_article", None) if ed is not None else None
        if article is None:
            return None
        profile = self._profile_store.default()
        author = getattr(ed, "_published_author", "")
        if profile is None or author != profile.user_pubkey.lower():
            return None
        return article

    def _article_details_of(self, ed) -> ArticleDetails:
        """The details of the article in a tab. Those set in the tab win
        (Article Details, the publish window, a Markdown file's front
        matter): one model for all of them. Else those its draft holds (an
        imported article's cover, hashtags, first publication date and
        source among them), with the tab's own identifier and title.
        Empty for a tab that is no article draft.

        An article published from this computer since its draft was last
        saved gets its first publication date from this computer's record,
        so the next save puts it into the draft, and every device that
        opens the draft keeps the date."""
        own = getattr(ed, "_article_details", None) if ed is not None else None
        if own is not None:
            return own
        binding = getattr(ed, "_draft_binding", None) if ed is not None else None
        if binding is None or binding.inner_kind != INNER_KIND_LONG_FORM:
            # An article opened from the Published view carries its own.
            return self._published_article_of(ed) or ArticleDetails()
        record = self._draft_store.get(binding.identifier)
        details = (ArticleDetails.from_tags(record.inner_tags, signer=binding.profile_pubkey)
                   if record is not None
                   else ArticleDetails())
        details = details.with_changes(identifier=binding.identifier,
                                       title=binding.title or details.title)
        first_publications = getattr(self, "_first_publications", None)
        profile = (self._profile_store.default()
                   if getattr(self, "_profile_store", None) is not None else None)
        if details.published_at is None and first_publications is not None and profile:
            details = details.with_changes(published_at=first_publications.get(
                profile.user_pubkey, binding.identifier))
        return details

    def _ask_can_receive_zaps(self, pubkey: str, hint: str, answer) -> None:
        """Whether a co-author's profile names a lightning address."""
        can_receive_zaps(self._relay_pool, self._relay_directory, pubkey, hint, answer,
                         parent=self._article_details_panel)

    def _author_of(self, profile) -> Tuple[str, str]:
        """(pubkey, relay hint) of the account ``profile`` writes as: how
        an article names its author and where they can be found."""
        directory = getattr(self, "_relay_directory", None)
        return (profile.user_pubkey,
                directory.relay_hint(profile.user_pubkey) if directory is not None else "")

    def _set_article_details(self, ed, details: ArticleDetails) -> None:
        """The details of the article in ``ed``, as set in Article Details or
        the publish window: kept with the tab (its next draft save, its
        restart and crash records, a Markdown file's front matter). A change
        marks the tab modified, as a change to its text does."""
        if ed is None or details == self._article_details_of(ed):
            return
        ed._article_details = details
        ed.document().setModified(True)
        if ed is self.current_editor():
            self._fill_article_details_panel()

    def _stash_defaults_for(
        self,
        ed,
        prior: Optional[DraftBinding],
    ) -> tuple[str, str, str, str]:
        """Compute title / slug / summary defaults for the kind dialog.

        Priority: prior draft binding → store metadata → file basename.
        Returns ``(title, slug, summary, existing_note_identifier)``.
        """
        title = ""
        slug = ""
        summary = ""
        existing_note_id = ""

        if prior is not None and prior.inner_kind == INNER_KIND_SHORT_NOTE:
            existing_note_id = prior.identifier

        if prior is not None:
            title = prior.title or ""
            if prior.inner_kind == INNER_KIND_LONG_FORM:
                slug = prior.identifier
            # Pull the cached summary off the store if available.
            record = self._draft_store.get(prior.identifier)
            if record is not None:
                for tag in record.inner_tags:
                    if len(tag) >= 2 and tag[0] == "summary" and not summary:
                        summary = tag[1]

        published = self._published_article_of(ed)
        if prior is None and published is not None:
            # An article opened from the Published view: the same article.
            title, slug, summary = published.title, published.identifier, published.summary
        own = getattr(ed, "_article_details", None)
        if own is not None:
            # As set in Article Details (or a file's front matter).
            title = own.title or title
            summary = own.summary or summary
            slug = slug or own.identifier

        if not title:
            # Fall back to the file basename without extension.
            path = getattr(ed, "_file_path", None)
            if path:
                base = os.path.splitext(os.path.basename(path))[0]
                # Underscores/hyphens to spaces; title-case for a humane default.
                title = base.replace("_", " ").replace("-", " ").strip()
        return title, slug, summary, existing_note_id

    def _build_inner_for_choice(
        self,
        profile: Profile,
        choice: StashChoice,
        content: str,
        *,
        details: Optional[ArticleDetails] = None,
    ) -> Optional[dict]:
        """Compose the inner unsigned event from the kind-picker choice.

        An article draft carries all of the article's details (d, title,
        summary, cover, hashtags, first publication date, source), so
        publishing it later has every one of them. The choice's
        identifier, title and summary win over ``details``; saved under
        another identifier, it is another article, with a first
        publication of its own.
        """
        tags: list[list[str]] = []
        if choice.kind is StashKind.ARTICLE:
            tags = _details_saved_as(details, choice).draft_tags(
                content, author=self._author_of(profile))
        try:
            return build_inner_event(
                kind=choice.kind.value,
                content=content,
                pubkey_hex=profile.user_pubkey,
                tags=tags,
            )
        except ValueError as exc:
            inform(self, title=_("Couldn't prepare the draft"), message=str(exc))
            return None

    def _on_draft_stashed(
        self,
        ed,
        choice: StashChoice,
        event_id: str,
        created_at: int,
        inner: dict,
    ) -> None:
        """Job has signed the wrap. Bind the tab + update the store
        optimistically so the panel reflects the new state immediately
        rather than waiting for the relay echo to round-trip back
        through ``DraftSync``."""
        # The editor may have been closed mid-flight - bail gracefully.
        if ed is None or self._editor_widget_index(ed) < 0:
            return
        active = self._profile_store.default()
        ed._draft_binding = DraftBinding(
            identifier=choice.identifier,
            inner_kind=choice.kind.value,
            event_id=event_id,
            created_at=created_at,
            title=choice.title or (choice.identifier if choice.kind is StashKind.NOTE else ""),
            profile_pubkey=active.user_pubkey.lower() if active else "",
        )
        # Optimistic store update - by the time the relay echo arrives
        # via DraftSync, the panel already shows the row. First, so that
        # nothing read after this point finds the draft as it was.
        self._draft_store.upsert_from_inner(
            identifier=choice.identifier,
            inner=inner,
            event_id=event_id,
            created_at=created_at,
            expiration=None,
        )
        # The tab's article is what was just saved: its model holds exactly
        # that (title, summary, cover, hashtags, co-authors, zaps), so
        # Article Details goes on showing it, never an older draft. The
        # tab's own details as typed (hashtags in their case, which the
        # draft's tags are not), else those the draft holds.
        if choice.kind is StashKind.ARTICLE:
            own = getattr(ed, "_article_details", None)
            ed._article_details = _details_saved_as(
                own if own is not None else ArticleDetails.from_tags(
                    inner.get("tags") or [], signer=str(inner.get("pubkey") or "")), choice)
        # A successful stash clears the modified flag - the tab's
        # contents now match the latest draft snapshot on the network.
        ed._recovered_title = ""
        ed.document().setModified(False)
        self._update_tab_title()
        if ed is self.current_editor():
            # A note or an article now, with the draft's address.
            self._fill_article_details_panel()

    def _editor_widget_index(self, ed) -> int:
        """Return the tab index that hosts ``ed``, or -1 if not found.

        Walks the tab widgets defensively - used by the post-job
        callback to verify the tab still exists before mutating it.
        """
        for i in range(self.tabs.count()):
            if self._editor_from_widget(self.tabs.widget(i)) is ed:
                return i
        return -1

    # ----------------------------------------------------------------------
    # NOSTR - stash debounce: at most one in-flight stash per tab
    # ----------------------------------------------------------------------

    def _is_stash_in_flight_for(self, ed) -> bool:
        """True if a ``DraftPublishJob`` is currently running for this tab.

        Prevents the rapid-Ctrl+Shift+S footgun where a user holding the
        shortcut would spawn N parallel stash jobs, each opening its own
        signer approval prompt. The guard sits at every entry point so
        the chooser + kind dialogs don't even appear while a stash is
        already underway.
        """
        return getattr(ed, "_active_stash_job", None) is not None

    def _stash_already_running_message(self) -> None:
        self.status.showMessage(_("Already saving this draft - wait for it to finish."), 4000)

    def _attach_active_stash(self, ed, job) -> None:
        ed._active_stash_job = job

        def _release(*_args) -> None:
            # Only clear if this is still the registered job - guards
            # against an out-of-order completed/failed pair from an
            # earlier cancelled job clobbering a newer one.
            if getattr(ed, "_active_stash_job", None) is job:
                ed._active_stash_job = None

        job.completed.connect(_release)
        job.failed.connect(_release)

    # ----------------------------------------------------------------------
    # NOSTR - draft / active-profile mismatch
    # ----------------------------------------------------------------------

    def _draft_binding_profile_mismatch(self, ed) -> Optional[str]:
        """Return the draft binding's pubkey if it doesn't match the active
        profile, or ``None`` when the binding either matches or is absent.

        The active profile drives signing and relay routing. Saving a
        draft tab under a different identity would silently fork the
        draft's addressable coordinate, which is rarely the user's
        intent - so this check funnels mismatches through a clear
        confirmation dialog rather than letting them happen by accident.
        """
        binding: Optional[DraftBinding] = getattr(ed, "_draft_binding", None)
        if binding is None or not binding.profile_pubkey:
            return None
        active = self._profile_store.default()
        if active is None:
            # No active profile: the caller already short-circuits to
            # disk save, so the mismatch doesn't matter here.
            return None
        if binding.profile_pubkey.lower() == active.user_pubkey.lower():
            return None
        return binding.profile_pubkey.lower()

    def _resolve_mismatch(
        self,
        ed,
        original_pubkey: str,
        *,
        allow_fork: bool,
    ) -> str:
        """Show the mismatch dialog and return one of ``switch`` /
        ``fork`` / ``cancel`` based on the user's choice.

        ``allow_fork`` is False on the Ctrl+S path because silent
        ``save`` should never quietly change which identity owns the
        draft; the user must explicitly Save-As to fork.
        """
        active = self._profile_store.default()
        original = self._profile_store.get(original_pubkey)

        def _label_for(p: Optional[Profile], pk: str) -> str:
            if p is not None:
                return p.display_name or p.npub_short()
            if pk:
                return short_npub(pk)
            return _("(unknown account)")

        active_label = _label_for(active, active.user_pubkey if active else "")
        original_label = _label_for(original, original_pubkey)

        buttons = []
        if original is not None:
            buttons.append(Button(_("Switch to {name} and Save").format(name=original_label),
                                  "switch", NORMAL))
        if allow_fork:
            buttons.append(Button(_("Save a Copy as {name}").format(name=active_label),
                                  "fork", NORMAL))
        buttons.append(Button(_("Cancel"), "cancel", DEFAULT))
        return ask(
            self,
            title=_("This draft was last saved as {original}, but you're "
                    "signed in as {active}").format(original=original_label,
                                                    active=active_label),
            message=_("Saving under the current account would create a separate "
                      "draft. How would you like to handle it?"),
            buttons=buttons, caution=True)

    def _switch_active_profile_to(self, pubkey_hex: str) -> bool:
        """Switch the active Nostr profile by pubkey. Returns True on
        success, False if the requested profile is no longer in the
        store."""
        profile = self._profile_store.get(pubkey_hex)
        if profile is None:
            return False
        self._on_nostr_select_profile(profile)
        return True

    def _fork_draft_binding(self, ed) -> None:
        """Detach a tab from its current draft so the next stash mints
        a fresh identifier under the active profile."""
        ed._draft_binding = None
        ed._save_destination = None  # ensure the chooser re-appears
        self._update_tab_title()

    # ----------------------------------------------------------------------
    # NOSTR - conflict banner
    # ----------------------------------------------------------------------

    def _on_draft_record_changed(self, identifier: str) -> None:
        """Detect "newer version arrived from another device" conflicts.

        Fires whenever ``DraftStore.record_changed`` triggers. For each
        tab bound to ``identifier`` whose locally-cached event_id is
        older than the store's current one, surface the conflict banner.
        """
        record = self._draft_store.get(identifier)
        if record is None or record.state is not DraftState.READY:
            return
        for i in range(self.tabs.count()):
            container = self.tabs.widget(i)
            ed = self._editor_from_widget(container)
            if ed is None:
                continue
            binding = getattr(ed, "_draft_binding", None)
            if binding is None or binding.identifier != identifier:
                continue
            if not record.event_id or record.event_id == binding.event_id:
                continue  # No new server-side version.
            # If the tab isn't dirty, silently refresh - there's
            # nothing to conflict with.
            if not ed.document().isModified():
                self._load_draft_content(ed, record)
                ed.document().setModified(False)
                binding.event_id = record.event_id
                binding.created_at = record.created_at
                binding.title = record.title
                self._update_tab_title()
                continue
            # Local edits + remote update → show the banner.
            self._show_conflict_banner(container, ed, record)

    @staticmethod
    def _load_markdown(ed, content: str) -> None:
        """Put Markdown in the editor so that nothing of it is lost.

        Markdown the editor can hold all of opens with its formatting
        shown (footnotes as their text), and is written back the same.
        Anything else (raw HTML, an image without alt text, a footnote of
        two paragraphs, a draft saved as plain text by an earlier version)
        opens as its Markdown text, and is saved and published exactly as
        written."""
        if holds_faithfully(content):
            read_markdown(ed.document(), content)
            # Mentions, kept as bare addresses, read back as names; only
            # while a Nostr account is in use (without one the editor shows
            # nothing of Nostr, so an address stays as it is written).
            if ed.nostr_in_use():
                rich_text.link_references(
                    ed.document(), getattr(ed, "_mention_label", None) or refs.person_name)
            ed._loaded_as_markdown = True
            ed._markdown_source = False
        else:
            ed.setPlainText(content)
            ed._markdown_source = True
        # Undo starts here: undoing the load would empty the tab.
        ed.document().clearUndoRedoStacks()

    @classmethod
    def _load_draft_content(cls, ed, record) -> None:
        """Put a draft's text in the editor the way it was written: an
        article draft is Markdown, a note is plain text."""
        if record.inner_kind == INNER_KIND_LONG_FORM:
            cls._load_markdown(ed, record.content)
        else:
            ed.setPlainText(record.content)
            ed._markdown_source = False
            ed.document().clearUndoRedoStacks()

    def _show_conflict_banner(self, container, ed, record) -> None:
        """Insert (or update) the per-tab conflict banner."""
        if ed in self._tab_conflict_banners:
            # Already showing - refresh the message in case the remote
            # version has updated again.
            self._tab_conflict_banners[ed].show()
            return
        banner = DraftConflictBanner(is_dark=self.is_dark_theme)
        identifier = record.identifier
        banner.view_remote.connect(
            lambda i=identifier: self._on_conflict_view(i)
        )
        banner.reload.connect(
            lambda e=ed, i=identifier: self._on_conflict_reload(e, i)
        )
        banner.keep_mine.connect(lambda e=ed: self._dismiss_conflict_banner(e))
        banner.dismissed.connect(lambda e=ed: self._dismiss_conflict_banner(e))

        # The container's layout is a QVBoxLayout holding [bar, editor_area].
        # We insert the banner at the very top so it sits above both.
        layout = container.layout()
        layout.insertWidget(0, banner)
        self._tab_conflict_banners[ed] = banner

    def _dismiss_conflict_banner(self, ed) -> None:
        banner = self._tab_conflict_banners.pop(ed, None)
        if banner is None:
            return
        banner.setParent(None)
        banner.deleteLater()

    def _on_conflict_view(self, identifier: str) -> None:
        """View: the other device's version in a new tab of its own, not
        bound to the draft, so comparing it changes nothing. (Opening the
        draft would only bring the conflicted tab to the front.)"""
        record = self._draft_store.get(identifier)
        if record is None or record.state is not DraftState.READY:
            return
        self.new_tab()
        ed = self.current_editor()
        if ed is None:
            return
        self._load_draft_content(ed, record)
        ed.document().setModified(False)
        self._update_tab_title()

    def _on_conflict_reload(self, ed, identifier: str) -> None:
        record = self._draft_store.get(identifier)
        if record is None or record.state is not DraftState.READY:
            return
        self._load_draft_content(ed, record)
        ed.document().setModified(False)
        binding = getattr(ed, "_draft_binding", None)
        if binding is not None:
            binding.event_id = record.event_id
            binding.created_at = record.created_at
            binding.title = record.title
        self._update_tab_title()
        self._dismiss_conflict_banner(ed)


def _details_saved_as(details: Optional[ArticleDetails], choice: StashChoice) -> ArticleDetails:
    """An article's details as a draft is saved with them: the identifier,
    title and summary chosen in Save as Draft win; under another identifier
    it is another article, with a first publication of its own."""
    base = details or ArticleDetails()
    if base.identifier and base.identifier != choice.identifier:
        base = base.with_changes(published_at=None)
    return base.with_changes(identifier=choice.identifier, title=choice.title,
                             summary=choice.summary)


def _media_asset(assets, name: str):
    """The audio or video asset the object named ``name`` is, or None."""
    sha = parse_asset_key(name)
    asset = assets.get(sha) if sha else None
    return asset if asset is not None and asset.is_media else None


class _MediaRead:
    """An audio or video file being read for a document, off the UI thread,
    which can be dropped (its document closes, the app quits) at whatever
    point it has reached. Whether its copy is kept in the local store and
    whether it is dropped are decided one at a time (the lock): a read
    dropped before it keeps its copy keeps none; dropped while it keeps it
    or after, its copy is known (``drop`` answers it) and taken out again,
    whether or not its result is ever handled."""

    WRITE_WAIT_S = 30.0       # the longest a copy of a file takes to be kept

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._written = threading.Event()
        self._written.set()
        self.dropped = False
        self.handled = False
        self._stored: Optional[str] = None

    def keeping(self) -> bool:
        """On the worker, once the file is read: whether to keep its copy."""
        with self._lock:
            if self.dropped:
                return False
            self._written.clear()
            return True

    def kept(self, sha: Optional[str]) -> None:
        """On the worker: the copy is kept (``sha``), or failed (None)."""
        with self._lock:
            self._stored = sha
            self._written.set()

    def take_over(self) -> bool:
        """On the UI thread, its result handled: the document takes the
        copy over, unless it was dropped first. True when it did."""
        with self._lock:
            if self.dropped:
                return False
            self.handled = True
            return True

    def drop(self) -> Optional[str]:
        """Drop it: the sha256 of a copy it kept that its result has not
        taken over, to take out again; None when there is none. Waits for a
        copy being kept right now."""
        with self._lock:
            self.dropped = True
        self._written.wait(self.WRITE_WAIT_S)
        with self._lock:
            stored, self._stored = self._stored, None       # answered once
            return None if self.handled else stored


def _is_media(assets, name: str) -> bool:
    """Whether the object named ``name`` is an audio or video asset."""
    return _media_asset(assets, name) is not None


def _published_address(asset) -> str:
    """Where an uploaded asset is read: its address, an audio or video
    file's with the extension of its type."""
    if not asset.is_media:
        return asset.remote_url
    return media_files.address_with_extension(asset.remote_url, asset.mime)
