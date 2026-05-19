"""Nostr-powered social layer for the plugin marketplace.

Anchored on each plugin's ``kind:30700`` Nostr listing event, this
package implements:

  - Read-only aggregation of ratings (NIP-32), comments (NIP-22),
    zaps (NIP-57). Anonymous users see the same trust signals
    everyone else does.
  - A typed write path that signs ratings, comments, replies, and
    deletions via the existing NIP-46 bunker pool. Requires a
    connected profile.
  - A trust model based on NIP-05 verification, NIP-02 follow
    graphs, and NIP-51 mute lists. Defaults err on the safe side:
    NIP-05 verified contributors are surfaced first; muted accounts
    never appear.

This sub-package never imports from Qt directly. The UI in
``plugin_marketplace/ui/social_panel.py`` renders what these
modules produce.
"""

from .models import (
    PluginAnchor,
    Stars,
    RatingEvent,
    CommentEvent,
    ZapReceipt,
    DeletionRequest,
    MuteList,
    ContactList,
    Nip05Verification,
    RatingDistribution,
    CommentNode,
    EngagementSnapshot,
)
from .parser import (
    RATING_LABEL_NAMESPACE,
    PLUGIN_LISTING_KIND,
    NIP22_COMMENT_KIND,
    NIP32_LABEL_KIND,
    NIP57_ZAP_RECEIPT_KIND,
    NIP09_DELETION_KIND,
    parse_rating,
    parse_comment,
    parse_zap_receipt,
    parse_deletion,
    parse_mute_list,
    parse_contact_list,
)
from .aggregator import EngagementAggregator, TrustPolicy
from .author_reviews_fetcher import (
    AuthorReview,
    AuthorReviewSet,
    AuthorReviewsFetcher,
)
from .cache import EngagementCache
from .fetcher import EngagementFetcher
from .follow_trust import FollowTrustCache
from .mute_list_cache import MuteListCache
from .outbox_router import OutboxRouter, RoutingPlan
from .lnurl import (
    InvoiceWithVerify,
    LnurlError,
    LnurlPayData,
    VerifyResult,
    build_zap_request_tags,
    check_invoice_settled,
    fetch_invoice,
    fetch_lnurl_pay_data,
    fetch_paid_invoice,
    lightning_address_to_lnurl_endpoint,
)
from .nip05_verifier import Nip05Verifier
from .profile_fetcher import CommenterProfileFetcher
from .publisher import EngagementPublisher, PublishingJob

__all__ = [
    # models
    "PluginAnchor",
    "Stars",
    "RatingEvent",
    "CommentEvent",
    "ZapReceipt",
    "DeletionRequest",
    "MuteList",
    "ContactList",
    "Nip05Verification",
    "RatingDistribution",
    "CommentNode",
    "EngagementSnapshot",
    # parser
    "RATING_LABEL_NAMESPACE",
    "PLUGIN_LISTING_KIND",
    "NIP22_COMMENT_KIND",
    "NIP32_LABEL_KIND",
    "NIP57_ZAP_RECEIPT_KIND",
    "NIP09_DELETION_KIND",
    "parse_rating",
    "parse_comment",
    "parse_zap_receipt",
    "parse_deletion",
    "parse_mute_list",
    "parse_contact_list",
    # aggregator
    "EngagementAggregator",
    "TrustPolicy",
    # author cross-plugin review fetch
    "AuthorReview",
    "AuthorReviewSet",
    "AuthorReviewsFetcher",
    # cache
    "EngagementCache",
    # outbox routing (NIP-65)
    "OutboxRouter",
    "RoutingPlan",
    # mute lists (NIP-51)
    "MuteListCache",
    # NIP-02 follow trust (owner-keyed)
    "FollowTrustCache",
    # fetcher / verifier / publisher
    "EngagementFetcher",
    "Nip05Verifier",
    "CommenterProfileFetcher",
    "EngagementPublisher",
    "PublishingJob",
    # lnurl + zap
    "LnurlError",
    "LnurlPayData",
    "InvoiceWithVerify",
    "VerifyResult",
    "build_zap_request_tags",
    "check_invoice_settled",
    "fetch_invoice",
    "fetch_lnurl_pay_data",
    "fetch_paid_invoice",
    "lightning_address_to_lnurl_endpoint",
]
