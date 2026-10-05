# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Manual end-to-end check: the app's membership client through the real
sidecar to a stand-in association.

Not run by pytest (no ``test_`` prefix). Needs the sidecar's dependencies
(``pip install -r sidecar/requirements.txt``). Run from the repo root:

    .venv/bin/python tests/smoke_sidecar_e2e.py

It starts a tiny stand-in association that insists on the client key, the
sidecar (uvicorn) pointing at it with that key, and the app's own
MembershipApi signing with a throwaway local key. It proves the two halves
agree: the sidecar accepts the app's signature (which names the
association's URL), adds the key, and the answer comes back parsed.
"""

import json, os, socket, subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

seen = []
class Association(BaseHTTPRequestHandler):
    def do_GET(self):
        seen.append((self.command, self.path, self.headers.get("X-Api-Key"), bool(self.headers.get("Authorization"))))
        if self.headers.get("X-Api-Key") != "e2e-key":
            body = json.dumps({"message": "bad key"}).encode(); self.send_response(401)
        else:
            body = json.dumps({"data": {"pubkey": "ab"*32, "association_status": "DEFAULT",
                "association_status_value": 1, "membership_status": "none", "statutes_accepted_at": None,
                "applied_at": None, "current_year": {"year": 2026, "fee": 21, "currency": "CHF",
                "paid": False, "receipt_url": None}}}).encode(); self.send_response(200)
        self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

a_port, s_port = free_port(), free_port()
srv = ThreadingHTTPServer(("127.0.0.1", a_port), Association)
threading.Thread(target=srv.serve_forever, daemon=True).start()
env = dict(os.environ, E21_API_KEY="e2e-key", E21_UPSTREAM=f"http://127.0.0.1:{a_port}", SIDECAR_LOG_LEVEL="WARNING")
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "sidecar.app:app", "--port", str(s_port), "--log-level", "warning"], env=env)
time.sleep(2.5)
try:
    from PySide6.QtCore import QCoreApplication, QTimer
    app = QCoreApplication(sys.argv)
    from nostr import crypto
    from nostr.local_signer import LocalSigner
    from nostr.einundzwanzig_api import MembershipApi
    signer = LocalSigner(crypto.generate_secret_key())
    sign = lambda unsigned, ok, fail: signer.sign_event(unsigned, ok, fail)
    api = MembershipApi(sign, service_url=f"http://localhost:{s_port}", base_url=f"http://127.0.0.1:{a_port}")
    result = {}
    api.check_service(lambda ok: result.setdefault("status", ok))
    api.me(lambda s: (result.setdefault("me", s.membership_status), app.quit()),
           lambda e: (result.setdefault("me", f"FAILED {e.code} {e.status}"), app.quit()))
    QTimer.singleShot(15000, app.quit); app.exec()
    print("status available:", result.get("status"))
    print("me:", result.get("me"))
    print("association saw:", seen)
finally:
    proc.terminate(); srv.shutdown()
