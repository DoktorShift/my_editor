# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live check against ndak: EINUNDZWANZIG membership, end to end on this computer.

Not run by pytest (no ``test_`` prefix). Needs the sidecar's dependencies
(``pip install -r sidecar/requirements.txt``; the project venv has them):

    (cd ~/projects/ndak && docker compose up -d)
    .venv/bin/python tests/smoke_ndak_membership.py

Nothing of the association is contacted. A fake roster and price feed are
served by tests/smoke_ndak_common.py FakeWeb; a stand-in association (this
file) answers the membership API, insists on the client key and checks every
NIP-98 signature; the real sidecar (sidecar/app.py under uvicorn) sits in
between, holding a throwaway key. The members' relay is obelisk and the
members' Blossom server a stand-in (LOCAL_TOPOLOGY).

Covers the roster check and the benefits it grants (relay for notes, drafts,
the media server), the membership window driven through its buttons (apply,
pay, paid, member, address on the profile), the client calls the window does
not make (payments, export, erase), the sidecar's replay protection, and a
review of every kind 27235 event the app signed.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_ndak_common as nd  # noqa: E402

nd.bootstrap("membership")
app = nd.qt_app()

from nostr import einundzwanzig as e21dir  # noqa: E402
from nostr import nip98  # noqa: E402
from nostr.einundzwanzig_api import MembershipApi  # noqa: E402
from nostr.einundzwanzig_api.prices import PriceLookup  # noqa: E402
from nostr.membership_controller import MembershipController  # noqa: E402
from nostr.publisher import DraftPublishJob, PublishJob, build_note  # noqa: E402
from nostr.drafts import build_inner_event, new_note_identifier  # noqa: E402
from nostr.ui import membership_window as mw  # noqa: E402

RNOSTR, NRS, STRFRY, OBELISK = nd.RNOSTR, nd.NRS, nd.STRFRY, nd.OBELISK
ASSOC = f"http://127.0.0.1:{nd.ASSOCIATION_PORT}"
SIDECAR = f"http://127.0.0.1:{nd.SIDECAR_PORT}"
KEY = "ndak-test-key-not-real"
YEAR = time.gmtime().tm_year
SPEC_INVOICE = (
    "lnbc2500u1pvjluezpp5qqqsyqcyq5rqwzqfqqqsyqcyq5rqwzqfqqqsyqcyq5rqwzqfqypqdq5xysxxatsyp3k7"
    "enxv4jsxqzpuaztrnwngzn3kdzw5hydlzf03qdgm2hdq27cqv3agm2awhz5se903vruatfhq77w3ls4evs3ch9zw9"
    "7j25emudupq63nyw24cg27h2rspfj9srp")

rep = nd.Report("membership")
web = nd.FakeWeb().start()
web.put("/api/v1/prices", {"time": int(time.time()), "USD": 80000, "EUR": 76470, "CHF": 71340})

# --------------------------------------------------------------------------- #
# The stand-in association                                                     #
# --------------------------------------------------------------------------- #

STATE = {"status": "none", "handle": None, "refreshes": 0, "paid": False}
SEEN = []          # (method, path, key ok, nip98 verdict, event)


def membership_doc(pubkey):
    return {"pubkey": pubkey, "association_status": "PASSIVE", "association_status_value": 2,
            "membership_status": STATE["status"],
            "statutes_accepted_at": "2026-10-05T09:00:00+00:00" if STATE["status"] != "none" else None,
            "applied_at": "2026-10-05T09:00:00+00:00" if STATE["status"] != "none" else None,
            "current_year": {"year": YEAR, "fee": 21, "currency": "CHF", "paid": STATE["paid"],
                             "receipt_url": f"{nd.FAKE_WEB}/receipt" if STATE["paid"] else None}}


def invoice_doc(created=True):
    return {"checkout_url": None if STATE["paid"] else f"{nd.FAKE_WEB}/checkout",
            "bolt11": None if STATE["paid"] else SPEC_INVOICE, "created": created,
            "payment": {"year": YEAR, "amount": 21, "currency": "CHF", "paid": STATE["paid"],
                        "receipt_url": f"{nd.FAKE_WEB}/receipt" if STATE["paid"] else None}}


class Association(BaseHTTPRequestHandler):
    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        prefix = "/api/v1/membership"
        path = self.path[len(prefix):] if self.path.startswith(prefix) else self.path
        key_ok = self.headers.get("X-Api-Key") == KEY
        header = self.headers.get("Authorization")
        event, verdict = None, "unsigned"
        if header:
            try:
                event = nip98.check_auth_header(header, url=f"{ASSOC}{self.path}",
                                                method=self.command, body=body, now=time.time())
                verdict = "ok"
            except nip98.AuthRefused as exc:
                verdict = f"refused: {exc}"
                try:
                    event = json.loads(base64.b64decode(header[6:]))
                except Exception:  # noqa: BLE001
                    event = None
        SEEN.append({"method": self.command, "path": path, "key_ok": key_ok, "verdict": verdict,
                     "event": event, "body": body.decode() if body else ""})
        if not key_ok:
            return self._send(401, {"message": "bad key"})
        if path != "/config" and verdict != "ok":
            return self._send(401, {"message": "Unauthenticated."})
        pubkey = event["pubkey"] if event else None
        route = f"{self.command} {path}"
        if route == "GET /config":
            return self._send(200, {"data": {
                "fee": 21, "currency": "CHF", "year": YEAR,
                "statutes": {"url": f"{nd.FAKE_WEB}/statutes.pdf", "version": "1.3",
                             "adopted_at": "2024-04-20"},
                "application": {"required_fields": ["statutes_accepted"],
                                "optional_fields": ["application_text", "email", "no_email",
                                                    "nip05_handle"],
                                "application_text_max_length": 2000}}})
        if route == "GET /me":
            return self._send(200, {"data": membership_doc(pubkey)})
        if route == "POST /applications":
            payload = json.loads(body or b"{}")
            STATE["handle"] = payload.get("nip05_handle", STATE["handle"])
            if STATE["status"] == "none":
                STATE["status"] = "awaiting_payment"
            return self._send(201, {"data": membership_doc(pubkey)})
        if route == f"POST /payments/{YEAR}/invoice":
            return self._send(200, {"data": invoice_doc()})
        if route == f"POST /payments/{YEAR}/refresh":
            STATE["refreshes"] += 1
            if STATE["refreshes"] >= 2:
                STATE["paid"], STATE["status"] = True, "member"
            return self._send(200, {"data": invoice_doc(created=False)})
        if route == "GET /payments":
            return self._send(200, {"data": [invoice_doc()["payment"]]})
        if route == "GET /export":
            return self._send(200, {"data": {"subject": {"pubkey": pubkey, "npub": None},
                                             "membership_status": STATE["status"], "member": None,
                                             "payments": [], "membership_grants": [],
                                             "nostr_profile": None}})
        if route == "DELETE /me":
            return self._send(200, {"data": {"erased": True, "retained_payments": 1}})
        return self._send(404, {"message": "not found"})

    do_GET = do_POST = do_DELETE = _answer

    def _send(self, status, document):
        data = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


association = ThreadingHTTPServer(("127.0.0.1", nd.ASSOCIATION_PORT), Association)
threading.Thread(target=association.serve_forever, daemon=True).start()
sidecar = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "sidecar.app:app", "--host", "127.0.0.1",
     "--port", str(nd.SIDECAR_PORT), "--log-level", "warning", "--no-access-log"],
    cwd=str(nd.ROOT), env=dict(os.environ, E21_API_KEY=KEY, E21_UPSTREAM=ASSOC,
                               SIDECAR_LOG_LEVEL="WARNING", SIDECAR_RATE_PER_MINUTE="10000",
                               SIDECAR_INVOICES_PER_DAY="1000"))
os.environ["MYEDITOR_MEMBERSHIP_SERVICE"] = SIDECAR
os.environ["MYEDITOR_MEMBERSHIP_UPSTREAM"] = ASSOC

try:
    nd._wait_http(f"{SIDECAR}/healthz", 30)
    stack = nd.Stack()
    alice, sk, setup = nd.local_account(stack, "Alice Member")
    pk = alice.user_pubkey
    rep.check("Alice's account is set up", setup.get("ok") is True)

    # ----------------------------------------------------------------------- #
    rep.section("Roster check: a member gets the members' relay and media server")
    web.put(f"/api/members/{YEAR}", [{"id": 1, "npub": "npub1x", "pubkey": pk.upper(),
                                      "nip05_handle": "alice"},
                                     {"id": 2, "pubkey": "not-a-key"}])
    links, statuses = [], []
    controller = MembershipController(
        profile_provider=lambda: stack.store.default(), relay_pool=stack.pool,
        session_pool=stack.sessions, relay_directory=stack.directory, is_dark=lambda: False,
        open_link=links.append, show_status=lambda text, ms: statuses.append(text),
        prices_factory=lambda: PriceLookup())
    changed = nd.Catch(controller.benefits_changed)
    controller.start()
    changed.wait(15_000)
    rep.check("the roster is fetched from the (local) roster URL",
              any(f"/api/members/{YEAR}" in line for line in web.requests()), str(web.requests()))
    rep.check("Alice (in the roster, pubkey in capitals) is recognized as a member",
              controller.benefits().is_member, str(controller.benefits()))
    rep.check("entitled relay and media server are the members' ones",
              controller.entitled_relays() == [OBELISK]
              and controller.entitled_blossom_servers() == [nd.BLOSSOM_M],
              f"{controller.entitled_relays()} {controller.entitled_blossom_servers()}")
    rep.check("the members' server has the 5 GiB allowance and its label",
              controller.quota(nd.BLOSSOM_M) == 5 * 1024 ** 3
              and controller.server_label(nd.BLOSSOM_M) == "EINUNDZWANZIG",
              f"{controller.quota(nd.BLOSSOM_M)} {controller.server_label(nd.BLOSSOM_M)}")
    rep.check("the person is told once", statuses.count(statuses[0]) == 1 if statuses else False,
              str(statuses))

    box = {}
    job = PublishJob(relay_pool=stack.pool, relay_directory=stack.directory,
                     session_pool=stack.sessions, profile=alice,
                     entitled_relays=controller.entitled_relays(),
                     unsigned_event=build_note("A member's note", pk))
    job.signed.connect(lambda i: box.update(id=i))
    job.completed.connect(lambda r: box.update(r=r))
    job.start()
    nd.wait_until(lambda: "r" in box, 20_000)
    where = {r for r, ok in nd.presence(box.get("id", "")).items() if ok}
    rep.check("a member's note also goes to the members' relay", where == {RNOSTR, NRS, OBELISK},
              ", ".join(sorted(nd.RELAY_NAMES[r] for r in where)))
    res = {}
    draft = DraftPublishJob(relay_pool=stack.pool, relay_directory=stack.directory,
                            session_pool=stack.sessions, profile=alice,
                            inner_event=build_inner_event(kind=1, content="member draft", pubkey_hex=pk),
                            identifier=new_note_identifier(),
                            entitled_relays=controller.entitled_relays())
    draft.stashed.connect(lambda d, eid, ts: res.update(eid=eid))
    draft.completed.connect(lambda r: res.update(r=r))
    draft.start()
    nd.wait_until(lambda: "r" in res, 20_000)
    where = {r for r, ok in nd.presence(res.get("eid", "")).items() if ok}
    rep.check("a member's private draft also goes to the members' relay",
              where == {RNOSTR, NRS, OBELISK}, ", ".join(sorted(nd.RELAY_NAMES[r] for r in where)))

    # A broken answer from the roster host (maintenance page, changed format).
    web.put(f"/api/members/{YEAR}", "<html><body>Maintenance</body></html>", "text/html")
    web.put(f"/api/members/{YEAR - 1}", "<html><body>Maintenance</body></html>", "text/html")
    before = controller.benefits().is_member
    lost = nd.Catch(controller.benefits_changed)
    controller.refresh(force=True)
    lost.wait(8_000)
    nd.spin(500)
    rep.check("a broken roster answer (HTML with status 200) keeps the roster already held",
              before and controller.benefits().is_member,
              "every member lost relay and media server: einundzwanzig.py parses an "
              "unreadable body as an empty roster" if not controller.benefits().is_member else "kept")
    web.put(f"/api/members/{YEAR}", [{"id": 1, "pubkey": pk, "nip05_handle": "alice"}])
    controller.refresh(force=True)
    nd.wait_until(lambda: controller.benefits().is_member, 8_000)

    _, _, bob = nd.new_identity()
    bob_dir = e21dir.MembershipDirectory()
    answer = nd.Catch(bob_dir.resolved)
    bob_dir.resolve(bob, force=True)
    answer.wait(8_000)
    rep.check("someone not in the roster is not a member", answer.fired and answer.last == (bob, False),
              str(answer.last))

    # ----------------------------------------------------------------------- #
    rep.section("Joining in the window, through the sidecar to the stand-in association")
    controller2_profile = {"p": alice}
    # A fresh controller for an account that is not in the roster yet.
    web.put(f"/api/members/{YEAR}", [])
    web.put(f"/api/members/{YEAR - 1}", [])
    joiner = MembershipController(
        profile_provider=lambda: stack.store.default(), relay_pool=stack.pool,
        session_pool=stack.sessions, relay_directory=stack.directory, is_dark=lambda: False,
        open_link=links.append, show_status=lambda text, ms: statuses.append(text),
        prices_factory=lambda: PriceLookup())
    joiner.refresh(force=True)
    nd.spin(1500)
    rep.check("with an empty roster Alice is not a member yet", not joiner.benefits().is_member)
    published = nd.Catch(joiner.profile_published)
    joined = nd.Catch(joiner.benefits_changed)
    nd.ALERT_POLICY = lambda alert: True if alert["title"].startswith("Show Your Nostr Address") \
        else nd.default_alert_policy(alert)
    joiner.open_window()
    win = joiner.window
    rep.check("the window opens on the overview", win is not None and win.page == "overview",
              win.page if win else "")
    nd.wait_until(lambda: "continue" in win.buttons and win.buttons["continue"].isEnabled(), 15_000)
    rep.check("the sidecar says joining is available (Continue enabled)",
              win.buttons["continue"].isEnabled())
    nd.wait_until(lambda: "sats" in json.dumps([l.text() for l in win.findChildren(mw.QLabel)]), 8_000)
    fee_texts = [l.text() for l in win.findChildren(mw.QLabel) if "CHF" in l.text() or "sats" in l.text()]
    rep.info("fee as shown", " | ".join(fee_texts)[:300])
    win.buttons["continue"].click()
    nd.wait_until(lambda: win.page == "apply", 15_000)
    rep.check("Continue asks the association where the membership stands, then shows Apply",
              win.page == "apply", win.page)
    win._consent.setChecked(True)
    win._handle_edit.setText("alice")
    nd.wait_until(lambda: win.buttons["send"].isEnabled(), 3_000)
    win.buttons["send"].click()
    nd.wait_until(lambda: win.page == "pay", 20_000)
    rep.check("sending the application leads to the payment page with an invoice",
              win.page == "pay" and win._qr.isVisible() if hasattr(win, "_qr") else win.page == "pay",
              win.page)
    win.buttons["paid"].click()
    nd.wait_until(lambda: win.page == "member", 40_000)
    rep.check("I've Paid polls until the payment arrives, then shows the member page",
              win.page == "member", f"{win.page}; refreshes {STATE['refreshes']}")
    nd.wait_until(lambda: published.fired, 30_000)
    rep.check("the member is asked once to show the address, and Show on Profile publishes it",
              any(a["title"].startswith("Show Your Nostr Address") for a in nd.ALERTS_SEEN)
              and published.fired, str([a["title"] for a in nd.ALERTS_SEEN]))
    nd.ALERT_POLICY = nd.default_alert_policy
    nd.spin(500)
    prof = nd.nak_req([RNOSTR, NRS, STRFRY], kinds=[0], authors=[pk])
    prof = max(prof, key=lambda e: e["created_at"]) if prof else None
    if rep.check("the profile now shows alice@einundzwanzig.space and keeps its name",
                 prof is not None and json.loads(prof["content"]).get("nip05") == "alice@einundzwanzig.space"
                 and json.loads(prof["content"]).get("name") == "Alice Member",
                 prof["content"] if prof else ""):
        nd.review_event(rep, "profile with the members' address", prof, kind=0)
        rep.event("profile, address set by the membership window", prof, present=nd.presence(prof["id"]))
    rep.check("being confirmed as a member in the window grants the benefits at once",
              joiner.benefits().is_member and joined.fired, str(joiner.benefits()))
    win.buttons["done"].click()
    rep.check("no link was opened in a browser", not nd.OPENED_LINKS and not links,
              str(nd.OPENED_LINKS + links))

    # ----------------------------------------------------------------------- #
    rep.section("Client calls the window does not make: payments, export, erase")
    api = MembershipApi(lambda u, ok, fail: stack.signer_for(alice).sign_event(u, ok, fail))
    rep.check("the client names the local sidecar and signs for the stand-in's URL",
              api.request_url_for("/me").startswith(SIDECAR) and api.url_for("/me").startswith(ASSOC),
              f"{api.url_for('/me')} / {api.request_url_for('/me')}")
    for name, call in (("payments", api.payments), ("export", api.export_data), ("erase", api.erase)):
        box = {}
        call(lambda value: box.update(ok=value), lambda error: box.update(err=error))
        nd.wait_until(lambda: box, 15_000)
        rep.check(f"{name} answers through the sidecar", "ok" in box, str(box.get("err")))

    # ----------------------------------------------------------------------- #
    rep.section("The NIP-98 signatures the app sent (kind 27235)")
    signed = [s for s in SEEN if s["event"] is not None]
    rep.check("every signed request reached the association with the client key and a "
              "valid NIP-98 signature",
              signed and all(s["key_ok"] and s["verdict"] == "ok" for s in signed),
              str([(s["method"], s["path"], s["verdict"]) for s in signed if s["verdict"] != "ok"]))
    rep.check("the config request is unsigned, the others signed",
              all(s["event"] is None for s in SEEN if s["path"] == "/config")
              and all(s["event"] is not None for s in SEEN if s["path"] != "/config"),
              str([(s["method"], s["path"], s["event"] is not None) for s in SEEN]))
    for s in signed[:1] + [x for x in signed if x["body"]][:1]:
        ev = s["event"]
        nd.review_event(rep, f"NIP-98 {s['method']} {s['path']}", ev, kind=27235, max_age_s=600)
        names = [t[0] for t in ev["tags"]]
        rep.check(f"NIP-98 {s['method']} {s['path']}: u is the exact URL, method, empty content"
                  + (", payload is the body's sha256" if s["body"] else ", no payload tag"),
                  nd.tag_values(ev, "u") == [f"{ASSOC}/api/v1/membership{s['path']}"]
                  and nd.tag_values(ev, "method") == [s["method"]] and ev["content"] == ""
                  and ((nd.tag_values(ev, "payload") == [nip98.sha256_hex(s["body"].encode())])
                       if s["body"] else "payload" not in names), json.dumps(ev["tags"]))
        rep.event(f"NIP-98 auth {s['method']} {s['path']}", ev)
    replay = next((s for s in signed if s["method"] == "GET"), None)
    if replay:
        import urllib.error
        import urllib.request
        header = "Nostr " + base64.b64encode(json.dumps(replay["event"], separators=(",", ":"))
                                             .encode()).decode()
        request = urllib.request.Request(f"{SIDECAR}/api/v1/membership{replay['path']}",
                                         headers={"Authorization": header})
        try:
            with urllib.request.urlopen(request, timeout=5) as answer:
                code = answer.status
        except urllib.error.HTTPError as exc:
            code = exc.code
        rep.check("the sidecar refuses a signature it has already seen (replay)", code == 401,
                  f"HTTP {code}")
    stack.close()
finally:
    sidecar.terminate()
    try:
        sidecar.wait(10)
    except subprocess.TimeoutExpired:
        sidecar.kill()
    association.shutdown()
    web.stop()

sys.exit(rep.finish())
