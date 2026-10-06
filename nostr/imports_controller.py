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
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

import shiboken6
from PySide6.QtCore import QLockFile, QObject, QTimer, Signal

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
    SKIPPED,
    Counts,
    InboxStore,
    SourceRow,
    View,
    database_path,
)
from .imports.jobs import ImportRunner
from .imports.pipeline import ImportItemsJob
from .imports.registry import ResolveInput, resolve_source
from .imports.remote_images import RemoteImages
from .imports.sources.nostr import RelayQueryAdapter
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


class ImportsController(QObject):
    """Imports for the active account.

    Signals:
      bound_changed(bool)        an account's inbox was opened or closed
      sources_changed()          sources, their counts or their health changed
      posts_changed()            what a list shows may have changed
      inbox_count_changed(int)   posts waiting in the Inbox
      activity_changed()         an import job started, moved or ended
      new_posts(str, int)        a check put posts in the Inbox (source, count)
    """

    bound_changed = Signal(bool)
    sources_changed = Signal()
    posts_changed = Signal()
    inbox_count_changed = Signal(int)
    activity_changed = Signal()
    new_posts = Signal(str, int)

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
        self._collections: Dict[str, Collection] = {}
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
            self._profile = profile
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
        path = database_path(self._config_dir, self._profile.user_pubkey)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = QLockFile(str(path.with_suffix(".lock")))
        # Held for the whole session: stale only when its process is gone.
        self._lock.setStaleLockTime(0)
        self.read_only = not self._lock.tryLock(0)
        if self.read_only:
            _log.info("imports inbox in use by another process; showing it read-only")
        self.inbox = InboxStore(path)
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
        self.checker = self._checker_factory(self.inbox)
        self.checker.source_checked.connect(self._on_source_checked)
        self.checker.checking.connect(lambda *_args: self.sources_changed.emit())
        self._sync_sources()
        self._reconcile_from_drafts()
        if not self.read_only:
            self.checker.start()
        self.bound_changed.emit(True)
        self._announce()

    def _unbind(self, reason: str) -> None:
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
        if self._lock is not None and not self.read_only:
            self._lock.unlock()
        self._lock = None
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
        self.inbox.sync_sources(
            (source_key(feed.url), feed.url, feed.title, checks_automatically(feed.url))
            for feed in self.subscriptions.feeds)
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

    def _reconcile_new_posts(self, key: str) -> None:
        tags = [p.d_tag for p in self.inbox.page(View("source", key, NEW), limit=200)]
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
    # Imports                                                              #
    # ------------------------------------------------------------------ #

    def _on_job_finished(self, _job_id: str) -> None:
        self._announce()

    def _make_item_job(self, *, items, feed_url, fetch_full_text, rehost_images,
                       skip_image_urls, is_imported, parent):
        store = self._draft_store
        return ImportItemsJob(
            items=items, feed_url=feed_url, profile=self._profile,
            relay_pool=self._relay_pool, relay_directory=self._relay_directory,
            session_pool=self._session_pool,
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


def _manual_id(key: str) -> str:
    return "m-" + key


def _title_known(source: Optional[SourceRow]) -> bool:
    return source is not None and bool(source.title or source.feed_title)
