# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Import fetches connect to the address the guard checked (risk R-6).

A name is resolved once and every address checked; the request then goes
to the checked address, naming the host in its Host header and, for
HTTPS, as the name the certificate is checked for and sent as (SNI). A
name whose answer changes after the check (DNS rebinding) cannot reach
the local network. Redirects are checked, resolved and pinned again.
Behind a proxy the address is still checked, but not pinned.

The first tests watch the requests a fake transport receives; the last
ones fetch from real local servers (HTTP and HTTPS), with a resolver
that sends the test names to this computer and the loopback address
allowed for them, as the ndak test bed does.
"""

from __future__ import annotations

import datetime
import logging
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkProxy,
    QNetworkReply,
    QNetworkRequest,
    QSslCertificate,
    QSslConfiguration,
)

from nostr.imports import netguard
from nostr.imports.errors import ERROR_CODES
from nostr.imports.fetch import BlobFetcher, SourceFetcher
from nostr.imports.netguard import (
    REFUSED_MESSAGE,
    TOO_MANY_REDIRECTS,
    NetGuard,
    connect_order,
)
from tests.blossom_fakes import FakeNam, FakeReply

V4, V4_OTHER = "93.184.216.34", "93.184.216.35"
V6 = "2606:2800:220:1:248:1893:25c8:1946"


def resolver(table, asked=None):
    def resolve(host, on_done):
        if asked is not None:
            asked.append(host)
        on_done(list(table.get(host, [V4])))
    return resolve


def fetch(fetcher, url):
    out = {}
    fetcher.fetch(url, on_success=lambda body: out.update(body=body),
                  on_failure=lambda error: out.update(error=error))
    return out


def requested(nam, n=0) -> QNetworkRequest:
    return nam.calls[n][1]


def host_header(request) -> bytes:
    return bytes(request.rawHeader("Host"))


class TestTheRequest:
    def test_it_goes_to_the_checked_address_and_names_the_host(self):
        nam = FakeNam([FakeReply(body=b"<rss/>")])
        fetcher = SourceFetcher(guard=NetGuard(resolver({"blog.example": [V6, V4]})), nam=nam)
        fetch(fetcher, "https://blog.example/feed?x=1")
        request = requested(nam)
        assert request.url().toString() == f"https://{V4}/feed?x=1"     # IPv4 first
        assert host_header(request) == b"blog.example"
        assert request.peerVerifyName() == "blog.example"
        assert request.attribute(QNetworkRequest.Attribute.Http2AllowedAttribute) is False
        assert request.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == \
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
        assert request.attribute(QNetworkRequest.Attribute.CookieLoadControlAttribute) == \
            QNetworkRequest.LoadControl.Manual

    def test_an_ipv6_address_goes_in_brackets(self):
        nam = FakeNam([FakeReply(body=b"<rss/>")])
        fetcher = SourceFetcher(guard=NetGuard(resolver({"blog.example": [V6]})), nam=nam)
        fetch(fetcher, "https://blog.example/feed")
        assert requested(nam).url().toString() == f"https://[{V6}]/feed"
        assert host_header(requested(nam)) == b"blog.example"

    def test_a_port_stays_and_is_named(self):
        nam = FakeNam([FakeReply(body=b"<rss/>")])
        fetcher = SourceFetcher(guard=NetGuard(resolver({})), nam=nam)
        fetch(fetcher, "https://blog.example:8443/feed")
        assert requested(nam).url().toString() == f"https://{V4}:8443/feed"
        assert host_header(requested(nam)) == b"blog.example:8443"

    def test_plain_http_has_no_certificate_to_check(self):
        nam = FakeNam([FakeReply(body=b"<rss/>")])
        fetcher = SourceFetcher(guard=NetGuard(resolver({})), nam=nam)
        fetch(fetcher, "http://blog.example/feed")
        assert requested(nam).url().toString() == f"http://{V4}/feed"
        assert requested(nam).peerVerifyName() == ""

    def test_an_address_literal_is_asked_as_it_is(self):
        asked = []
        nam = FakeNam([FakeReply(body=b"<rss/>")])
        fetcher = SourceFetcher(guard=NetGuard(resolver({}, asked)), nam=nam)
        fetch(fetcher, f"https://{V4}/feed")
        assert asked == []
        assert requested(nam).url().toString() == f"https://{V4}/feed"
        assert host_header(requested(nam)) == b""

    def test_images_are_pinned_too(self):
        nam = FakeNam([FakeReply(body=b"\x89PNG\r\n\x1a\n")])
        fetcher = BlobFetcher(nam=nam, guard=NetGuard(resolver({})))
        fetcher.fetch("https://img.example/a.png", on_success=lambda *_a: None,
                      on_failure=lambda _r: None)
        assert requested(nam).url().toString() == f"https://{V4}/a.png"
        assert host_header(requested(nam)) == b"img.example"

    def test_the_order_tries_ipv4_first(self):
        assert connect_order([V6, V4, V4, V4_OTHER]) == [V4, V4_OTHER, V6]


class TestRedirects:
    def test_each_hop_is_resolved_and_pinned_again(self):
        asked = []
        moved = FakeReply(status=302, raw_headers={"Location": b"https://cdn.example/a/feed"})
        relative = FakeReply(status=301, raw_headers={"Location": b"../b/feed?y=2"})
        final = FakeReply(body=b"<rss/>", raw_headers={"ETag": b'"v1"'})
        nam = FakeNam([moved, relative, final])
        fetcher = SourceFetcher(
            guard=NetGuard(resolver({"cdn.example": [V4_OTHER]}, asked)), nam=nam)
        meta = {}
        fetcher.fetch_feed("https://blog.example/feed", on_body=lambda text, m: meta.update(m),
                           on_not_modified=lambda: None, on_failure=lambda e: None)
        moved.finish()
        relative.finish()
        final.finish()
        assert asked == ["blog.example", "cdn.example", "cdn.example"]
        assert requested(nam, 1).url().toString() == f"https://{V4_OTHER}/a/feed"
        assert host_header(requested(nam, 1)) == b"cdn.example"
        # A relative address is read against the name, never the address.
        assert requested(nam, 2).url().toString() == f"https://{V4_OTHER}/b/feed?y=2"
        assert meta["final_url"] == "https://cdn.example/b/feed?y=2"

    def test_a_redirect_whose_name_turns_private_is_refused(self):
        moved = FakeReply(status=302, raw_headers={"Location": b"https://rebind.example/feed"})
        nam = FakeNam([moved])
        fetcher = SourceFetcher(guard=NetGuard(resolver({"rebind.example": [V4, "10.0.0.5"]})),
                                nam=nam)
        out = fetch(fetcher, "https://blog.example/feed")
        moved.finish()
        assert len(nam.calls) == 1
        assert out["error"].code == ERROR_CODES.LOCAL_NETWORK
        assert out["error"].message == REFUSED_MESSAGE

    def test_at_most_five(self):
        replies = [FakeReply(status=302, raw_headers={"Location": f"/hop{i}".encode()})
                   for i in range(6)]
        nam = FakeNam(replies)
        fetcher = SourceFetcher(guard=NetGuard(resolver({})), nam=nam)
        out = fetch(fetcher, "https://blog.example/feed")
        for reply in replies:
            if reply in nam.issued:
                reply.finish()
        assert len(nam.calls) == 6
        assert out["error"].message == TOO_MANY_REDIRECTS


class TestTheNextAddress:
    def test_an_address_that_cannot_be_reached_gives_way_to_the_next(self):
        refused = FakeReply(status=0, error=QNetworkReply.NetworkError.ConnectionRefusedError,
                            error_string="Connection refused")
        nam = FakeNam([refused, FakeReply(body=b"<rss/>")])
        fetcher = SourceFetcher(guard=NetGuard(resolver({"blog.example": [V4, V4_OTHER]})),
                                nam=nam)
        out = fetch(fetcher, "https://blog.example/feed")
        refused.finish()
        nam.issued[1].finish()
        assert requested(nam, 1).url().toString() == f"https://{V4_OTHER}/feed"
        assert out["body"] == "<rss/>"

    def test_an_answer_is_never_asked_again_elsewhere(self):
        missing = FakeReply(status=404, error=QNetworkReply.NetworkError.ContentNotFoundError,
                            error_string=f"Error transferring https://{V4}/feed - server "
                                         "replied: Not Found")
        nam = FakeNam([missing])
        fetcher = SourceFetcher(guard=NetGuard(resolver({"blog.example": [V4, V4_OTHER]})),
                                nam=nam)
        out = fetch(fetcher, "https://blog.example/feed")
        missing.finish()
        assert len(nam.calls) == 1
        # Said with the name that was asked, not the address it went to.
        assert out["error"].message == ("Error transferring https://blog.example/feed - "
                                        "server replied: Not Found")


class TestBehindAProxy:
    def proxy(self, _url):
        return [QNetworkProxy(QNetworkProxy.ProxyType.HttpProxy, "proxy.example", 3128)]

    def test_the_address_is_checked_but_not_pinned(self, caplog, monkeypatch):
        monkeypatch.setattr(NetGuard, "_proxy_logged", False)
        nam = FakeNam([FakeReply(body=b"<rss/>"), FakeReply(body=b"<rss/>")])
        guard = NetGuard(resolver({}), proxies=self.proxy)
        fetcher = SourceFetcher(guard=guard, nam=nam)
        with caplog.at_level(logging.INFO, logger="nostr.imports.netguard"):
            fetch(fetcher, "https://blog.example/feed")
            fetch(fetcher, "https://blog.example/other")
        assert requested(nam).url().toString() == "https://blog.example/feed"
        assert host_header(requested(nam)) == b""
        said = [r for r in caplog.records if "proxy" in r.getMessage()]
        assert len(said) == 1           # once, not once a fetch

    def test_a_private_answer_is_still_refused(self):
        nam = FakeNam()
        guard = NetGuard(resolver({"rebind.example": ["192.168.1.20"]}), proxies=self.proxy)
        out = fetch(SourceFetcher(guard=guard, nam=nam), "https://rebind.example/feed")
        assert nam.calls == []
        assert out["error"].code == ERROR_CODES.LOCAL_NETWORK


# --------------------------------------------------------------------------- #
# End to end, against servers on this computer                                #
# --------------------------------------------------------------------------- #

class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):
        type(self).seen.append((self.path, self.headers.get("Host")))
        if self.path == "/hop":
            port = self.server.server_address[1]
            self.send_response(302)
            self.send_header("Location", f"http://other.example:{port}/feed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<rss><channel><title>Local</title></channel></rss>"
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture
def local_names(monkeypatch):
    """The test names resolve to this computer, and loopback counts as
    public for this test only (as on the ndak test bed)."""
    real = netguard.is_public_address
    monkeypatch.setattr(netguard, "is_public_address",
                        lambda address: address == "127.0.0.1" or real(address))
    return resolver({"pinned.example": ["127.0.0.1"], "other.example": ["127.0.0.1"]})


def serve(tls=None):
    _Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    names = []
    if tls is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(*tls)
        context.sni_callback = lambda _sock, name, _ctx: names.append(name)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, names


def wait_for(out, seconds=15.0):
    deadline = time.monotonic() + seconds
    while not out and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return out


def test_a_feed_comes_from_the_checked_address_with_its_name(local_names):
    server, _names = serve()
    try:
        port = server.server_address[1]
        fetcher = SourceFetcher(guard=NetGuard(local_names), nam=QNetworkAccessManager())
        meta = {}
        out = {}
        fetcher.fetch_feed(f"http://pinned.example:{port}/hop",
                           on_body=lambda text, m: (meta.update(m), out.update(body=text)),
                           on_not_modified=lambda: None,
                           on_failure=lambda error: out.update(error=error))
        wait_for(out)
        assert "<title>Local</title>" in out.get("body", ""), out
        assert _Handler.seen == [("/hop", f"pinned.example:{port}"),
                                 ("/feed", f"other.example:{port}")]
        assert meta["final_url"] == f"http://other.example:{port}/feed"
    finally:
        server.shutdown()
        server.server_close()


def _certificate(tmp_path, name):
    """A server certificate for ``name``, issued by a test authority that
    ``trust`` (the returned configuration) trusts.

    The two are kept apart, as real ones are: the authority may sign
    certificates and nothing else, the server's may serve TLS for its name
    and nothing else. A self-signed authority used as the server's own
    certificate passes OpenSSL, but Apple's TLS refuses it ("not trusted
    for this purpose"), and Qt uses Apple's where OpenSSL is missing."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = datetime.datetime.now(datetime.timezone.utc)

    def usage(*, sign_certificates):
        return x509.KeyUsage(
            digital_signature=not sign_certificates, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=sign_certificates, crl_sign=sign_certificates,
            encipher_only=False, decipher_only=False)

    def issued(subject, key, issuer, issuer_key, *extensions):
        builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=1)))
        for extension, critical in extensions:
            builder = builder.add_extension(extension, critical=critical)
        return builder.sign(issuer_key, hashes.SHA256())

    authority_key = ec.generate_private_key(ec.SECP256R1())
    authority_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test authority")])
    authority = issued(
        authority_name, authority_key, authority_name, authority_key,
        (x509.BasicConstraints(ca=True, path_length=0), True),
        (usage(sign_certificates=True), True),
        (x509.SubjectKeyIdentifier.from_public_key(authority_key.public_key()), False))
    key = ec.generate_private_key(ec.SECP256R1())
    server = issued(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]), key,
        authority_name, authority_key,
        (x509.BasicConstraints(ca=False, path_length=None), True),
        (usage(sign_certificates=False), True),
        (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False),
        (x509.SubjectAlternativeName([x509.DNSName(name)]), False),
        (x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), False))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    trust = QSslConfiguration.defaultConfiguration()
    trust.addCaCertificate(QSslCertificate(authority.public_bytes(serialization.Encoding.PEM)))
    return (str(cert_path), str(key_path)), trust


def test_https_checks_the_certificate_for_the_name_and_sends_it(local_names, tmp_path):
    files, trust = _certificate(tmp_path, "pinned.example")
    server, names = serve(tls=files)
    try:
        port = server.server_address[1]
        fetcher = SourceFetcher(guard=NetGuard(local_names, tls=trust),
                                nam=QNetworkAccessManager())
        out = wait_for(fetch(fetcher, f"https://pinned.example:{port}/feed"))
        assert "<title>Local</title>" in out.get("body", ""), out
        assert names == ["pinned.example"]                    # SNI: the name
        assert _Handler.seen == [("/feed", f"pinned.example:{port}")]
        # The certificate is checked for the name asked, not the address:
        # another name on the same address does not pass.
        out = wait_for(fetch(fetcher, f"https://other.example:{port}/feed"))
        assert out["error"].code == ERROR_CODES.FETCH_ERROR
        assert "body" not in out
    finally:
        server.shutdown()
        server.server_close()


def test_a_fetch_under_way_is_freed_without_the_collector():
    """A transfer is its network manager's child. Holding the manager (or
    the fetcher) in an attribute made a cycle only the cycle collector
    freed, and freeing Qt objects that way crashed a later test."""
    import gc
    import weakref
    nam = FakeNam([FakeReply(body=b"<rss/>")])
    fetcher = SourceFetcher(guard=NetGuard(resolver({})), nam=nam)
    fetch(fetcher, "https://blog.example/feed")       # never answered
    refs = [weakref.ref(fetcher), weakref.ref(nam)]
    gc.collect()
    automatic = gc.isenabled()     # as found: the app may run without it
    gc.disable()
    try:
        del fetcher, nam
        assert [ref() for ref in refs] == [None, None]
    finally:
        if automatic:
            gc.enable()


class TestItAlwaysEnds:
    """A fetch ends, whatever raises on the way: Qt's name lookup drops an
    exception raised in its callback without a word, and a transfer that
    never ended held one of the two check slots for good."""

    def test_when_making_the_request_raises(self, monkeypatch, caplog):
        def broken(*_args):
            raise TypeError("wrong argument types")
        monkeypatch.setattr(netguard, "pin", broken)
        nam = FakeNam([FakeReply(body=b"<rss/>")])
        fetcher = SourceFetcher(guard=NetGuard(resolver({"blog.example": [V4]})), nam=nam)
        with caplog.at_level(logging.ERROR, logger="nostr.imports.netguard"):
            out = fetch(fetcher, "https://blog.example/feed")
        assert out["error"].code == ERROR_CODES.FETCH_ERROR
        assert netguard.REQUEST_FAILED in str(out["error"])
        assert "ended with an error" in caplog.text

    def test_when_reading_the_answer_raises(self, monkeypatch):
        def broken(self, reply):
            raise ValueError("unexpected")
        monkeypatch.setattr(netguard._Transfer, "_answered", broken)
        nam = FakeNam([FakeReply(body=b"<rss/>")])
        fetcher = SourceFetcher(guard=NetGuard(resolver({"blog.example": [V4]})), nam=nam)
        out = fetch(fetcher, "https://blog.example/feed")
        nam.settle()
        assert "error" in out and "body" not in out
