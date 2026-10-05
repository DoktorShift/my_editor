# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins the membership sidecar: it lends the association's key only to
requests the association would accept, and never gives the key away.

What must hold:

  Without a key it says so (/status, and "not_configured" on every
  membership call), so MyEditor offers the website instead of an error.

  Only the association's membership endpoints are forwarded. Every call
  but the fee lookup needs a NIP-98 signature naming the association's own
  URL for exactly this request (method, body hash, under a minute old,
  validly signed, used once). Anything else is refused before the key is
  spent.

  What is forwarded carries the key and the user's signature unchanged;
  the answer comes back unchanged, Retry-After included, except that the
  key is removed should the association ever echo it.

  The key's shared quota is protected: requests per address per minute,
  invoices per account per day.

  The key never appears in an answer or in the log.
"""

import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SIDECAR_NO_AUTOSTART"] = "1"

httpx = pytest.importorskip("httpx")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from nostr import crypto, events, nip98  # noqa: E402
from sidecar import app as sidecar  # noqa: E402

KEY = "association-client-key-0123"
UPSTREAM = "https://verein.einundzwanzig.space"
SK = bytes.fromhex("4d" * 32)
PK = crypto.get_public_key(SK).hex()
NOW = 1_800_000_000


class Clock:
    def __init__(self):
        self.now = float(NOW)

    def __call__(self):
        return self.now


class Association:
    """A fake association behind the sidecar, recording what reached it."""

    def __init__(self):
        self.requests = []
        self.answer = lambda request: httpx.Response(
            200, json={"data": {"ok": True}}, headers={"Content-Type": "application/json"})

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)


def make(*, key=KEY, per_minute=60, invoices=10):
    association = Association()
    clock = Clock()
    settings = sidecar.Settings(api_key=key, upstream=UPSTREAM,
                                rate_per_minute=per_minute, invoices_per_day=invoices)
    app = sidecar.create_app(settings, transport=httpx.MockTransport(association.handler),
                             clock=clock)
    return TestClient(app), association, clock


def auth(method, path, body=b"", *, url=None, created_at=NOW, sk=SK, tamper=None):
    unsigned = nip98.build_unsigned_auth_event(
        url or f"{UPSTREAM}/api/v1/membership{path}", method, body or None,
        now=created_at)
    event = events.sign_event(unsigned, sk)
    if tamper:
        event = tamper(event)
    return nip98.authorization_header_value(event).decode("ascii")


def call(client, method, path, body=b"", **auth_kw):
    headers = {"Authorization": auth(method, path, body, **auth_kw)}
    if body:
        headers["Content-Type"] = "application/json"
    return client.request(method, f"/api/v1/membership{path}", content=body or None,
                          headers=headers)


# -- availability ------------------------------------------------------------------

def test_status_says_whether_joining_is_possible():
    client, *_ = make()
    assert client.get("/status").json() == {"service": "myeditor-sidecar", "version": "1",
                                            "membership": True}
    client, *_ = make(key="")
    assert client.get("/status").json()["membership"] is False
    assert client.get("/healthz").json() == {"ok": True}


def test_without_a_key_every_call_says_not_configured():
    client, association, _ = make(key="")
    response = call(client, "GET", "/me")
    assert response.status_code == 503 and response.json()["code"] == "not_configured"
    assert association.requests == []


# -- what is forwarded ----------------------------------------------------------------

def test_the_fee_lookup_needs_no_signature_and_is_cached():
    client, association, _ = make()
    first = client.get("/api/v1/membership/config")
    second = client.get("/api/v1/membership/config")
    assert first.status_code == second.status_code == 200
    assert len(association.requests) == 1
    forwarded = association.requests[0]
    assert str(forwarded.url) == f"{UPSTREAM}/api/v1/membership/config"
    assert forwarded.headers["X-Api-Key"] == KEY
    assert "authorization" not in forwarded.headers


def test_a_signed_call_is_forwarded_with_the_key_and_the_signature_unchanged():
    client, association, _ = make()
    body = json.dumps({"statutes_accepted": True}).encode()
    response = call(client, "POST", "/applications", body)
    assert response.status_code == 200
    forwarded = association.requests[0]
    assert str(forwarded.url) == f"{UPSTREAM}/api/v1/membership/applications"
    assert forwarded.headers["X-Api-Key"] == KEY
    assert forwarded.headers["Authorization"].startswith("Nostr ")
    assert forwarded.content == body
    assert forwarded.headers["Content-Type"] == "application/json"


def test_the_answer_comes_back_unchanged_with_its_wait():
    client, association, _ = make()
    association.answer = lambda r: httpx.Response(
        429, json={"message": "Too Many Attempts."}, headers={"Retry-After": "42"})
    response = call(client, "GET", "/me")
    assert response.status_code == 429 and response.headers["Retry-After"] == "42"
    assert response.json() == {"message": "Too Many Attempts."}


@pytest.mark.parametrize("method, path", [
    ("GET", "/me"), ("DELETE", "/me"), ("GET", "/payments"), ("GET", "/export"),
    ("POST", "/payments/2026/invoice"), ("POST", "/payments/2026/refresh"),
])
def test_every_membership_call_is_forwarded(method, path):
    client, association, _ = make()
    assert call(client, method, path).status_code == 200
    assert str(association.requests[0].url) == f"{UPSTREAM}/api/v1/membership{path}"


@pytest.mark.parametrize("method, path", [
    ("GET", "/admin"), ("POST", "/me"), ("GET", "/payments/26/invoice"),
    ("PUT", "/applications"), ("GET", "/../secret"),
])
def test_anything_else_is_not_forwarded(method, path):
    client, association, _ = make()
    response = client.request(method, f"/api/v1/membership{path}")
    assert response.status_code in (404, 405)
    assert association.requests == []


# -- what is refused before the key is spent ------------------------------------------

def tampered_sig(event):
    return dict(event, sig=("0" if event["sig"][0] != "0" else "1") + event["sig"][1:])


@pytest.mark.parametrize("kw", [
    {"url": "https://sidecar.example/api/v1/membership/me"},   # names the sidecar
    {"url": f"{UPSTREAM}/api/v1/membership/payments"},          # another endpoint
    {"created_at": NOW - 120},                                  # too old
    {"created_at": NOW + 120},                                  # from the future
    {"tamper": tampered_sig},                                   # forged
])
def test_a_signature_for_anything_but_this_request_is_refused(kw):
    client, association, _ = make()
    response = call(client, "GET", "/me", **kw)
    assert response.status_code == 401 and association.requests == []


def test_a_missing_or_garbled_signature_is_refused():
    client, association, _ = make()
    assert client.get("/api/v1/membership/me").status_code == 401
    garbled = {"Authorization": "Nostr " + base64.b64encode(b"{not json").decode()}
    assert client.get("/api/v1/membership/me", headers=garbled).status_code == 401
    assert association.requests == []


def raw_credential(event) -> str:
    return "Nostr " + base64.b64encode(json.dumps(event).encode()).decode()


def signed_event(method="GET", path="/me", body=None):
    unsigned = nip98.build_unsigned_auth_event(
        f"{UPSTREAM}/api/v1/membership{path}", method, body, now=NOW)
    return events.sign_event(unsigned, SK)


@pytest.mark.parametrize("change", [
    {"tags": None}, {"tags": 5}, {"tags": "u"}, {"tags": [None]}, {"tags": [5]},
    {"tags": [[]]}, {"tags": [["u", 5]]}, {"tags": [["u", None]]},
    {"kind": "27235"}, {"kind": None}, {"created_at": None}, {"created_at": True},
    {"created_at": "1800000000"}, {"content": None}, {"id": 5}, {"pubkey": None},
    {"sig": ["x"]},
])
def test_a_malformed_credential_is_refused_not_a_crash(change):
    client, association, _ = make()
    event = dict(signed_event(), **change)
    response = client.get("/api/v1/membership/me",
                          headers={"Authorization": raw_credential(event)})
    assert response.status_code == 401 and association.requests == []


@pytest.mark.parametrize("document", [None, 5, "event", [], [1, 2]])
def test_a_credential_that_is_not_an_event_is_refused(document):
    client, association, _ = make()
    response = client.get("/api/v1/membership/me",
                          headers={"Authorization": raw_credential(document)})
    assert response.status_code == 401 and association.requests == []


def test_a_tag_named_twice_names_nothing():
    client, association, _ = make()
    unsigned = nip98.build_unsigned_auth_event(
        f"{UPSTREAM}/api/v1/membership/me", "GET", None, now=NOW)
    unsigned["tags"].append(["u", f"{UPSTREAM}/api/v1/membership/me"])
    headers = {"Authorization": raw_credential(events.sign_event(unsigned, SK))}
    assert client.get("/api/v1/membership/me", headers=headers).status_code == 401
    assert association.requests == []


def test_a_payload_tag_without_a_body_is_refused():
    client, association, _ = make()
    event = signed_event("POST", "/payments/2026/refresh", b'{"a":1}')
    response = client.post("/api/v1/membership/payments/2026/refresh",
                           headers={"Authorization": raw_credential(event)})
    assert response.status_code == 401 and association.requests == []


def test_the_method_must_match():
    client, association, _ = make()
    headers = {"Authorization": auth("GET", "/me")}
    assert client.delete("/api/v1/membership/me", headers=headers).status_code == 401
    assert association.requests == []


def test_the_body_must_be_the_one_that_was_signed():
    client, association, _ = make()
    headers = {"Authorization": auth("POST", "/applications", b'{"a":1}'),
               "Content-Type": "application/json"}
    response = client.post("/api/v1/membership/applications", content=b'{"a":2}',
                           headers=headers)
    assert response.status_code == 401 and association.requests == []


def test_a_signature_is_used_once():
    client, association, _ = make()
    headers = {"Authorization": auth("GET", "/me")}
    assert client.get("/api/v1/membership/me", headers=headers).status_code == 200
    assert client.get("/api/v1/membership/me", headers=headers).status_code == 401
    assert len(association.requests) == 1


def test_a_body_must_be_json_and_small():
    client, association, _ = make()
    body = b'{"statutes_accepted": true}'
    headers = {"Authorization": auth("POST", "/applications", body),
               "Content-Type": "text/plain"}
    assert client.post("/api/v1/membership/applications", content=body,
                       headers=headers).status_code == 415
    big = b'{"x":"' + b"a" * (40 * 1024) + b'"}'
    assert call(client, "POST", "/applications", big).status_code == 413
    assert association.requests == []


# -- the shared quota ------------------------------------------------------------------

def test_requests_per_address_are_limited():
    client, association, clock = make(per_minute=3)
    codes = [call(client, "GET", "/me").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    clock.now += 61
    assert call(client, "GET", "/me", created_at=int(clock.now)).status_code == 200


def test_invoices_per_account_are_limited_per_day():
    client, association, clock = make(invoices=2)
    codes = [call(client, "POST", "/payments/2026/invoice").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    clock.now += 86400
    assert call(client, "POST", "/payments/2026/invoice",
                created_at=int(clock.now)).status_code == 200


# -- the key stays here ----------------------------------------------------------------

def test_the_key_never_leaves_in_an_answer(caplog):
    client, association, _ = make()
    association.answer = lambda r: httpx.Response(
        401, json={"message": f"Unknown client key {KEY}"})
    caplog.set_level("INFO", logger="myeditor-sidecar")
    response = call(client, "GET", "/me")
    assert KEY not in response.text and "[redacted]" in response.text
    for path in ("/me", "/payments"):
        call(client, "GET", path, tamper=tampered_sig)
    assert KEY not in caplog.text


def test_an_unreachable_association_is_a_clear_502():
    def down(request):
        raise httpx.ConnectError("down", request=request)
    client, association, _ = make()
    association.answer = down
    response = call(client, "GET", "/me")
    assert response.status_code == 502 and "not reachable" in response.json()["message"]


def test_a_redirect_from_upstream_is_not_followed():
    client, association, _ = make()
    association.answer = lambda r: httpx.Response(302, headers={"Location": "https://evil.example"})
    response = call(client, "GET", "/me")
    assert response.status_code == 502 and len(association.requests) == 1
