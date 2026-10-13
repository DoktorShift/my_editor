# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Keep import fetches off the person's own network.

A feed, a page linked from one, or an image in a post names addresses
chosen by strangers. Fetched as they are, one of them can make this app
knock on a router, a printer or a service on this computer
(``http://192.168.1.1/``, ``http://nas.local/``, a name that resolves to
``127.0.0.1``). Every fetch the importer makes (feeds, discovery, full
text, podcast chapters, images, site icons) goes through this guard:

- The address itself follows url_safety's rule for third-party URLs
  (:func:`url_safety.is_safe_mirror_source`): http or https, no user
  name or password, an address literal only when it is public, no
  local-only names (``localhost``, ``router``, ``.local``, ``.lan``...),
  and at most 2048 characters.
- The name is resolved once, and refused when **any** address it
  resolves to is not public (private, loopback, link-local, reserved,
  multicast), the test url_safety applies to an address literal. A name
  that does not resolve is refused too (STANDUP's server refuses such
  names as well).
- The connection goes to the address that was checked, not to the name
  (risk R-6, DNS rebinding: a name whose answer changes between the
  check and the connection would otherwise reach the local network).
  The request names the host in its ``Host`` header, and HTTPS checks
  the certificate for the name and sends it as the server name (SNI),
  through ``QNetworkRequest.setPeerVerifyName``. HTTP/2 is not used for
  these requests: it names the host differently, and the server would
  see the address instead. An IPv4 address is tried first (the network
  may have no IPv6); when it cannot be reached, the next checked one.
- Redirects are not left to Qt: each new address is checked, resolved
  and pinned the same way before it is asked, five at most.

Behind a proxy (the system's settings name one for the address) the
proxy connects, so nothing is pinned; the address is still checked as
above, and the log says once that pinning is off.

Relays and the person's own media server do not pass through here: the
person chose those.
"""

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

import shiboken6
from PySide6.QtCore import QObject, QUrl
from PySide6.QtNetwork import (
    QHostInfo,
    QNetworkProxy,
    QNetworkProxyFactory,
    QNetworkProxyQuery,
    QNetworkReply,
    QNetworkRequest,
)

import url_safety

from i18n import _

from .errors import LOCAL_NETWORK_MESSAGE as REFUSED_MESSAGE

_log = logging.getLogger(__name__)

MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 5
TRANSFER_TIMEOUT_MS = 30 * 1000           # 30 s without a byte
REDIRECTS = (301, 302, 303, 307, 308)

# Why a name that did not resolve is not fetched, as the reason in
# "Couldn't reach that URL: {reason}." (errors.friendly_message).
UNRESOLVED_REASON = _("the address could not be found")
TOO_MANY_REDIRECTS = _("it redirects too many times")
REQUEST_FAILED = _("the request could not be made")

# Errors after which the next checked address of the same name is tried:
# nothing was answered, the address could not be reached.
_NOT_REACHED = (
    QNetworkReply.NetworkError.ConnectionRefusedError,
    QNetworkReply.NetworkError.HostNotFoundError,
    QNetworkReply.NetworkError.TimeoutError,
    QNetworkReply.NetworkError.NetworkSessionFailedError,
    QNetworkReply.NetworkError.TemporaryNetworkFailureError,
    QNetworkReply.NetworkError.UnknownNetworkError,
)

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


def system_proxies(url: QUrl) -> List[QNetworkProxy]:
    """The proxies Qt would use for ``url``, from the system's settings."""
    return QNetworkProxyFactory.proxyForQuery(QNetworkProxyQuery(url))


def _proxied(proxies: Sequence[QNetworkProxy]) -> bool:
    return any(p.type() not in (QNetworkProxy.ProxyType.NoProxy,
                                QNetworkProxy.ProxyType.DefaultProxy) for p in proxies)


def connect_order(addresses: Sequence[str]) -> List[str]:
    """Checked addresses in the order they are tried: IPv4 first, since a
    network without IPv6 would otherwise wait for a timeout."""
    def is_v6(address: str) -> bool:
        return ":" in address
    return sorted(dict.fromkeys(addresses), key=is_v6)


def pin(request: QNetworkRequest, url: QUrl, address: str) -> None:
    """Point ``request`` at ``address`` while it still names ``url``'s host:
    in the Host header, and for HTTPS as the name the certificate is
    checked for and sent as (SNI)."""
    host = url.host(QUrl.ComponentFormattingOption.FullyEncoded)
    target = QUrl(url)
    target.setHost(address)         # QUrl adds the brackets of an IPv6 address
    request.setUrl(target)
    port = url.port()
    request.setRawHeader(b"Host", (host if port < 0 else f"{host}:{port}").encode("ascii"))
    if url.scheme().lower() == "https":
        request.setPeerVerifyName(host)
    # Cookies are kept by host, and here the host is an address that
    # other names share: none are sent or kept.
    request.setAttribute(QNetworkRequest.Attribute.CookieLoadControlAttribute,
                         QNetworkRequest.LoadControl.Manual)
    request.setAttribute(QNetworkRequest.Attribute.CookieSaveControlAttribute,
                         QNetworkRequest.LoadControl.Manual)


@dataclass(frozen=True)
class Fetched:
    """What one guarded GET ended with. Exactly one of the cases holds:
    ``refused`` (the reason), ``unresolved``, ``too_large``, ``error`` (a
    transfer that failed, in Qt's words), or an answer (``status``,
    ``data``)."""

    url: str = ""                 # the address that answered, by name
    status: int = 0
    data: bytes = b""
    content_type: str = ""
    etag: str = ""
    last_modified: str = ""
    refused: str = ""
    unresolved: bool = False
    too_large: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not (self.refused or self.unresolved or self.too_large or self.error)


class NetGuard:
    """Checks addresses before the importer fetches them, and fetches them
    pinned to the checked address (:meth:`get`).

    ``resolver`` and ``proxies`` are the seams tests use; they default to
    :func:`qt_resolver` and :func:`system_proxies`. ``tls`` is an SSL
    configuration for the requests (tests trust their own certificate
    with it); None keeps Qt's.
    """

    _proxy_logged = False

    def __init__(self, resolver: Optional[Resolver] = None, *,
                 proxies: Callable[[QUrl], Sequence[QNetworkProxy]] = system_proxies,
                 tls=None) -> None:
        self._resolve = resolver or qt_resolver
        self._proxies = proxies
        self.tls = tls

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
        self.vet(url, on_ok=lambda _addresses: on_allowed(), on_refused=on_refused,
                 on_unresolved=on_unresolved or (lambda: on_refused(UNRESOLVED_REASON)))

    def vet(self, url: str, *, on_ok: Callable[[Optional[List[str]]], None],
            on_refused: Callable[[str], None], on_unresolved: Callable[[], None]) -> None:
        """Check ``url``; ``on_ok`` gets the addresses its name resolved to,
        all of them public (None for an address literal: nothing to pin)."""
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
                on_ok(None)
            else:
                on_refused(REFUSED_MESSAGE)
            return

        def _resolved(addresses: Optional[List[str]]) -> None:
            if not addresses:
                on_unresolved()
            elif all(is_public_address(a) for a in addresses):
                on_ok(list(addresses))
            else:
                on_refused(REFUSED_MESSAGE)

        self._resolve(host, _resolved)

    def proxied(self, url: QUrl) -> bool:
        """Whether a proxy connects to ``url`` (then nothing is pinned)."""
        if not _proxied(self._proxies(url)):
            return False
        if not NetGuard._proxy_logged:
            NetGuard._proxy_logged = True
            _log.info("a proxy is set for import fetches: addresses are checked, "
                      "connections are not pinned to them")
        return True

    def get(self, nam, url: str, *, headers: Sequence[Tuple[bytes, bytes]] = (),
            max_bytes: int, on_done: Callable[[Fetched], None]) -> None:
        """GET ``url`` with ``nam``, every hop checked and pinned (see the
        module docstring); ``on_done`` once, with what it ended with."""
        _Transfer(self, nam, url, headers, max_bytes, on_done).start()


class _Transfer(QObject):
    """One guarded GET, from the first request to the last redirect.

    A child of the network manager it asks, which it reaches through
    ``parent()``: an attribute holding the manager would make the two a
    reference cycle that only Python's cycle collector frees, and that
    frees Qt objects in an order Qt does not survive.
    """

    def __init__(self, guard: NetGuard, nam, url: str,
                 headers: Sequence[Tuple[bytes, bytes]], max_bytes: int,
                 on_done: Callable[[Fetched], None]) -> None:
        super().__init__(nam)       # goes with the fetcher that asked
        self._guard = guard
        self._url = url             # the address being asked, by name
        self._headers = list(headers)
        self._max_bytes = max_bytes
        self._on_done = on_done
        self._hops = 0
        self._untried: List[str] = []
        self._oversize = False

    def start(self) -> None:
        self._vet()

    def _vet(self) -> None:
        url = self._url
        self._guard.vet(url, on_ok=self._checked,
                        on_refused=lambda reason: self._done(Fetched(url=url, refused=reason)),
                        on_unresolved=lambda: self._done(Fetched(url=url, unresolved=True)))

    def _checked(self, addresses: Optional[List[str]]) -> None:
        if not shiboken6.isValid(self):
            return              # the fetcher went while the name was resolved
        try:
            if addresses and not self._guard.proxied(QUrl(self._url)):
                self._untried = connect_order(addresses)
            else:
                self._untried = []
            self._ask(self._untried.pop(0) if self._untried else None)
        except Exception:   # noqa: BLE001, see _failed
            self._failed()

    def _ask(self, address: Optional[str]) -> None:
        url = QUrl(self._url)
        request = QNetworkRequest(url)
        if address is not None:
            pin(request, url, address)
        request.setTransferTimeout(TRANSFER_TIMEOUT_MS)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        request.setAttribute(QNetworkRequest.Attribute.Http2AllowedAttribute, False)
        if self._guard.tls is not None:
            request.setSslConfiguration(self._guard.tls)
        for name, value in self._headers:
            request.setRawHeader(name, value)
        self._oversize = False
        reply = self.parent().get(request)
        # Abort the moment the cap is crossed (a declared length over it
        # transfers nothing), instead of keeping an endless body.
        reply.downloadProgress.connect(self._on_progress)
        reply.finished.connect(self._on_finished)

    def _on_progress(self, received: int, total: int) -> None:
        reply = self.sender()
        if not self._oversize and (received > self._max_bytes or total > self._max_bytes):
            self._oversize = True
            if reply is not None:
                reply.abort()

    def _on_finished(self) -> None:
        reply = self.sender()
        if reply is None:
            return
        try:
            self._answered(reply)
        except Exception:   # noqa: BLE001, see _failed
            self._failed()
        finally:
            reply.deleteLater()

    def _failed(self) -> None:
        """End the transfer when something raised on the way. Qt calls
        ``_checked`` and ``_on_finished`` from its own callbacks, and the
        name lookup's drops an exception without a word: the transfer then
        never ended, and the check waiting for it (one of the two that run
        at a time) waited for good."""
        _log.exception("an import fetch of %s ended with an error", self._url)
        self._done(Fetched(url=self._url, error=REQUEST_FAILED))

    def _answered(self, reply) -> None:
        url = self._url
        if self._oversize:
            self._done(Fetched(url=url, too_large=True))
            return
        error = reply.error()
        status = int(reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) or 0)
        if error != QNetworkReply.NetworkError.NoError and not status:
            if error in _NOT_REACHED and self._untried:
                self._ask(self._untried.pop(0))
                return
            self._done(Fetched(url=url, error=self._words(reply)))
            return
        if status in REDIRECTS:
            location = bytes(reply.rawHeader("Location")).decode("latin-1").strip()
            if location:
                if self._hops >= MAX_REDIRECTS:
                    self._done(Fetched(url=url, error=TOO_MANY_REDIRECTS))
                    return
                self._hops += 1
                self._url = QUrl(url).resolved(QUrl(location)).toString(
                    QUrl.ComponentFormattingOption.FullyEncoded)
                self._vet()
                return
        if error != QNetworkReply.NetworkError.NoError:
            self._done(Fetched(url=url, status=status, error=self._words(reply)))
            return
        data = bytes(reply.readAll())
        if len(data) > self._max_bytes:     # delivered in one burst, no progress
            self._done(Fetched(url=url, too_large=True))
            return
        content_type = reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader)
        self._done(Fetched(
            url=url, status=status, data=data, content_type=str(content_type or ""),
            etag=bytes(reply.rawHeader("ETag")).decode("latin-1").strip(),
            last_modified=bytes(reply.rawHeader("Last-Modified")).decode("latin-1").strip()))

    def _words(self, reply) -> str:
        """Qt's words for a failure, naming the address asked by its name,
        not by the address it was pinned to."""
        return (reply.errorString() or "").replace(reply.url().toString(), self._url)

    def _done(self, result: Fetched) -> None:
        on_done, self._on_done = self._on_done, None
        if on_done is None or not shiboken6.isValid(self):
            return
        try:
            on_done(result)
        finally:
            self.deleteLater()
