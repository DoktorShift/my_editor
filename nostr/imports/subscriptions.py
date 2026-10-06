# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The person's sources, synced as one encrypted kind 30078 event.

The list is shared with EINUNDZWANZIG STANDUP: same event (``d`` =
``einundzwanzig:feed-sources``), same payload, same ``["encrypted",
"nip44"]`` tag (feed_list.py describes the payload and the merge). A
source followed in one app shows up in the other, and neither app's
edits are lost to the other's.

Disciplines:

- Optimistic local update, and a JSON cache file per account for an
  instant first paint. The cache also keeps the version last seen on
  the relays and whether local edits still have to go out, so edits
  made just before quitting are sent on the next launch.
- Debounced publish, so rapid edits become one signed event (one
  signer prompt, not five).
- Merge, never overwrite: before publishing, the newest list is read
  again and both apps' edits since the last sync are merged
  (feed_list.merge). A relay answer that arrives while a publish is on
  its way is merged the same way, so it cannot undo an edit.
- A list this app cannot read (saved with older encryption, or the
  signer is unreachable) is never written over: that would delete the
  other app's sources. The edits stay here until it can be read.
- Only real changes are published. A source's last read time travels
  along with one, or when the person opens a manual source; automatic
  checks never cause a publish (a signer prompt each time).
- The list this app wrote before (``myeditor:feed-sources``) is merged
  into the shared one once per account, and never written again.
- ``flush()`` for sign-out and quit, and :meth:`wait_until_settled` so
  quitting can wait (a few seconds at most) for it to arrive.

Every external boundary (queries, publishing, signer, timer, clock,
cache path) is injectable, so the whole store is testable without
relays, a signer, or wall-clock time.
"""

from __future__ import annotations

import json
import logging
import time as _time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal

from i18n import _

from .. import CLIENT_NAME
from ..bunker import BunkerSessionPool
from ..events import build_event, verify_event
from ..outbox import RelayDirectory, ask_private_relays
from ..outbox.policy import replacement_created_at
from ..profiles import Profile
from ..relay import RelayPool
from .constants import SUBSCRIPTIONS_DEBOUNCE_MS, SUBSCRIPTIONS_KIND
from .feed_list import (
    FEED_LIST_DTAG,
    LEGACY_FEED_LIST_DTAG,
    FeedList,
    FeedSubscription,
    SourceOptions,
    is_storable_url,
    merge,
    read_payload,
    write_payload,
)
from .registry import can_resolve_source
from .sources.nostr import RelayQueryAdapter

__all__ = ["FeedSubscription", "FeedSubscriptionStore", "SourceOptions"]

_log = logging.getLogger(__name__)

_CACHE_DIR = Path.home() / ".config" / "my_editor"
_CACHE_VERSION = 2

# What a list read from the relays can turn out to be.
_UNREADABLE = "unreadable"

_OLD_ENCRYPTION = _(
    "Your list of sources was saved with an older kind of encryption. Open "
    "it once in the app that saved it; until then, changes made here stay "
    "on this computer.")


class FeedSubscriptionStore(QObject):
    """The person's sources, per active profile.

    Signals:
      feeds_changed()        the list changed (any reason)
      sync_status(str)       human-readable sync progress / errors
      settled()              nothing is waiting to be published any more
    """

    feeds_changed = Signal()
    sync_status = Signal(str)
    settled = Signal()

    def __init__(
        self,
        *,
        session_pool: BunkerSessionPool,
        relay_pool: RelayPool,
        relay_directory: RelayDirectory,
        cache_dir: Optional[Path] = None,
        query=None,
        publisher: Optional[Callable[..., None]] = None,
        scheduler: Optional[Callable[..., Callable[[], None]]] = None,
        clock: Optional[Callable[[], int]] = None,
        debounce_ms: int = SUBSCRIPTIONS_DEBOUNCE_MS,
        entitled_relays: Optional[Callable[[], Sequence[str]]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._session_pool = session_pool
        self._relay_pool = relay_pool
        self._relay_directory = relay_directory
        self._entitled_relays = entitled_relays
        self._cache_dir = Path(cache_dir) if cache_dir else _CACHE_DIR
        self._query = query if query is not None else (
            RelayQueryAdapter(relay_pool, parent=self)
            if relay_pool is not None else None
        )
        self._publisher = publisher or self._default_publisher
        self._scheduler = scheduler or self._default_scheduler
        self._clock = clock or (lambda: int(_time.time()))
        self._debounce_ms = debounce_ms

        self._profile: Optional[Profile] = None
        # Bumped on every profile change; a callback for an older one
        # leaves the current state alone.
        self._generation = 0
        self._local = FeedList()
        # The version last known to be on the relays (None: never seen).
        self._base: Optional[FeedList] = None
        # True while local edits haven't reached relays.
        self._dirty = False
        # The next publish also sends read times (a manual source opened).
        self._send_read_times = False
        self._legacy_merged = False
        self._cancel_timer: Optional[Callable[[], None]] = None
        # Publishes on their way, by generation; one at a time per account.
        self._runs: Dict[int, int] = {}
        # Edits made while a publish was on its way go out right after it.
        self._publish_after_run = False

    # -- profile binding ---------------------------------------------------

    def bind_profile(self, profile: Optional[Profile]) -> None:
        """Switch to (or clear) the active profile and load its list.

        Edits still waiting for the previous profile are sent first; that
        publish finishes on its own and only touches that profile's cache.
        """
        if profile is self._profile:
            return
        if self._profile is not None and (self._dirty or self._cancel_timer is not None):
            self.flush()
        self._cancel_pending_timer()
        self._generation += 1
        self._profile = profile
        self._local, self._base = FeedList(), None
        self._dirty = self._send_read_times = self._legacy_merged = False
        self._publish_after_run = False
        if profile is None:
            self.feeds_changed.emit()
            return
        self._load_cache()
        self.feeds_changed.emit()
        self.refresh()

    # -- read API ----------------------------------------------------------

    @property
    def feeds(self) -> List[FeedSubscription]:
        return self._local.feeds()

    def has_feed(self, url: str) -> bool:
        return self._local.row(url) is not None

    def get(self, url: str) -> Optional[FeedSubscription]:
        row = self._local.row(url)
        if row is None:
            return None
        return next((f for f in self.feeds if f.url == row["url"].strip()), None)

    def options_for(self, url: str) -> SourceOptions:
        """The defaults for new drafts from ``url`` (both on until changed)."""
        return self._local.options_for(url)

    @property
    def is_busy(self) -> bool:
        """Whether something is still waiting to be published, or on its way."""
        return self._cancel_timer is not None or any(self._runs.values())

    # -- mutations ---------------------------------------------------------

    def add_feed(self, url: str, title: str = "") -> dict:
        """Follow a source. The registry is the validation gate, so anything
        a resolver claims (feed URL, Nostr address, ...) is storable and
        nothing else ever syncs to relays."""
        cleaned = (url or "").strip()
        if not is_storable_url(cleaned) or not can_resolve_source(cleaned):
            return {"added": False, "invalid": True}
        if not self._local.add(cleaned, title or ""):
            return {"added": False, "duplicate": True}
        self._after_mutation()
        return {"added": True}

    def remove_feed(self, url: str) -> bool:
        if not self._local.remove(url):
            return False
        self._after_mutation()
        return True

    def update_title(self, url: str, title: str) -> None:
        if self._local.set_title(url, title or ""):
            self._after_mutation()

    def set_source_options(self, url: str, *, rehost_images: Optional[bool] = None,
                           fetch_full_text: Optional[bool] = None) -> None:
        """Remember a source's defaults for new drafts (synced)."""
        values = {}
        if rehost_images is not None:
            values["rehostImages"] = bool(rehost_images)
        if fetch_full_text is not None:
            values["fetchFullText"] = bool(fetch_full_text)
        if values and self._local.set_options(url, **values):
            self._after_mutation()

    def mark_fetched(self, url: str, when: Optional[int] = None, *,
                     publish: bool = False) -> None:
        """Note when a source was last read. Kept here, and sent along with
        the next real change; ``publish`` sends it now (the person opened
        a manual source, as STANDUP does)."""
        stamp = int(when if when is not None else self._clock())
        if not self._local.set_fetched(url, stamp):
            return
        if publish:
            self._send_read_times = True
            self._after_mutation()
        else:
            self._save_cache()
            self.feeds_changed.emit()

    def flush(self) -> None:
        """Publish any debounced changes immediately (sign-out / quit)."""
        self._cancel_pending_timer()
        if self._dirty:
            self._publish_now()
        elif not self.is_busy:
            self.settled.emit()

    def wait_until_settled(self, timeout_ms: int) -> bool:
        """Run the event loop until nothing is waiting to be published, at
        most ``timeout_ms``. True when everything was sent (or nothing
        needed to be). For quitting: the signer and relay sockets close
        right after."""
        if not self.is_busy:
            return True
        loop = QEventLoop()
        self.settled.connect(loop.quit)
        QTimer.singleShot(max(0, int(timeout_ms)), loop.quit)
        try:
            if self.is_busy:
                loop.exec()
        finally:
            self.settled.disconnect(loop.quit)
        return not self.is_busy

    def refresh(self) -> None:
        """Read the list from the relays and merge it in."""
        profile = self._profile
        if profile is None or self._query is None:
            return
        generation = self._generation
        self.sync_status.emit(_("Syncing feed subscriptions…"))

        def _on_relays(relays) -> None:
            if generation != self._generation:
                return
            self._read_list(profile, relays, FEED_LIST_DTAG,
                            lambda result, event: _on_shared(relays, result, event))

        def _on_shared(relays, result, event) -> None:
            if generation != self._generation:
                return
            if isinstance(result, FeedList):
                self._adopt(result)
            if self._legacy_merged:
                if result != _UNREADABLE:
                    self.sync_status.emit("")
                return
            self._read_list(profile, relays, LEGACY_FEED_LIST_DTAG,
                            lambda legacy, _event: _on_legacy(legacy, result))

        def _on_legacy(legacy, shared_result) -> None:
            if generation != self._generation:
                return
            if legacy == _UNREADABLE:
                return  # tried again on the next launch
            if isinstance(legacy, FeedList):
                self._merge_legacy(legacy)
            self._legacy_merged = True
            self._save_cache()
            if shared_result != _UNREADABLE:
                self.sync_status.emit("")

        self._with_relays(profile, _on_relays, reading=True)

    # -- internals: merging --------------------------------------------------

    def _adopt(self, remote: FeedList) -> None:
        """Merge a version read from the relays into the local list."""
        merged = merge(self._base, self._local, remote)
        changed = merged != self._local
        self._local = merged
        self._base = remote
        self._dirty = not merged.same_sources(remote) or self._send_read_times
        self._save_cache()
        if changed:
            self.feeds_changed.emit()
        if self._dirty:
            self._schedule_publish()

    def _merge_legacy(self, legacy: FeedList) -> None:
        """Add the sources of this app's older list that are not here yet."""
        added = False
        for row in legacy.rows:
            if self._local.row(row["url"]) is None:
                self._local.rows.append(dict(row))
                added = True
        if added:
            self._after_mutation()

    # -- internals: mutation plumbing --------------------------------------

    def _after_mutation(self) -> None:
        self._dirty = True
        self._save_cache()
        self.feeds_changed.emit()
        self._schedule_publish()

    def _schedule_publish(self) -> None:
        self._cancel_pending_timer()
        self._cancel_timer = self._scheduler(
            self._debounce_ms, self._on_debounce_fired)

    def _on_debounce_fired(self) -> None:
        self._cancel_timer = None
        self._publish_now()

    def _cancel_pending_timer(self) -> None:
        if self._cancel_timer is not None:
            try:
                self._cancel_timer()
            finally:
                self._cancel_timer = None

    def _default_scheduler(
        self, ms: int, fn: Callable[[], None]
    ) -> Callable[[], None]:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(fn)
        timer.start(ms)

        def _cancel() -> None:
            timer.stop()
            timer.deleteLater()

        return _cancel

    # -- internals: local cache --------------------------------------------

    def _cache_path(self, pubkey: Optional[str] = None) -> Optional[Path]:
        pubkey = pubkey or (self._profile.user_pubkey if self._profile else None)
        if not pubkey:
            return None
        return self._cache_dir / f"feed_sources_{pubkey}.json"

    def _load_cache(self) -> None:
        path = self._cache_path()
        if path is None or not path.exists():
            return
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(cached, dict):
            return
        if cached.get("version") != _CACHE_VERSION:
            # This app's older cache: the list it synced on its own. It is
            # merged with the shared list as soon as that is read.
            self._local = read_payload(cached)
            return
        self._local = read_payload(cached.get("local"))
        base = cached.get("base")
        self._base = read_payload(base) if isinstance(base, dict) else None
        self._dirty = bool(cached.get("dirty"))
        self._legacy_merged = bool(cached.get("legacyMerged"))

    def _save_cache(self) -> None:
        if self._profile is None:
            return
        self._write_cache(self._profile.user_pubkey, self._local, self._base,
                          self._dirty, self._legacy_merged)

    def _write_cache(self, pubkey: str, local: FeedList, base: Optional[FeedList],
                     dirty: bool, legacy_merged: bool) -> None:
        path = self._cache_path(pubkey)
        if path is None:
            return
        now = int(self._clock())
        data = {
            "version": _CACHE_VERSION,
            "local": write_payload(local, now),
            "base": write_payload(base, now) if base is not None else None,
            "dirty": dirty,
            "legacyMerged": legacy_merged,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            _log.warning("feed cache write failed: %s", exc)

    # -- internals: relays ---------------------------------------------------

    def _with_relays(self, profile: Profile, on_done: Callable[[List[str]], None], *,
                     reading: bool = False) -> None:
        """The account's private relays: where the list is written and,
        so every device finds it, where it is read (which also asks the
        relays a list written before the account's own was known went)."""
        ask_private_relays(self._relay_directory, profile, on_done,
                           entitled=self._entitled_relays, reading=reading)

    def _read_list(self, profile: Profile, relays, d_tag: str, on_done) -> None:
        """The newest list under ``d_tag``: ``on_done(result, event)`` with a
        FeedList, None when there is none, or _UNREADABLE (with the
        reason already reported)."""
        if self._query is None:
            on_done(None, None)
            return

        def _on_event(event) -> None:
            if not event or not str(event.get("content") or "").strip():
                on_done(None, None)
                return
            # Only the account's own signature counts: a relay must not be
            # able to plant sources in the list.
            if (str(event.get("pubkey", "")).lower() != profile.user_pubkey.lower()
                    or not verify_event(event)):
                on_done(None, None)
                return
            self._decrypt(profile, event,
                          lambda text: on_done(_parse(text), event),
                          lambda reason: _unreadable(reason, event))

        def _parse(text: str):
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                payload = None
            if not isinstance(payload, dict):
                self.sync_status.emit(_("Couldn't decrypt the synced feed list: {reason}")
                                      .format(reason=_("it isn't a list of sources")))
                return _UNREADABLE
            return read_payload(payload)

        def _unreadable(reason: str, event) -> None:
            self.sync_status.emit(reason)
            on_done(_UNREADABLE, event)

        self._query.latest(relays, [{
            "kinds": [SUBSCRIPTIONS_KIND],
            "authors": [profile.user_pubkey],
            "#d": [d_tag],
            "limit": 1,
        }], _on_event)

    def _decrypt(self, profile: Profile, event: dict, on_text, on_unreadable) -> None:
        """The plaintext of a list, by its ``encrypted`` tag, as STANDUP
        reads it: NIP-44, NIP-04 when the signer offers it, or none."""
        algorithm = next((t[1] for t in event.get("tags", [])
                          if isinstance(t, list) and len(t) >= 2 and t[0] == "encrypted"), "")
        content = str(event.get("content") or "")
        if algorithm not in ("nip44", "nip44_v2", "nip04"):
            on_text(content)  # no encryption (a list from before it was used)
            return

        def _on_ready(client) -> None:
            if algorithm == "nip04":
                decrypt = getattr(client, "nip04_decrypt_self", None)
                if decrypt is None:
                    on_unreadable(_OLD_ENCRYPTION)
                    return
            else:
                decrypt = client.nip44_decrypt_self
            decrypt(content, on_success=on_text,
                    on_failure=lambda reason: on_unreadable(
                        _("Couldn't decrypt the synced feed list: {reason}").format(
                            reason=reason)))

        self._session_pool.get(
            profile, on_ready=_on_ready,
            on_error=lambda reason: on_unreadable(
                _("Couldn't reach the signer to sync feeds: {reason}").format(reason=reason)))

    # -- internals: publishing -------------------------------------------------

    def _publish_now(self) -> None:
        profile = self._profile
        if profile is None:
            return
        generation = self._generation
        if self._runs.get(generation):
            # The latest state still ships, right after the publish on
            # its way settles.
            self._publish_after_run = True
            return
        self._runs[generation] = 1
        # What this publish starts from, kept for the case the profile is
        # switched before it is done.
        snapshot = {"base": self._base, "local": self._local.copy(),
                    "legacy": self._legacy_merged, "reads": self._send_read_times}

        def current() -> bool:
            return generation == self._generation

        def _finish(status: Optional[str] = "", *, sent: bool = False) -> None:
            """``status`` None leaves the status line as it is."""
            self._runs.pop(generation, None)
            if current():
                if not sent:
                    self._dirty = True
                self._save_cache()
                if status is not None:
                    self.sync_status.emit(status)
                again, self._publish_after_run = self._publish_after_run, False
                if self._dirty and self._cancel_timer is None and (sent or again):
                    if again:
                        self._publish_now()
                        return
                    self._schedule_publish()
            if not self.is_busy:
                self.settled.emit()

        def _on_read_relays(relays) -> None:
            self._read_list(profile, relays, FEED_LIST_DTAG, _on_remote)

        def _on_remote(remote, event) -> None:
            if remote == _UNREADABLE:
                # Never write over a list that could not be read: it holds
                # the other app's sources. The reason is on the status line.
                _finish(None)
                return
            base = self._base if current() else snapshot["base"]
            local = self._local if current() else snapshot["local"]
            merged = merge(base, local, remote)
            if current():
                changed = merged != self._local
                self._local = merged
                if remote is not None:
                    self._base = remote
                if changed:
                    self.feeds_changed.emit()
            if remote is not None and merged.same_sources(remote) and not snapshot["reads"]:
                # Everything here is on the relays already.
                if current():
                    self._dirty = not self._local.same_sources(remote)
                _finish(sent=True)
                return
            _sign_and_send(merged.copy(), event)

        def _sign_and_send(content: FeedList, remote_event) -> None:
            plaintext = json.dumps(write_payload(content, int(self._clock())),
                                   separators=(",", ":"))

            def _on_ready(client) -> None:
                client.nip44_encrypt_self(
                    plaintext,
                    on_success=lambda ciphertext: _sign(client, ciphertext),
                    on_failure=_failed)

            def _sign(client, ciphertext: str) -> None:
                unsigned = build_event(
                    kind=SUBSCRIPTIONS_KIND,
                    content=ciphertext,
                    tags=[["d", FEED_LIST_DTAG], ["client", CLIENT_NAME],
                          ["encrypted", "nip44"]],
                    pubkey_hex=profile.user_pubkey,
                    created_at=replacement_created_at(remote_event, self._clock()),
                )
                client.sign_event(unsigned, on_success=_publish, on_failure=_failed)

            def _publish(signed: dict) -> None:
                self._with_relays(profile, lambda relays: self._publisher(
                    relays, signed, on_done=lambda accepted, total: _done(accepted)))

            def _done(accepted: int) -> None:
                if accepted <= 0:
                    _finish(_("Feed subscriptions saved locally; no relay accepted "
                              "the sync yet."))
                    return
                if current():
                    self._base = content
                    self._send_read_times = False
                    # Edits made while this was on its way go out next.
                    self._dirty = not self._local.same_sources(content)
                else:
                    self._write_cache(profile.user_pubkey, content, content, False,
                                      snapshot["legacy"])
                _finish(sent=True)

            self._session_pool.get(profile, on_ready=_on_ready, on_error=_failed)

        def _failed(reason: str) -> None:
            _finish(_("Couldn't sync feed subscriptions: {reason}").format(reason=reason))

        self._with_relays(profile, _on_read_relays, reading=True)

    def _default_publisher(self, relays, signed, *, on_done) -> None:
        try:
            job = self._relay_pool.publish(list(relays), signed)
        except Exception as exc:  # noqa: BLE001, publish must never raise into UI
            _log.warning("subscription publish failed: %s", exc)
            on_done(0, len(list(relays)))
            return
        job.all_done.connect(
            lambda results: on_done(
                sum(1 for _, ok, _ in results if ok), len(results)))
