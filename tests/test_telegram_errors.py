"""Unit tests for the Telegram error taxonomy."""

from __future__ import annotations

import pytest

from publishers.telegram.errors import (
    ApiError,
    BotRemoved,
    ChatNotFound,
    ConcurrentPoller,
    InvalidToken,
    NoPostRights,
    RateLimited,
    StaleFileId,
    UserBlockedBot,
    UserNeverStarted,
    WebhookActive,
    classify,
    transport_error,
)


@pytest.mark.parametrize("status,desc,kind", [
    (401, "Unauthorized", InvalidToken),
    (403, "Forbidden: bot was blocked by the user", UserBlockedBot),
    (403, "Forbidden: bot can't initiate conversation with a user", UserNeverStarted),
    (403, "Forbidden: bot was kicked from the supergroup chat", BotRemoved),
    (403, "Forbidden: bot is not a member of the channel chat", BotRemoved),
    (400, "Bad Request: not enough rights to send text messages to the chat",
     NoPostRights),
    (400, "Bad Request: wrong file_id specified", StaleFileId),
    (400, "Bad Request: chat not found", ChatNotFound),
    (404, "not found", ChatNotFound),
    (409, "Conflict: can't use getUpdates method while webhook is active",
     WebhookActive),
    (409, "Conflict: terminated by other getUpdates request", ConcurrentPoller),
])
def test_classify_maps_known_descriptions(status, desc, kind):
    err = classify(status, {"description": desc})
    assert isinstance(err, kind), f"expected {kind.__name__}, got {type(err).__name__}"
    assert err.status == status


def test_classify_rate_limited_extracts_retry_after():
    err = classify(429, {
        "description": "Too Many Requests: retry after 7",
        "parameters": {"retry_after": 7},
    })
    assert isinstance(err, RateLimited)
    assert err.retry_after == 7


def test_classify_unknown_falls_back_to_generic_apierror():
    err = classify(500, {"description": "Internal Server Error"})
    assert isinstance(err, ApiError)
    assert err.status == 500
    assert "Internal" in err.description


def test_transport_error_has_zero_status_and_default_message():
    err = transport_error("")
    assert err.status == 0
    assert err.user_message  # never empty


def test_rate_limited_clamps_retry_after_to_minimum_one():
    err = classify(429, {"description": "Too Many", "parameters": {"retry_after": 0}})
    assert isinstance(err, RateLimited)
    assert err.retry_after >= 1


def test_user_messages_are_action_oriented():
    # Spot-check a few specific messages: should not contain the raw
    # Telegram description ("Forbidden: ..."), since the UI shows
    # user_message and not description.
    for cls in (InvalidToken, UserBlockedBot, UserNeverStarted, BotRemoved,
                NoPostRights, WebhookActive):
        err = cls("Forbidden: irrelevant")
        msg = err.user_message
        assert msg, f"{cls.__name__} returned empty user_message"
        assert "Forbidden" not in msg, (
            f"{cls.__name__} leaked raw description into user_message"
        )
