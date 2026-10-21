# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A post's HTML made ready for Markdown.

Every imported body passes through here before ``markdownify`` sees it,
whatever source it came from (a feed, an export, a page read for its
full text), so each fix below helps every source at once:

- addresses made whole against the post's own page (links, images,
  embeds, video and audio);
- lazy-loaded images: the real picture lives in ``data-src``,
  ``data-lazy-src`` or a ``srcset`` while ``src`` holds a placeholder
  (or nothing); a copy in ``<noscript>`` is kept once, not twice;
- embeds of known players (YouTube, Vimeo, Spotify, SoundCloud, ...) and
  ``<video>`` / ``<audio>`` become a line with their address, which
  Nostr clients show as a player, instead of vanishing or leaving an
  empty ``[](url)``; any other ``<iframe>`` (an ad, a tracker, a sign-up
  form) is dropped;
- what is page furniture rather than text (scripts, styles, forms,
  buttons, inline icons) is dropped, so its words do not leak into the
  article;
- tracking pixels (WordPress.com stats, Medium's, FeedBurner's, any
  1x1 image) are dropped, and WordPress's emoji pictures become the
  emoji they show;
- the "The post X appeared first on Y." line SEO plugins add to every
  feed item is dropped.

:func:`first_image` finds a post's picture the same way, for a cover:
the real one behind a lazy placeholder, never a pixel or an avatar.

:func:`code_language` tells ``markdownify`` the language of a code
block, so the fence keeps it (```` ```python ````).

Pure (no Qt, no network): HTML in, HTML out.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup, Comment, Tag

from .urls import WEB_SCHEMES, is_web, scheme, whole_url

# What a link may point at besides the web.
_LINK_SCHEMES = (*WEB_SCHEMES, "mailto", "nostr", "lightning", "bitcoin", "tel")

# Page furniture: its text is never part of the article.
_DROP = ("script", "style", "template", "form", "button", "input", "select",
         "textarea", "svg", "canvas", "head", "link", "meta")

# Attributes lazy-loading scripts keep the real picture in, best first:
# lazysizes (data-src, data-srcset), WP Rocket (data-lazy-src,
# data-lazy-srcset), jQuery Lazy Load (data-original), Jetpack
# (data-orig-file), the block editor's gallery (data-full-url).
_LAZY_SRC = ("data-src", "data-lazy-src", "data-original", "data-orig-file",
             "data-full-url")
_LAZY_SRCSET = ("data-srcset", "data-lazy-srcset", "srcset")
_PLACEHOLDER = re.compile(r"(placeholder|blank\.gif|spacer\.gif|lazy[-_]?load|"
                          r"transparent\.(gif|png)|pixel\.gif)", re.IGNORECASE)

# Images that are not pictures of the post: counters and pixels (Jetpack
# stats, Medium's feed counter, FeedBurner's, common ad counters), and
# the author's avatar (WordPress.com puts it in every item's
# media:content).
_TRACKER = re.compile(
    r"^(?:https?:)?//([^/]*\.)?(pixel\.wp\.com|stats\.wp\.com|stats\.wordpress\.com|"
    r"medium\.com/_/stat|feeds\.feedburner\.com/~r/|pixel\.quantserve\.com|"
    r"www\.google-analytics\.com|ad\.doubleclick\.net)", re.IGNORECASE)
_AVATAR = re.compile(r"^(?:https?:)?//([^/]*\.)?gravatar\.com/avatar/", re.IGNORECASE)
_EMOJI = re.compile(r"^(?:https?:)?//s\.w\.org/images/core/emoji/", re.IGNORECASE)

_SRCSET_SPLIT = re.compile(r",\s+")
# The hosts of the players whose embed is worth a link in the article
# (a PeerTube instance is known by its /videos/embed/ path). Anything
# else in an <iframe> is page furniture.
_PLAYERS = ("youtube.com", "youtube-nocookie.com", "player.vimeo.com",
            "open.spotify.com", "w.soundcloud.com", "rumble.com", "odysee.com",
            "dailymotion.com", "loom.com", "bandcamp.com", "embed.podcasts.apple.com",
            "embed.music.apple.com", "player.twitch.tv", "clips.twitch.tv", "codepen.io",
            "platform.twitter.com", "anchor.fm", "podbean.com", "buzzsprout.com",
            "simplecast.com", "player.fm")
# A YouTube player (/embed/ID, and /v/ID of the old Flash <embed>).
_YOUTUBE_ID = re.compile(r"^/(?:embed|v)/([A-Za-z0-9_-]{6,})")
_VIMEO_ID = re.compile(r"^/video/(\d+)")

# "The post <a>X</a> appeared first on <a>Blog</a>.": Yoast SEO's feed
# line in its translations (the current and the earlier wording), and
# All in One SEO's.
_APPEARED_FIRST_FORMS = (
    r"The post\s.+\sappeared first on", r"The post\s.+\sfirst appeared on",
    r"Der Beitrag\s.+\serschien zuerst auf", r"Het bericht\s.+\sverscheen eerst op",
    r"(?:L[’']article|Cet article)\s.+\sest apparu en premier sur",
    r"La entrada\s.+\s(?:se publicó primero en|aparece primero en)",
    r"L[’']articolo\s.+\s(?:proviene da|sembra essere il primo su)",
    r"Artykuł\s.+\spochodzi z serwisu", r"O post\s.+\sapareceu primeiro em",
    r"O conteúdo\s.+\saparece primeiro em", r"O post\s.+\saparece primeiro no",
)
APPEARED_FIRST = re.compile(r"(?:%s)\b" % "|".join(_APPEARED_FIRST_FORMS),
                            re.IGNORECASE | re.DOTALL)

# Where a code block names its language: "language-" (CommonMark,
# Prism, highlight.js), "lang-", GitHub's "highlight-source-".
_LANGUAGE_PREFIXES = ("language-", "lang-", "highlight-source-")


def clean_html(html: str, *, base_url: Optional[str] = None) -> str:
    """``html`` with the fixes above. ``base_url`` is the post's own page;
    without a web address there, relative addresses stay as written.

    feedparser's own cleaning is off (it dropped every video embed), so
    this is also where an address that is no web address goes: a link
    to ``javascript:`` or ``data:`` keeps its words and loses the link,
    a picture or player there is dropped."""
    if not html or not html.strip():
        return html or ""
    soup = BeautifulSoup(html, "html.parser")

    for node in soup.find_all(string=lambda s: isinstance(s, Comment)):
        node.extract()
    for tag in soup.find_all(_DROP):
        tag.decompose()
    for img in soup.find_all("img"):
        src = _real_src(img)
        if src:
            img["src"] = src
        else:
            img.decompose()
    for frame in soup.find_all("iframe"):
        if not (frame.get("src") or "").strip() and frame.get("data-src"):
            frame["src"] = frame["data-src"]  # a lazily loaded player
    _make_addresses_whole(soup, base_url)
    for img in soup.find_all("img"):
        if _EMOJI.match(img["src"]) or "wp-smiley" in (img.get("class") or ()):
            img.replace_with(img.get("alt") or "")
        elif _is_pixel(img):
            img.decompose()
    _settle_noscript(soup)

    for player in soup.find_all("object"):
        # The old Flash player: its address is the object's data, or its
        # "movie" parameter (with an <embed> of the same inside).
        movie = player.find("param", attrs={"name": "movie"})
        _replace_with_link(soup, player, _embed_address(
            player.get("data") or (movie.get("value") if movie else "") or ""))
    for frame in soup.find_all(("iframe", "embed")):
        _replace_with_link(soup, frame, _embed_address(frame.get("src") or ""))
    for media in soup.find_all(("video", "audio")):
        _replace_with_link(soup, media, _media_address(media))
    for anchor in soup.find_all("a"):
        if not anchor.get_text(strip=True) and anchor.find("img") is None:
            anchor.decompose()

    _drop_appeared_first(soup)
    return str(soup)


def _make_addresses_whole(soup: BeautifulSoup, base_url: Optional[str]) -> None:
    """Every address made whole against the post's page. A link keeps a
    web address or one of the schemes a Nostr post links with (mail,
    Nostr, Lightning, Bitcoin, phone); a picture or player keeps only a
    web address."""
    for anchor in soup.find_all("a", href=True):
        href = whole_url(anchor["href"], base_url).strip()
        if scheme(href) and scheme(href) not in _LINK_SCHEMES:
            anchor.unwrap()
        else:
            anchor["href"] = href
    for element in soup.find_all(("img", "iframe", "embed", "video", "audio", "source"),
                                 src=True):
        src = whole_url(element["src"], base_url).strip()
        if scheme(src) and not is_web(src):
            if element.name == "img":
                element.decompose()
            else:
                del element["src"]
        else:
            element["src"] = src


def first_image(html: str) -> Optional[str]:
    """The first picture of a post's HTML, for its cover: the real one
    behind a lazy placeholder, never a pixel, an emoji or an avatar."""
    if not html or "<img" not in html.lower():
        return None
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = _real_src(img)
        if src and is_cover_worthy(src) and "wp-smiley" not in (img.get("class") or ()) \
                and not _is_pixel(img):
            return src
    return None


def is_cover_worthy(url: str) -> bool:
    """Whether an image address can be a post's cover: not a counter, a
    pixel, an emoji or the author's avatar."""
    url = (url or "").strip()
    return bool(url) and not (_TRACKER.match(url) or _AVATAR.match(url) or _EMOJI.match(url))


def code_language(pre: Tag) -> Optional[str]:
    """The language a ``<pre>`` (or the ``<code>`` in it) names, for
    ``markdownify``'s ``code_language_callback``. ``None`` when it names
    none."""
    for element in (pre, pre.find("code") if isinstance(pre, Tag) else None):
        if not isinstance(element, Tag):
            continue
        named = (element.get("data-lang") or element.get("data-language") or "").strip()
        if _is_language(named):
            return named.lower()
        classes = list(element.get("class") or ())
        for index, cls in enumerate(classes):
            for prefix in _LANGUAGE_PREFIXES:
                if cls.startswith(prefix) and _is_language(cls[len(prefix):]):
                    return cls[len(prefix):].lower()
            # WordPress SyntaxHighlighter: class="brush: php; title: ;"
            if cls == "brush:" and index + 1 < len(classes):
                candidate = classes[index + 1].rstrip(";")
                if _is_language(candidate):
                    return candidate.lower()
    return None


# -- images ----------------------------------------------------------------- #

def _real_src(img: Tag) -> str:
    """The picture an ``<img>`` shows once its page has loaded: ``src``,
    unless that is a placeholder and a lazy-loading attribute or a
    ``srcset`` names the real one. "" when there is nothing to show."""
    src = (img.get("src") or "").strip()
    placeholder = not src or src.startswith("data:")
    if not placeholder and not _PLACEHOLDER.search(src):
        return src
    for attribute in _LAZY_SRC:
        if (img.get(attribute) or "").strip():
            return img[attribute].strip()
    for attribute in _LAZY_SRCSET:
        best = _largest(img.get(attribute))
        if best:
            return best
    return "" if placeholder else src


def _is_pixel(img: Tag) -> bool:
    src = (img.get("src") or "").strip()
    if _TRACKER.match(src):
        return True
    return all((img.get(side) or "").strip() in ("0", "1", "1px")
               for side in ("width", "height"))


def _largest(srcset: Optional[str]) -> str:
    """The widest candidate of a ``srcset`` (``url 300w, url 1024w``)."""
    best, best_size = "", -1.0
    for candidate in _SRCSET_SPLIT.split((srcset or "").strip()):
        parts = candidate.strip().split()
        if not parts:
            continue
        size = 0.0
        if len(parts) > 1:
            try:
                size = float(parts[1][:-1])
            except ValueError:
                size = 0.0
        if size > best_size:
            best, best_size = parts[0], size
    return best


def _settle_noscript(soup: BeautifulSoup) -> None:
    """Lazy-loading pages repeat each image in ``<noscript>``: kept when it
    is the only copy, dropped when the page already shows it."""
    shown = {(img.get("src") or "").strip() for img in soup.find_all("img")
             if img.find_parent("noscript") is None}
    for block in soup.find_all("noscript"):
        sources = [(img.get("src") or "").strip() for img in block.find_all("img")]
        if sources and all(src in shown for src in sources):
            block.decompose()
        else:
            block.unwrap()


# -- embeds, video and audio ------------------------------------------------- #

def _embed_address(src: str) -> str:
    """The page a reader opens for an embed: the watch page of a YouTube
    video or playlist, a Vimeo video, a PeerTube video or a tweet, the
    embed's own address for another known player, and "" (dropped) for
    anything else."""
    if not is_web(src):
        return ""
    parts = urlsplit(src)
    host = (parts.hostname or "").lower()
    query = parse_qs(parts.query)
    if "/videos/embed/" in parts.path:
        return src.replace("/videos/embed/", "/videos/watch/", 1).split("?")[0]
    if not any(host == p or host.endswith("." + p) for p in _PLAYERS):
        return ""
    if host.endswith(("youtube.com", "youtube-nocookie.com")):
        if parts.path == "/embed/videoseries" and query.get("list"):
            return f"https://www.youtube.com/playlist?list={query['list'][0]}"
        match = _YOUTUBE_ID.match(parts.path)
        if match:
            return f"https://www.youtube.com/watch?v={match.group(1)}"
        if query.get("v"):
            return f"https://www.youtube.com/watch?v={query['v'][0]}"
    if host == "player.vimeo.com":
        match = _VIMEO_ID.match(parts.path)
        if match:
            return f"https://vimeo.com/{match.group(1)}"
    if host == "platform.twitter.com" and query.get("id"):
        return f"https://twitter.com/i/web/status/{query['id'][0]}"
    return src


def _media_address(media: Tag) -> str:
    src = (media.get("src") or "").strip()
    if is_web(src):
        return src
    for source in media.find_all("source"):
        value = (source.get("src") or "").strip()
        if is_web(value):
            return value
    return ""


def _replace_with_link(soup: BeautifulSoup, element: Tag, address: str) -> None:
    if not address:
        element.decompose()
        return
    paragraph = soup.new_tag("p")
    link = soup.new_tag("a", href=address)
    link.string = address
    paragraph.append(link)
    element.replace_with(paragraph)


# -- plugin lines --------------------------------------------------------------- #

def _drop_appeared_first(soup: BeautifulSoup) -> None:
    blocks = [b for b in soup.find_all(("p", "div")) if b.find(("p", "div")) is None]
    for block in reversed(blocks[-2:]):
        if APPEARED_FIRST.match(" ".join(block.get_text(" ").split())):
            block.decompose()
            return


def _is_language(value: str) -> bool:
    return bool(value) and len(value) <= 24 and bool(re.fullmatch(r"[A-Za-z0-9_+#.-]+", value)) \
        and value.lower() not in ("none", "plain", "text", "nohighlight")
