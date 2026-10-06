# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nostr > Imports: the sources you follow, their posts, and the open one.

One window in three panes, the way Mail is laid out, so the relationship
reads by itself: **sources -> their posts -> the post you are reading**.

    +-------------+------------------------------+----------------------+
    | Inbox    14 | Inbox                        | A journal · 2 h ago  |
    | Older    32 | 14 posts                     |      [Open Original] |
    | Imported 48 | [-] 3 of 14 selected         |                      |
    | Skipped   3 | [x] Why self-custody...  [ ] | Why self-custody ... |
    |             | [ ] Notes from Lisbon    [ ] | by you · Oct 3 · ... |
    | Sources     |                              |                      |
    |  A Journal 6|                              |  Every cycle ...     |
    +-------------+------------------------------+----------------------+

The toolbar holds Show Sidebar, Add (Follow a Website, Import a File,
Import a Link: the sheets of imports_sheets.py) and the search field. The
sidebar (imports_sidebar.py) chooses the list, the list
(imports_post_list.py) chooses the post, the article pane
(imports_article.py) shows it the way an import will publish it. A
source that is read when it is opened (a Nostr author, a sitemap) is
read when it is chosen; one that is checked on its own shows its new or
older posts. A file or a link opened to import once is listed under
Files and Links with all its posts checked; a file dropped anywhere on
the window opens the same way. Under the list, the action bar
(imports_actions.py) makes drafts of the checked posts, or of the open
one when none is checked, or skips them (Delete or Backspace, with Undo
in a message and in Edit > Undo). How an import is going shows in one
place, the toolbar's activity button (imports_activity.py), and in the
words of the posts it holds.

The window has no menu bar of its own: the app's menu bar stays, and
its Edit commands act on this window while it is in front (see
``undo``, ``can_undo`` and ``edit``), as a Mac app's menu bar acts on
its key window.

Following Apple's guidelines for split views and sidebars: the selection
stays visible in every pane, the panes keep a minimum width so no divider
disappears, the sidebar can be hidden from the toolbar (and folds away
by itself when the window gets narrow), and the window remembers its
size, its dividers and the list it showed. Everything is reachable from
the keyboard: Tab moves between the panes, the arrow keys within them,
Command-F or Ctrl+F finds, and a source's commands are in its context
menu and in the list header's Source menu.

The window holds no import state of its own: it shows what the
controller (nostr/imports_controller.py) has, and forwards what the
person does to it.
"""

from __future__ import annotations

import base64
import sys
import time
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QByteArray, QPoint, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QKeySequence,
    QPainter,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QTextEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import alerts
import url_safety
from i18n import _, ngettext

from ..imports.inbox_store import (
    IMPORTED,
    INBOX,
    NEW,
    OLDER,
    OLDER_POSTS,
    PAGE_SIZE,
    SKIPPED_POSTS,
    SourceRow,
    View,
)
from ..imports_controller import ELSEWHERE
from ..imports.workspace import Post, Scope, checked_text, date_text, matches, targets
from .image_review_dialog import ImageReviewDialog
from .imports_actions import ActionBar, OptionsPopover
from .imports_activity import ActivityButton, JobCard, finished_text
from .eliding_label import ElidingLabel
from .imports_article import ArticlePane
from .imports_glyphs import glyph_icon
from .imports_post_list import PostList
from .imports_sheets import FileSheet, FollowSheet, LinkSheet, dropped_file
from .imports_sidebar import FILE, HEADER, LIST, SOURCE, Entry, Sidebar

SETTINGS_KEY = "imports_window"
NARROW = 900
SIDEBAR_MIN, LIST_MIN, ARTICLE_MIN = 170, 320, 360
SEARCH_DELAY_MS = 150

# Every color comes from the app's palette, so both themes follow.
STYLE = """
QMainWindow#imports_window, QWidget#imports_list_pane, QWidget#imports_article {
    background: palette(window);
}
QListView#imports_sidebar { background: palette(alternate-base); border: none; }
QSplitter#imports_splitter::handle { background: palette(mid); }
QWidget#imports_rule { background: palette(mid); }
QLabel#imports_list_subtitle, QLabel#imports_placeholder,
QLabel#imports_article_origin { color: palette(placeholder-text); }
QLabel#imports_notice { background: palette(alternate-base); }
QFrame#imports_banner { background: palette(alternate-base); border: none;
    border-bottom: 1px solid palette(mid); }
QToolButton#imports_banner_close { border: none; padding: 2px; }
QWidget#imports_actions { background: palette(window); border-top: 1px solid palette(mid); }
QLabel#imports_footnote, QLabel#imports_hint { color: palette(placeholder-text); }
QPushButton#imports_create {
    background: palette(highlight); color: palette(highlighted-text);
    border: none; border-radius: 5px; padding: 4px 14px; min-height: 18px;
}
QPushButton#imports_create:disabled {
    background: palette(button); color: palette(placeholder-text);
}
QPushButton#imports_segment {
    border: 1px solid palette(mid); padding: 3px 12px; min-width: 0;
    background: palette(base); color: palette(text);
}
QPushButton#imports_segment:checked {
    background: palette(highlight); color: palette(highlighted-text);
    border-color: palette(highlight);
}
QPushButton#imports_segment[edge="first"] {
    border-top-left-radius: 5px; border-bottom-left-radius: 5px; border-right: none;
}
QToolButton#imports_source_menu {
    border: 1px solid palette(mid); border-radius: 5px; padding: 1px 8px;
    font-weight: 600;
}
QToolButton#imports_source_menu::menu-indicator { image: none; width: 0; }
QPushButton#imports_segment[edge="last"] {
    border-top-right-radius: 5px; border-bottom-right-radius: 5px;
}
"""

# The lists of the sidebar: key, title, glyph.
LISTS = (
    (INBOX, _("Inbox"), "inbox"),
    (OLDER_POSTS, _("Older Posts"), "clock"),
    (IMPORTED, _("Imported"), "check"),
    (SKIPPED_POSTS, _("Skipped"), "skip"),
)
_LIST_TITLES = {key: title for key, title, _glyph in LISTS}


def freshness(source: SourceRow, *, now: Optional[float] = None) -> str:
    """When a source was last checked, or why that failed and when the
    next try is: the words its tooltip and its list header use."""
    now = time.time() if now is None else now
    if not source.automatic:
        return _("MyEditor reads this source when you open it.")
    if source.error:
        wait = max(0, source.next_check - int(now))
        if wait >= 3600:
            hours = (wait + 1800) // 3600
            when = ngettext("Next try in {n} hour.", "Next try in {n} hours.", hours).format(
                n=hours)
        else:
            minutes = max(1, (wait + 59) // 60)
            when = ngettext("Next try in {n} minute.", "Next try in {n} minutes.",
                            minutes).format(n=minutes)
        return f"{source.error} {when}"
    if not source.last_checked:
        return _("Not checked yet.")
    # No period after the time: in German it ends in one already
    # ("vor 2 Min."), and a doubled one reads as a mistake (review L2).
    if now - source.last_checked < 60:
        return _("Checked just now")
    return _("Checked {when}").format(when=date_text(source.last_checked, now=now))


class _SearchField(QLineEdit):
    """The toolbar's search field: Escape clears it and goes back to the
    list."""

    def __init__(self, on_escape: Callable[[], None], parent=None) -> None:
        super().__init__(parent)
        self._on_escape = on_escape

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.clear()
            self._on_escape()
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_escape()
            return
        super().keyPressEvent(event)


class ImportsWindow(QMainWindow):
    """The Imports window for one account (see the module docstring).

    Signals:
      undo_changed()   what Edit > Undo would undo here changed
    """

    undo_changed = Signal()

    def __init__(self, controller, *, dark: bool = False,
                 load_settings: Callable[[], dict] = dict,
                 save_settings: Callable[[dict], None] = lambda _value: None,
                 open_url: Callable[[QUrl], bool] = QDesktopServices.openUrl,
                 show_drafts: Optional[Callable[[], None]] = None,
                 confirm: Optional[Callable[..., bool]] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("imports_window")
        self.setWindowTitle(_("Imports"))
        self.setUnifiedTitleAndToolBarOnMac(True)
        self._controller = controller
        self._dark = dark
        self._save_settings = save_settings
        self._open_url = open_url
        self._scope = Scope(view=View(INBOX))
        self._source_state = NEW
        self._auto_hidden = False
        self._shown_by_person = False
        self._sheet = None
        self._show_drafts = show_drafts
        # The last skip, for Undo: (source, post, revision) each.
        self._last_skip: List[tuple] = []
        # What the person chose for the next drafts (this run only), and
        # the images they chose to leave where they are.
        self._choices: Dict[str, bool] = {}
        self._skip_images: set = set()
        # Files and links get all their posts checked when first shown.
        self._checked_once: set = set()
        # Nothing is saved until the window has been restored.
        self._ready = False
        self._restored_geometry = False
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self._show_list)

        self._build_toolbar()
        self._build_panes()
        self._build_shortcuts()
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)
        self._drop_overlay = _DropOverlay(self)
        self._drop_overlay.hide()

        # Asks before something cannot be undone: the app's alert, as
        # alerts.confirm_destructive (title, message, action) -> bool.
        self._confirm = confirm or (lambda **kw: alerts.confirm_destructive(self, **kw))
        self.job_card = JobCard(self, confirm_stop=lambda _parent: self._confirm(
            title=_("Stop this import?"),
            message=_("Drafts already created stay. The other posts are not imported."),
            action=_("Stop Import")))
        self.job_card.pause.connect(controller.pause_import)
        self.job_card.resume.connect(controller.resume_import)
        self.job_card.stop.connect(controller.stop_import)

        controller.sources_changed.connect(self._refresh_sidebar)
        controller.posts_changed.connect(self._on_posts_changed)
        controller.activity_changed.connect(self._update_activity)
        controller.import_finished.connect(self._import_finished)
        self._refresh_sidebar()
        self._update_activity()
        self._restore(load_settings() or {})
        self._show_list()
        if not self._restored_geometry:
            self.resize(1180, 760)
        # Space and Command-A act on the posts from the start, as in Mail
        # (review L3), not on the search field.
        self.posts.setFocus(Qt.FocusReason.OtherFocusReason)
        self._ready = True

    # ------------------------------------------------------------------ #
    # Building                                                             #
    # ------------------------------------------------------------------ #

    def _build_toolbar(self) -> None:
        bar = QToolBar(_("Imports Toolbar"))
        bar.setObjectName("imports_toolbar")
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setIconSize(QSize(18, 18))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

        self.act_sidebar = QAction(self)
        self.act_sidebar.setObjectName("imports_toggle_sidebar")
        self.act_sidebar.setCheckable(True)
        self.act_sidebar.setChecked(True)
        if _is_mac():
            # Finder's and Mail's View > Show Sidebar; no convention elsewhere.
            self.act_sidebar.setShortcut(QKeySequence("Ctrl+Meta+S"))
        self.act_sidebar.toggled.connect(self._on_sidebar_toggled)
        bar.addAction(self.act_sidebar)

        self.act_follow = QAction(_("Follow a Website…"), self)
        self.act_follow.setObjectName("imports_follow")
        self.act_follow.triggered.connect(lambda: self.follow_website())
        self.act_import_file = QAction(_("Import a File…"), self)
        self.act_import_file.setObjectName("imports_import_file")
        self.act_import_file.triggered.connect(lambda: self.import_file())
        self.act_import_link = QAction(_("Import a Link…"), self)
        self.act_import_link.setObjectName("imports_import_link")
        self.act_import_link.triggered.connect(self.import_link)
        add_menu = QMenu(self)
        for action in (self.act_follow, self.act_import_file, self.act_import_link):
            action.setEnabled(not self._controller.read_only)
            add_menu.addAction(action)
        self.add_button = QToolButton()
        self.add_button.setObjectName("imports_add")
        self.add_button.setAccessibleName(_("Add"))
        self.add_button.setToolTip(_("Follow a website, or import a file or a link"))
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_button.setMenu(add_menu)
        bar.addWidget(self.add_button)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)

        self.activity_button = ActivityButton()
        self.activity_button.clicked.connect(self._show_job_card)
        self._activity_action = bar.addWidget(self.activity_button)
        self._activity_action.setVisible(False)

        self.search = _SearchField(self._focus_list)
        self.search.setObjectName("imports_search")
        self.search.setPlaceholderText(_("Search"))
        self.search.setAccessibleName(_("Search Posts"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(180)
        self.search.setMaximumWidth(260)
        self.search.textChanged.connect(lambda _text: self._search_timer.start())
        bar.addWidget(self.search)
        self._toolbar = bar
        # Qt's own button for toolbar items that do not fit.
        more = bar.findChild(QToolButton, "qt_toolbar_ext_button")
        if more is not None:
            more.setAccessibleName(_("More Toolbar Items"))
        self._update_sidebar_action()

    def _build_panes(self) -> None:
        self.sidebar = Sidebar()
        self.sidebar.setMinimumWidth(SIDEBAR_MIN)
        self.sidebar.entry_selected.connect(self._on_entry)
        self.sidebar.menu_requested.connect(self._show_source_menu)

        self.posts = PostList(images=self._controller.images)
        self.posts.open_post.connect(self._on_open_post)
        self.posts.model_.checks_changed.connect(self._update_selection_band)
        self.posts.model_.checks_changed.connect(self._choices_for_new_targets)
        self.posts.model_.modelReset.connect(self._update_list_state)
        self.posts.model_.rowsInserted.connect(lambda *_a: self._update_list_state())
        self.posts.model_.layoutChanged.connect(self._update_list_state)

        self.article = ArticlePane(prepare=self._controller.prepare_article,
                                   images=self._controller.images, dark=self._dark,
                                   open_url=self._open_url)
        # A link the article cannot open says so (review L9).
        self.article.link_refused.connect(lambda _link: self.banner.say(
            _("That link can't be opened from here.")))
        self.article.setMinimumWidth(ARTICLE_MIN)

        list_pane = QWidget()
        list_pane.setObjectName("imports_list_pane")
        list_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        list_pane.setMinimumWidth(LIST_MIN)
        column = QVBoxLayout(list_pane)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_list_header())
        self.notice = QLabel(ELSEWHERE if self._controller.read_only
                             else self._controller.notice)
        self.notice.setObjectName("imports_notice")
        self.notice.setWordWrap(True)
        self.notice.setContentsMargins(16, 6, 16, 6)
        self.notice.setVisible(bool(self.notice.text()))
        column.addWidget(self.notice)
        self.banner = _Banner()
        self.banner.hide()
        column.addWidget(self.banner)
        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.posts)
        self.list_stack.addWidget(self._build_placeholder())
        column.addWidget(self.list_stack, 1)
        self.action_bar = ActionBar()
        self.action_bar.create_clicked.connect(self.create_drafts)
        self.action_bar.options_clicked.connect(self._show_options)
        self.action_bar.skip_clicked.connect(self.skip_or_restore)
        self.posts.skip_requested.connect(self.skip_or_restore)
        column.addWidget(self.action_bar)
        self.options_popover = OptionsPopover(self)
        self.options_popover.changed.connect(self._choose)
        self.options_popover.review_requested.connect(self._review_images)
        self.posts.open_post.connect(lambda _post: self._update_actions())

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("imports_splitter")
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(list_pane)
        self.splitter.addWidget(self.article)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setSizes([220, 400, 560])
        self.setCentralWidget(self.splitter)
        self._list_pane = list_pane

    def _build_list_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("imports_list_header")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(16, 12, 12, 6)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        # A long source title elides (whole in its tooltip) and never
        # squeezes the New and Older segments beside it.
        self.list_title = ElidingLabel()
        self.list_title.setObjectName("imports_list_title")
        title_font = QFont(self.list_title.font())
        title_font.setPointSizeF(title_font.pointSizeF() + 5)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.list_title.setFont(title_font)
        top.addWidget(self.list_title, 1)

        self.segments = QWidget()
        segment_row = QHBoxLayout(self.segments)
        segment_row.setContentsMargins(0, 0, 0, 0)
        segment_row.setSpacing(0)
        self._segment_group = QButtonGroup(self)
        self._segment_group.setExclusive(True)
        self.segment_new = self._segment(_("New"), _("New Posts"), NEW, segment_row, "first")
        self.segment_older = self._segment(_("Older"), _("Older Posts"), OLDER, segment_row,
                                           "last")
        self.segment_new.setChecked(True)
        top.addWidget(self.segments)

        self.source_menu_button = QToolButton()
        self.source_menu_button.setObjectName("imports_source_menu")
        # A small control: what a source can do is secondary to its posts.
        self.source_menu_button.setText("\u2026")
        self.source_menu_button.setAccessibleName(_("Source Actions"))
        self.source_menu_button.setToolTip(_("What you can do with this source"))
        self.source_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.source_menu_button.setMenu(QMenu(self.source_menu_button))
        self.source_menu_button.menu().aboutToShow.connect(self._fill_source_menu)
        top.addWidget(self.source_menu_button)
        layout.addLayout(top)

        self.list_subtitle = QLabel()
        self.list_subtitle.setObjectName("imports_list_subtitle")
        self.list_subtitle.setTextFormat(Qt.TextFormat.PlainText)
        self.list_subtitle.setWordWrap(True)
        layout.addWidget(self.list_subtitle)

        band = QHBoxLayout()
        band.setContentsMargins(0, 6, 0, 0)
        band.setSpacing(8)
        self.select_all = QCheckBox()
        self.select_all.setObjectName("imports_select_all")
        self.select_all.setTristate(True)
        self.select_all.setAccessibleName(_("Select All Posts"))
        self.select_all.clicked.connect(self._on_select_all)
        band.addWidget(self.select_all)
        band.addStretch(1)
        self.selection_band = QWidget()
        self.selection_band.setLayout(band)
        layout.addWidget(self.selection_band)
        return header

    def _segment(self, text: str, name: str, state: str, row: QHBoxLayout,
                 edge: str) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setObjectName("imports_segment")
        button.setProperty("edge", edge)
        button.setAccessibleName(name)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.clicked.connect(lambda _checked=False, s=state: self._on_segment(s))
        self._segment_group.addButton(button)
        row.addWidget(button)
        return button

    def _build_placeholder(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 40, 32, 32)
        layout.setSpacing(8)
        self.placeholder_title = QLabel()
        self.placeholder_title.setObjectName("imports_placeholder_title")
        self.placeholder_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_title.setWordWrap(True)
        font = QFont(self.placeholder_title.font())
        font.setPointSizeF(font.pointSizeF() + 2)
        font.setWeight(QFont.Weight.DemiBold)
        self.placeholder_title.setFont(font)
        self.placeholder_text = QLabel()
        self.placeholder_text.setObjectName("imports_placeholder")
        self.placeholder_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_text.setWordWrap(True)
        # Made when an empty list has something to offer (an unnamed
        # button would wait hidden for screen readers otherwise).
        self.placeholder_button: Optional[QPushButton] = None
        layout.addStretch(1)
        layout.addWidget(self.placeholder_title)
        layout.addWidget(self.placeholder_text)
        layout.addStretch(2)
        self._placeholder_layout = layout
        return page

    def _build_shortcuts(self) -> None:
        find = QAction(self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self.focus_search)
        self.addAction(find)
        close = QAction(self)
        close.setShortcut(QKeySequence.StandardKey.Close)
        close.triggered.connect(self.close)
        self.addAction(close)
        # Edit > Undo while this window is in front (the app's menu bar
        # sends it here too).
        undo = QAction(self)
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        undo.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        undo.triggered.connect(self.undo)
        self.addAction(undo)
        # Command-Return or Ctrl+Return, as Send in Mail.
        create = QAction(self)
        create.setShortcut(QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_Return))
        create.triggered.connect(self.create_drafts)
        self.addAction(create)
        self.addAction(self.act_sidebar)

    # ------------------------------------------------------------------ #
    # The sidebar                                                          #
    # ------------------------------------------------------------------ #

    def _refresh_sidebar(self) -> None:
        controller = self._controller
        counts = controller.counts()
        values = {INBOX: counts.inbox, OLDER_POSTS: counts.older,
                  IMPORTED: counts.imported, SKIPPED_POSTS: counts.skipped}
        entries: List[Entry] = [
            Entry(LIST, key, title, glyph=name, count=values[key],
                  emphasized=key == INBOX)
            for key, title, name in LISTS]
        sources = controller.sources()
        if sources:
            entries.append(Entry(HEADER, "sources", _("Sources")))
        for source in sources:
            failed = bool(source.error)
            tooltip = "\n".join(p for p in (source.display_title, source.url,
                                             freshness(source)) if p)
            entries.append(Entry(SOURCE, source.key, source.display_title,
                                 count=source.new_count, failed=failed, tooltip=tooltip,
                                 url=source.url, automatic=source.automatic))
        opened = controller.collections("file", "link")
        if opened:
            entries.append(Entry(HEADER, "files", _("Files and Links")))
        for collection in reversed(opened):
            entries.append(Entry(FILE, collection.id, collection.label,
                                 glyph="link" if collection.kind == "link" else "file",
                                 count=len(collection.posts()),
                                 tooltip="\n".join(p for p in (collection.label,
                                                               collection.source_url) if p),
                                 url=collection.source_url))
        self.sidebar.set_entries(entries)
        self._update_header()

    def _on_entry(self, entry: Entry) -> None:
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        if entry.kind == LIST:
            self._scope = Scope(view=View(entry.key))
        elif entry.kind == SOURCE:
            if entry.automatic:
                self._source_state = NEW
                self.segment_new.setChecked(True)
                self._scope = Scope(view=View("source", entry.key, NEW))
            else:
                collection = self._controller.read_source(entry.url)
                self._scope = Scope(collection=collection.id if collection else "")
        elif entry.kind == FILE:
            self._scope = Scope(collection=entry.key)
        self._show_list()
        if entry.kind == FILE and entry.key not in self._checked_once:
            # A file's or a link's posts are there to be imported: all of
            # them are checked to begin with.
            self._checked_once.add(entry.key)
            self.posts.model_.check_all()
            self._update_selection_band()
        if entry.kind != FILE:
            self._save()

    def _on_segment(self, state: str) -> None:
        entry = self.sidebar.chosen()
        if entry is None or entry.kind != SOURCE:
            return
        self._source_state = state
        self._scope = Scope(view=View("source", entry.key, state))
        self._show_list()

    def _show_source_menu(self, entry: Entry, position: QPoint) -> None:
        menu = QMenu(self)
        if entry.kind == FILE:
            remove = menu.addAction(_("Remove from List"))
            remove.triggered.connect(lambda: self._controller.close_collection(entry.key))
        else:
            self._source_actions(menu, entry)
        menu.exec(position)

    def _fill_source_menu(self) -> None:
        menu = self.source_menu_button.menu()
        menu.clear()
        entry = self.sidebar.chosen()
        if entry is not None and entry.kind == SOURCE:
            self._source_actions(menu, entry)
        elif entry is not None and entry.kind == FILE:
            remove = menu.addAction(_("Remove from List"))
            remove.triggered.connect(lambda: self._controller.close_collection(entry.key))

    def _source_actions(self, menu: QMenu, entry: Entry) -> None:
        check = menu.addAction(_("Check Now") if entry.automatic else _("Read Now"))
        check.setEnabled(not self._controller.read_only)
        check.triggered.connect(lambda: self.check_now(entry))
        # The website, not the feed's address (review L4): what the feed
        # names as its site, else the address's own start page.
        site = self._website_of(entry)
        website = menu.addAction(_("Open Website"))
        website.setEnabled(url_safety.is_safe_external_url(site))
        website.triggered.connect(lambda: self._open_url(QUrl(site)))
        copy = menu.addAction(_("Copy Address"))
        copy.triggered.connect(lambda: QApplication.clipboard().setText(entry.url))
        menu.addSeparator()
        unsubscribe = menu.addAction(_("Unsubscribe\u2026"))
        unsubscribe.setEnabled(not self._controller.read_only)
        unsubscribe.triggered.connect(lambda: self.unsubscribe(entry))

    def unsubscribe(self, entry: Entry) -> None:
        """Stop following a source, after asking: its posts leave the
        lists, the drafts made from them stay."""
        if self._confirm(
                title=_("Unsubscribe from \u201c{title}\u201d?").format(title=entry.title),
                message=_("Its posts leave Imports. Drafts you already created stay."),
                action=_("Unsubscribe")):
            self._controller.unfollow(entry.key)

    def _website_of(self, entry: Entry) -> str:
        source = self._controller.source(entry.key)
        if source is not None and source.site_url:
            return source.site_url
        return url_safety.origin_of(entry.url) or entry.url

    def check_now(self, entry: Entry) -> None:
        under_way = self._controller.is_checking(entry.key)
        started = self._controller.check_now(entry.key)
        if not started and entry.automatic:
            self.list_subtitle.setText(_("Checking for new posts\u2026") if under_way
                                       else _("You can check again in a minute."))
        elif not entry.automatic:
            collection = self._controller.collection_of(entry.key)
            if collection is not None and self.sidebar.chosen() == entry:
                self._scope = Scope(collection=collection.id)
                self._show_list()

    # ------------------------------------------------------------------ #
    # The list                                                             #
    # ------------------------------------------------------------------ #

    def _query(self) -> str:
        return self.search.text().strip()

    def _show_list(self) -> None:
        self._reset_choices()
        scope, query = self._scope, self._query()
        hidden = {SKIPPED_POSTS: _("Skipped"), IMPORTED: _("Imported"), INBOX: _("New")}
        if scope.is_collection:
            collection = self._controller.collection(scope.collection)

            def fetch(after: Optional[Post]) -> List[Post]:
                if after is not None or collection is None:
                    return []
                return [p for p in collection.posts() if matches(p, query)]

            self.posts.model_.show(fetch, paged=False)
        else:
            view = scope.view
            hidden_word = hidden.get(view.scope, "")
            if view.scope == "source" and view.source_state == NEW:
                hidden_word = _("New")
            self.posts.model_.show(
                lambda after, v=view, q=query: self._controller.page(v, query=q, after=after),
                paged=True, page_size=PAGE_SIZE, hidden_word=hidden_word)
        if self.posts.model_.rowCount():
            self.posts.setCurrentIndex(self.posts.model_.index(0))
        self._update_header()
        self._update_list_state()

    def _on_posts_changed(self) -> None:
        current = self.posts.current_post()
        if self._scope.is_collection and self._controller.collection(
                self._scope.collection) is None:
            self.sidebar.select(LIST, INBOX)
            return
        self.posts.model_.reload()
        if current is not None and not self.posts.open(current.key) \
                and self.posts.model_.rowCount():
            self.posts.setCurrentIndex(self.posts.model_.index(0))
        self._update_header()
        self._update_list_state()

    def _on_open_post(self, post: Optional[Post]) -> None:
        if post is None or self.article.post is None or post.key != self.article.post.key:
            self.article.show_post(post)

    def _update_header(self) -> None:
        entry = self.sidebar.chosen()
        is_source = entry is not None and entry.kind == SOURCE
        self.segments.setVisible(is_source and entry.automatic)
        self.source_menu_button.setVisible(is_source or (entry is not None
                                                         and entry.kind == FILE))
        if entry is None:
            return
        if entry.kind == LIST:
            self.list_title.setText(_LIST_TITLES.get(entry.key, entry.title))
        else:
            self.list_title.setText(entry.title)
        self.list_subtitle.setText(self._subtitle(entry))
        self.list_subtitle.setVisible(bool(self.list_subtitle.text()))

    def _subtitle(self, entry: Entry) -> str:
        if entry.kind == LIST and entry.key == SKIPPED_POSTS:
            return _("Posts you skipped on this computer.")
        if entry.kind == LIST:
            if not entry.count:
                return ""
            if entry.key == INBOX:
                return ngettext("{n} new post", "{n} new posts", entry.count).format(
                    n=entry.count)
            return ngettext("{n} post", "{n} posts", entry.count).format(n=entry.count)
        if entry.kind == FILE:
            collection = self._controller.collection(entry.key)
            if collection is None:
                return ""
            return _("Imported once, not kept as a source.")
        if entry.kind == SOURCE:
            source = self._controller.source(entry.key)
            if source is None:
                return ""
            if self._controller.is_checking(entry.key):
                return _("Checking for new posts…") if source.automatic else _("Reading…")
            return freshness(source)
        return ""

    def _update_list_state(self) -> None:
        model = self.posts.model_
        if model.rowCount():
            self.list_stack.setCurrentIndex(0)
        else:
            self._fill_placeholder()
            self.list_stack.setCurrentIndex(1)
            self.article.show_post(None)
        self.selection_band.setVisible(model.rowCount() > 0)
        self._update_selection_band()
        self._update_actions()

    def _update_selection_band(self) -> None:
        self._update_actions()
        model = self.posts.model_
        checked = len(model.checked())
        self.select_all.setCheckState(model.check_state())
        if checked:
            self.select_all.setText(checked_text(
                checked, self._known_total(model.selectable_count())))
        else:
            self.select_all.setText(_("Select All"))
        self.select_all.setEnabled(model.selectable_count() > 0)

    def _known_total(self, loaded: int) -> int:
        """How many posts the list holds, also those not loaded yet."""
        if self._query() or self._scope.is_collection:
            return loaded
        view = self._scope.view
        counts = self._controller.counts()
        known = {INBOX: counts.inbox, OLDER_POSTS: counts.older}.get(view.scope)
        if view.scope == "source":
            source = self._controller.source(view.source_key)
            if source is not None:
                known = source.new_count if view.source_state == NEW else source.older_count
        return max(loaded, known or 0)

    def _on_select_all(self) -> None:
        model = self.posts.model_
        if model.checked():
            model.clear_checks()
        else:
            model.check_all()
        self._update_selection_band()

    # ------------------------------------------------------------------ #
    # Creating drafts                                                      #
    # ------------------------------------------------------------------ #

    def targets(self) -> List[Post]:
        """What an action applies to: the checked posts, or the open one."""
        model = self.posts.model_
        return targets(model.checked(), self.posts.current_post(),
                       busy=model.busy_identifiers())

    def _update_actions(self) -> None:
        model = self.posts.model_
        chosen = self.targets()
        controller = self._controller
        copy, _full = self._copy_and_full(chosen)
        restoring = self._restoring()
        movable = [p for p in chosen if (p.skipped if restoring else p.skippable)]
        self.action_bar.show_targets(
            len(chosen), can_create=not controller.read_only,
            busy=not controller.can_create(),
            skip_text=_("Restore") if restoring else _("Skip"),
            can_skip=bool(movable) and not controller.read_only,
            prompts=controller.signer_prompts(chosen, copy_images=copy is not False))
        # Files and links are imported once: nothing there is skipped.
        self.action_bar.skip.setVisible(not self._scope.is_collection)
        self.action_bar.setVisible(model.selectable_count() > 0)

    def _copy_and_full(self, posts: List[Post]):
        """The options the next drafts get: the sources' defaults, and over
        them what the person chose for this run."""
        copy, full = self._controller.defaults_for(posts)
        return (self._choices.get("rehost_images", copy),
                self._choices.get("fetch_full_text", full))

    def _reset_choices(self) -> None:
        self._choices.clear()
        self._skip_images = set()

    def _choices_for_new_targets(self) -> None:
        # Images left out apply to the posts they were chosen for.
        self._skip_images = set()

    def _choose(self, key: str, on: bool) -> None:
        self._choices[key] = on
        self._update_actions()

    def _show_options(self) -> None:
        chosen = self.targets()
        copy, full = self._copy_and_full(chosen)
        self.options_popover.show_choices(
            copy=copy, full=full, has_images=any(p.image_count or p.image for p in chosen))
        self.options_popover.open_below(self.action_bar.options)

    def _review_images(self) -> None:
        chosen = self.targets()

        def ready(images: List[str]) -> None:
            if not images or chosen != self.targets():
                return
            dialog = ImageReviewDialog(images, self._skip_images, parent=self,
                                       image_source=self._controller.images)

            def done(result: int) -> None:
                if result == ImageReviewDialog.DialogCode.Accepted:
                    self._skip_images = dialog.skip_urls()
                dialog.deleteLater()

            dialog.finished.connect(done)
            dialog.open()

        self._controller.images_of(chosen, on_ready=ready)

    def create_drafts(self) -> None:
        """Create N Drafts: an import of the targets, as private drafts."""
        chosen = self.targets()
        if not chosen or not self._controller.can_create():
            return
        job = self._controller.create_drafts(
            chosen, label=self.list_title.text(), choices=dict(self._choices),
            skip_image_urls=self._skip_images)
        if job:
            self.posts.model_.clear_checks()
            self._reset_choices()
            self._update_selection_band()

    # ------------------------------------------------------------------ #
    # Skipping, and Undo                                                   #
    # ------------------------------------------------------------------ #

    def _restoring(self) -> bool:
        """In the Skipped list the action brings posts back."""
        view = self._scope.view
        return view is not None and view.scope == SKIPPED_POSTS

    def skip_or_restore(self) -> None:
        """Skip (Delete or Backspace), or Restore in the Skipped list."""
        chosen = self.targets()
        if self._restoring():
            restored = self._controller.restore(chosen)
            if restored:
                self.banner.say(ngettext("Restored {count} post.", "Restored {count} posts.",
                                         len(restored)).format(count=len(restored)))
            return
        moved = self._controller.skip(chosen)
        if not moved:
            return
        self.posts.model_.clear_checks()
        self._last_skip = moved
        self.banner.say(ngettext("Skipped {count} post.", "Skipped {count} posts.",
                                 len(moved)).format(count=len(moved)),
                        _("Undo"), self.undo_skip)
        self.undo_changed.emit()

    def undo_skip(self) -> None:
        moved, self._last_skip = self._last_skip, []
        if moved and self._controller.undo_skip(moved):
            self.banner.hide()
        self.undo_changed.emit()

    def can_undo(self) -> bool:
        """Whether Edit > Undo has something to undo here."""
        focus = self.focusWidget()
        if _is_text_field(focus):
            return _text_can_undo(focus)
        return bool(self._last_skip)

    def undo_text(self) -> str:
        """Edit > Undo's words while this window is in front."""
        if self._last_skip and not _is_text_field(self.focusWidget()):
            return _("Undo Skip")
        return _("Undo")

    def undo(self) -> None:
        """Edit > Undo: the text being typed, or else the last skip."""
        focus = self.focusWidget()
        if _is_text_field(focus):
            focus.undo()
            return
        self.undo_skip()

    def edit(self, name: str) -> None:
        """The app's Edit commands for this window (copy, select_all,
        delete) when the focus is not in a text field."""
        if name == "select_all":
            self.posts.model_.check_all()
            self._update_selection_band()
        elif name == "delete":
            self.skip_or_restore()
        elif name == "copy":
            post = self.posts.current_post()
            if post is not None and post.link:
                QApplication.clipboard().setText(post.link)

    # ------------------------------------------------------------------ #
    # How an import is going                                               #
    # ------------------------------------------------------------------ #

    def _update_activity(self) -> None:
        controller = self._controller
        job = controller.activity()
        self.activity_button.show_job(job)
        self._activity_action.setVisible(job is not None)
        if self.job_card.isVisible():
            self.job_card.show_job(job)
        busy, failed = controller.posts_in_progress()
        self.posts.model_.set_progress(busy, failed)
        self._update_actions()

    def _show_job_card(self) -> None:
        job = self._controller.activity()
        if job is None:
            return
        self.job_card.show_job(job)
        self.job_card.open_below(self.activity_button)

    def _import_finished(self, job_id: str) -> None:
        job = self._controller.job(job_id)
        if job is None:
            return
        if self._show_drafts is not None:
            self.banner.say(finished_text(job), _("Show Drafts"), self._show_drafts)
        else:
            self.banner.say(finished_text(job))

    def _fill_placeholder(self) -> None:
        title, text, button = self._placeholder()
        self.placeholder_title.setText(title)
        self.placeholder_text.setText(text)
        self.placeholder_text.setVisible(bool(text))
        old = self.placeholder_button
        if old is not None and old.text() == button:
            return
        if old is not None:
            self._placeholder_layout.removeWidget(old)
            old.hide()
            old.deleteLater()
            self.placeholder_button = None
        if button:
            self.placeholder_button = QPushButton(button)
            self.placeholder_button.setObjectName("imports_placeholder_button")
            self.placeholder_button.setAutoDefault(False)
            self.placeholder_button.clicked.connect(self._on_placeholder_button)
            # After the title and the sentence, before the space below.
            self._placeholder_layout.insertWidget(3, self.placeholder_button, 0,
                                                  Qt.AlignmentFlag.AlignHCenter)

    def _placeholder(self):
        """``(title, sentence, button)`` for an empty list."""
        query = self._query()
        if query:
            return (_("No Results"),
                    _("No posts match “{query}”.").format(query=query), "")
        scope = self._scope
        if scope.is_collection:
            collection = self._controller.collection(scope.collection)
            if collection is None:
                return "", "", ""
            if collection.loading:
                return (_("Reading {source}…").format(source=collection.label), "", "")
            if collection.error:
                return (_("Couldn't read this source"), collection.error, _("Try Again"))
            return (_("No posts"), _("This source has no posts to import."), "")
        view = scope.view
        if view.scope == INBOX:
            if not self._controller.sources():
                return (_("No sources yet"),
                        _("Follow a website to see its new posts here."),
                        "" if self._controller.read_only else _("Follow a Website…"))
            return (_("You're up to date"),
                    _("New posts appear here when MyEditor checks your sources."), "")
        if view.scope == OLDER_POSTS:
            return (_("No older posts"),
                    _("Posts that were already on a source when you followed it appear "
                      "here."), "")
        if view.scope == IMPORTED:
            return (_("Nothing imported yet"),
                    _("Posts you import as drafts appear here."), "")
        if view.scope == SKIPPED_POSTS:
            return (_("Nothing skipped"),
                    _("Posts you skip on this computer appear here, so you can bring them "
                      "back."), "")
        if view.source_state == NEW:
            return (_("No new posts"), _("New posts from this source appear here."), "")
        return (_("No older posts"),
                _("Posts that were already on this source when you followed it appear "
                  "here."), "")

    def _on_placeholder_button(self) -> None:
        entry = self.sidebar.chosen()
        if entry is not None and entry.kind == SOURCE:
            self.check_now(entry)
        elif entry is not None and entry.kind == LIST and entry.key == INBOX:
            self.follow_website()

    # ------------------------------------------------------------------ #
    # Following, files and links                                           #
    # ------------------------------------------------------------------ #

    def _open_sheet(self, sheet) -> None:
        if self._sheet is not None:
            self._sheet.close()
        self._sheet = sheet

        def finished(_result: int) -> None:
            if self._sheet is sheet:
                self._sheet = None
            sheet.deleteLater()

        sheet.finished.connect(finished)
        sheet.open()

    def sheet(self):
        """The sheet open now, if any."""
        return self._sheet

    def follow_website(self, address: str = "") -> None:
        """Add > Follow a Website..."""
        if self._controller.read_only:
            return
        sheet = FollowSheet(self._controller, dark=self._dark, address=address, parent=self)
        sheet.followed.connect(lambda key: self.show_view(SOURCE, key))
        sheet.show_source.connect(lambda key: self.show_view(SOURCE, key))
        sheet.import_once.connect(self._open_once)
        sheet.followed_list.connect(self._followed_list)
        self._open_sheet(sheet)
        if address:
            sheet.look_up()

    def import_file(self, path: str = "") -> None:
        """Add > Import a File... (and a file dropped on the window)."""
        if self._controller.read_only:
            return
        sheet = FileSheet(self._controller, dark=self._dark, parent=self)
        sheet.opened.connect(lambda collection_id: self.show_view(FILE, collection_id))
        sheet.followed_list.connect(self._followed_list)
        self._open_sheet(sheet)
        if path:
            sheet.read(path)

    def import_link(self) -> None:
        """Add > Import a Link..."""
        if self._controller.read_only:
            return
        sheet = LinkSheet(self._controller, dark=self._dark, parent=self)
        sheet.opened.connect(lambda collection_id: self.show_view(FILE, collection_id))
        sheet.follow.connect(lambda address: QTimer.singleShot(
            0, self, lambda: self.follow_website(address)))
        self._open_sheet(sheet)

    def _open_once(self, result) -> None:
        """A single post from Follow a Website: open it like a link."""
        if result is None:
            return
        collection = self._controller.open_posts(
            kind="link", label=result.feed.title or result.url, items=result.feed.items,
            source_url=result.url)
        self.show_view(FILE, collection.id)

    def _followed_list(self, followed: int, known: int, refused: int = 0) -> None:
        parts = [ngettext("Following {count} new source.", "Following {count} new sources.",
                          followed).format(count=followed)]
        if known:
            parts.append(ngettext("{count} was followed already.",
                                  "{count} were followed already.", known).format(count=known))
        if refused:
            parts.append(ngettext("{count} couldn't be followed.",
                                  "{count} couldn't be followed.", refused).format(
                count=refused))
        self.banner.say(" ".join(parts))

    # -- dropping a file on the window -------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if not self._controller.read_only and dropped_file(event.mimeData()):
            event.acceptProposedAction()
            self._drop_overlay.setGeometry(self.centralWidget().geometry())
            self._drop_overlay.raise_()
            self._drop_overlay.show()

    def dragMoveEvent(self, event) -> None:
        if not self._drop_overlay.isHidden():
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_overlay.hide()

    def dropEvent(self, event) -> None:
        self._drop_overlay.hide()
        path = dropped_file(event.mimeData())
        if path and not self._controller.read_only:
            event.acceptProposedAction()
            self.import_file(path)

    # ------------------------------------------------------------------ #
    # Sidebar visibility, focus, theme                                     #
    # ------------------------------------------------------------------ #

    def _on_sidebar_toggled(self, shown: bool) -> None:
        # The person's choice: the window never folds the sidebar against
        # a Show, and showing it in a narrow window makes the window wide
        # enough for it (the panes' minimum widths ask for that).
        self._auto_hidden = False
        self._shown_by_person = shown
        self.sidebar.setVisible(shown)
        self._update_sidebar_action()
        self._save()

    def _update_sidebar_action(self) -> None:
        shown = self.act_sidebar.isChecked()
        text = _("Hide Sidebar") if shown else _("Show Sidebar")
        self.act_sidebar.setText(text)
        self.act_sidebar.setToolTip(text)
        color = self.palette().color(QPalette.ColorRole.WindowText)
        self.act_sidebar.setIcon(glyph_icon("sidebar", 18, color))
        self.add_button.setIcon(glyph_icon("plus", 18, color))

    def resizeEvent(self, event) -> None:
        """The sidebar folds away when the window is made narrower than
        NARROW, and comes back when it is made wide again: only when the
        width crosses NARROW, and never against the person's own Show."""
        super().resizeEvent(event)
        width, before = self.width(), event.oldSize().width()
        if before < 0:
            return
        if (width < NARROW <= before and self.sidebar.isVisible()
                and not self._shown_by_person):
            self._auto_hidden = True
            self._set_sidebar(False)
        elif width >= NARROW > before and self._auto_hidden:
            self._auto_hidden = False
            self._set_sidebar(True)

    def _set_sidebar(self, shown: bool) -> None:
        auto = self._auto_hidden
        self.act_sidebar.blockSignals(True)
        self.act_sidebar.setChecked(shown)
        self.act_sidebar.blockSignals(False)
        self.sidebar.setVisible(shown)
        self._auto_hidden = auto
        self._update_sidebar_action()

    def focus_search(self) -> None:
        self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search.selectAll()

    def _focus_list(self) -> None:
        self.posts.setFocus(Qt.FocusReason.OtherFocusReason)

    def apply_theme(self, dark: bool) -> None:
        self._dark = dark
        # Read the palette's colors again.
        self.setStyleSheet(STYLE)
        self.article.set_dark(dark)
        self._update_sidebar_action()
        self.sidebar.viewport().update()
        self.posts.viewport().update()

    def show_view(self, kind: str, key: str) -> None:
        """Show a list, a source or a file (the Drafts panel opens the
        Inbox)."""
        if self.sidebar.model_.row_of(kind, key) < 0:
            self._refresh_sidebar()
        self.sidebar.select(kind, key)
        self.posts.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------ #
    # Remembering the window                                               #
    # ------------------------------------------------------------------ #

    def _restore(self, state: Dict) -> None:
        geometry = _bytes(state.get("geometry"))
        if geometry is not None:
            self._restored_geometry = self.restoreGeometry(geometry)
        splitter = _bytes(state.get("splitter"))
        if splitter is not None:
            self.splitter.restoreState(splitter)
        shown = state.get("sidebar", True)
        self.act_sidebar.setChecked(bool(shown))
        self.sidebar.setVisible(bool(shown))
        selection = state.get("selection")
        if (isinstance(selection, list) and len(selection) == 2
                and all(isinstance(v, str) for v in selection)):
            self.sidebar.select(*selection)
        else:
            self.sidebar.select(LIST, INBOX)

    def _save(self) -> None:
        if not self._ready:
            return
        entry = self.sidebar.chosen()
        self._save_settings({
            "geometry": base64.b64encode(bytes(self.saveGeometry())).decode("ascii"),
            "splitter": base64.b64encode(bytes(self.splitter.saveState())).decode("ascii"),
            "sidebar": self.act_sidebar.isChecked() or self._auto_hidden,
            "selection": [entry.kind, entry.key] if entry is not None else [LIST, INBOX],
        })

    def closeEvent(self, event) -> None:
        self._save()
        super().closeEvent(event)


class _Banner(QFrame):
    """A one-line message above the list, with an optional action and a
    close button: what just happened, and the way back where there is
    one."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("imports_banner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 6, 8, 6)
        row.setSpacing(8)
        self.label = QLabel()
        self.label.setObjectName("imports_banner_text")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        row.addWidget(self.label, 1)
        # The action is made for a message that has one.
        self.action: Optional[QPushButton] = None
        self._row = row
        self.close_button = QToolButton()
        self.close_button.setObjectName("imports_banner_close")
        self.close_button.setAccessibleName(_("Close Message"))
        self.close_button.setToolTip(_("Close Message"))
        self.close_button.clicked.connect(self.hide)
        row.addWidget(self.close_button)
        self._handler: Optional[Callable[[], None]] = None

    def say(self, text: str, action: str = "",
            handler: Optional[Callable[[], None]] = None) -> None:
        self.label.setText(text)
        self.label.setAccessibleName(text)
        if self.action is not None:
            self._row.removeWidget(self.action)
            self.action.hide()
            self.action.deleteLater()
            self.action = None
        if action:
            self.action = QPushButton(action)
            self.action.setObjectName("imports_banner_action")
            self.action.setAutoDefault(False)
            self.action.clicked.connect(self._act)
            self._row.insertWidget(1, self.action)
        self._handler = handler
        color = self.palette().color(QPalette.ColorRole.WindowText)
        self.close_button.setIcon(glyph_icon("close", 14, color))
        self.show()

    def _act(self) -> None:
        handler = self._handler
        self.hide()
        if handler is not None:
            handler()


class _DropOverlay(QWidget):
    """Shown over the window while a file is dragged onto it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName(_("Drop to Import"))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        highlight = QColor(self.palette().color(QPalette.ColorRole.Highlight))
        fill = QColor(self.palette().color(QPalette.ColorRole.Window))
        fill.setAlpha(225)
        rect = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        painter.setBrush(fill)
        painter.setPen(QPen(highlight, 3))
        painter.drawRoundedRect(rect, 12, 12)
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() + 6)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, _("Drop to Import"))


def _is_text_field(widget) -> bool:
    return isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)) and not (
        isinstance(widget, (QTextEdit, QPlainTextEdit)) and widget.isReadOnly())


def _text_can_undo(widget) -> bool:
    if isinstance(widget, QLineEdit):
        return widget.isUndoAvailable()
    return widget.document().isUndoAvailable()


def _bytes(value) -> Optional[QByteArray]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return QByteArray(base64.b64decode(value.encode("ascii")))
    except (ValueError, UnicodeEncodeError):
        return None


def _is_mac() -> bool:
    return sys.platform == "darwin"
