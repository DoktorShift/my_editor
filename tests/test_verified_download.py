# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins downloading from an official source (verified_download.py).

What must hold: only HTTPS on the hosts given (a dotted entry allows its
subdomains, a look-alike domain never), and a redirect elsewhere is
refused; a file that does not match its pinned SHA-256, or a download
stopped, leaves nothing behind; a good one lands whole. No test touches
the network: urlopen is replaced.
"""

import hashlib
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import verified_download as vd  # noqa: E402

HOSTS = ("huggingface.co", ".hf.co")


class Response:
    def __init__(self, url, payload):
        self._url, self._payload, self.headers = url, payload, {"Content-Length": len(payload)}

    def geturl(self):
        return self._url

    def read(self, size):
        chunk, self._payload = self._payload[:size], self._payload[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def serve(monkeypatch, payload, landing=None):
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=0: Response(
        landing or request.full_url, payload))


def test_only_https_on_the_hosts_given():
    assert vd.host_allowed("https://huggingface.co/x", HOSTS)
    assert vd.host_allowed("https://cdn-lfs.hf.co/x", HOSTS)
    assert not vd.host_allowed("http://huggingface.co/x", HOSTS)
    assert not vd.host_allowed("https://huggingface.co.evil.example/x", HOSTS)
    assert not vd.host_allowed("https://evilhf.co/x", HOSTS)


def test_a_good_file_lands_whole_and_a_bad_one_leaves_nothing(tmp_path, monkeypatch):
    payload = b"model bytes" * 1000
    serve(monkeypatch, payload)
    dest = str(tmp_path / "model.onnx")
    seen = []
    vd.download("https://huggingface.co/m", dest, hosts=HOSTS,
                sha256=hashlib.sha256(payload).hexdigest(), progress=lambda d, t: seen.append(d))
    assert open(dest, "rb").read() == payload and seen[-1] == len(payload)
    with pytest.raises(vd.ChecksumMismatch):
        vd.download("https://huggingface.co/m", dest + "2", hosts=HOSTS, sha256="0" * 64)
    assert os.listdir(tmp_path) == ["model.onnx"]


def test_a_redirect_elsewhere_or_a_stop_leaves_nothing(tmp_path, monkeypatch):
    serve(monkeypatch, b"x" * 10, landing="https://elsewhere.example/m")
    with pytest.raises(vd.NotOfficial):
        vd.download("https://huggingface.co/m", str(tmp_path / "m"), hosts=HOSTS)
    serve(monkeypatch, b"x" * 10)

    class Stopped:
        def is_set(self):
            return True
    with pytest.raises(vd.Cancelled):
        vd.download("https://huggingface.co/m", str(tmp_path / "m"), hosts=HOSTS,
                    cancel=Stopped())
    assert os.listdir(tmp_path) == []
