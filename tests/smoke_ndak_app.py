# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live check against ndak: the real main window with a signed-in account.

Not run by pytest (no ``test_`` prefix). Run with the ndak relays up:

    (cd ~/projects/ndak && docker compose up -d)
    .venv/bin/python tests/smoke_ndak_app.py

The other smoke_ndak scripts build the Nostr stack the way main_window.py
does; this one builds MainWindow itself (offscreen, in a temporary HOME,
with its own single-instance socket name and the update check skipped), as
a launch with an account from an earlier session, and checks that startup
reads the account's profile, avatar, contacts, media server list, drafts and
membership from the local relays and services only. Then the draft flow of
the window itself: opening a draft into a tab, an edit from another device
refreshing it, a conflicting edit raising the banner, Ctrl+S saving the tab.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_ndak_common as nd  # noqa: E402

# A name of its own for the single-instance socket, so a real MyEditor
# running on this computer keeps its own.
nd.own_instance_socket(f"ndak-ipc-{os.getpid()}")
nd.bootstrap("app")
app = nd.qt_app()

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402

from nostr.blossom.server_list import build_server_list_event  # noqa: E402
from nostr.draft_store import DraftState  # noqa: E402
from nostr.drafts import build_inner_event, new_note_identifier  # noqa: E402
from nostr.publisher import DraftPublishJob  # noqa: E402
from nostr.ui.account_windows import local_profile  # noqa: E402

RNOSTR, NRS, STRFRY, OBELISK, KHATRU = nd.RNOSTR, nd.NRS, nd.STRFRY, nd.OBELISK, nd.KHATRU
rep = nd.Report("app")
web = nd.FakeWeb().start()


def png(color: str) -> bytes:
    image = QImage(32, 32, QImage.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def save_draft(stack, profile, inner, identifier):
    res = {}
    job = DraftPublishJob(relay_pool=stack.pool, relay_directory=stack.directory,
                          session_pool=stack.sessions, profile=profile, inner_event=inner,
                          identifier=identifier)
    job.stashed.connect(lambda d, eid, ts: res.update(eid=eid, ts=ts))
    job.completed.connect(lambda r: res.update(r=r))
    job.start()
    nd.wait_until(lambda: "r" in res, 30_000)
    return res


# --------------------------------------------------------------------------- #
rep.section("An account from an earlier session")
(nd.HOME / ".config" / "my_editor").mkdir(parents=True, exist_ok=True)
(nd.HOME / ".config" / "my_editor" / "settings.json").write_text(
    json.dumps({"last_update_check": time.time(), "welcome_shown": True}), encoding="utf-8")
web.put("/avatar.png", png("#4466aa"))
first = nd.Stack()
alice, sk, setup = nd.local_account(first, "Alice App")
pk = alice.user_pubkey
rep.check("the account was created and set up earlier", setup.get("ok") is True)
time.sleep(1.1)     # a later second than the setup's profile, so this one replaces it
nd.nak_publish(sk.hex(), [RNOSTR, NRS, STRFRY], kind=0, content=json.dumps(
    {"name": "Alice App", "display_name": "Alice from kind 0", "picture": nd.FAKE_WEB + "/avatar.png",
     "nip05": "alice@ndak.test"}))
_, bob_hex, bob = nd.new_identity()
nd.nak_publish(bob_hex, [STRFRY], kind=0, content=json.dumps({"name": "Bob Contact"}))
nd.nak_publish(sk.hex(), [RNOSTR, NRS, STRFRY], kind=3, tags=[["p", bob, "", "bob"]])
first.publish_raw([RNOSTR, NRS, STRFRY], first.sign(alice, build_server_list_event(
    [nd.BLOSSOM_B], pk)))
web.put(f"/api/members/{time.gmtime().tm_year}", [{"pubkey": pk, "nip05_handle": "alice"}])
draft_id = new_note_identifier()
res = save_draft(first, alice, build_inner_event(kind=1, content="Draft from yesterday", pubkey_hex=pk),
                 draft_id)
rep.check("a draft saved in that session is on the relays", "eid" in res)
first.close()

# --------------------------------------------------------------------------- #
rep.section("Launching the window")
import main_window  # noqa: E402

window = main_window.MainWindow(None)
rep.check("the window starts with the account active",
          window._profile_store.default() is not None
          and window._profile_store.default().user_pubkey == pk)
nd.wait_until(lambda: window._profile_store.default().display_name == "Alice from kind 0", 20_000)
active = window._profile_store.default()
rep.check("startup reads the profile (kind 0): name, picture, Nostr address",
          active.display_name == "Alice from kind 0" and active.picture.endswith("/avatar.png")
          and active.nip05 == "alice@ndak.test", f"{active.display_name!r} {active.nip05!r}")
nd.wait_until(lambda: window._avatars.get(pk) is not None, 15_000)
rep.check("the avatar is downloaded for the profile chip", window._avatars.get(pk) is not None)
nd.wait_until(lambda: any(p.pubkey == bob and p.display_name == "Bob Contact"
                          for p in window._known_people.all()), 25_000)
rep.check("the contact list (kind 3) seeds the mention picker",
          any(p.pubkey == bob and p.display_name == "Bob Contact" for p in window._known_people.all()),
          str([p.display_name for p in window._known_people.all()]))
nd.wait_until(lambda: window._draft_store.get(draft_id) is not None
              and window._draft_store.get(draft_id).state is DraftState.READY, 30_000)
record = window._draft_store.get(draft_id)
rep.check("the draft from the earlier session is synced and decrypted at startup",
          record is not None and record.content == "Draft from yesterday",
          str(record.state if record else None))
nd.wait_until(lambda: window._membership.benefits().is_member, 15_000)
rep.check("the roster recognizes the membership at startup", window._membership.benefits().is_member)
servers = window._media_store.settings.configured_servers
servers = servers() if callable(servers) else servers
rep.check("the account's media server list (kind 10063) is adopted, nothing being configured",
          list(servers) == [nd.BLOSSOM_B], str(servers))

# --------------------------------------------------------------------------- #
rep.section("A draft in a tab: refresh, conflict, Ctrl+S")
device = nd.Stack("phone")
device.vault.store(sk)
alice_phone = local_profile(pk)
device.store.upsert(alice_phone)

window._on_panel_open_draft(draft_id)
ed = window.current_editor()
binding = getattr(ed, "_draft_binding", None)
rep.check("opening the draft makes a tab bound to it",
          binding is not None and binding.identifier == draft_id
          and ed.toPlainText() == "Draft from yesterday")
time.sleep(1.1)
res = save_draft(device, alice_phone, build_inner_event(kind=1, content="Edited on the phone",
                                                        pubkey_hex=pk), draft_id)
nd.wait_until(lambda: ed.toPlainText() == "Edited on the phone", 20_000)
rep.check("an edit from another device refreshes the unmodified tab silently",
          ed.toPlainText() == "Edited on the phone" and binding.event_id == res.get("eid"),
          repr(ed.toPlainText()))
ed.insertPlainText("Local words. ")
rep.check("typing marks the tab modified", ed.document().isModified())
time.sleep(1.1)
res2 = save_draft(device, alice_phone, build_inner_event(kind=1, content="Edited on the phone again",
                                                         pubkey_hex=pk), draft_id)
nd.wait_until(lambda: ed in window._tab_conflict_banners, 20_000)
rep.check("a newer version from elsewhere while the tab has edits raises the conflict banner",
          ed in window._tab_conflict_banners and "Local words." in ed.toPlainText())
tabs_before = window.tabs.count()
banner = window._tab_conflict_banners.get(ed)
if banner is not None:
    banner.view_remote.emit()
    nd.spin(300)
    rep.check("View on the banner opens the other version in a new tab, the tab with edits untouched",
              window.tabs.count() == tabs_before + 1 and "Local words." in ed.toPlainText(),
              f"tabs {tabs_before} -> {window.tabs.count()} (main_window.py _on_conflict_view calls "
              "_on_panel_open_draft, which finds the tab already bound to the draft and only "
              "activates it)")
    window.tabs.setCurrentIndex(window.tabs.indexOf(ed.parent()) if window.tabs.indexOf(ed.parent()) >= 0
                                else window.tabs.currentIndex())
    banner.closed.emit()
    nd.spin(200)
for i in range(window.tabs.count()):
    if window._editor_from_widget(window.tabs.widget(i)) is ed:
        window.tabs.setCurrentIndex(i)
time.sleep(1.1)
text = ed.toPlainText()
window.save()
nd.wait_until(lambda: getattr(ed, "_active_stash_job", None) is None and not ed.document().isModified(),
              30_000)
nd.spin(1500)
wraps = nd.nak_req([RNOSTR], kinds=[31234], authors=[pk], tags={"d": [draft_id]})
latest = max(wraps, key=lambda w: w["created_at"]) if wraps else None
opened = None
if latest:
    out = nd.nak("decrypt", "--sec", sk.hex(), "-p", pk, latest["content"])
    opened = json.loads(out.stdout) if out.returncode == 0 else None
rep.check("Ctrl+S on the tab saves it as the newest version of the same draft (Keep Mine)",
          opened is not None and opened["content"] == text.strip() or (opened and opened["content"] == text),
          repr(opened["content"][:80]) if opened else "no wrap")
if latest:
    nd.review_event(rep, "draft saved with Ctrl+S", latest, kind=31234)
    rep.event("draft wrap, Ctrl+S from the window", latest, present=nd.presence(latest["id"]))

device.close()
for i in range(window.tabs.count()):
    e = window._editor_from_widget(window.tabs.widget(i))
    if e is not None:
        e.document().setModified(False)
window.close()
nd.spin(500)
web.stop()
sys.exit(rep.finish())
