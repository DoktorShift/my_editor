# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live check against ndak: profiles, relay lists, contacts, search, NIP-19.

Not run by pytest (no ``test_`` prefix). Run with the ndak relays up:

    (cd ~/projects/ndak && docker compose up -d)
    .venv/bin/python tests/smoke_ndak_people.py

Covers reading the own profile (kind 0) and its avatar, the safe profile
writer (update_profile keeps fields it does not know), showing a Nostr
address (nostr/profile_address.py), reading other people's relay lists,
adding a relay and the recommended list (nostr/relay_list_addition.py), the
contact list (kind 3) and known people, NIP-50 mention search on rnostr and
obelisk, and NIP-19 encodings checked against nak in both directions.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_ndak_common as nd  # noqa: E402

nd.bootstrap("people")
app = nd.qt_app()

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402

from nostr import bech32  # noqa: E402
from nostr.contacts import ContactListFetcher  # noqa: E402
from nostr.known_people import KnownPeople  # noqa: E402
from nostr.metadata import AvatarLoader, ProfileMetadataFetcher  # noqa: E402
from nostr.outbox import writer as outbox_writer  # noqa: E402
from nostr.profile_address import ProfileAddress  # noqa: E402
from nostr.relay_list_addition import RecommendedRelayList, RelayListAddition  # noqa: E402
from nostr.search import Nip50SearchClient  # noqa: E402

RNOSTR, NRS, STRFRY, OBELISK, KHATRU = nd.RNOSTR, nd.NRS, nd.STRFRY, nd.OBELISK, nd.KHATRU
rep = nd.Report("people")
stack = nd.Stack()
web = nd.FakeWeb().start()


def png(color: str) -> bytes:
    image = QImage(48, 48, QImage.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


web.put("/avatar.png", png("#3366cc"))


def latest(relays, kind, author):
    found = nd.nak_req(relays, kinds=[kind], authors=[author])
    return max(found, key=lambda e: e["created_at"]) if found else None


def names(relays) -> str:
    return ", ".join(sorted(nd.RELAY_NAMES.get(r, r) for r in relays)) or "none"


def run_writer(writer, timeout_ms=30_000):
    box = nd.Catch(writer.finished)
    writer.start()
    box.wait(timeout_ms)
    return box.last[0] if box.fired else None


def deps(profile):
    return {"pool": stack.pool, "directory": stack.directory,
            "session_pool": stack.sessions, "profile": profile}


# --------------------------------------------------------------------------- #
rep.section("Setup")
alice, alice_sk, setup = nd.local_account(stack, "Alice People")
rep.check("Alice's account is set up", setup.get("ok") is True)
alice_hex = alice_sk.hex()
_, bob_hex, bob = nd.new_identity()
_, carol_hex, carol = nd.new_identity()
token = uuid.uuid4().hex[:6]
nd.nak_publish(bob_hex, [STRFRY, KHATRU], kind=0,
               content=json.dumps({"name": f"Bob{token}", "nip05": "bob@example.com"}))
nd.nak_publish(bob_hex, [STRFRY, KHATRU], kind=10002, tags=[["r", KHATRU], ["r", STRFRY, "write"]])
nd.nak_publish(carol_hex, [STRFRY, RNOSTR, OBELISK], kind=0,
               content=json.dumps({"name": f"Carol{token}", "about": "searchable person"}))

# --------------------------------------------------------------------------- #
rep.section("The safe profile writer: update_profile keeps what it does not know")

time.sleep(1.1)
rich = {"name": "Alice People", "display_name": "Alice People", "about": "before",
        "picture": nd.FAKE_WEB + "/avatar.png", "banner": "https://example.com/banner.jpg",
        "website": "https://example.com", "lud16": "alice@example.com",
        "x_custom": {"nested": [1, 2, 3]}, "bot": False}
base = nd.nak_publish(alice_hex, [RNOSTR, NRS, STRFRY], kind=0, content=json.dumps(rich),
                      tags=[["i", "github:alice", "abc123"]])
time.sleep(1.1)
outcome = run_writer(outbox_writer.update_profile(
    changes={"about": "after, from MyEditor", "website": ""}, **deps(alice)))
rep.check("update_profile writes", outcome is not None and outcome.status == outbox_writer.WRITTEN,
          f"{outcome.status if outcome else None} {outcome.reason if outcome else ''}")
written = latest([RNOSTR, NRS, STRFRY], 0, alice.user_pubkey)
if written and outcome and outcome.event:
    nd.review_event(rep, "profile written by update_profile", written, kind=0)
    content = json.loads(written["content"])
    expected = dict(rich, about="after, from MyEditor")
    expected.pop("website")
    rep.check("every other field is kept (lud16, banner, nested custom field, bot), "
              "the empty one removed", content == expected, written["content"])
    rep.check("the profile's tags are kept", written["tags"] == [["i", "github:alice", "abc123"]],
              json.dumps(written["tags"]))
    rep.check("it is dated after the profile it replaces", written["created_at"] > base["created_at"])
    rep.check("the relays have the new profile as the newest", written["id"] == outcome.event["id"])
    rep.event("profile, update_profile", written, claimed=list(outcome.accepted),
              expected=[RNOSTR, NRS, STRFRY], present=nd.presence(written["id"]))

outcome = run_writer(outbox_writer.update_profile(changes={"about": "after, from MyEditor"},
                                                  **deps(alice)))
rep.check("an update that changes nothing sends nothing (UNCHANGED)",
          outcome is not None and outcome.status == outbox_writer.UNCHANGED,
          outcome.status if outcome else "")

time.sleep(1.1)
bad = nd.nak_publish(alice_hex, [RNOSTR, NRS, STRFRY], kind=0, content="[1, 2]")
time.sleep(1.1)
outcome = run_writer(outbox_writer.update_profile(changes={"about": "x"}, **deps(alice)))
rep.check("a profile that is not a JSON object is never overwritten",
          outcome is not None and outcome.status == outbox_writer.FAILED
          and latest([RNOSTR], 0, alice.user_pubkey)["id"] == bad["id"],
          f"{outcome.status if outcome else None}: {outcome.reason if outcome else ''}")
time.sleep(1.1)
nd.nak_publish(alice_hex, [RNOSTR, NRS, STRFRY], kind=0, content=json.dumps(rich))

# --------------------------------------------------------------------------- #
rep.section("Reading the own profile (kind 0) and its avatar")

fetcher = ProfileMetadataFetcher(stack.pool, stack.store, relay_directory=stack.directory)
updated = nd.Catch(fetcher.updated, fetcher.failed)
fetcher.fetch(stack.store.get(alice.user_pubkey))
updated.wait(20_000)
p = stack.store.get(alice.user_pubkey)
rep.check("the metadata fetcher reads the newest verified kind 0",
          p.display_name == "Alice People" and p.picture == rich["picture"]
          and p.metadata_cached_at > 0, f"{p.display_name!r} {p.picture!r}")

loader = AvatarLoader()
got = nd.Catch(loader.ready, loader.failed)
loader.load(alice.user_pubkey, p.picture)
got.wait(15_000)
ok = got.fired and len(got.last) == 2 and hasattr(got.last[1], "width")
rep.check("the avatar is downloaded, decoded and cached",
          ok and got.last[1].width() == 48
          and any((nd.HOME / ".cache/my_editor/nostr_avatars").glob(f"{alice.user_pubkey}-*")),
          str(got.last))
for bad_url in ("http://127.0.0.1:7811/avatar.png", "javascript:alert(1)", "file:///etc/hosts"):
    got = nd.Catch(loader.ready, loader.failed)
    loader.load(bob, bad_url)
    got.wait(5_000)
    rep.check(f"an avatar URL like {bad_url.split(':')[0]}:… on a non-public address is refused",
              got.fired and isinstance(got.last[1], str), str(got.last))
rep.note("is_safe_mirror_source lets any host NAME through (localhost, *.local, a name "
         "resolving to a private address): a stranger's kind 0 picture can still make the "
         "app GET a URL on the local network (url_safety.py:141-161). Seen live: the "
         "http://localhost avatar above was fetched.")

# --------------------------------------------------------------------------- #
rep.section("Showing a Nostr address on the profile (profile_address.py)")

time.sleep(1.1)
pa = ProfileAddress(stack.pool, stack.sessions, alice, "alice@einundzwanzig.space",
                    directory=stack.directory)
result = run_writer(pa)
rep.check("ProfileAddress shows the address", result == "shown", str(result))
shown = latest([RNOSTR, NRS, STRFRY], 0, alice.user_pubkey)
if shown:
    content = json.loads(shown["content"])
    rep.check("kind 0 has nip05 and every field it had before",
              content.get("nip05") == "alice@einundzwanzig.space"
              and {k: v for k, v in content.items() if k != "nip05"} == rich, shown["content"])
    nd.review_event(rep, "profile with a Nostr address", shown, kind=0)
    rep.event("profile, Nostr address", shown, present=nd.presence(shown["id"]),
              expected=[RNOSTR, NRS, STRFRY])
result = run_writer(ProfileAddress(stack.pool, stack.sessions, alice, "alice@einundzwanzig.space",
                                   directory=stack.directory))
rep.check("showing it again changes nothing", result == "already", str(result))

zed, zed_sk, _ = nd.local_account(stack, "", setup=False)
result = run_writer(ProfileAddress(stack.pool, stack.sessions, zed, "zed@einundzwanzig.space",
                                   directory=stack.directory))
rep.check("an account with no profile is never given one by the address step",
          result == "no_profile" and latest(nd.ALL_RELAYS, 0, zed.user_pubkey) is None, str(result))
stack.store.set_default(alice.user_pubkey)

# --------------------------------------------------------------------------- #
rep.section("Relay lists: reading other people's, adding a relay, the recommended list")

bob_rl = stack.lookup(bob)
rep.check("Bob's relay list is found and parsed (write khatru+strfry, read khatru)",
          bob_rl is not None and bob_rl.found and bob_rl.write == [KHATRU, STRFRY]
          and bob_rl.read == [KHATRU], f"{bob_rl.write if bob_rl else None} / "
                                       f"{bob_rl.read if bob_rl else None}")
box = {}
stack.directory.outbox_of(bob, lambda relays: box.update(r=relays))
nd.wait_until(lambda: box)
rep.check("reading Bob's notes: his ws://localhost relays are refused (other people's "
          "relays must be public wss://), so the fallback set is asked",
          box.get("r") == [NRS, RNOSTR, STRFRY], str(box.get("r")))
undo = nd.allow_local_relays_of_others()
box = {}
stack.directory.outbox_of(bob, lambda relays: box.update(r=relays))
nd.wait_until(lambda: box)
rep.check("with the harness patch, Bob's own write relays are asked", box.get("r") == [KHATRU, STRFRY],
          str(box.get("r")))
undo()

before = latest([RNOSTR], 10002, alice.user_pubkey)
time.sleep(1.1)
result = run_writer(RelayListAddition(stack.pool, stack.sessions, alice, KHATRU,
                                      directory=stack.directory))
rep.check("adding a relay to the list", result == "added", str(result))
after = latest([RNOSTR, KHATRU], 10002, alice.user_pubkey)
if after and before:
    nd.review_event(rep, "relay list with an added relay", after, kind=10002)
    rep.check("the new list is the old one plus the relay as a write relay",
              after["tags"] == before["tags"] + [["r", KHATRU, "write"]], json.dumps(after["tags"]))
    where = {r for r, ok in nd.presence(after["id"]).items() if ok}
    rep.check("it went to every relay it names, the old ones, and the indexer",
              where == {RNOSTR, NRS, KHATRU, STRFRY}, f"on {names(where)}")
    rep.event("relay list, relay added", after, present=nd.presence(after["id"]),
              expected=[RNOSTR, NRS, KHATRU, STRFRY])
result = run_writer(RelayListAddition(stack.pool, stack.sessions, alice, KHATRU,
                                      directory=stack.directory))
rep.check("adding it again changes nothing", result == "already", str(result))
result = run_writer(RelayListAddition(stack.pool, stack.sessions, zed, KHATRU,
                                      directory=stack.directory))
rep.check("an account with no relay list is not given one by Add", result == "no_list", str(result))
result = run_writer(RecommendedRelayList(stack.pool, stack.sessions, zed, OBELISK,
                                         directory=stack.directory))
rep.check("the recommended list is published for an account with none", result == "published",
          str(result))
zed_rl = latest([RNOSTR, NRS, STRFRY], 10002, zed.user_pubkey)
if zed_rl:
    nd.review_event(rep, "recommended relay list", zed_rl, kind=10002)
    rep.check("recommended list: the starter relays, then the relay as a write relay",
              zed_rl["tags"] == [["r", RNOSTR], ["r", NRS], ["r", OBELISK, "write"]],
              json.dumps(zed_rl["tags"]))
    rep.event("relay list, recommended", zed_rl, present=nd.presence(zed_rl["id"]),
              expected=[RNOSTR, NRS, OBELISK, STRFRY])
result = run_writer(RecommendedRelayList(stack.pool, stack.sessions, zed, OBELISK,
                                         directory=stack.directory))
rep.check("the recommended list never replaces an existing one", result == "list_exists",
          str(result))

# --------------------------------------------------------------------------- #
rep.section("Contacts (kind 3), known people and NIP-50 mention search")

nd.nak_publish(alice_hex, [RNOSTR, NRS, STRFRY], kind=3,
               tags=[["p", bob, KHATRU, "bobby"], ["p", carol]])
known = KnownPeople()
contacts = ContactListFetcher(stack.pool, known, relay_directory=stack.directory)
done = nd.Catch(contacts.completed, contacts.failed)
contacts.fetch(alice.user_pubkey)
done.wait(25_000)
people = {p.pubkey: p for p in known.all()} if hasattr(known, "all") else \
    {p.pubkey: p for p in known.search("")}
rep.check("the contact list is read and both people are known",
          bob in people and carol in people, f"{len(people)} people; signal {done.last}")
if bob in people:
    rep.check("names come from their kind 0, the relay hint from the p tag",
              people[bob].display_name.startswith("Bob") and people[bob].relay_hint == KHATRU
              and people[bob].source == "contact", str(people[bob]))
rep.check("the known people search finds Bob by name",
          any(p.pubkey == bob for p in known.search(f"bob{token}")))
rep.skip("NIP-05 verification of people", "not implemented: a person's nip05 is copied from "
         "their kind 0 and shown and searched unverified (contacts.py, mention_picker.py); "
         "the only NIP-05 lookup is the import resolver (smoke_ndak_imports.py)")

search = Nip50SearchClient(stack.pool, KnownPeople(nd.HOME / "search-people.json"))
results = nd.Catch(search.results, search.failed)
search.search(f"Carol{token}")
results.wait(10_000)
found = results.last[1] if results.fired and isinstance(results.last[1], list) else []
rep.check("NIP-50 search on the search relays finds Carol by name",
          any(p.pubkey == carol for p in found),
          f"{len(found)} results from {names(search._relays)}")

# --------------------------------------------------------------------------- #
rep.section("NIP-19 encodings against nak, both ways")

event_id = "ab" * 32
cases = {
    "npub": (bech32.encode_npub(bob), ["nak", "encode", "npub", bob]),
    "note": (bech32.encode_note(event_id), None),        # nak cannot encode a note1
    "nsec": (bech32.encode_nsec(bob_hex), ["nak", "encode", "nsec", bob_hex]),
    "nprofile": (bech32.encode_nprofile(bob, [KHATRU]),
                 ["nak", "encode", "nprofile", "--relay", KHATRU, bob]),
    "nevent": (bech32.encode_nevent(event_id, [RNOSTR], bob, 1),
               ["nak", "encode", "nevent", "--relay", RNOSTR, "--author", bob, event_id]),
    "naddr": (bech32.encode_naddr("my-slug", bob, 30023, [RNOSTR]),
              ["nak", "encode", "naddr", "-d", "my-slug", "--pubkey", bob, "--kind", "30023",
               "--relay", RNOSTR]),
}
for label, (ours, nak_args) in cases.items():
    decoded = nd.nak("decode", ours).stdout
    rep.check(f"nak decodes the app's {label}",
              (bob in decoded or event_id in decoded or bob_hex in decoded)
              and ("my-slug" in decoded if label == "naddr" else True),
              decoded.replace("\n", " ")[:160] if label != "nsec" else "(secret)")
    if nak_args is None:
        continue
    theirs = nd.nak(*nak_args[1:]).stdout.strip()
    try:
        if label == "npub":
            back = bech32.decode_npub(theirs) == bob
        elif label == "nsec":
            back = bech32.decode_nsec(theirs) == bob_hex
            theirs = "nsec1…"
        elif label == "nprofile":
            back = bech32.decode_nprofile(theirs) == (bob, [KHATRU])
        elif label == "nevent":
            ev_id, relays, author, _kind = bech32.decode_nevent(theirs)
            back = ev_id == event_id and relays == [RNOSTR] and author == bob
        else:
            back = bech32.decode_naddr(theirs) == ("my-slug", bob, 30023, [RNOSTR])
        rep.check(f"the app decodes nak's {label}", back, theirs[:60])
    except Exception as exc:  # noqa: BLE001
        rep.check(f"the app decodes nak's {label}", False, f"{theirs[:60]} {exc!r}")

web.stop()
stack.close()
sys.exit(rep.finish())
