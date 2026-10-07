# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Site icons for the sources (C12).

A source shows its site's icon once it is known, its letter until then.
The icon is the one the home page names, else /favicon.ico; every
request is a guarded one (here a fake), 64 KiB at most, decoded by the
app's image rules with ICO added; what is found is kept on this computer
and looked for again after 30 days, a site without one after a week.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from nostr.imports import favicons
from nostr.imports.favicons import FaviconCache, decode_icon, icons_named, site_of
from nostr.imports.netguard import Fetched

DAY = 24 * 3600
T0 = 1_800_000_000.0


def image_bytes(fmt: str, size: int = 32, color: str = "#d03030") -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, fmt)
    return bytes(buffer.data())


PNG = image_bytes("PNG")
ICO = image_bytes("ICO")


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Web:
    """The guarded GET, answered from a table; ``hold`` keeps answers
    until :meth:`release`."""

    def __init__(self, pages=None, *, hold=False):
        self.pages = dict(pages or {})
        self.asked = []
        self.hold = hold
        self.waiting = []

    def __call__(self, url, max_bytes, on_done):
        self.asked.append((url, max_bytes))
        status, data = self.pages.get(url, (404, b""))
        answer = Fetched(url=url, status=status, data=data)
        if len(data) > max_bytes:
            answer = Fetched(url=url, too_large=True)
        if self.hold:
            self.waiting.append(lambda: on_done(answer))
        else:
            on_done(answer)

    def release(self):
        while self.waiting:
            self.waiting.pop(0)()


def cache(tmp_path, web, clock=None):
    return FaviconCache(tmp_path / "favicons", get=web, clock=clock or Clock())


HOME = "https://blog.example/"
PAGE = b"""<!doctype html><html><head>
<link rel="stylesheet" href="/style.css">
<link rel="icon" type="image/svg+xml" href="/icon.svg">
<link rel="shortcut icon" href="/favicon.ico">
<link rel="apple-touch-icon" href="/apple.png">
<link rel="icon" type="image/png" sizes="32x32" href="/icon-32.png">
</head><body><link rel="icon" href="/not-in-the-head.png"></body></html>"""


class TestFindingTheIcon:
    def test_the_heads_icons_best_first(self):
        assert icons_named(PAGE.decode(), HOME) == [
            "https://blog.example/icon-32.png",       # a PNG of a good size
            "https://blog.example/apple.png",
            "https://blog.example/favicon.ico",
        ]

    def test_the_site_of_an_address(self):
        assert site_of("https://blog.example/feed?x=1") == "https://blog.example"
        assert site_of("http://blog.example:8080/") == "http://blog.example:8080"
        assert site_of("npub1example") == ""
        assert site_of("ftp://blog.example/") == ""

    def test_decoding(self):
        assert decode_icon(PNG).width() == 32
        assert decode_icon(ICO).width() == 32
        assert decode_icon(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>") is None
        assert decode_icon(b"\x89PNG" + b"\0" * (favicons.MAX_ICON_BYTES + 1)) is None


class TestTheCache:
    def test_the_icon_the_page_names_is_found_and_kept(self, tmp_path):
        web = Web({HOME: (200, PAGE), HOME + "icon-32.png": (200, PNG)})
        icons = cache(tmp_path, web)
        ready = []
        icons.ready.connect(ready.append)
        icons.icon("https://blog.example/feed")                    # looked up now
        assert ready == ["https://blog.example"]
        assert web.asked[0] == (HOME, favicons.MAX_PAGE_BYTES)     # the home, not the feed
        assert ("https://blog.example/icon-32.png", favicons.MAX_ICON_BYTES) in web.asked
        assert icons.icon(HOME).width() == favicons.ICON_PIXELS
        # Another run reads it from this computer, asking nothing.
        again = Web()
        assert cache(tmp_path, again).icon(HOME) is not None
        assert again.asked == []

    def test_without_one_named_the_favicon_ico(self, tmp_path):
        web = Web({HOME: (200, b"<html><head></head></html>"),
                   HOME + "favicon.ico": (200, ICO)})
        icons = cache(tmp_path, web)
        icons.icon(HOME)
        assert icons.icon(HOME) is not None

    def test_a_page_that_cannot_be_read_still_tries_the_favicon(self, tmp_path):
        web = Web({HOME + "favicon.ico": (200, ICO)})       # the home page 404s
        icons = cache(tmp_path, web)
        icons.icon(HOME)
        assert icons.icon(HOME) is not None

    def test_an_icon_too_large_or_not_an_image_is_passed_over(self, tmp_path):
        web = Web({HOME: (200, PAGE),
                   HOME + "icon-32.png": (200, b"\x89PNG" + b"\0" * (70 * 1024)),
                   HOME + "apple.png": (200, b"<html>not an image</html>"),
                   HOME + "favicon.ico": (200, ICO)})
        icons = cache(tmp_path, web)
        icons.icon(HOME)
        assert icons.icon(HOME) is not None
        assert [url for url, _ in web.asked][-1] == HOME + "favicon.ico"

    def test_a_site_without_one_is_asked_again_after_a_week(self, tmp_path):
        clock = Clock()
        web = Web()
        icons = cache(tmp_path, web, clock)
        assert icons.icon(HOME) is None
        asked = len(web.asked)
        assert asked == 2                          # the page, the favicon
        fresh = cache(tmp_path, web, clock)        # a later run, the same week
        fresh.icon(HOME)
        assert len(web.asked) == asked
        clock.now += 8 * DAY
        cache(tmp_path, web, clock).icon(HOME)
        assert len(web.asked) == asked + 2

    def test_a_known_icon_is_refreshed_after_thirty_days_and_shown_meanwhile(self, tmp_path):
        clock = Clock()
        web = Web({HOME: (200, PAGE), HOME + "icon-32.png": (200, PNG)})
        cache(tmp_path, web, clock).icon(HOME)
        kept = tmp_path / "favicons"
        (png,) = kept.glob("*.png")
        os.utime(png, (T0, T0))
        clock.now = T0 + 31 * DAY
        later = Web({HOME: (200, PAGE), HOME + "icon-32.png": (200, PNG)}, hold=True)
        icons = cache(tmp_path, later, clock)
        assert icons.icon(HOME) is not None        # the old one, while asked again
        assert later.asked == [(HOME, favicons.MAX_PAGE_BYTES)]

    def test_two_sites_at_a_time(self, tmp_path):
        web = Web(hold=True)
        icons = cache(tmp_path, web)
        for n in range(4):
            icons.icon(f"https://site{n}.example/")
        assert [url for url, _ in web.asked] == ["https://site0.example/",
                                                 "https://site1.example/"]
        web.release()                              # each page answers, then its favicon...
        web.release()
        web.release()
        assert {url for url, _ in web.asked} >= {"https://site2.example/",
                                                 "https://site3.example/"}

    def test_a_nostr_author_has_none(self, tmp_path):
        web = Web()
        assert cache(tmp_path, web).icon("npub1example") is None
        assert web.asked == []


class TestShown:
    def test_the_controller_asks_with_the_sources_website(self, tmp_path):
        from nostr.imports.feed_list import source_key
        from tests.test_imports_window import controller_for
        controller = controller_for(tmp_path)
        assert controller.site_icon(url=HOME) is None            # no cache: nothing asked
        web = Web({"https://site.example/": (200, PAGE),
                   "https://site.example/icon-32.png": (200, PNG)})
        controller.icons = cache(tmp_path, web)
        controller.icons.ready.connect(controller.icon_ready)
        ready = []
        controller.icon_ready.connect(ready.append)
        feed = "https://feeds.example/blog.xml"
        controller.subscriptions.add_feed(feed, "Blog")
        controller.inbox.set_site(source_key(feed), site_url="https://site.example/")
        controller.sources_changed.emit()
        controller.site_icon(source_key(feed), feed)
        assert web.asked[0][0] == "https://site.example/"        # its website, not the feed's
        assert ready == ["https://site.example"]
        assert controller.site_icon(source_key(feed), feed) is not None
        controller.account_changed(None)

    def test_the_article_shows_the_icon(self, tmp_path):
        from nostr.imports.workspace import INBOX_ORIGIN, Post
        from nostr.ui.imports_article import ArticlePane
        pane = ArticlePane(prepare=lambda post, on_ready, on_failed: None, images=None)
        red = QImage(32, 32, QImage.Format.Format_ARGB32)
        red.fill(QColor("#ff0000"))
        pane.icon_for = lambda key, url: red
        pane.show_post(Post(key="c:k:d", origin=INBOX_ORIGIN, source_key="k",
                            source_title="Blog", source_url="https://blog.example/feed",
                            d_tag="d", title="A post", excerpt="", image="",
                            link="https://blog.example/a", author="", published_at=0,
                            found_at=0, read_minutes=0, image_count=0))
        shown = pane._avatar.pixmap().toImage()
        middle = shown.pixelColor(shown.width() // 2, shown.height() // 2)
        assert (middle.red(), middle.green(), middle.blue()) == (255, 0, 0)
        pane.deleteLater()


def test_a_lookup_under_way_does_not_keep_the_cache(tmp_path):
    """The request is a child of the cache's network manager: a callback
    holding the cache would make a cycle only the collector frees (the
    crash pattern of CP2)."""
    import gc
    import weakref
    calls = []
    icons = FaviconCache(tmp_path, get=lambda url, max_bytes, on_done: calls.append(on_done))
    icons.icon(HOME)
    ref = weakref.ref(icons)
    automatic = gc.isenabled()
    gc.disable()
    try:
        del icons
        assert ref() is None
        calls[0](Fetched(url=HOME, status=404))      # a late answer changes nothing
    finally:
        if automatic:
            gc.enable()
