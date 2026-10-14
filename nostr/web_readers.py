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

Each link is the one the app's own code defines, read from its router in
October 2026: Discover's backend/app.py (``/a/<naddr>`` on its public
origin; a curation, a Standup site, at ``/s/by/<owner>/<d tag in URL-safe
base64>`` on the reader origin its collection links use), Primal's
Router.tsx, noStrudel's app.tsx and articles/routes.tsx, YakiHonne's
pages. Habla (archived) and Highlighter (not verifiable) are left out.

Discover shows an article once it has indexed it, and says "Article not
found" until then. :class:`PageCheck` asks for the page first (one
request, made only when the person chose Discover), so the host can open
njump instead and say why.
"""

from __future__ import annotations

import base64
from typing import Callable, NamedTuple, Optional, Tuple

from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

import url_safety

ARTICLE, NOTE, CURATION = "article", "note", "curation"
DISCOVER, NJUMP = "discover", "njump"

# How long the Discover check may take before the page opens anyway.
CHECK_TIMEOUT_MS = 3_000


class Reader(NamedTuple):
    """One reading app: its name and its link for each kind ("" where it
    shows no such post). ``{entity}`` is the post's naddr or nevent."""

    key: str
    name: str
    article: str
    note: str
    curation: str


READERS: Tuple[Reader, ...] = (
    Reader(DISCOVER, "Standup Discover",
           article="https://discover.einundzwanzig.space/a/{entity}",
           note="",
           curation="https://media.einundzwanzig.space/s/by/{owner}/{site}"),
    Reader(NJUMP, "njump",
           article="https://njump.me/{entity}",
           note="https://njump.me/{entity}",
           curation="https://njump.me/{entity}"),
    Reader("yakihonne", "YakiHonne",
           article="https://yakihonne.com/article/{entity}",
           note="https://yakihonne.com/note/{entity}",
           curation="https://yakihonne.com/curation/{entity}"),
    Reader("primal", "Primal",
           article="https://primal.net/a/{entity}",
           note="https://primal.net/e/{entity}",
           curation=""),
    Reader("nostrudel", "noStrudel",
           article="https://nostrudel.ninja/articles/{entity}",
           note="https://nostrudel.ninja/n/{entity}",
           curation=""),
)


def reader(key: str) -> Optional[Reader]:
    return next((entry for entry in READERS if entry.key == key), None)


def readers_for(kind: str) -> Tuple[Reader, ...]:
    """The apps that show a post of ``kind``, in the order menus list them."""
    return tuple(entry for entry in READERS if getattr(entry, kind, ""))


def default_reader(kind: str, chosen: str) -> Reader:
    """``chosen`` (Settings) where it shows a post of ``kind``, otherwise
    the first app that does: Discover for an article or a curation, njump
    for a note."""
    able = readers_for(kind)
    return next((entry for entry in able if entry.key == chosen), able[0])


def _site_token(identifier: str) -> str:
    """A Standup site's identifier in its route: URL-safe base64 without
    padding (standup's core/site_address.site_route_key)."""
    raw = base64.urlsafe_b64encode(identifier.encode("utf-8")).decode("ascii")
    return raw.rstrip("=")


def link_for(app: Optional[Reader], kind: str, *, entity: str, owner: str = "",
             identifier: str = "") -> str:
    """``app``'s link for a post of ``kind`` (``entity`` its naddr or
    nevent; a curation's ``owner`` and ``identifier`` for Discover), or ""
    where there is none a browser may be handed."""
    if app is None:
        return ""
    template = getattr(app, kind, "")
    if not template:
        return ""
    if "{site}" in template:
        if not owner or not identifier:
            return ""
        link = template.format(owner=owner.lower(), site=_site_token(identifier))
    else:
        if not entity:
            return ""
        link = template.format(entity=entity)
    return link if url_safety.is_safe_external_url(link) else ""


class PageCheck(QObject):
    """Asks whether a web page is there (a HEAD request): ``check(url,
    done)`` calls ``done(True)`` when it is, ``done(False)`` when the site
    says it is not (404), and ``done(None)`` when it could not tell (no
    answer in CHECK_TIMEOUT_MS, no network)."""

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
