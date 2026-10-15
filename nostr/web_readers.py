# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Where a published post opens on the web: the reading apps, and their links.

    article (NIP-23)    Standup Discover · njump · YakiHonne · Primal · noStrudel
    curation (30004)    Standup Discover · njump · YakiHonne
    short note (1)      njump · YakiHonne · Primal · noStrudel

Standup Discover, EINUNDZWANZIG's reader, comes first for articles and
curations and is their default (links to it are part of the community
funding MyEditor gets); it shows no single short note, so for a note it
is not offered at all and njump comes first. A person can choose another
default in Settings (``default_reader``); the menus keep this order.

Each app's link is the one its own code defines, read from its router
(Primal's Router.tsx, noStrudel's app.tsx and articles/routes.tsx,
YakiHonne's pages; Habla is archived and Highlighter unverifiable, so
both are left out).

Standup shows posts on two hosts (Standup's backend/app.py): its blog
reader, where a curation is a site with its own pages, on
media.einundzwanzig.space; and Discover, where an article in no site is
read, on discover.einundzwanzig.space. An article that is in a site is
read on the site's page, as Discover's own cards link it. So a Standup
link is a short list of addresses, the most precise first
(:func:`links_for`), because not every one is served everywhere: the
owner-qualified site address (``/s/by/<owner>/<site key>``) and
``/a/<naddr>`` come with Standup 1.5, and a site's short address is
refused when two owners named their sites alike. ``/a/<author>/<d>``
reads any article Standup has collected. :class:`PageCheck` asks for each
in turn and the first that is there opens; njump opens where none is.

Identifiers go into Standup's addresses as Standup writes them: a site's
in URL-safe base64 in the owner-qualified form, and otherwise every
character outside ``A-Za-z0-9._~:-`` as "-" (an identifier can be a whole
web address).
"""

from __future__ import annotations

import base64
import re
from typing import Callable, Dict, NamedTuple, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

import url_safety

ARTICLE, NOTE, CURATION = "article", "note", "curation"
DISCOVER, NJUMP = "discover", "njump"

# Standup's two hosts: the blog reader with the sites, and Discover.
STANDUP_SITES = "https://media.einundzwanzig.space"
STANDUP_DISCOVER = "https://discover.einundzwanzig.space"

# How long each check may take before the page opens anyway.
CHECK_TIMEOUT_MS = 3_000

_UNSAFE = re.compile(r"[^A-Za-z0-9._~:-]")


class Site(NamedTuple):
    """A Standup site (a curation): its owner and its identifier."""

    owner: str
    identifier: str


class Post(NamedTuple):
    """A published post as the reading apps address it: its ``kind``, its
    ``entity`` (naddr or nevent), its author and identifier, and for an
    article the Standup site it is read in, if any."""

    kind: str
    entity: str = ""
    author: str = ""
    identifier: str = ""
    site: Optional[Site] = None


def url_safe(identifier: str) -> str:
    """``identifier`` as one segment of a Standup address (its _slugs.py)."""
    return _UNSAFE.sub("-", identifier or "")


def site_key(identifier: str) -> str:
    """A site's identifier in its owner-qualified address: URL-safe base64
    without padding (Standup's core/site_address.py)."""
    return base64.urlsafe_b64encode(identifier.encode("utf-8")).decode("ascii").rstrip("=")


def _standup_links(post: Post) -> Tuple[str, ...]:
    """Standup's addresses for ``post``, the most precise first."""
    if post.kind == CURATION and post.author and post.identifier:
        owner = post.author.lower()
        return (f"{STANDUP_SITES}/s/by/{owner}/{site_key(post.identifier)}",
                f"{STANDUP_SITES}/s/{url_safe(post.identifier)}")
    if post.kind != ARTICLE or not post.author or not post.identifier:
        return ()
    article = url_safe(post.identifier)
    links = []
    if post.site is not None and post.site.owner and post.site.identifier:
        owner = post.site.owner.lower()
        links += [f"{STANDUP_SITES}/s/by/{owner}/{site_key(post.site.identifier)}/{article}",
                  f"{STANDUP_SITES}/s/{url_safe(post.site.identifier)}/{article}"]
    links.append(f"{STANDUP_DISCOVER}/a/{post.author.lower()}/{article}")
    return tuple(links)


class Reader(NamedTuple):
    """One reading app: its name and its link for each kind it shows, a
    template with ``{entity}`` (the post's naddr or nevent); Standup
    Discover builds its own (_standup_links)."""

    key: str
    name: str
    templates: Dict[str, str]

    def shows(self, kind: str) -> bool:
        return kind in self.templates

    def links(self, post: Post) -> Tuple[str, ...]:
        """Where ``post`` opens in this app, the first choice first."""
        if not self.shows(post.kind):
            return ()
        if self.key == DISCOVER:
            found = _standup_links(post)
        else:
            found = (self.templates[post.kind].format(entity=post.entity),) if post.entity else ()
        return tuple(link for link in found if url_safety.is_safe_external_url(link))


READERS: Tuple[Reader, ...] = (
    # Standup's addresses are built (_standup_links); its templates only
    # say what it shows.
    Reader(DISCOVER, "Standup Discover", {ARTICLE: "", CURATION: ""}),
    Reader(NJUMP, "njump", {ARTICLE: "https://njump.me/{entity}",
                            NOTE: "https://njump.me/{entity}",
                            CURATION: "https://njump.me/{entity}"}),
    Reader("yakihonne", "YakiHonne", {ARTICLE: "https://yakihonne.com/article/{entity}",
                                      NOTE: "https://yakihonne.com/note/{entity}",
                                      CURATION: "https://yakihonne.com/curation/{entity}"}),
    Reader("primal", "Primal", {ARTICLE: "https://primal.net/a/{entity}",
                                NOTE: "https://primal.net/e/{entity}"}),
    Reader("nostrudel", "noStrudel", {ARTICLE: "https://nostrudel.ninja/articles/{entity}",
                                      NOTE: "https://nostrudel.ninja/n/{entity}"}),
)


def reader(key: str) -> Optional[Reader]:
    return next((entry for entry in READERS if entry.key == key), None)


def readers_for(kind: str) -> Tuple[Reader, ...]:
    """The apps that show a post of ``kind``, in the order menus list them."""
    return tuple(entry for entry in READERS if entry.shows(kind))


def default_reader(kind: str, chosen: str) -> Reader:
    """``chosen`` (Settings) where it shows a post of ``kind``, otherwise
    the first app that does: Discover for an article or a curation, njump
    for a note."""
    able = readers_for(kind)
    return next((entry for entry in able if entry.key == chosen), able[0])


def links_for(app: Optional[Reader], post: Post) -> Tuple[str, ...]:
    """``app``'s addresses for ``post``, the first choice first; () where it
    has none a browser may be handed."""
    return app.links(post) if app is not None else ()


def link_for(app: Optional[Reader], post: Post) -> str:
    """``app``'s first choice for ``post``, or ""."""
    found = links_for(app, post)
    return found[0] if found else ""


class PageCheck(QObject):
    """Asks whether web pages are there (HEAD requests). ``check(url,
    done)`` calls ``done(True)`` when it is, ``done(False)`` when the site
    says it is not (404), and ``done(None)`` when it could not tell (no
    answer in CHECK_TIMEOUT_MS, no network). ``first_found(urls, done)``
    asks for each in turn."""

    def __init__(self, parent: Optional[QObject] = None,
                 nam: Optional[QNetworkAccessManager] = None) -> None:
        super().__init__(parent)
        self._nam = nam or QNetworkAccessManager(self)

    def check(self, url: str, done: Callable[[Optional[bool]], None]) -> None:
        request = QNetworkRequest(QUrl(url))
        request.setTransferTimeout(CHECK_TIMEOUT_MS)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self._nam.head(request)
        reply.finished.connect(lambda: self._finished(reply, done))

    def first_found(self, urls: Sequence[str],
                    done: Callable[[str, Optional[bool]], None]) -> None:
        """``done(url, found)`` with the first of ``urls`` that is there
        (found True), or the first nobody could tell about (None: the
        network is away, so it is tried as it is), or the last with False
        when every one said it is not there."""
        remaining = list(urls)
        if not remaining:
            return

        def answered(found: Optional[bool]) -> None:
            url = remaining.pop(0)
            if found is not False or not remaining:
                done(url, found)
            else:
                self.check(remaining[0], answered)
        self.check(remaining[0], answered)

    @staticmethod
    def _finished(reply: QNetworkReply, done: Callable[[Optional[bool]], None]) -> None:
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        reply.deleteLater()
        if isinstance(status, int) and status == 404:
            done(False)
        elif isinstance(status, int) and 200 <= status < 400:
            done(True)
        else:
            done(None)
