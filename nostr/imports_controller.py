# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Imports, as the running app sees them.

One object owns everything the app does about imports for the active
account. The main window constructs it, tells it when the account
changes and asks it to flush on quit; the Imports window and the drafts
panel only show what it holds and forward what the person does:

- the list of sources shared with EINUNDZWANZIG STANDUP
  (imports/subscriptions.py);
- the account's inbox on this computer (imports/inbox_store.py), kept in
  step with that list and with the drafts the account has;
- the feed checker that runs while the app is open (imports/checker.py);
- the import jobs (imports/jobs.py) and the catalogue of what already
  exists (imports/catalogue.py);
- posts held only while they are shown: a source that is read when it is
  opened (imports/workspace.py, ``Collection``);
- the images the window shows (imports/remote_images.py), fetched
  through the same guard as everything else an import reads;
- the Imports window (nostr/ui/imports_window.py), made when first
  opened.

Nostr stays opt-in: until an account is in use, nothing is bound,
nothing is checked and nothing reaches the network. Leaving the account
(switching, signing out) stops the checker, pauses a running import and
closes the window; answers that arrive for the account left are dropped.

A second MyEditor process on the same account finds the inbox locked (a
lock file next to it). It shows the inbox but checks nothing and starts
no import, so two processes never sign the same import twice.

The inbox is a convenience kept on this computer, so it never stops the
app: a damaged file is set aside (renamed, never deleted) and a new one
started, and an inbox that cannot be opened at all (a folder that cannot
be written to) leaves imports unbound with ``problem`` saying why.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

import shiboken6
from PySide6.QtCore import QLockFile, QObject, QTimer, Signal

import url_safety
from i18n import _

from .imports import snapshots, workers
from .imports.catalogue import ExistingCatalogue
from .imports.checker import FeedChecker, checks_automatically
from .imports.constants import IDENTIFIER_PREFIX
from .imports.errors import SourceError, friendly_message
from .imports.feed_list import source_key
from .imports.fetch import SourceFetcher
from .imports.inbox_store import (
    DRAFTED,
    NEW,
    OLDER,
    ROW_FAILED,
    ROW_PENDING,
    SKIPPED,
    Conflict,
    Counts,
    InboxStore,
    Job,
    SourceRow,
    View,
    database_path,
)
from .imports.intake import read_export
from .imports.jobs import ImportRunner, unfinished
from .imports.pipeline import ImportItemsJob
from .imports.registry import ResolveInput, resolve_source
from .imports.remote_images import RemoteImages
from .imports.sources.nostr import RelayQueryAdapter
from .imports.sources.opml import is_opml, parse_opml
from .imports.subscriptions import FeedSubscriptionStore
from .imports.workspace import Collection, Post, post_from_row
from .outbox import relays_from
from .preview import Article
from .publisher import DraftPublishJob
from .rss.normalize import item_to_article

_log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".config" / "my_editor"

ACCOUNT_CHANGED = _("The account changed. Resume when you're signed in to that account "
                    "again.")
QUIT_PAUSED = _("MyEditor was closed during the import. Resume it to go on.")
ELSEWHERE = _("Imports are checked in another MyEditor window. This one only shows them.")
ARTICLE_FAILED = _("Couldn't show this post.")
CANNOT_KEEP = _("MyEditor can't keep imports on this computer: {reason}. Your sources are safe "
                "on your relays; check that your user folder can be written to, then sign in "
                "again.")
SET_ASIDE = _("The file that keeps imports on this computer was damaged, so MyEditor set it "
              "aside and started a new one. Your sources are kept; posts you had skipped show "
              "again.")
NO_SOURCES_IN_LIST = _("No sources were found in that list. Is it a subscription export "
                       "from a feed reader?")


class ImportsController(QObject):
    """Imports for the active account.

    Signals:
      bound_changed(bool)        an account's inbox was opened or closed
      sources_changed()          sources, their counts or their health changed
      posts_changed()            what a list shows may have changed
      inbox_count_changed(int)   posts waiting in the Inbox
      activity_changed()         an import job started, moved or ended
      new_posts(str, int)        a check put posts in the Inbox (source, count)
      import_finished(str)       an import went through all its posts (its id;
                                 some may have failed)
    """

    bound_changed = Signal(bool)
    sources_changed = Signal()
    posts_changed = Signal()
    inbox_count_changed = Signal(int)
    activity_changed = Signal()
    new_posts = Signal(str, int)
    import_finished = Signal(str)

    def __init__(self, *, relay_pool, relay_directory, session_pool, draft_store=None,
                 entitled_relays: Optional[Callable[[], Sequence[str]]] = None,
                 blossom_primary: Callable[[], str] = lambda: "",
                 config_dir: Optional[Path] = None,
                 subscription_store: Optional[FeedSubscriptionStore] = None,
                 fetcher=None, images: Optional[RemoteImages] = None,
                 item_job_factory=None, resend_factory=None,
                 checker_factory=None, catalogue_factory=None,
                 run_blocking=None, window_factory=None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._relay_pool = relay_pool
        self._relay_directory = relay_directory
        self._session_pool = session_pool
        self._draft_store = draft_store
        self._entitled_relays = entitled_relays
        self._blossom_primary = blossom_primary
        self._config_dir = Path(config_dir) if config_dir else CONFIG_DIR
        self._fetcher = fetcher if fetcher is not None else SourceFetcher(self)
        self._run_blocking = run_blocking or (
            lambda fn, ok, err: workers.run_blocking(fn, ok, err, parent=self))
        self._item_job_factory = item_job_factory or self._make_item_job
        self._resend_factory = resend_factory or self._make_resend
        self._checker_factory = checker_factory or (
            lambda inbox: FeedChecker(store=inbox, fetcher=self._fetcher, parent=self))
        self._catalogue_factory = catalogue_factory or (
            lambda inbox: ExistingCatalogue(
                relay_pool=relay_pool, relay_directory=relay_directory,
                draft_store=draft_store, ledger=inbox.ledger_states,
                entitled_relays=entitled_relays, parent=self))
        self._window_factory = window_factory
        self._nostr_query = (RelayQueryAdapter(relay_pool, parent=self)
                             if relay_pool is not None else None)

        self.subscriptions = subscription_store or FeedSubscriptionStore(
            session_pool=session_pool, relay_pool=relay_pool,
            relay_directory=relay_directory, entitled_relays=entitled_relays,
            parent=self)
        self.subscriptions.feeds_changed.connect(self._sync_sources)
        self.images = images or RemoteImages(parent=self)

        self._profile = None
        self.inbox: Optional[InboxStore] = None
        self.catalogue = None
        self.runner: Optional[ImportRunner] = None
        self.checker = None
        self._lock: Optional[QLockFile] = None
        self.read_only = False
        # Why imports could not be bound (a folder that cannot be written
        # to), and a one-time word for the window (a damaged file set aside).
        self.problem = ""
        self.notice = ""
        self._collections: Dict[str, Collection] = {}
        self._opened = 0                    # files and links opened, for their ids
        self._window = None
        self._reconcile_pending = False
        # Bumped whenever the account changes: an answer for the account
        # left behind is dropped.
        self._generation = 0

        if draft_store is not None:
            for signal in (draft_store.record_added, draft_store.record_changed,
                           draft_store.record_removed):
                signal.connect(self._schedule_reconcile)
            draft_store.loading_state_changed.connect(
                lambda loading: None if loading else self._schedule_reconcile())

    # ------------------------------------------------------------------ #
    # The account                                                          #
    # ------------------------------------------------------------------ #

    @property
    def profile(self):
        return self._profile

    @property
    def bound(self) -> bool:
        return self.inbox is not None

    def account_changed(self, profile) -> None:
        """The account in use changed; None when Nostr is not in use."""
        if (profile is not None and self._profile is not None
                and profile.user_pubkey.lower() == self._profile.user_pubkey.lower()):
            # The same account, signing another way now (a signer app
            # paired, or its key restored here): everything that signs or
            # decrypts for it follows, or the list of sources would keep
            # asking the signer it no longer has.
            self._profile = profile
            self.subscriptions.update_profile(profile)
            if self.runner is not None:
                self.runner.set_profile(profile)
            return
        self._unbind(ACCOUNT_CHANGED)
        self._generation += 1
        self._profile = profile
        self.subscriptions.bind_profile(profile)
        if profile is not None:
            self._bind()

    def flush(self) -> None:
        """Quitting: send pending changes to the list of sources, pause a
        running import (it resumes where it was), stop checking, close
        the window."""
        self.subscriptions.flush()
        if self.runner is not None and self.runner.busy():
            self.runner.halt(QUIT_PAUSED)
        if self.checker is not None:
            self.checker.stop()
        self.close_window()

    def _bind(self) -> None:
        self.problem = self.notice = ""
        path = database_path(self._config_dir, self._profile.user_pubkey)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._cannot_bind(exc)
            return
        self._lock = QLockFile(str(path.with_suffix(".lock")))
        # Held for the whole session: stale only when its process is gone.
        self._lock.setStaleLockTime(0)
        self.read_only = not self._lock.tryLock(0)
        if self.read_only:
            _log.info("imports inbox in use by another process; showing it read-only")
        try:
            self.inbox = self._open_inbox(path)
        except (sqlite3.Error, OSError) as exc:
            self._release_lock()
            self._cannot_bind(exc)
            return
        if not self.read_only:
            self.inbox.recover_jobs()
            self.inbox.prune_jobs()
        self.catalogue = self._catalogue_factory(self.inbox)
        self.runner = ImportRunner(
            store=self.inbox, catalogue=self.catalogue, profile=self._profile,
            item_job_factory=self._item_job_factory, resend_factory=self._resend_factory,
            parent=self)
        self.runner.job_changed.connect(lambda _id: self.activity_changed.emit())
        self.runner.job_finished.connect(self._on_job_finished)
        self.runner.draft_created.connect(self._on_draft_created)
        self.checker = self._checker_factory(self.inbox)
        self.checker.source_checked.connect(self._on_source_checked)
        self.checker.checking.connect(lambda *_args: self.sources_changed.emit())
        self._sync_sources()
        self._reconcile_from_drafts()
        if not self.read_only:
            self.checker.start()
        self.bound_changed.emit(True)
        self._announce()

    def _open_inbox(self, path: Path) -> InboxStore:
        """The account's inbox. A file that is not a database (damaged) is
        set aside and a new inbox started; one that cannot be opened at
        all raises."""
        try:
            return InboxStore(path)
        except sqlite3.OperationalError:
            raise
        except sqlite3.DatabaseError as exc:
            if self.read_only:
                raise       # the other process holds it: leave it alone
            aside = path.with_name(f"{path.name}.damaged-{int(time.time())}")
            _log.warning("imports inbox %s is damaged (%s); set aside as %s",
                         path, exc, aside.name)
            for suffix in ("", "-wal", "-shm"):
                part = Path(f"{path}{suffix}")
                if part.exists():
                    part.replace(Path(f"{aside}{suffix}"))
            self.notice = SET_ASIDE
            return InboxStore(path)

    def _cannot_bind(self, exc: BaseException) -> None:
        _log.warning("imports cannot be kept on this computer: %s", exc)
        self.inbox = None
        self.read_only = False
        self.problem = CANNOT_KEEP.format(reason=getattr(exc, "strerror", None) or str(exc))
        self.bound_changed.emit(False)

    def _release_lock(self) -> None:
        if self._lock is not None and not self.read_only:
            self._lock.unlock()
        self._lock = None

    def _unbind(self, reason: str) -> None:
        self.problem = self.notice = ""
        if self.inbox is None:
            return
        self.close_window()
        if self.runner is not None and self.runner.busy():
            self.runner.halt(reason)
        self.checker.stop()
        for owned in (self.checker, self.runner, self.catalogue):
            if isinstance(owned, QObject):
                owned.deleteLater()
        self.inbox.close()
        self.inbox = self.catalogue = self.runner = self.checker = None
        self._release_lock()
        self.read_only = False
        self._collections.clear()
        self.bound_changed.emit(False)
        self._announce()

    def _announce(self) -> None:
        self.sources_changed.emit()
        self.posts_changed.emit()
        self.activity_changed.emit()
        self.inbox_count_changed.emit(self.counts().inbox)

    # ------------------------------------------------------------------ #
    # Sources                                                              #
    # ------------------------------------------------------------------ #

    def _sync_sources(self) -> None:
        if self.inbox is None:
            return
        if self.read_only:
            # The process that checks keeps the inbox in step.
            self._announce()
            return
        # Until the list is known (this computer's copy read, or the
        # relays answered), a source missing from it is not removed: a
        # lost copy must never empty the inbox and its skips.
        self.inbox.sync_sources(
            ((source_key(feed.url), feed.url, feed.title, checks_automatically(feed.url))
             for feed in self.subscriptions.feeds),
            remove_missing=self.subscriptions.known)
        for collection_id in [cid for cid, c in self._collections.items()
                              if c.kind == "manual"
                              and not self.subscriptions.has_feed(c.source_url)]:
            del self._collections[collection_id]
        self._announce()

    def sources(self) -> List[SourceRow]:
        return self.inbox.sources() if self.inbox is not None else []

    def source(self, key: str) -> Optional[SourceRow]:
        return self.inbox.source(key) if self.inbox is not None else None

    def check_now(self, key: str) -> bool:
        """The person asked to check a source now (once a minute at most)."""
        source = self.source(key)
        if source is None or self.read_only:
            return False
        if not source.automatic:
            self.read_source(source.url)
            return True
        started = self.checker.check_now(key)
        self.sources_changed.emit()
        return started

    def is_checking(self, key: str) -> bool:
        if self.checker is not None and self.checker.is_checking(key):
            return True
        collection = self._collections.get(_manual_id(key))
        return collection is not None and collection.loading

    def _on_source_checked(self, key: str, new: int) -> None:
        self._announce()
        if new:
            self._reconcile_new_posts(key)
            self.new_posts.emit(key, new)

    # ------------------------------------------------------------------ #
    # Lists                                                                #
    # ------------------------------------------------------------------ #

    def counts(self) -> Counts:
        return self.inbox.counts() if self.inbox is not None else Counts()

    def page(self, view: View, *, query: str = "", after: Optional[Post] = None) -> List[Post]:
        """One page of a stored list, after the post ``after``."""
        if self.inbox is None:
            return []
        cursor = None
        if after is not None:
            cursor = InboxStore.cursor_of(after.found_at, after.published_at,
                                          after.source_key, after.d_tag)
        return [post_from_row(row)
                for row in self.inbox.page(view, query=query, after=cursor)]

    def item(self, post: Post):
        """The full post (its body too) behind a list row."""
        if post.collection:
            collection = self._collections.get(post.collection)
            return collection.item(post.d_tag) if collection else None
        if self.inbox is None:
            return None
        return self.inbox.item(post.source_key, post.d_tag)

    def collection(self, collection_id: str) -> Optional[Collection]:
        return self._collections.get(collection_id)

    def read_source(self, url: str) -> Optional[Collection]:
        """Read a source that is not checked on its own (a Nostr author, a
        sitemap, a GitHub folder): now, while it is shown."""
        if self.inbox is None:
            return None
        key = source_key(url)
        collection = self._collections.get(_manual_id(key))
        if collection is None:
            collection = Collection(id=_manual_id(key), kind="manual", label=url,
                                    source_url=url, source_key=key)
            self._collections[collection.id] = collection
        source = self.inbox.source(key)
        if source is not None:
            collection.label = source.display_title
        collection.loading, collection.error = True, ""
        generation = self._generation
        self.posts_changed.emit()
        self.sources_changed.emit()

        def done(result) -> None:
            if generation != self._generation or self._collections.get(
                    collection.id) is not collection:
                return
            collection.loading = False
            collection.items = list(result.feed.items)
            # Remembered with the list, and sent with its next real change:
            # reading a source never asks the signer by itself.
            self.subscriptions.mark_fetched(url)
            if not _title_known(source) and result.feed.title:
                collection.label = result.feed.title
                self.inbox.set_site(key, feed_title=result.feed.title,
                                    site_url=result.feed.link or "")
            self._look_up_states(collection)
            self.sources_changed.emit()

        def failed(error) -> None:
            if generation != self._generation or self._collections.get(
                    collection.id) is not collection:
                return
            collection.loading = False
            collection.error = (friendly_message(error) if isinstance(error, SourceError)
                                else str(error))
            self.posts_changed.emit()
            self.sources_changed.emit()

        resolve_source(ResolveInput(url=url), fetcher=self._fetcher, on_success=done,
                       on_failure=failed, run_blocking=self._run_blocking,
                       nostr_query=self._nostr_query, relay_directory=self._relay_directory)
        return collection

    def _look_up_states(self, collection: Collection) -> None:
        """Which of a collection's posts are imported already."""
        tags = [p.d_tag for p in collection.posts()]
        for d_tag in tags:
            local = self.catalogue.known_locally(d_tag) if self.catalogue else None
            if local:
                collection.states[d_tag] = local
        self.posts_changed.emit()
        if not tags or self.catalogue is None:
            return
        generation = self._generation

        def found(existing) -> None:
            if generation != self._generation:
                return
            collection.states.update(existing.states)
            self.posts_changed.emit()

        self.catalogue.look_up(self._profile, tags, on_ready=found,
                               on_unavailable=lambda _reason: None)

    # ------------------------------------------------------------------ #
    # Following, files and links                                           #
    # ------------------------------------------------------------------ #

    def look_up(self, address: str, *,
                on_done: Callable[[Optional[object], str], None]) -> None:
        """Read what an address holds, for a sheet: ``on_done(result, "")``
        or ``on_done(None, words for the person)``."""
        self._resolve(ResolveInput(url=address), on_done)

    def look_up_paste(self, text: str, address: str, *,
                      on_done: Callable[[Optional[object], str], None]) -> None:
        """Read pasted feed text; ``address`` makes its relative links whole."""
        self._resolve(ResolveInput(url=address.strip() or None, pasted_body=text), on_done)

    def _resolve(self, request: ResolveInput, on_done) -> None:
        generation = self._generation

        def answer(result, error: str) -> None:
            if generation == self._generation:
                on_done(result, error)

        resolve_source(
            request, fetcher=self._fetcher,
            on_success=lambda result: answer(result, ""),
            on_failure=lambda error: answer(None, friendly_message(error)
                                            if isinstance(error, SourceError) else str(error)),
            run_blocking=self._run_blocking, nostr_query=self._nostr_query,
            relay_directory=self._relay_directory)

    def read_list(self, url: str, *,
                  on_done: Callable[[Optional[object], str], None]) -> None:
        """Read a list of sources (OPML) at ``url``: ``on_done(document,
        "")`` or ``on_done(None, words for the person)``."""
        generation = self._generation

        def read(text: str) -> None:
            if generation != self._generation:
                return
            document = parse_opml(text) if is_opml(text) else None
            if document is None or not document.feeds:
                on_done(None, NO_SOURCES_IN_LIST)
            else:
                on_done(document, "")

        def failed(error) -> None:
            if generation == self._generation:
                on_done(None, friendly_message(error) if isinstance(error, SourceError)
                        else str(error))

        self._fetcher.fetch(url, on_success=read, on_failure=failed)

    def is_followed(self, url: str) -> bool:
        return self.subscriptions.has_feed(url)

    def follow(self, url: str, title: str = "", *, result=None) -> str:
        """Follow ``url``. With the ``result`` its lookup gave, its posts are
        there at once: a feed's as a baseline (Older Posts, as the first
        check would make them), another source's as read now. Returns the
        source's key, or "" when it could not be followed."""
        if not _public(url):
            return ""
        added = self.subscriptions.add_feed(url, title)
        key = source_key(url)
        if not added.get("added") and not added.get("duplicate"):
            return ""
        if added.get("added") and result is not None and self.inbox is not None:
            source = self.inbox.source(key)
            if source is not None and source.automatic and not self.read_only:
                self.inbox.ingest(
                    key, list(result.feed.items), final_url=result.url,
                    feed_title=result.feed.title or "", site_url=result.feed.link or "")
                self._reconcile_new_posts(key, state=OLDER)
            elif source is not None and not source.automatic:
                collection = Collection(id=_manual_id(key), kind="manual",
                                        label=source.display_title or result.feed.title or url,
                                        source_url=url, source_key=key,
                                        items=list(result.feed.items))
                self._collections[collection.id] = collection
                self._look_up_states(collection)
            self._announce()
        return key

    def unfollow(self, key: str) -> bool:
        """Stop following a source. Its posts leave the inbox; the drafts
        made from them, and the record of what was imported, stay."""
        source = self.source(key)
        if source is None or self.read_only:
            return False
        return self.subscriptions.remove_feed(source.url)

    def follow_all(self, document) -> Tuple[int, int, int]:
        """Follow every source of a list (OPML): how many were followed
        now, were followed already, and could not be followed (not a
        source, or an address on this computer or its network)."""
        followed = known = refused = 0
        for feed in document.feeds:
            added = (self.subscriptions.add_feed(feed.xml_url, feed.title)
                     if _public(feed.xml_url) else {})
            if added.get("added"):
                followed += 1
            elif added.get("duplicate"):
                known += 1
            else:
                refused += 1
        return followed, known, refused

    def open_posts(self, *, kind: str, label: str, items, source_url: str = "") -> Collection:
        """Posts of a file or a link, held while shown, to import once."""
        self._opened += 1
        collection = Collection(id=f"{kind}-{self._opened}", kind=kind, label=label,
                                source_url=source_url, items=list(items))
        self._collections[collection.id] = collection
        self._look_up_states(collection)
        self.sources_changed.emit()
        return collection

    def read_file(self, path: str, *,
                  on_done: Callable[[Optional[Collection], Optional[object], str], None]
                  ) -> None:
        """Read an export file: ``on_done(collection, None, "")`` with its
        posts, ``on_done(None, opml_document, "")`` for a list of sources,
        or ``on_done(None, None, words for the person)``."""
        generation = self._generation

        def read(export) -> None:
            if generation != self._generation:
                return
            if export.sources is not None:
                on_done(None, export.sources, "")
            elif export.items:
                on_done(self.open_posts(kind="file", label=export.label, items=export.items,
                                        source_url=export.label), None, "")
            else:
                self.look_up_paste(export.text, "", on_done=lambda result, error: (
                    on_done(self.open_posts(kind="file", label=export.label,
                                            items=result.feed.items, source_url=export.label),
                            None, "") if result is not None else on_done(None, None, error)))

        def failed(exc) -> None:
            if generation == self._generation:
                on_done(None, None, str(exc))

        self._run_blocking(lambda: read_export(path), read, failed)

    def close_collection(self, collection_id: str) -> None:
        """Take a file or a link off the list (its drafts stay)."""
        if self._collections.pop(collection_id, None) is not None:
            self.sources_changed.emit()

    def collections(self, *kinds: str) -> List[Collection]:
        return [c for c in self._collections.values() if not kinds or c.kind in kinds]

    # ------------------------------------------------------------------ #
    # What is imported                                                     #
    # ------------------------------------------------------------------ #

    def _schedule_reconcile(self, *_args) -> None:
        if self._reconcile_pending or self.inbox is None:
            return
        self._reconcile_pending = True
        QTimer.singleShot(0, self, self._reconcile_from_drafts)

    def _reconcile_from_drafts(self) -> None:
        """Posts with a draft are Imported, also posts drafted elsewhere."""
        self._reconcile_pending = False
        store = self._draft_store
        if self.inbox is None or store is None or self._profile is None or self.read_only:
            return
        if store.profile_pubkey != self._profile.user_pubkey.lower():
            return
        drafted = [record.identifier for record in store.all()]
        changed = self.inbox.reconcile(drafted=drafted, removed=store.deleted_identifiers())
        for collection in self._collections.values():
            for d_tag in drafted:
                collection.states.setdefault(d_tag, DRAFTED)
        if changed:
            self._announce()

    def reconcile_inbox(self) -> None:
        """Ask the relays which Inbox posts were imported by another app."""
        if self.inbox is not None:
            self._reconcile_tags(self.inbox.d_tags((NEW, SKIPPED))[:500])

    def _reconcile_new_posts(self, key: str, state: str = NEW) -> None:
        tags = [p.d_tag for p in self.inbox.page(View("source", key, state), limit=500)]
        self._reconcile_tags(tags)

    def _reconcile_tags(self, tags: List[str]) -> None:
        if not tags or self.catalogue is None or self.read_only:
            return
        inbox, generation = self.inbox, self._generation

        def found(existing) -> None:
            if generation != self._generation or self.inbox is not inbox:
                return
            by_state: Dict[str, list] = {}
            for d_tag, state in existing.states.items():
                by_state.setdefault(state, []).append(d_tag)
            if by_state and inbox.reconcile(**by_state):
                self._announce()

        self.catalogue.look_up(self._profile, tags, on_ready=found,
                               on_unavailable=lambda _reason: None)

    # ------------------------------------------------------------------ #
    # One post, as it will be published                                    #
    # ------------------------------------------------------------------ #

    def prepare_article(self, post: Post, *, on_ready: Callable[[str, Article], None],
                        on_failed: Callable[[str], None]) -> None:
        """The post as the article an import of it publishes: its Markdown
        and the details around it, made off the UI thread."""
        item = self.item(post)
        if item is None:
            on_failed(ARTICLE_FAILED)
            return
        author = getattr(self._profile, "display_name", "") or ""
        generation = self._generation

        def make():
            return item_to_article(item, identifier_prefix=IDENTIFIER_PREFIX)

        def made(template) -> None:
            if generation != self._generation:
                return
            on_ready(template.content, Article(
                title=template.title, summary=template.summary,
                image=template.image or snapshots.cover(item), tags=template.hashtags,
                author=author, published_at=template.published_at or 0))

        def failed(exc) -> None:
            _log.warning("could not prepare an imported post: %s", exc)
            if generation == self._generation:
                on_failed(ARTICLE_FAILED)

        self._run_blocking(make, made, failed)

    # ------------------------------------------------------------------ #
    # Creating drafts                                                      #
    # ------------------------------------------------------------------ #

    def defaults_for(self, posts: Sequence[Post]) -> Tuple[Optional[bool], Optional[bool]]:
        """What the sources of ``posts`` choose for new drafts: copy images,
        fetch the full article. Each is None when the sources differ."""
        found = {(self._source_defaults(post.source_key)) for post in posts} or {(True, True)}
        copy = {pair[0] for pair in found}
        full = {pair[1] for pair in found}
        return (copy.pop() if len(copy) == 1 else None,
                full.pop() if len(full) == 1 else None)

    def _source_defaults(self, key: str) -> Tuple[bool, bool]:
        source = self.source(key) if key else None
        if source is None:
            return True, True
        options = self.subscriptions.options_for(source.url)
        return options.rehost_images, options.fetch_full_text

    def signer_prompts(self, posts: Sequence[Post], *, copy_images: bool) -> int:
        """How often a signer app may ask while ``posts`` become drafts: once
        a draft, and once an image copied. 0 when the key is kept here."""
        if self._profile is None or getattr(self._profile, "signer", "remote") == "local":
            return 0
        images = sum(max(post.image_count, 1 if post.image else 0) for post in posts)
        return len(posts) + (images if copy_images else 0)

    def can_create(self) -> bool:
        """Whether drafts can be created now (one import at a time)."""
        return self.runner is not None and not self.read_only and not self.runner.busy()

    def create_drafts(self, posts: Sequence[Post], *, label: str,
                      choices: Optional[dict] = None, skip_image_urls=()) -> str:
        """Start making drafts of ``posts``. ``choices`` are the person's for
        this run (rehost_images, fetch_full_text); where it says nothing,
        each source's defaults apply. Returns the import's id, or "" when
        none could start."""
        if not self.can_create():
            return ""
        pairs, by_source, collections = [], {}, set()
        for post in posts:
            item = self.item(post)
            if item is None or not post.d_tag:
                continue
            pairs.append((item, post.source_key))
            collections.add(post.collection)
            if post.source_key and post.source_key not in by_source:
                copy, full = self._source_defaults(post.source_key)
                by_source[post.source_key] = {"rehost_images": copy, "fetch_full_text": full}
        if not pairs:
            return ""
        collection = (self._collections.get(next(iter(collections)))
                      if len(collections) == 1 else None)
        options = {"by_source": by_source, **(choices or {})}
        if skip_image_urls:
            options["skip_image_urls"] = sorted(skip_image_urls)
        job = self.runner.create(
            label=label, source_type=collection.kind if collection else "inbox",
            posts=pairs, source_url=collection.source_url if collection else "",
            options=options)
        self.runner.run(job.id)
        self.activity_changed.emit()
        self.posts_changed.emit()
        return job.id

    def images_of(self, posts: Sequence[Post], *,
                  on_ready: Callable[[List[str]], None]) -> None:
        """Every image an import of ``posts`` would copy, for the review
        (found off the UI thread: it converts each post)."""
        items = [item for item in map(self.item, posts) if item is not None]
        generation = self._generation

        def found(images) -> None:
            if generation == self._generation:
                on_ready(list(images))

        self._run_blocking(lambda: snapshots.images_to_copy(items), found,
                           lambda _exc: found([]))

    # ------------------------------------------------------------------ #
    # Imports                                                              #
    # ------------------------------------------------------------------ #

    def _on_job_finished(self, job_id: str) -> None:
        self._announce()
        job = self.inbox.job(job_id) if self.inbox is not None else None
        if job is not None and job.status in ("completed", "partial"):
            self.import_finished.emit(job_id)

    # ------------------------------------------------------------------ #
    # Progress                                                             #
    # ------------------------------------------------------------------ #

    def activity(self) -> Optional[Job]:
        """The import to show: the one running, else the newest one that
        still has posts to do (paused, or some failed)."""
        if self.runner is None:
            return None
        if self.runner.running is not None:
            return self.runner.running
        waiting = unfinished(self.inbox.jobs())
        return waiting[0] if waiting else None

    def job(self, job_id: str) -> Optional[Job]:
        if self.runner is not None and self.runner.running is not None \
                and self.runner.running.id == job_id:
            return self.runner.running
        return self.inbox.job(job_id) if self.inbox is not None else None

    def posts_in_progress(self) -> Tuple[Set[str], Set[str]]:
        """The posts an unfinished import still holds (they cannot be
        chosen again), and those that failed in one (they can)."""
        busy: Set[str] = set()
        failed: Set[str] = set()
        if self.inbox is None:
            return busy, failed
        jobs = unfinished(self.inbox.jobs())
        running = self.runner.running if self.runner is not None else None
        if running is not None:
            jobs = [running] + [job for job in jobs if job.id != running.id]
        for job in jobs:
            for row in job.rows:
                if row.status == ROW_PENDING:
                    busy.add(row.d_tag)
                elif row.status == ROW_FAILED:
                    failed.add(row.d_tag)
        return busy, failed - busy

    def pause_import(self) -> None:
        """Pause after the post being made."""
        if self.runner is not None:
            self.runner.pause()

    def resume_import(self, job_id: str) -> bool:
        """Go on with an import, or try its failed posts again."""
        if self.runner is None or self.read_only:
            return False
        started = self.runner.run(job_id)
        self._announce()
        return started

    def stop_import(self, job_id: str) -> None:
        """Stop an import for good; the drafts made stay."""
        if self.runner is not None:
            self.runner.stop_paused(job_id)
            self._announce()

    # ------------------------------------------------------------------ #
    # Skipping                                                             #
    # ------------------------------------------------------------------ #

    def skip(self, posts: Sequence[Post]) -> List[Tuple[str, str, int]]:
        """Set posts aside on this computer. Returns what moved, for Undo:
        (source, post, its new revision) each."""
        moved = []
        if self.inbox is None or self.read_only:
            return moved
        for post in posts:
            if not post.skippable:
                continue
            try:
                revision = self.inbox.skip(post.source_key, post.d_tag, post.revision)
            except Conflict:
                continue        # imported or changed meanwhile: leave it
            moved.append((post.source_key, post.d_tag, revision))
        if moved:
            self._announce()
        return moved

    def restore(self, posts: Sequence[Post]) -> List[Tuple[str, str, int]]:
        """Bring skipped posts back where they were."""
        return self._restore([(post.source_key, post.d_tag, post.revision)
                              for post in posts if post.skipped])

    def undo_skip(self, moved: Sequence[Tuple[str, str, int]]) -> int:
        """Undo a skip: what :meth:`skip` returned goes back. Returns how
        many posts did (one changed since stays as it is)."""
        return len(self._restore(moved))

    def _restore(self, moves) -> List[Tuple[str, str, int]]:
        restored = []
        if self.inbox is None or self.read_only:
            return restored
        for source_key_, d_tag, revision in moves:
            try:
                restored.append((source_key_, d_tag,
                                 self.inbox.restore(source_key_, d_tag, revision)))
            except Conflict:
                continue
        if restored:
            self._announce()
        return restored

    def _on_draft_created(self, d_tag: str) -> None:
        """A post became a draft: it is Imported everywhere it is shown."""
        for collection in self._collections.values():
            if collection.item(d_tag) is not None:
                collection.states[d_tag] = DRAFTED
        self._announce()

    def _make_item_job(self, *, items, feed_url, fetch_full_text, rehost_images,
                       skip_image_urls, is_imported, parent):
        store = self._draft_store
        return ImportItemsJob(
            items=items, feed_url=feed_url, profile=self._profile,
            relay_pool=self._relay_pool, relay_directory=self._relay_directory,
            session_pool=self._session_pool,
            # One fetcher for every post, not a network manager each.
            fetcher=self._fetcher,
            identifier_exists=(lambda d: d in store) if store is not None else None,
            is_imported=is_imported, fetch_full_text=fetch_full_text,
            rehost_images=rehost_images,
            blossom_server=self._blossom_primary() if rehost_images else "",
            skip_image_urls=skip_image_urls,
            entitled_relays=relays_from(self._entitled_relays), parent=parent)

    def _make_resend(self, *, identifier, parent):
        return DraftPublishJob(
            relay_pool=self._relay_pool, relay_directory=self._relay_directory,
            session_pool=self._session_pool, profile=self._profile, inner_event=None,
            identifier=identifier, entitled_relays=relays_from(self._entitled_relays),
            parent=parent)

    # ------------------------------------------------------------------ #
    # The window                                                           #
    # ------------------------------------------------------------------ #

    def window(self):
        window = self._window
        return window if window is not None and shiboken6.isValid(window) else None

    def open_window(self) -> None:
        """Nostr > Imports: show the window (made on first use)."""
        if self.inbox is None or self._window_factory is None:
            return
        window = self.window()
        if window is None:
            window = self._window = self._window_factory(self)
            self.reconcile_inbox()
        window.show()
        window.raise_()
        window.activateWindow()

    def close_window(self) -> None:
        window = self.window()
        self._window = None
        if window is not None:
            window.close()
            window.deleteLater()


def _public(url: str) -> bool:
    """Whether a web address may be followed: never one on this computer or
    its network (the owner's decision Q-6). A Nostr address is judged by
    the readers."""
    if not url.strip().lower().startswith(("http://", "https://")):
        return True
    return url_safety.is_safe_mirror_source(url.strip())


def _manual_id(key: str) -> str:
    return "m-" + key


def _title_known(source: Optional[SourceRow]) -> bool:
    return source is not None and bool(source.title or source.feed_title)
