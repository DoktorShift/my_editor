"""Thin wrapper over the Telegram Bot HTTP API.

All requests go to ``https://api.telegram.org/bot<TOKEN>/<METHOD>``.
Responses follow Telegram's envelope::

    { "ok": true,  "result": ... }
    { "ok": false, "description": "...", "error_code": N,
                   "parameters": { "retry_after": N }? }

Every ``BotApi`` method returns an :class:`ApiCall` ``QObject`` that
emits ``succeeded(dict)`` with the ``result`` payload, or
``failed(ApiError)`` with the typed error. Callers connect to either
signal; the call is fire-and-forget after ``start()``.

Threading model: ``QNetworkAccessManager`` is single-threaded but
non-blocking. Because every call carries its own ``QNetworkReply`` and
parent ``ApiCall``, multiple in-flight calls are safe as long as they
all originate from the Qt event-loop thread (true for every UI caller
and for the scheduler's timer slot).
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode

from PySide6.QtCore import QByteArray, QObject, QUrl, Qt, Signal
from PySide6.QtNetwork import (
    QHttpMultiPart,
    QHttpPart,
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)

from .errors import ApiError, classify, transport_error


API_HOST = "https://api.telegram.org"

# Conservative upper bound on a single POST. Telegram's documented file
# limit on the public Bot API is 50 MB.
MAX_BODY_BYTES: int = 50 * 1024 * 1024

# Soft per-request timeout. The Bot API itself can hold a long-poll
# open up to its ``timeout`` parameter, so we add a generous margin.
_REQUEST_TIMEOUT_MS: int = 60_000


# ---------------------------------------------------------------------------- #
# ApiCall - one in-flight request                                              #
# ---------------------------------------------------------------------------- #


class ApiCall(QObject):
    """One Bot API request as a Qt-friendly future.

    Signals (exactly one fires, then never again):

      ``succeeded(dict)``  - the JSON ``result`` field on HTTP 2xx + ``ok=true``.
      ``failed(ApiError)`` - any failure: transport, non-2xx, or ``ok=false``.
    """

    succeeded = Signal(dict)
    failed = Signal(object)  # ApiError

    def __init__(self, reply: QNetworkReply, *, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._reply = reply
        self._done = False
        reply.finished.connect(self._on_finished, Qt.QueuedConnection)

    def cancel(self) -> None:
        """Abort the request. ``failed`` fires with a ``Transport`` error."""
        if self._done:
            return
        try:
            self._reply.abort()
        except RuntimeError:
            pass

    # -- internals -- #

    def _on_finished(self) -> None:
        if self._done:
            return
        self._done = True
        reply = self._reply
        try:
            status = int(
                reply.attribute(QNetworkRequest.HttpStatusCodeAttribute) or 0
            )
            raw = bytes(reply.readAll())
            err = reply.error()
        finally:
            reply.deleteLater()

        if err != QNetworkReply.NoError and not raw:
            # Transport-level failure with no body to inspect.
            self.failed.emit(transport_error(reply.errorString() or "network error"))
            return

        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}

        if not isinstance(body, dict):
            self.failed.emit(transport_error("malformed response from Telegram"))
            return

        if status == 0 or not (200 <= status < 300) or body.get("ok") is False:
            self.failed.emit(classify(status or 500, body))
            return

        result = body.get("result")
        if not isinstance(result, (dict, list, bool, int, str)):
            # Some methods (deleteWebhook, sendChatAction) return ``true``.
            result = {}
        if isinstance(result, dict):
            self.succeeded.emit(result)
        else:
            self.succeeded.emit({"result": result})


# ---------------------------------------------------------------------------- #
# BotApi - one client per bot token                                            #
# ---------------------------------------------------------------------------- #


class BotApi(QObject):
    """All Bot API methods used by the editor, scoped to one bot token.

    Held by :class:`publishers.telegram.bots.BotRegistry`; instances
    are reused across calls so the underlying ``QNetworkAccessManager``
    can keep TLS sessions warm.
    """

    def __init__(
        self,
        token: str,
        *,
        nam: Optional[QNetworkAccessManager] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        if not token or ":" not in token:
            raise ValueError("token must look like 12345:ABC...")
        self._token = token
        self._nam = nam or QNetworkAccessManager(self)

    # -- methods ----------------------------------------------------- #

    def get_me(self) -> ApiCall:
        return self._post_form("getMe", {})

    def get_updates(
        self,
        *,
        offset: int = 0,
        timeout: int = 0,
        allowed_updates: Optional[List[str]] = None,
        limit: int = 100,
    ) -> ApiCall:
        params: Dict[str, str] = {
            "offset": str(int(offset)),
            "timeout": str(int(timeout)),
            "limit": str(int(limit)),
        }
        if allowed_updates is not None:
            params["allowed_updates"] = json.dumps(list(allowed_updates))
        return self._post_form("getUpdates", params)

    def delete_webhook(self, *, drop_pending_updates: bool = False) -> ApiCall:
        return self._post_form(
            "deleteWebhook",
            {"drop_pending_updates": "true" if drop_pending_updates else "false"},
        )

    def get_chat(self, chat_id: str) -> ApiCall:
        return self._post_form("getChat", {"chat_id": chat_id})

    def get_chat_member(self, chat_id: str, user_id: int) -> ApiCall:
        return self._post_form(
            "getChatMember",
            {"chat_id": chat_id, "user_id": str(int(user_id))},
        )

    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        parse_mode: str = "HTML",
        message_thread_id: Optional[int] = None,
        reply_to_message_id: Optional[int] = None,
        disable_notification: bool = False,
        protect_content: bool = False,
        disable_link_preview: bool = False,
    ) -> ApiCall:
        params: Dict[str, str] = {
            "chat_id": str(int(chat_id)),
            "text": text,
            "parse_mode": parse_mode,
        }
        if message_thread_id is not None:
            params["message_thread_id"] = str(int(message_thread_id))
        if reply_to_message_id is not None:
            params["reply_parameters"] = json.dumps(
                {"message_id": int(reply_to_message_id), "allow_sending_without_reply": True}
            )
        if disable_notification:
            params["disable_notification"] = "true"
        if protect_content:
            params["protect_content"] = "true"
        if disable_link_preview:
            params["link_preview_options"] = json.dumps({"is_disabled": True})
        return self._post_form("sendMessage", params)

    def send_photo_by_file_id(
        self,
        *,
        chat_id: int,
        file_id: str,
        caption: str = "",
        parse_mode: str = "HTML",
        message_thread_id: Optional[int] = None,
        disable_notification: bool = False,
        protect_content: bool = False,
    ) -> ApiCall:
        params: Dict[str, str] = {
            "chat_id": str(int(chat_id)),
            "photo": file_id,
        }
        if caption:
            params["caption"] = caption
            params["parse_mode"] = parse_mode
        if message_thread_id is not None:
            params["message_thread_id"] = str(int(message_thread_id))
        if disable_notification:
            params["disable_notification"] = "true"
        if protect_content:
            params["protect_content"] = "true"
        return self._post_form("sendPhoto", params)

    def send_photo_by_url(
        self,
        *,
        chat_id: int,
        url: str,
        caption: str = "",
        parse_mode: str = "HTML",
        message_thread_id: Optional[int] = None,
        disable_notification: bool = False,
        protect_content: bool = False,
    ) -> ApiCall:
        params: Dict[str, str] = {
            "chat_id": str(int(chat_id)),
            "photo": url,
        }
        if caption:
            params["caption"] = caption
            params["parse_mode"] = parse_mode
        if message_thread_id is not None:
            params["message_thread_id"] = str(int(message_thread_id))
        if disable_notification:
            params["disable_notification"] = "true"
        if protect_content:
            params["protect_content"] = "true"
        return self._post_form("sendPhoto", params)

    def send_photo_upload(
        self,
        *,
        chat_id: int,
        file_path: str,
        caption: str = "",
        parse_mode: str = "HTML",
        message_thread_id: Optional[int] = None,
        disable_notification: bool = False,
        protect_content: bool = False,
    ) -> ApiCall:
        return self._post_multipart(
            "sendPhoto",
            text_fields={
                "chat_id": str(int(chat_id)),
                **({"caption": caption, "parse_mode": parse_mode} if caption else {}),
                **(
                    {"message_thread_id": str(int(message_thread_id))}
                    if message_thread_id is not None
                    else {}
                ),
                **({"disable_notification": "true"} if disable_notification else {}),
                **({"protect_content": "true"} if protect_content else {}),
            },
            file_field="photo",
            file_path=file_path,
        )

    def pin_chat_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        disable_notification: bool = True,
    ) -> ApiCall:
        return self._post_form(
            "pinChatMessage",
            {
                "chat_id": str(int(chat_id)),
                "message_id": str(int(message_id)),
                "disable_notification": "true" if disable_notification else "false",
            },
        )

    # -- internals --------------------------------------------------- #

    def _endpoint(self, method: str) -> QUrl:
        return QUrl(f"{API_HOST}/bot{self._token}/{method}")

    def _build_request(self, method: str) -> QNetworkRequest:
        req = QNetworkRequest(self._endpoint(method))
        req.setTransferTimeout(_REQUEST_TIMEOUT_MS)
        req.setRawHeader(b"User-Agent", b"my-editor (Telegram publisher)")
        return req

    def _post_form(self, method: str, params: Dict[str, str]) -> ApiCall:
        req = self._build_request(method)
        req.setHeader(
            QNetworkRequest.ContentTypeHeader,
            "application/x-www-form-urlencoded",
        )
        body = urlencode(params).encode("utf-8")
        reply = self._nam.post(req, body)
        return ApiCall(reply, parent=self)

    def _post_multipart(
        self,
        method: str,
        *,
        text_fields: Dict[str, str],
        file_field: str,
        file_path: str,
    ) -> ApiCall:
        # Read the file into memory once. Telegram caps uploads at
        # 50 MB on the public server; that fits comfortably in RAM and
        # makes life easier than streaming with QFile and lifetime
        # management against Qt's multipart ownership rules.
        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except OSError as exc:
            # Synthesize an ApiCall that fires ``failed`` on the next
            # event-loop tick so callers can connect signals first.
            return _PreFailedCall(transport_error(f"cannot read {file_path}: {exc}"))

        if len(data) > MAX_BODY_BYTES:
            return _PreFailedCall(
                ApiError(
                    f"file is {len(data):,} bytes; max is {MAX_BODY_BYTES:,}",
                    status=413,
                )
            )

        multipart = QHttpMultiPart(QHttpMultiPart.FormDataType)
        for name, value in text_fields.items():
            part = QHttpPart()
            part.setHeader(
                QNetworkRequest.ContentDispositionHeader,
                f'form-data; name="{name}"',
            )
            part.setBody(value.encode("utf-8"))
            multipart.append(part)

        file_part = QHttpPart()
        # We send the file under a generic name; Telegram only cares
        # about the bytes for sendPhoto / sendDocument.
        filename = file_path.rsplit("/", 1)[-1] or "upload"
        file_part.setHeader(
            QNetworkRequest.ContentDispositionHeader,
            f'form-data; name="{file_field}"; filename="{filename}"',
        )
        file_part.setBody(QByteArray(data))
        multipart.append(file_part)

        req = self._build_request(method)
        reply = self._nam.post(req, multipart)
        multipart.setParent(reply)  # tie lifetime to the reply
        return ApiCall(reply, parent=self)


# ---------------------------------------------------------------------------- #
# Helper: synthesize a pre-failed ApiCall                                       #
# ---------------------------------------------------------------------------- #


class _PreFailedCall(ApiCall):
    """An ``ApiCall``-shaped object that fires ``failed`` on the next tick.

    Used when the wrapper can rule out the request before it's even
    sent (e.g. unreadable file) so the caller's signal-handling code
    stays uniform.
    """

    def __init__(self, error: ApiError) -> None:
        # We deliberately do NOT call super().__init__ - no QNetworkReply
        # exists. We mimic the signals and queue the failure.
        QObject.__init__(self)
        self._done = False
        self._error = error
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._fire)

    def cancel(self) -> None:
        self._done = True

    def _fire(self) -> None:
        if self._done:
            return
        self._done = True
        self.failed.emit(self._error)
        # No QNetworkReply to ride on for lifetime; clean ourselves up.
        self.deleteLater()


# ---------------------------------------------------------------------------- #
# Logging redaction helper (used by any future logger)                          #
# ---------------------------------------------------------------------------- #


def redact(url: str) -> str:
    """Replace ``/bot<TOKEN>/`` with ``/bot<REDACTED>/`` in a URL string."""
    marker = "/bot"
    idx = url.find(marker)
    if idx < 0:
        return url
    end = url.find("/", idx + len(marker))
    if end < 0:
        end = len(url)
    return url[: idx + len(marker)] + "REDACTED" + url[end:]
