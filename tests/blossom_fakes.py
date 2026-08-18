# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared network fakes for the Blossom and loader test-suites.

A fake QNetworkAccessManager plus a fake reply, both settling only when
a test says so, so request attributes stay assertable and no test opens
a socket. Modelled on ``tests/imports_fakes.py``.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest


PUBKEY = "ab" * 32

SERVER = "https://good.example"
SHA = "c" * 64


def auth_event(server: str = SERVER, action: str = "upload") -> dict:
    """A signed-looking kind 24242 event scoped to ``server``."""
    return {
        "id": "ff" * 32,
        "pubkey": PUBKEY,
        "kind": 24242,
        "created_at": 1,
        "content": f"Authorize {action}",
        "tags": [["t", action], ["expiration", "9999999999"],
                 ["server", server]],
        "sig": "aa" * 64,
    }


SIGNED_AUTH_EVENT = auth_event()


class FakeReply(QObject):
    """Reply stand-in. Nothing happens until a test calls ``finish``."""

    finished = Signal()
    uploadProgress = Signal(int, int)
    downloadProgress = Signal(int, int)

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes = b"",
        error=QNetworkReply.NoError,
        error_string: str = "",
        attributes=None,
        url: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._status = status
        self._body = body
        self._error = error
        self._error_string = error_string
        self._attributes = dict(attributes or {})
        self._url = url
        self._request = None
        self.aborted = False
        self.deleted = False

    # -- QNetworkReply surface --------------------------------------------

    def error(self):
        return self._error

    def errorString(self) -> str:
        return self._error_string

    def attribute(self, attr):
        if attr == QNetworkRequest.HttpStatusCodeAttribute:
            return self._status
        return self._attributes.get(attr)

    def header(self, _header):
        return None

    def readAll(self) -> QByteArray:
        return QByteArray(self._body)

    def url(self) -> QUrl:
        if self._url:
            return QUrl(self._url)
        if self._request is not None:
            return self._request.url()
        return QUrl()

    def request(self):
        return self._request

    def abort(self) -> None:
        self.aborted = True
        self._error = QNetworkReply.OperationCanceledError

    def deleteLater(self) -> None:
        # Overridden so a fake never gets scheduled for real deletion
        # while a test still holds it.
        self.deleted = True

    # -- test driving ------------------------------------------------------

    def set_request(self, request) -> None:
        self._request = request

    def finish(self) -> None:
        self.finished.emit()

    def progress(self, received: int, total: int = -1) -> None:
        self.downloadProgress.emit(received, total)


class FakeNam(QObject):
    """Records every request and hands out scripted replies."""

    def __init__(self, replies=None, parent=None) -> None:
        super().__init__(parent)
        self.calls = []      # (verb, QNetworkRequest, body bytes or None)
        self.issued = []     # FakeReply objects, in order
        self._scripted = list(replies or [])

    def _issue(self, verb, request, body=None) -> FakeReply:
        self.calls.append((verb, request, body))
        reply = self._scripted.pop(0) if self._scripted else FakeReply()
        reply.set_request(request)
        self.issued.append(reply)
        return reply

    def get(self, request) -> FakeReply:
        return self._issue("get", request)

    def put(self, request, data) -> FakeReply:
        return self._issue("put", request, bytes(data))

    def post(self, request, data) -> FakeReply:
        return self._issue("post", request, bytes(data))

    def deleteResource(self, request) -> FakeReply:
        return self._issue("delete", request)


def redirect_reply(location: str = "https://evil.example/x") -> FakeReply:
    """A 302 the way Qt reports one under ManualRedirectPolicy: no
    transport error, a 3xx status, and the redirection target set."""
    return FakeReply(
        status=302,
        attributes={
            QNetworkRequest.Attribute.RedirectionTargetAttribute: QUrl(location),
        },
    )


def collect(store: list):
    """Callback that appends whatever it is handed."""
    return lambda value=None: store.append(value)
