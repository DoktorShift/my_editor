# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Live check against ndak: publishing notes and articles, mentions, NIP-65
routing, a membership relay, and what happens when relays refuse.

Not run by pytest (no ``test_`` prefix). Run with the ndak relays up:

    (cd ~/projects/ndak && docker compose up -d)
    .venv/bin/python tests/smoke_ndak_publish.py

Everything stays on this computer (see tests/smoke_ndak_common.py). The
publish dialogs are built offscreen and driven the way a click drives them;
every event is read back from the relays with nak, verified, and reviewed
against NIP-01, NIP-10/27 mentions, NIP-23 and NIP-65.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_ndak_common as nd  # noqa: E402

nd.bootstrap("publish")
app = nd.qt_app()

from nostr import bech32, crypto  # noqa: E402
from nostr.avatar_store import AvatarStore  # noqa: E402
from nostr.known_people import KnownPeople, Person  # noqa: E402
from nostr.outbox import policy  # noqa: E402
from nostr.search import Nip50SearchClient  # noqa: E402
from nostr.ui.publish_article_dialog import PublishArticleDialog  # noqa: E402
from nostr.ui.publish_note_dialog import PublishNoteDialog  # noqa: E402

RNOSTR, NRS, STRFRY, OBELISK, KHATRU = nd.RNOSTR, nd.NRS, nd.STRFRY, nd.OBELISK, nd.KHATRU
DEAD_1, DEAD_2 = "ws://localhost:7797", "ws://localhost:7798"

rep = nd.Report("publish")
stack = nd.Stack()
known = KnownPeople()
search = Nip50SearchClient(stack.pool, known)
avatars = AvatarStore()
web = nd.FakeWeb().start()
web.put("/cover.png", bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))


def present_on(event_id: str) -> set:
    return {relay for relay, ok in nd.presence(event_id).items() if ok}


def names(relays) -> str:
    return ", ".join(sorted(nd.RELAY_NAMES.get(r, r) for r in relays)) or "none"


def note_dialog(profile, content: str, chips=(), entitled=lambda: []):
    dialog = PublishNoteDialog(
        content=content, active_profile=profile, store=stack.store,
        relay_pool=stack.pool, relay_directory=stack.directory,
        session_pool=stack.sessions, entitled_relays=entitled,
        known_people=known, search_client=search, avatars=avatars, is_dark=False)
    for person in chips:
        dialog._mention_row._on_picked(person)
    return dialog


def publish_note(profile, content: str, chips=(), entitled=lambda: [], timeout_ms=25_000):
    dialog = note_dialog(profile, content, chips, entitled)
    published = nd.Catch(dialog.published)
    dialog._on_publish()
    nd.wait_until(lambda: published.fired or (dialog._job is None and
                                               "No relay" in dialog._status.text()), timeout_ms)
    return dialog, published


def fetch_one(event_id: str, relays=nd.ALL_RELAYS):
    for relay in relays:
        found = nd.nak_req([relay], ids=[event_id])
        if found:
            return found[0]
    return None


# --------------------------------------------------------------------------- #
rep.section("Setup: Alice (this app), Bob, Carol and Erin (other people)")

alice, alice_sk, setup = nd.local_account(stack, "Alice ndak")
rep.check("Alice's account is set up (relay list, then profile)", setup.get("ok") is True,
          f"steps: {[s[:2] for s in setup.get('steps', [])]}")
alice_rl = stack.directory.cached(alice.user_pubkey)
rep.check("Alice's own relay list is known to routing right after setup",
          alice_rl.found and set(alice_rl.write) == {RNOSTR, NRS}, f"write {alice_rl.write}")

_, bob_hex, bob = nd.new_identity()
_, carol_hex, carol = nd.new_identity()
_, dave_hex, dave = nd.new_identity()
_, erin_hex, erin = nd.new_identity()
nd.nak_publish(bob_hex, [STRFRY, KHATRU], kind=0, content=json.dumps({"name": "Bob ndak"}))
nd.nak_publish(bob_hex, [STRFRY, KHATRU], kind=10002,
               tags=[["r", KHATRU], ["r", STRFRY, "write"]])
nd.nak_publish(carol_hex, [STRFRY, RNOSTR], kind=0, content=json.dumps({"name": "Carol ndak"}))
nd.nak_publish(carol_hex, [STRFRY, RNOSTR], kind=10002,
               tags=[["r", OBELISK, "read"], ["r", KHATRU, "read"], ["r", NRS, "read"],
                     ["r", RNOSTR, "write"]])
rep.info("other people", "Bob reads khatru, Carol reads obelisk, khatru, nostr-rs-relay; "
                         "Dave and Erin have no relay list")

# --------------------------------------------------------------------------- #
rep.section("Short note, no mentions")

content_1 = f"ndak live test {uuid.uuid4().hex[:8]}: plain note with unicode ä ö ü 🌸, \"quotes\", \\ and\nnew lines."
dialog, published = publish_note(alice, content_1)
if rep.check("the note dialog publishes and closes", published.fired,
             dialog._status.text() if not published.fired else ""):
    note_id, results = published.last
    claimed = [url for url, ok, _ in results if ok]
    ev = fetch_one(note_id)
    rep.check("the note can be read back from a relay", ev is not None, note_id)
    if ev:
        nd.review_event(rep, "note", ev, kind=1)
        rep.check("note content is exactly what was written", ev["content"] == content_1.strip())
        rep.check("note tags are only the client tag", ev["tags"] == [["client", "MyEditor"]],
                  json.dumps(ev["tags"]))
        where = present_on(note_id)
        rep.check("the note is on Alice's write relays and nowhere else",
                  where == {RNOSTR, NRS}, f"on {names(where)}; app claims {names(claimed)}")
        rep.event("note, no mentions", ev, claimed=claimed, expected=[RNOSTR, NRS],
                  present=nd.presence(note_id))
        rep.check("status line text", dialog._status.text().startswith("Published."),
                  dialog._status.text())

# --------------------------------------------------------------------------- #
rep.section("Note with mentions: chips and an inline nostr:npub (other people's ws:// relays refused)")

dave_npub = bech32.encode_npub(dave)
content_2 = f"Hello nostr:{dave_npub}, mentions test {uuid.uuid4().hex[:6]}"
chips = [Person(pubkey=bob, display_name="Bob ndak", relay_hint=KHATRU, source="search"),
         Person(pubkey=carol, display_name="Carol ndak", source="contact")]
dialog, published = publish_note(alice, content_2, chips)
if rep.check("a note with mentions publishes", published.fired, dialog._status.text()):
    note2_id, results = published.last
    ev = fetch_one(note2_id)
    if ev:
        nd.review_event(rep, "note with mentions", ev, kind=1)
        p_tags = [t for t in ev["tags"] if t[0] == "p"]
        rep.check("p tags: inline mention first, then the chips, each once",
                  [t[1] for t in p_tags] == [dave, bob, carol], json.dumps(p_tags))
        rep.check("the chip with a relay hint carries it in its p tag (NIP-10)",
                  ["p", bob, KHATRU] in p_tags and ["p", carol] in p_tags, json.dumps(p_tags))
        tail = ev["content"].split("\n\n")[-1].split(" ")
        decoded = [bech32.decode_nprofile(u[len("nostr:"):]) for u in tail if u.startswith("nostr:nprofile1")]
        rep.check("chips are appended as nostr:nprofile URIs (NIP-27), the inline npub kept",
                  ev["content"].startswith(content_2) and decoded == [(bob, [KHATRU]), (carol, [])],
                  repr(ev["content"][len(content_2):]))
        nak_decoded = nd.nak("decode", tail[0][len("nostr:"):]).stdout.strip()
        rep.check("nak decodes the appended nprofile", bob in nak_decoded, nak_decoded[:160])
        where = present_on(note2_id)
        rep.check("by design, other people's ws://localhost read relays are not used "
                  "(is_public_relay): the note stays on Alice's relays",
                  where == {RNOSTR, NRS}, f"on {names(where)}")
        rep.event("note, mentions, policy as shipped", ev,
                  claimed=[u for u, ok, _ in results if ok], expected=[RNOSTR, NRS],
                  present=nd.presence(note2_id))

# --------------------------------------------------------------------------- #
rep.section("Note with mentions, inbox routing observed (HARNESS PATCH: ws://localhost counts as public)")

undo = nd.allow_local_relays_of_others()
stack.directory._entries.pop(bob, None)
stack.directory._entries.pop(carol, None)
plan_box = {}
stack.directory.publish_plan(alice.user_pubkey, lambda plan: plan_box.update(plan=plan),
                             mentioned=[(dave, ""), (bob, KHATRU), (carol, ""), (erin, STRFRY)])
nd.wait_until(lambda: plan_box, 15_000)
plan = plan_box.get("plan")
rep.check("publish plan: Alice's outbox, then everyone's first read relay in turn, "
          "then the second ones (INBOX_PER_MENTION=2)",
          plan is not None and list(plan.author) == [RNOSTR, NRS]
          and list(plan.inbox) == [KHATRU, OBELISK, STRFRY],
          f"author {plan.author if plan else None}, inbox {plan.inbox if plan else None}")
content_3 = f"Routing test {uuid.uuid4().hex[:6]} for nostr:{dave_npub}"
chips3 = chips + [Person(pubkey=erin, display_name="Erin", relay_hint=STRFRY, source="mention")]
dialog, published = publish_note(alice, content_3, chips3)
if rep.check("a note mentioning four people publishes", published.fired, dialog._status.text()):
    note3_id, results = published.last
    ev = fetch_one(note3_id)
    nd.review_event(rep, "note routed to inboxes", ev, kind=1)
    nd.spin(1500)
    where = present_on(note3_id)
    expected = {RNOSTR, NRS, KHATRU, OBELISK, STRFRY}
    rep.check("the note reached Bob's and Carol's read relays and Erin's hint relay",
              where == expected, f"on {names(where)}")
    rl_event = alice_rl.event or {}
    rl_where = present_on(rl_event.get("id", ""))
    rep.check("Alice's relay list was handed to the inbox relays that took the note (NIP-65)",
              {KHATRU, OBELISK, STRFRY} <= rl_where, f"kind 10002 on {names(rl_where)}")
    rep.event("note, mentions, inbox routing", ev, claimed=[u for u, ok, _ in results if ok],
              expected=sorted(expected), present=nd.presence(note3_id))
undo()

# --------------------------------------------------------------------------- #
rep.section("A membership relay (entitled) joins the outbox")

dialog, published = publish_note(alice, f"Entitled relay test {uuid.uuid4().hex[:6]}",
                                 entitled=lambda: [OBELISK])
if rep.check("a note with an entitled relay publishes", published.fired, dialog._status.text()):
    note4_id, results = published.last
    where = present_on(note4_id)
    rep.check("it goes to Alice's write relays plus the membership relay",
              where == {RNOSTR, NRS, OBELISK}, f"on {names(where)}")
    rep.event("note, entitled relay", fetch_one(note4_id) or {"id": note4_id},
              claimed=[u for u, ok, _ in results if ok], expected=[RNOSTR, NRS, OBELISK],
              present=nd.presence(note4_id))

# --------------------------------------------------------------------------- #
rep.section("Long-form article (NIP-23), then an edit of it")


def article_dialog(profile, body, *, title, summary, slug, image, tags):
    dialog = PublishArticleDialog(
        body_markdown=body, active_profile=profile, store=stack.store,
        relay_pool=stack.pool, relay_directory=stack.directory, session_pool=stack.sessions,
        entitled_relays=lambda: [], known_people=known, search_client=search,
        avatars=avatars, default_title=title, default_slug=slug, is_dark=False)
    dialog._title_edit.setText(title)
    dialog._summary_edit.setText(summary)
    dialog._slug_edit.setText(slug)
    dialog._image_edit.setText(image)
    dialog._tags_edit.setText(tags)
    return dialog


slug = f"ndak-live-{uuid.uuid4().hex[:8]}"
bob_npub = bech32.encode_npub(bob)
body_1 = (f"# A live article\n\nWritten by the ndak smoke test. It mentions nostr:{bob_npub}.\n\n"
          "- one\n- two\n\n[a link](https://example.com/page) and `code`.\n")
cover = nd.FAKE_WEB + "/cover.png"
d1 = article_dialog(alice, body_1, title="ndak live article", summary="What the test wrote",
                    slug=slug, image=cover, tags="Nostr, #MyEditor, ndak")
published = nd.Catch(d1.published)
d1._on_publish()
nd.wait_until(lambda: published.fired or (d1._job is None and "No relay" in d1._status.text()), 25_000)
first_article = None
if rep.check("the article dialog publishes", published.fired, d1._status.text()):
    naddr, results = published.last
    ident, author, kind, hints = bech32.decode_naddr(naddr)
    rep.check("the dialog's naddr names the slug, Alice, kind 30023 and two relays that took it",
              ident == slug and author == alice.user_pubkey and kind == 30023
              and len(hints) == 2 and set(hints) <= {RNOSTR, NRS}, f"{naddr[:40]}… {hints}")
    nak_dec = nd.nak("decode", naddr).stdout
    rep.check("nak decodes the naddr", slug in nak_dec, nak_dec.strip()[:200])
    found = nd.nak_req([RNOSTR], kinds=[30023], authors=[alice.user_pubkey], tags={"d": [slug]})
    first_article = found[0] if found else None
    if rep.check("the article is on the relay by its address", first_article is not None):
        ev = first_article
        nd.review_event(rep, "article", ev, kind=30023)
        t = {name: nd.tag_values(ev, name) for name in
             ("d", "title", "summary", "image", "published_at", "t", "client", "p")}
        rep.check("NIP-23 tags: d, title, summary, image, published_at",
                  t["d"] == [slug] and t["title"] == ["ndak live article"]
                  and t["summary"] == ["What the test wrote"] and t["image"] == [cover]
                  and len(t["published_at"]) == 1 and t["published_at"][0].isdigit(),
                  json.dumps(ev["tags"]))
        rep.check("hashtags are lowercased t tags without '#'",
                  t["t"] == ["nostr", "myeditor", "ndak"], str(t["t"]))
        rep.check("the inline npub mention becomes a p tag", t["p"] == [bob], str(t["p"]))
        rep.check("content is the Markdown body", ev["content"] == body_1.strip(),
                  repr(ev["content"][:80]))
        published_at_1 = int(t["published_at"][0])
        rep.check("published_at is close to created_at on first publication",
                  abs(published_at_1 - ev["created_at"]) <= 5,
                  f"{published_at_1} vs {ev['created_at']}")
        rep.event("article", ev, claimed=[u for u, ok, _ in results if ok],
                  expected=[RNOSTR, NRS], present=nd.presence(ev["id"]))

nd.spin(1500)       # a later second, so the edit is the newer version
body_2 = body_1 + "\n\nEdited: one more paragraph.\n"
d2 = article_dialog(alice, body_2, title="ndak live article (edited)", summary="What the test wrote",
                    slug=slug, image=cover, tags="Nostr, #MyEditor, ndak")
published2 = nd.Catch(d2.published)
d2._on_publish()
nd.wait_until(lambda: published2.fired or (d2._job is None and "No relay" in d2._status.text()), 25_000)
if rep.check("editing the article (same slug) publishes", published2.fired, d2._status.text()) \
        and first_article is not None:
    nd.spin(500)
    per_relay = {r: nd.nak_req([r], kinds=[30023], authors=[alice.user_pubkey], tags={"d": [slug]})
                 for r in (RNOSTR, NRS)}
    rep.check("each relay keeps only the newest version (parameterized replaceable)",
              all(len(v) == 1 and v[0]["id"] != first_article["id"] for v in per_relay.values()),
              {nd.RELAY_NAMES[r]: [e["id"][:12] for e in v] for r, v in per_relay.items()})
    edited = per_relay[RNOSTR][0] if per_relay[RNOSTR] else None
    if edited:
        nd.review_event(rep, "edited article", edited, kind=30023)
        rep.check("the edit carries the new title and body",
                  nd.tag_values(edited, "title") == ["ndak live article (edited)"]
                  and edited["content"] == body_2.strip())
        published_at_2 = int(nd.tag_values(edited, "published_at")[0])
        rep.check("NIP-23: published_at keeps the first publication time on an edit",
                  published_at_2 == published_at_1,
                  f"first {published_at_1}, after the edit {published_at_2} "
                  "(publish_article_dialog.py sets published_at=int(time.time()) on every publish)")
        rep.event("article, edited", edited, claimed=[u for u, ok, _ in published2.last[1] if ok],
                  expected=[RNOSTR, NRS], present=nd.presence(edited["id"]))

# --------------------------------------------------------------------------- #
rep.section("Deleting published notes and articles (NIP-09 kind 5)")
rep.skip("delete a published note or article", "MyEditor has no NIP-09 deletion: nothing "
         "in nostr/ builds a kind 5 event. Only private drafts are deleted, by NIP-37 "
         "blank replacement (see smoke_ndak_drafts.py)")

# --------------------------------------------------------------------------- #
rep.section("When no relay takes it: an account whose relays are all down")

_, eve_sk, _setup = nd.local_account(stack, "Eve ndak", setup=False)
eve = stack.store.get(crypto.get_public_key(eve_sk).hex())
eve_rl = stack.sign(eve, {"kind": 10002, "content": "", "created_at": int(time.time()),
                          "tags": [["r", DEAD_1], ["r", DEAD_2]]})
stack.publish_raw([STRFRY], eve_rl)
rep.check("the app learns Eve's relay list (two relays that are down) from the indexer",
          set((stack.lookup(eve.user_pubkey) or policy.RelayList()).write) == {DEAD_1, DEAD_2})
dialog, published = publish_note(eve, "This note has nowhere to go", timeout_ms=20_000)
rep.check("no relay accepts: the dialog stays open and says so",
          not published.fired and "No relay accepted the note" in dialog._status.text(),
          dialog._status.text())
rep.check("and Publish can be pressed again (the retry path is a new publish)",
          dialog._publish_btn.isEnabled())
rep.note("PublishJob has no retry of its own: 0/N accepted ends the job; the person "
         "presses Publish again, which signs a new event.")

# --------------------------------------------------------------------------- #
rep.section("Account setup retry: Try Again sends the very events signed the first time")

real_targets = policy.relay_list_targets
policy.relay_list_targets = lambda new_event, old: [DEAD_1, DEAD_2]
frank, frank_sk, outcome = nd.local_account(stack, "Frank ndak")
rep.check("with every relay down the setup reports failure and keeps the account",
          outcome.get("ok") is False and stack.store.get(frank.user_pubkey) is not None,
          outcome.get("message", ""))
policy.relay_list_targets = real_targets
first_signed = outcome["setup"]._signed.get("relays")
retry = {}
outcome["setup"].finished.connect(lambda ok, msg: retry.update(ok=ok, msg=msg))
outcome["setup"].start()
nd.wait_until(lambda: retry, 30_000)
rep.check("Try Again finishes the setup", retry.get("ok") is True, retry.get("msg", ""))
if first_signed:
    on_relays = nd.nak_req([RNOSTR, NRS], kinds=[10002], authors=[frank.user_pubkey])
    rep.check("the relay list on the relays is the event signed the first time",
              bool(on_relays) and on_relays[0]["id"] == first_signed["id"],
              f"signed {first_signed['id'][:12]}, relays have "
              f"{[e['id'][:12] for e in on_relays]}")
    nd.review_event(rep, "relay list after a retry", first_signed, kind=10002)

rep.section("Reading back the account events")
for label, kind in (("relay list", 10002), ("profile", 0)):
    evs = nd.nak_req([RNOSTR], kinds=[kind], authors=[alice.user_pubkey])
    if rep.check(f"Alice's {label} (kind {kind}) is on her relays", bool(evs)):
        nd.review_event(rep, f"Alice's {label}", evs[0], kind=kind)
        rep.event(f"Alice's {label}", evs[0], present=nd.presence(evs[0]["id"]),
                  expected=[RNOSTR, NRS, STRFRY])

web.stop()
stack.close()
sys.exit(rep.finish())
