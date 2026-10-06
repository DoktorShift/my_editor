# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The article pane: a post as an import will publish it, and nothing more.

A feed's HTML is a stranger's. The pane shows the Markdown an import
makes of it with HTML switched off, loads images only through the
window's guarded image source, opens links only through the app's
policy for outside links, and drops an answer for a post no longer open.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QColor, QImage

from nostr.imports.inbox_store import NEW
from nostr.imports.workspace import INBOX_ORIGIN, Post
from nostr.preview import Article
from nostr.rss.normalize import html_to_markdown
from nostr.ui.imports_article import ArticlePane

POST = Post(key="c:s:rss-1", origin=INBOX_ORIGIN, source_key="s", source_title="Field notes",
            source_url="https://s.example/feed", d_tag="rss-1", title="Hello", excerpt="",
            image="", link="https://s.example/hello", author="", published_at=100,
            found_at=100, read_minutes=1, image_count=1, state=NEW)


class Images(QObject):
    ready = Signal(str)

    def __init__(self):
        super().__init__()
        self.kept = {}
        self.requested = []

    def image(self, url):
        return self.kept.get(url)

    def request(self, url, size=None, *, urgent=False):
        self.requested.append(url)

    def arrive(self, url):
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(QColor("#884422"))
        self.kept[url] = image
        self.ready.emit(url)


class Prepare:
    """Stands in for the controller's prepare_article; ``hold`` keeps the
    answer back until :meth:`answer`."""

    def __init__(self, markdown, article=None, hold=False):
        self.markdown = markdown
        self.article = article or Article(title="Hello", image="https://s.example/cover.png")
        self.hold = hold
        self.pending = []

    def __call__(self, post, *, on_ready, on_failed):
        if self.hold:
            self.pending.append(lambda: on_ready(self.markdown, self.article))
        else:
            on_ready(self.markdown, self.article)


def pane(markdown, **kw):
    images = Images()
    opened = []
    widget = ArticlePane(prepare=Prepare(markdown, **kw), images=images,
                         open_url=lambda url: opened.append(url.toString()))
    return widget, images, opened


def text_of(widget):
    return widget._preview.document().toPlainText()


def test_no_script_style_frame_or_handler_survives():
    hostile = html_to_markdown(
        "<p>Hi</p><script>alert(1)</script><style>p{color:red}</style>"
        "<iframe src='https://evil.example'></iframe><img src='https://s.example/a.png' "
        "onerror='alert(2)'><b onclick='alert(3)'>bold</b>")
    widget, _images, _opened = pane(hostile)
    widget.show_post(POST)
    shown = text_of(widget)
    assert "Hi" in shown and "bold" in shown
    assert "alert" not in shown and "color:red" not in shown
    assert "evil" not in widget._preview.document().toHtml()


def test_raw_html_in_markdown_is_shown_as_text_not_run():
    widget, _images, _opened = pane("<script>alert(1)</script>\n\nText")
    widget.show_post(POST)
    assert "<script>" in text_of(widget)


def test_images_come_only_from_the_image_source():
    widget, images, _opened = pane("![a](https://s.example/a.png)\n\nBody")
    widget.show_post(POST)
    assert images.requested == ["https://s.example/cover.png", "https://s.example/a.png"]
    # Nothing arrived yet: an empty frame, and the view asked nobody else.
    images.arrive("https://s.example/a.png")
    resource = widget._preview.document().resource(2, QUrl("https://s.example/a.png"))
    assert resource is not None and not resource.isNull()


def test_links_open_only_through_the_policy():
    widget, _images, opened = pane("[site](https://s.example/x) [bad](javascript:alert(1))")
    refused = []
    widget.link_refused.connect(refused.append)
    widget.show_post(POST)
    widget._on_link(QUrl("https://s.example/x"))
    widget._on_link(QUrl("javascript:alert(1)"))
    widget._on_link(QUrl("file:///etc/passwd"))
    assert opened == ["https://s.example/x"]
    assert refused == ["javascript:alert(1)", "file:///etc/passwd"]
    assert not widget._preview.openExternalLinks()
    assert not widget._preview.openLinks()


def test_open_original_follows_the_policy_too():
    widget, _images, opened = pane("Body")
    widget.show_post(POST)
    assert widget._open.isEnabled()
    widget.open_original()
    assert opened == ["https://s.example/hello"]
    widget.show_post(replace(POST, link="javascript:alert(1)"))
    assert not widget._open.isEnabled()


def test_a_late_answer_for_another_post_is_dropped():
    widget, _images, _opened = pane("First body", hold=True)
    widget.show_post(POST)
    late = widget._prepare.pending.pop()
    widget._prepare.markdown = "Second body"
    widget.show_post(replace(POST, key="c:s:rss-2", d_tag="rss-2"))
    current = widget._prepare.pending.pop()
    current()
    late()
    assert "Second body" in text_of(widget)
    assert "First body" not in text_of(widget)


def test_no_post_shows_the_hint():
    widget, _images, _opened = pane("x")
    widget.show_post(None)
    assert widget._stack.currentIndex() == 0
    assert not widget._header.isVisibleTo(widget)


def test_the_header_names_the_source_and_the_date():
    widget, _images, _opened = pane("x")
    widget.show_post(POST)
    assert widget._origin.text() == "Field notes"
    assert widget._date.text().startswith("\u00b7  ")


def test_a_long_source_name_elides_and_the_date_stays_whole():
    """Review M2: the header was cut to "...Pressemitteilungen \u00b7 vo"."""
    from dataclasses import replace
    widget, _images, _opened = pane("x")
    long = "Verbraucherzentrale Nordrhein-Westfalen Pressemitteilungen " * 2
    widget.resize(420, 400)
    widget.show()
    widget.show_post(replace(POST, source_title=long))
    widget.layout().activate()
    widget._header.layout().activate()
    assert widget._origin.painted_text().endswith("\u2026")
    assert widget._origin.toolTip() == long
    assert widget._date.width() >= widget._date.sizeHint().width()
    widget.hide()
