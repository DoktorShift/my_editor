# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Imports window's article pane: the open post, as it will be published.

Above, where the post comes from: its source, its date, and Open Original
to read it on the site. Below, the article an import of it makes, drawn
by the same preview the publish dialogs use (nostr/preview.py): cover,
title, summary, byline, the body as Markdown, the "Originally published
at" line, the tags.

What a feed sends is a stranger's HTML, so the pane never shows it as
it came:

- the body is the Markdown an import makes of it, and the preview reads
  Markdown with HTML switched off, so no script, style, frame or event
  handler survives, and nothing is loaded by the view itself;
- images come only from the window's image source, which fetches them
  through the importer's network guard; until one arrives its place is
  an empty frame;
- a link opens in the browser only when the app's policy for outside
  links allows it (http and https, no user name in it).
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import QSize, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import url_safety
from i18n import _

from ..imports.images import scan_markdown_images
from ..imports.workspace import Post, date_text
from ..preview import COLUMN_WIDTH, Article, NostrPreview
from .eliding_label import ElidingLabel
from .imports_glyphs import is_dark, letter_avatar

_EMPTY, _ARTICLE, _MESSAGE = 0, 1, 2


class ArticlePane(QWidget):
    """Shows one post.

    ``prepare(post, on_ready, on_failed)`` makes the article (the
    controller's ``prepare_article``); ``images`` answers and fetches
    images (the controller's RemoteImages).

    Signals:
      link_refused(str)   a link the outside-link policy refused
    """

    link_refused = Signal(str)

    def __init__(self, *, prepare: Callable, images, dark: bool = False,
                 open_url: Callable[[QUrl], bool] = QDesktopServices.openUrl,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("imports_article")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._prepare = prepare
        self._images = images
        self._open_url = open_url
        self._post: Optional[Post] = None
        self._generation = 0
        self._image_urls: List[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("imports_article_header")
        row = QHBoxLayout(self._header)
        row.setContentsMargins(16, 10, 12, 10)
        row.setSpacing(8)
        self._avatar = QLabel()
        self._avatar.setFixedSize(18, 18)
        # The source's name elides; the date beside it stays whole.
        self._origin = ElidingLabel()
        self._origin.setObjectName("imports_article_origin")
        self._date = QLabel()
        self._date.setObjectName("imports_article_origin")
        self._date.setTextFormat(Qt.TextFormat.PlainText)
        self._open = QPushButton(_("Open Original"))
        self._open.setObjectName("imports_open_original")
        self._open.setToolTip(_("Read this post on its website"))
        self._open.setAccessibleName(_("Open Original"))
        self._open.setAccessibleDescription(_("Opens the post on its website in your "
                                              "browser."))
        self._open.clicked.connect(self.open_original)
        row.addWidget(self._avatar)
        # Name and date read as one line ("Source  ·  date"): the name takes
        # no more room than it needs, the space goes after the date.
        self._origin.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        row.addWidget(self._origin)
        row.addWidget(self._date)
        row.addStretch(1)
        row.addWidget(self._open)
        layout.addWidget(self._header)

        rule = QWidget()
        rule.setObjectName("imports_rule")
        rule.setFixedHeight(1)
        rule.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout.addWidget(rule)

        self._stack = QStackedWidget()
        self._empty = _message(_("Select a post to read it here."))
        self._preview = NostrPreview(images=self._image, dark=dark)
        self._preview.setAccessibleName(_("Article"))
        # Links are checked here, never opened by the view on its own.
        self._preview.setOpenExternalLinks(False)
        self._preview.setOpenLinks(False)
        self._preview.anchorClicked.connect(self._on_link)
        self._message = _message("")
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._preview)
        self._stack.addWidget(self._message)
        layout.addWidget(self._stack, 1)
        if images is not None:
            images.ready.connect(self._on_image)
        self.show_post(None)

    # -- showing a post -----------------------------------------------------------

    @property
    def post(self) -> Optional[Post]:
        return self._post

    def show_post(self, post: Optional[Post]) -> None:
        self._generation += 1
        self._post = post
        self._header.setVisible(post is not None)
        if post is None:
            self._stack.setCurrentIndex(_EMPTY)
            return
        when = date_text(post.published_at or post.found_at)
        self._origin.setText(post.source_title)
        self._date.setText(f"\u00b7  {when}" if when and post.source_title else when)
        self._avatar.setPixmap(letter_avatar(post.source_title, post.source_url or
                                             post.source_key, 18, dark=is_dark(self.palette()),
                                             dpr=self.devicePixelRatioF()))
        self._open.setEnabled(url_safety.is_safe_external_url(post.link))
        # Never the previous post's body under this post's header while
        # this one is prepared (review L1).
        self._message.findChild(QLabel).setText("")
        self._stack.setCurrentIndex(_MESSAGE)
        generation = self._generation
        self._prepare(post,
                      on_ready=lambda markdown, article: self._show_article(
                          generation, markdown, article),
                      on_failed=lambda reason: self._show_message(generation, reason))

    def _show_article(self, generation: int, markdown: str, article: Article) -> None:
        if generation != self._generation:
            return
        self._image_urls = [u for u in [article.image, *scan_markdown_images(markdown)] if u]
        if self._images is not None:
            # The article being read goes ahead of every cover waiting,
            # decoded as wide as its column.
            width = QSize(int(COLUMN_WIDTH * self.devicePixelRatioF()), 0)
            for url in self._image_urls:
                self._images.request(url, width, urgent=True)
        self._preview.show_article(markdown, article)
        self._preview.verticalScrollBar().setValue(0)
        self._stack.setCurrentIndex(_ARTICLE)

    def _show_message(self, generation: int, reason: str) -> None:
        if generation != self._generation:
            return
        self._message.findChild(QLabel).setText(reason)
        self._stack.setCurrentIndex(_MESSAGE)

    def set_dark(self, dark: bool) -> None:
        self._preview.set_dark(dark)
        if self._post is not None:
            self.show_post(self._post)

    # -- images and links ----------------------------------------------------------

    def _image(self, url: str):
        return self._images.image(url) if self._images is not None else None

    def _on_image(self, url: str) -> None:
        if url not in self._image_urls or self._stack.currentIndex() != _ARTICLE:
            return
        image = self._images.image(url)
        if image is None:
            return
        document = self._preview.document()
        document.addResource(QTextDocument.ResourceType.ImageResource.value, QUrl(url), image)
        document.markContentsDirty(0, document.characterCount())

    def open_original(self) -> None:
        if self._post is not None:
            self._open_link(self._post.link)

    def _on_link(self, url: QUrl) -> None:
        self._open_link(url.toString())

    def _open_link(self, link: str) -> None:
        if url_safety.is_safe_external_url(link):
            self._open_url(QUrl(link))
        else:
            self.link_refused.emit(link)


def _message(text: str) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(32, 32, 32, 32)
    label = QLabel(text)
    label.setObjectName("imports_placeholder")
    label.setWordWrap(True)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addStretch(1)
    layout.addWidget(label)
    layout.addStretch(2)
    return page


__all__ = ["ArticlePane"]
