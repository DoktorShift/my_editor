# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep import fetches off the person's own network.

A feed, a page linked from one, or an image in a post names addresses
chosen by strangers. Fetched as they are, one of them can make this app
knock on a router, a printer or a service on this computer
(``http://192.168.1.1/``, ``http://nas.local/``, a name that resolves to
``127.0.0.1``). Every fetch the importer makes (feeds, discovery, full
text, podcast chapters, images, site icons) passes this guard first:

- The address itself follows url_safety's rule for third-party URLs
  (:func:`url_safety.is_safe_mirror_source`): http or https, no user
  name or password, an address literal only when it is public, no
  local-only names (``localhost``, ``router``, ``.local``, ``.lan``...),
  and at most 2048 characters.
- The name is then resolved, and refused when **any** address it
  resolves to is not public (private, loopback, link-local, reserved,
  multicast), the test url_safety applies to an address literal. A name
  that does not resolve is refused too: Qt would resolve it again to
  connect, and that answer is not checked (STANDUP's server refuses such
  names as well).
- Every redirect is checked the same way before it is followed (the
  fetchers ask Qt to wait for :meth:`NetGuard.follow`), five at most.

Known limit: Qt resolves the name once more when it connects, so a name
whose answer changes between the two lookups (DNS rebinding) is not
caught. Connecting to the checked address itself is left for later; it
needs care with certificates on every platform.

Relays and the person's own media server do not pass through here: the
person chose those.
"""

from __future__ import annotations

import ipaddress
from typing import Callable, List, Optional

from PySide6.QtNetwork import QHostInfo, QNetworkReply, QNetworkRequest

import url_safety

from i18n import _

from .errors import LOCAL_NETWORK_MESSAGE as REFUSED_MESSAGE

MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 5

# Why a name that did not resolve is not fetched, as the reason in
# "Couldn't reach that URL: {reason}." (errors.friendly_message).
UNRESOLVED_REASON = _("the address could not be found")

# resolve(host, on_done): on_done(addresses) with the addresses as text,
# or None when the name could not be resolved.
Resolver = Callable[[str, Callable[[Optional[List[str]]], None]], None]


def is_public_address(address: str) -> bool:
    """Whether a resolved address is on the public internet: global and
    not multicast (an IPv4 address written as IPv6 is judged as IPv4)."""
    try:
        ip = ipaddress.ip_address((address or "").split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


def is_allowed_url(url: str) -> bool:
    """The check that needs no network: url_safety's rule for third-party
    URLs, and the length STANDUP allows."""
    return (isinstance(url, str) and len(url) <= MAX_URL_LENGTH
            and url_safety.is_safe_mirror_source(url))


def qt_resolver(host: str, on_done: Callable[[Optional[List[str]]], None]) -> None:
    """Resolve ``host`` without blocking (QHostInfo)."""
    def _answer(info) -> None:
        if info.error() != QHostInfo.HostInfoError.NoError:
            on_done(None)
            return
        on_done([address.toString() for address in info.addresses()])

    QHostInfo.lookupHost(host, _answer)


class NetGuard:
    """Checks addresses before the importer fetches them.

    ``resolver`` is the seam tests use; it defaults to :func:`qt_resolver`.
    """

    def __init__(self, resolver: Optional[Resolver] = None) -> None:
        self._resolve = resolver or qt_resolver

    def check(self, url: str, *, on_allowed: Callable[[], None],
              on_refused: Callable[[str], None],
              on_unresolved: Optional[Callable[[], None]] = None) -> None:
        """Call ``on_allowed`` when ``url`` may be fetched, ``on_refused``
        with the reason when it points into the local network.

        A name that does not resolve is not fetched either: it goes to
        ``on_unresolved``, by default ``on_refused`` with
        :data:`UNRESOLVED_REASON` (review L10: a name the guard could not
        resolve was fetched, and Qt's own lookup could answer 127.0.0.1).
        """
        if not is_allowed_url(url):
            on_refused(REFUSED_MESSAGE)
            return
        host = url_safety.host_of(url) or ""
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            if is_public_address(host):
                on_allowed()
            else:
                on_refused(REFUSED_MESSAGE)
            return

        def _resolved(addresses: Optional[List[str]]) -> None:
            if not addresses:
                if on_unresolved is not None:
                    on_unresolved()
                else:
                    on_refused(UNRESOLVED_REASON)
                return
            if all(is_public_address(a) for a in addresses):
                on_allowed()
            else:
                on_refused(REFUSED_MESSAGE)

        self._resolve(host, _resolved)

    # -- redirects ------------------------------------------------------------

    @staticmethod
    def prepare(request: QNetworkRequest) -> None:
        """Make Qt wait for :meth:`follow` before following a redirect."""
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.UserVerifiedRedirectPolicy)
        request.setMaximumRedirectsAllowed(MAX_REDIRECTS)

    def follow(self, reply: QNetworkReply, on_refused: Callable[[str], None]) -> None:
        """Check every redirect of ``reply`` before it is followed; a
        refused one aborts the reply after ``on_refused``."""
        def _redirected(url) -> None:
            target = url.toString() if hasattr(url, "toString") else str(url)

            def _refuse(reason: str) -> None:
                on_refused(reason)
                reply.abort()

            self.check(target, on_allowed=reply.redirectAllowed.emit, on_refused=_refuse)

        reply.redirected.connect(_redirected)
