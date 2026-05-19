"""Build, sign, and publish engagement events.

Three write paths, all routed through the editor's existing
``BunkerSessionPool`` so no private key ever leaves the signer:

  - ``rate(...)``: kind:1985 NIP-32 rating
  - ``comment(...)``: kind:1111 NIP-22 top-level comment or reply
  - ``delete(...)``: kind:5 NIP-09 deletion of one of the user's own
    earlier events

Each call returns a ``PublishingJob`` whose signals (``signed``,
``failed``, ``published``) the UI listens to so it can render an
optimistic "publishing..." indicator and flip it to "published" or
"failed" without re-rendering the whole panel.

The publisher never decides whether the user is allowed to publish.
That gate lives in the UI (composer hidden for anonymous users).
This module trusts that ``profile`` is connected before it is called.
"""

from __future__ import annotations

import time
from typing import Iterable, List, Optional

from PySide6.QtCore import QObject, Signal

from nostr import CLIENT_NAME, DEFAULT_RELAYS
from nostr.bunker import BunkerClient, BunkerSessionPool
from nostr.events import build_event
from nostr.relay import RelayPool

from ._seeds import seed_relays
from .cache import EngagementCache
from .models import PluginAnchor
from .outbox_router import OutboxRouter
from .parser import (
    NIP09_DELETION_KIND,
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    PLUGIN_LISTING_KIND,
    RATING_LABEL_NAMESPACE,
)


# Generous so the signer has time to prompt the user on their phone
# (Amber, nsec.app, etc.). The bunker's own RPC timeout is shorter
# and the publisher's UI surfaces the failure cleanly when that fires.
DEFAULT_PUBLISH_TIMEOUT_MS: int = 15_000


# ──────────────────────────────────────────────────────────────────────
# Public job objects
# ──────────────────────────────────────────────────────────────────────

class PublishingJob(QObject):
    """One in-flight publish, exposing per-step signals to the UI.

    Lifecycle: ``signing -> signed -> publishing -> published``. The
    UI uses ``signed`` to update the cache optimistically and
    ``published`` (with at-least-one relay accept) as the final
    "ok, it's out there" signal. ``failed`` may fire at any step
    with a human-readable reason.
    """

    signing = Signal()
    signed = Signal(dict)        # the fully-signed event
    publishing = Signal()
    published = Signal(dict, list)   # (event, list[(url, ok, msg)])
    failed = Signal(str)


# ──────────────────────────────────────────────────────────────────────
# Publisher
# ──────────────────────────────────────────────────────────────────────

class EngagementPublisher(QObject):
    """Top-level coordinator for engagement writes.

    Holds references to the relay pool, the bunker pool, and the
    engagement cache so individual call sites stay declarative.
    """

    def __init__(
        self,
        *,
        relay_pool: RelayPool,
        bunker_pool: BunkerSessionPool,
        cache: EngagementCache,
        extra_relays: Iterable[str] = (),
        outbox_router: Optional[OutboxRouter] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._relay_pool = relay_pool
        self._bunker_pool = bunker_pool
        self._cache = cache
        self._extra_relays: tuple[str, ...] = tuple(extra_relays)
        self._router = outbox_router

    # ----------------------------------------------------------------------
    # Public write methods
    # ----------------------------------------------------------------------

    def rate(
        self,
        *,
        profile,            # nostr.profiles.Profile
        anchor: PluginAnchor,
        stars: int,
        content: str = "",
        relay_hint: str = "",
    ) -> PublishingJob:
        """Publish a kind:1985 rating tagged at ``anchor``.

        Per NIP-32 the rating revises any previous rating from the
        same author; the aggregator's last-write-wins dedup picks the
        newest. Callers don't need to delete a previous rating before
        re-rating, but they may delete it explicitly with ``delete``.
        """
        if not 1 <= stars <= 5:
            raise ValueError(f"stars must be 1..5, got {stars}")
        tags = [
            ["L", RATING_LABEL_NAMESPACE],
            ["l", str(stars), RATING_LABEL_NAMESPACE],
            ["a", anchor.coord, relay_hint],
            ["p", anchor.author_pubkey, relay_hint],
            ["client", CLIENT_NAME],
        ]
        return self._publish(
            profile=profile, anchor=anchor,
            kind=NIP32_LABEL_KIND, content=content, tags=tags,
        )

    def comment(
        self,
        *,
        profile,
        anchor: PluginAnchor,
        body: str,
        parent_event_id: Optional[str] = None,
        parent_author_pubkey: Optional[str] = None,
        relay_hint: str = "",
    ) -> PublishingJob:
        """Publish a kind:1111 comment or reply.

        Top-level comments leave ``parent_event_id`` as ``None``;
        the root and parent tags then both point at the plugin
        anchor (per NIP-22 §"Replies to addressable events").

        Replies set ``parent_event_id`` to the parent comment's id
        and ``parent_author_pubkey`` to its author. The uppercase
        root tags stay on the plugin anchor so the whole tree is
        recoverable from one filter.
        """
        body = body.strip()
        if not body:
            raise ValueError("comment body must not be empty")

        tags: List[List[str]] = [
            ["A", anchor.coord, relay_hint],
            ["K", str(PLUGIN_LISTING_KIND)],
            ["P", anchor.author_pubkey, relay_hint],
        ]
        if parent_event_id is None:
            # Top-level: lowercase parent mirrors the root.
            tags.extend([
                ["a", anchor.coord, relay_hint],
                ["k", str(PLUGIN_LISTING_KIND)],
                ["p", anchor.author_pubkey, relay_hint],
            ])
        else:
            # Reply: lowercase parent points at the parent comment.
            tags.extend([
                ["e", parent_event_id, relay_hint, parent_author_pubkey or ""],
                ["k", str(NIP22_COMMENT_KIND)],
                ["p", parent_author_pubkey or "", relay_hint],
            ])
        tags.append(["client", CLIENT_NAME])
        return self._publish(
            profile=profile, anchor=anchor,
            kind=NIP22_COMMENT_KIND, content=body, tags=tags,
        )

    def delete(
        self,
        *,
        profile,
        anchor: PluginAnchor,
        event_id: Optional[str] = None,
        target_kind: int = NIP32_LABEL_KIND,
        target_coord: Optional[str] = None,
        reason: str = "",
    ) -> PublishingJob:
        """Publish a kind:5 deletion request per NIP-09.

        At least one of ``event_id`` or ``target_coord`` must be set;
        the spec lets a client name either a specific event-id (``e``
        tag) or an addressable coord (``a`` tag), or both for
        belt-and-suspenders on a replaceable.

        ``target_kind`` is the kind of the event being deleted. It is
        emitted in an advisory ``["k", "<kind>"]`` tag so relays can
        index the deletion correctly. Each unique target kind generates
        one ``k`` tag.

        The aggregator only honors the deletion when its author matches
        the target event's author; signing through ``BunkerClient``
        guarantees that pubkey alignment.
        """
        if not event_id and not target_coord:
            raise ValueError("delete() requires event_id or target_coord")
        if target_kind < 0:
            raise ValueError(f"target_kind must be a valid Nostr kind, got {target_kind}")
        tags: List[List[str]] = []
        if event_id:
            tags.append(["e", event_id])
        if target_coord:
            tags.append(["a", target_coord])
        # NIP-09: one ``k`` per distinct kind being deleted. Emit once
        # since a single ``delete()`` call addresses one target.
        tags.append(["k", str(target_kind)])
        tags.append(["client", CLIENT_NAME])
        return self._publish(
            profile=profile, anchor=anchor,
            kind=NIP09_DELETION_KIND, content=reason, tags=tags,
        )

    # ----------------------------------------------------------------------
    # Plugin listing (kind:30700) author tools
    # ----------------------------------------------------------------------

    def publish_listing(
        self,
        *,
        profile,
        anchor: PluginAnchor,
        name: str,
        description: str = "",
        homepage: str = "",
        download_url: str = "",
        sha256: str = "",
        version: str = "",
        license: str = "",
        topics: Iterable[str] = (),
        lightning_address: str = "",
        price_sats: int = 0,
        screenshots: Iterable[str] = (),
        long_description: str = "",
    ) -> PublishingJob:
        """Publish (or replace) the addressable kind:30700 plugin listing.

        NIP-01 addressable: the ``d`` tag carries the ``plugin_id``
        from ``anchor``, so re-publishing with the same anchor produces
        a new version of the same listing and supersedes the old one
        on relays that honour replaceable semantics.

        Tag layout — kept stable so future versions of the marketplace
        can read older listings without a parser fork:

          ``["d", "<plugin_id>"]``                          required
          ``["name", "<name>"]``                            human title
          ``["version", "<x.y.z>"]``
          ``["description", "<short>"]``                    one-liner
          ``["summary", "<long>"]``                         long-form
          ``["homepage", "<url>"]``
          ``["t", "<topic>"]``                              repeated
          ``["image", "<url>"]``                            repeated
          ``["license", "<spdx>"]``
          ``["download", "<url>", "<sha256>"]``
          ``["zap", "<lud-16>"]``
          ``["price", "<sats>"]``                           when not free
          ``["client", "<CLIENT_NAME>"]``
        """
        if anchor.kind != PLUGIN_LISTING_KIND:
            raise ValueError(
                f"publish_listing expects a kind:{PLUGIN_LISTING_KIND} anchor, got {anchor.kind}"
            )
        if not anchor.plugin_id or not anchor.plugin_id.strip():
            raise ValueError("publish_listing requires anchor.plugin_id")
        if ":" in anchor.plugin_id:
            # Colons in the d-tag break the kind:pubkey:d-tag coord
            # split downstream; refuse rather than let it through.
            raise ValueError("anchor.plugin_id may not contain ':'")
        if not name or not name.strip():
            raise ValueError("publish_listing requires a non-empty name")
        # Author check: the publisher signs through the active bunker,
        # so the runtime pubkey will be the signer's. If the caller
        # supplied an anchor whose author_pubkey doesn't match the
        # profile, refuse — the relay would reject the signature, but
        # we surface the mistake before the bunker prompts the user.
        active_pubkey = getattr(profile, "user_pubkey", "")
        if active_pubkey and anchor.author_pubkey.lower() != active_pubkey.lower():
            raise ValueError(
                "publish_listing: anchor.author_pubkey must match the active profile"
            )
        listing_tags: List[List[str]] = [
            ["d", anchor.plugin_id],
            ["name", name.strip()],
        ]
        if version:
            listing_tags.append(["version", version.strip()])
        if description:
            listing_tags.append(["description", description.strip()])
        if long_description:
            listing_tags.append(["summary", long_description.strip()])
        if homepage:
            listing_tags.append(["homepage", homepage.strip()])
        for topic in topics or ():
            if isinstance(topic, str) and topic.strip():
                listing_tags.append(["t", topic.strip()])
        if license:
            listing_tags.append(["license", license.strip()])
        if download_url and sha256:
            listing_tags.append(["download", download_url.strip(), sha256.strip()])
        if lightning_address:
            listing_tags.append(["zap", lightning_address.strip()])
        if price_sats and price_sats > 0:
            listing_tags.append(["price", str(int(price_sats))])
        for url in screenshots or ():
            if isinstance(url, str) and url.strip():
                listing_tags.append(["image", url.strip()])
        listing_tags.append(["client", CLIENT_NAME])
        return self._publish(
            profile=profile, anchor=anchor,
            kind=PLUGIN_LISTING_KIND, content="", tags=listing_tags,
        )

    def delete_listing(
        self,
        *,
        profile,
        anchor: PluginAnchor,
        reason: str = "",
    ) -> PublishingJob:
        """Tombstone the plugin's kind:30700 listing via NIP-09.

        Emits both an ``a`` tag (addressable coord) and a ``k`` tag
        (advisory) so every relay can correctly retire all versions of
        the listing. Use this when the author wants to take down the
        plugin entirely; for a content rewrite, ``publish_listing`` is
        the right call.
        """
        return self.delete(
            profile=profile, anchor=anchor,
            target_coord=anchor.coord, target_kind=PLUGIN_LISTING_KIND,
            reason=reason,
        )

    # ----------------------------------------------------------------------
    # Common pipeline
    # ----------------------------------------------------------------------

    def _publish(
        self,
        *,
        profile,
        anchor: PluginAnchor,
        kind: int,
        content: str,
        tags: List[List[str]],
    ) -> PublishingJob:
        """Resolve signer -> sign -> publish on relays.

        Returns the job synchronously so the UI can connect its
        signals before any of them fire. The actual work happens on
        the Qt event loop.

        A hard timeout (``DEFAULT_PUBLISH_TIMEOUT_MS``) covers the
        case where the bunker connects but never returns a signature
        — e.g. the user dismissed the prompt or the signer is wedged.
        Without this guard the composer would stay disabled forever.
        """
        job = PublishingJob(self)
        unsigned = build_event(
            pubkey_hex=profile.user_pubkey,
            kind=kind,
            content=content,
            tags=tags,
            created_at=int(time.time()),
        )
        job.signing.emit()

        # Single-shot completion latch shared by the success, failure,
        # and timeout paths so the composer is only un-stuck once.
        completed = {"value": False}

        def _complete_failure(reason: str) -> None:
            if completed["value"]:
                return
            completed["value"] = True
            job.failed.emit(reason)

        def _complete_success_signed(signed: dict) -> None:
            if completed["value"]:
                return
            # Don't latch on signed — the publish step still has to
            # run. We only block subsequent *failure* paths to keep
            # the lifecycle linear.
            job.signed.emit(signed)
            self._cache.upsert_event(anchor=anchor, event=signed)
            self._publish_signed(job, anchor, signed)
            # Wire the publish job's all_done into the latch so a
            # late failure during broadcast can still mark the job.
            completed["value"] = True

        from PySide6.QtCore import QTimer as _QTimer
        timer = _QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(DEFAULT_PUBLISH_TIMEOUT_MS)
        timer.timeout.connect(
            lambda: _complete_failure("signer timed out — try again")
        )
        timer.start()

        def _on_signer_ready(client: BunkerClient) -> None:
            def _on_signed(signed: dict) -> None:
                timer.stop()
                _complete_success_signed(signed)

            def _on_sign_failed(reason: str) -> None:
                timer.stop()
                _complete_failure(f"signer rejected: {reason}")

            client.sign_event(
                unsigned, on_success=_on_signed, on_failure=_on_sign_failed,
            )

        def _on_signer_failed(reason: str) -> None:
            timer.stop()
            _complete_failure(f"signer unreachable: {reason}")

        self._bunker_pool.get(profile, _on_signer_ready, _on_signer_failed)
        return job

    def _publish_signed(
        self,
        job: PublishingJob,
        anchor: PluginAnchor,
        signed: dict,
    ) -> None:
        urls = self._relays_for(anchor, signed)
        if not urls:
            job.failed.emit("no relays available")
            return
        job.publishing.emit()
        publish = self._relay_pool.publish(urls, signed)
        # ``PublishJob`` is from ``nostr.relay``; it exposes
        # ``first_accept`` and ``all_done``. We treat ``all_done`` as
        # the final state so the UI can decide whether any relay said
        # OK before flipping its indicator.
        publish.all_done.connect(lambda results, e=signed: job.published.emit(e, results))


# --------------------------------------------------------------------------- #
# Relay selection                                                             #
# --------------------------------------------------------------------------- #

    def _relays_for(self, anchor: PluginAnchor, signed: dict) -> List[str]:
        """Pick the write relay set per NIP-65 outbox.

        With a router configured: the viewer's write relays unioned
        with each ``p``-tagged target's read relays, plus the seed
        set as a backstop. Without a router: the legacy union of
        extras + DEFAULT_RELAYS so older call sites keep working.
        """
        seeds = self._seed_relays()
        if self._router is None:
            return seeds
        viewer_pubkey = signed.get("pubkey")
        targets = self._p_tag_pubkeys(signed)
        # Always include the plugin author in the write fan-out so the
        # author's read relays see the event even when they're not
        # explicitly p-tagged (e.g. some clients omit p on top-level
        # comments of an addressable parent).
        if anchor.author_pubkey:
            targets.append(anchor.author_pubkey)
        plan = self._router.relays_for_write(
            viewer_pubkey, targets, seeds=seeds,
        )
        return plan.relays

    def _seed_relays(self) -> List[str]:
        return seed_relays(self._extra_relays)

    @staticmethod
    def _p_tag_pubkeys(event: dict) -> List[str]:
        """Extract every ``p`` tag pubkey from a signed event.

        Used to compute the inbox relays we should fan out to so the
        people mentioned in this event actually receive it.
        """
        out: List[str] = []
        seen: set[str] = set()
        for tag in event.get("tags", []):
            if not isinstance(tag, list) or len(tag) < 2 or tag[0] != "p":
                continue
            pubkey = (tag[1] or "").lower()
            if len(pubkey) != 64 or pubkey in seen:
                continue
            seen.add(pubkey)
            out.append(pubkey)
        return out
