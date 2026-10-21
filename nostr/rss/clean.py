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
- the "The post X appeared first on Y." line SEO plugins add to every
  feed item is dropped.

:func:`code_language` tells ``markdownify`` the language of a code
block, so the fence keeps it (```` ```python ````).

Pure (no Qt, no network): HTML in, HTML out.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, Tag

# Page furniture: its text is never part of the article.
_DROP = ("script", "style", "template", "form", "button", "input", "select",
         "textarea", "svg", "canvas", "object", "embed", "head", "link", "meta")

# Attributes lazy-loading scripts keep the real picture in, best first.
_LAZY_SRC = ("data-src", "data-lazy-src", "data-original", "data-orig-file",
             "data-full-url", "data-url")
_LAZY_SRCSET = ("data-srcset", "data-lazy-srcset", "srcset")
_PLACEHOLDER = re.compile(r"(placeholder|blank\.gif|spacer\.gif|lazy[-_]?load|"
                          r"transparent\.(gif|png)|pixel\.gif)", re.IGNORECASE)

_SRCSET_SPLIT = re.compile(r",\s+")
# Players whose embed is worth a link in the article. Anything else in an
# <iframe> is page furniture.
_PLAYERS = ("youtube.com", "youtube-nocookie.com", "youtu.be", "vimeo.com",
            "open.spotify.com", "soundcloud.com", "rumble.com", "odysee.com",
            "fountain.fm", "wavlake.com", "dailymotion.com", "loom.com",
            "bandcamp.com", "podcasts.apple.com", "music.apple.com",
            "twitch.tv", "codepen.io", "platform.twitter.com", "x.com",
            "twitter.com", "anchor.fm", "podbean.com", "buzzsprout.com",
            "simplecast.com", "player.fm", "peertube.tv", "tube.tchncs.de")
_YOUTUBE_ID = re.compile(r"^/(?:embed|v|shorts)/([A-Za-z0-9_-]{6,})")
_VIMEO_ID = re.compile(r"^/video/(\d+)")

# "The post <a>X</a> appeared first on <a>Blog</a>." (Yoast and others),
# in the languages its translations use most.
_APPEARED_FIRST = re.compile(
    r"^(The post|Der Beitrag|La entrada|L[’']article|Il post|Het bericht|"
    r"O post|Wpis)\b.+\b(appeared first on|erschien zuerst auf|"
    r"aparece primero en|est apparu en premier sur|sembra essere il primo su|"
    r"verscheen eerst op|apareceu primeiro em|pojawił się poraz pierwszy w)\b",
    re.IGNORECASE | re.DOTALL)

_LANGUAGE_PREFIXES = ("language-", "lang-", "highlight-source-", "highlight-")


def clean_html(html: str, *, base_url: Optional[str] = None) -> str:
    """``html`` with the fixes above. ``base_url`` is the post's own page;
    without a web address there, relative addresses stay as written."""
    if not html or not html.strip():
        return html or ""
    soup = BeautifulSoup(html, "html.parser")
    base = base_url if _is_web(base_url) else None

    for node in soup.find_all(string=lambda s: isinstance(s, Comment)):
        node.extract()
    for tag in soup.find_all(_DROP):
        tag.decompose()

    for img in soup.find_all("img"):
        _settle_image(img)
    _settle_noscript(soup)

    for tag, attribute in (("a", "href"), ("img", "src"), ("iframe", "src"),
                           ("video", "src"), ("audio", "src"), ("source", "src")):
        for element in soup.find_all(tag):
            value = (element.get(attribute) or "").strip()
            if value and base and not value.startswith("#"):
                element[attribute] = urljoin(base, value)

    for frame in soup.find_all("iframe"):
        _replace_with_link(soup, frame, _embed_address(frame.get("src") or
                                                       frame.get("data-src") or ""))
    for media in soup.find_all(("video", "audio")):
        _replace_with_link(soup, media, _media_address(media))

    for anchor in soup.find_all("a"):
        if not anchor.get_text(strip=True) and anchor.find("img") is None:
            anchor.decompose()

    _drop_appeared_first(soup)
    return str(soup)


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

def _settle_image(img: Tag) -> None:
    src = (img.get("src") or "").strip()
    if not src or src.startswith("data:") or _PLACEHOLDER.search(src):
        real = next((img.get(a).strip() for a in _LAZY_SRC
                     if (img.get(a) or "").strip()), "")
        if not real:
            real = next((best for best in (_largest(img.get(a)) for a in _LAZY_SRCSET)
                         if best), "")
        if real:
            img["src"] = real
        elif not src or src.startswith("data:"):
            img.decompose()


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
    """The page a reader opens for an embed: the watch page for YouTube
    and Vimeo players, the embed's own address for another known player,
    and "" (dropped) for anything else."""
    if not _is_web(src):
        return ""
    parts = urlsplit(src)
    host = (parts.hostname or "").lower()
    if not any(host == p or host.endswith("." + p) for p in _PLAYERS):
        return ""
    if host.endswith(("youtube.com", "youtube-nocookie.com")):
        match = _YOUTUBE_ID.match(parts.path)
        if match:
            return f"https://www.youtube.com/watch?v={match.group(1)}"
        video = parse_qs(parts.query).get("v")
        if video:
            return f"https://www.youtube.com/watch?v={video[0]}"
    if host == "player.vimeo.com":
        match = _VIMEO_ID.match(parts.path)
        if match:
            return f"https://vimeo.com/{match.group(1)}"
    return src


def _media_address(media: Tag) -> str:
    src = (media.get("src") or "").strip()
    if _is_web(src):
        return src
    for source in media.find_all("source"):
        value = (source.get("src") or "").strip()
        if _is_web(value):
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
        if _APPEARED_FIRST.match(" ".join(block.get_text(" ").split())):
            block.decompose()
            return


def _is_language(value: str) -> bool:
    return bool(value) and len(value) <= 24 and bool(re.fullmatch(r"[A-Za-z0-9_+#.-]+", value)) \
        and value.lower() not in ("none", "plain", "text", "nohighlight")


def _is_web(url: Optional[str]) -> bool:
    return urlsplit(url or "").scheme in ("http", "https")

