# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how the updater checks, prepares and swaps in a new version.

What must hold:

  Only a file whose SHA-256 matches the one GitHub published is installed.
  A mismatch, or a release that names no hash, installs nothing.

  A Mac app is only replaced where it can be: not from a translocated or
  read-only location. The swap puts the old app back if the new one cannot
  be moved in, and opens whichever is there.

  A .deb is installed while the window is still open, so a closed password
  prompt is "nothing changed", not an error.

  The helper scripts quote every path: a folder with spaces or quotes in
  its name must not break, or change, what runs.

No network, no process and no file swap happens here: replies and
processes are fakes, and scripts are checked as text.
"""

import hashlib
import os
import shlex
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QNetworkReply  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import updater  # noqa: E402
from update_check import parse_sha256  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class FakeReply:
    """Just enough of QNetworkReply for UpdateInstaller._on_finished."""

    def __init__(self, data: bytes, error=QNetworkReply.NetworkError.NoError):
        self._data = data
        self._error = error

    def error(self):
        return self._error

    def errorString(self):
        return "network down"

    def readAll(self):
        data, self._data = self._data, b""
        return data

    def deleteLater(self):
        pass

    def abort(self):
        pass


def download(tmp_path, payload: bytes, sha256: str, kind=updater.WINDOWS_INSTALLER):
    """Run a download to completion with a fake reply; return (signals, dest)."""
    installer = updater.UpdateInstaller(kind)
    seen = {"ready": [], "failed": []}
    installer.ready.connect(lambda p: seen["ready"].append(p))
    installer.failed.connect(lambda m: seen["failed"].append(m))
    dest = tmp_path / "setup.exe"
    installer._dest = str(dest)
    installer._fh = open(dest, "wb")
    installer._expected_size = len(payload)
    installer._expected_sha256 = sha256
    installer._hash = hashlib.sha256()
    installer._reply = FakeReply(payload)
    installer._on_finished()
    return seen, dest


# -- the published hash ---------------------------------------------------------

def test_github_digests_are_read_as_lowercase_hex():
    hex_ = "AB" * 32
    assert parse_sha256(f"sha256:{hex_}") == hex_.lower()
    for bad in (None, "", "sha256:xyz", "md5:" + "a" * 32, "sha256:" + "a" * 63, 42):
        assert parse_sha256(bad) == ""


def test_a_download_that_matches_its_hash_is_ready(tmp_path):
    payload = b"installer bytes"
    seen, dest = download(tmp_path, payload, hashlib.sha256(payload).hexdigest())
    assert seen == {"ready": [str(dest)], "failed": []}
    assert dest.exists()


def test_a_download_that_does_not_match_is_deleted_and_never_ready(tmp_path):
    seen, dest = download(tmp_path, b"tampered bytes", hashlib.sha256(b"original").hexdigest())
    assert seen["ready"] == []
    assert "damaged or changed" in seen["failed"][0]
    assert not dest.exists()


def test_a_release_without_a_hash_is_never_downloaded():
    installer = updater.UpdateInstaller(updater.WINDOWS_INSTALLER)
    failed = []
    installer.failed.connect(failed.append)
    installer.start(SimpleNamespace(name="setup.exe", url="https://x", size=1, sha256=""))
    assert failed and "couldn't check" in failed[0]
    assert installer._reply is None


# -- where a Mac app can be replaced ---------------------------------------------

def make_bundle(root, name="MyEditor.app"):
    exe = root / name / "Contents" / "MacOS" / "my-editor"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    return str(exe)


def test_the_bundle_is_found_from_the_executable(tmp_path):
    exe = make_bundle(tmp_path)
    assert updater.mac_bundle_path(exe) == os.path.realpath(str(tmp_path / "MyEditor.app"))
    assert updater.mac_bundle_path(str(tmp_path / "python3")) is None


def test_a_writable_bundle_can_update_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    assert updater._mac_bundle_replaceable(make_bundle(tmp_path))


def test_a_translocated_app_cannot_update_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    root = tmp_path / "AppTranslocation" / "ABCD" / "d"
    root.mkdir(parents=True)
    assert not updater._mac_bundle_replaceable(make_bundle(root))


def test_a_read_only_folder_cannot_be_updated_in_place(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    exe = make_bundle(tmp_path)
    monkeypatch.setattr(updater.os, "access", lambda path, mode: False)
    assert not updater._mac_bundle_replaceable(exe)


def test_missing_system_tools_mean_no_in_place_update(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda tool: None)
    assert not updater._mac_bundle_replaceable(make_bundle(tmp_path))


def test_the_staging_copy_sits_hidden_beside_the_app():
    assert updater.mac_staging_path("/Applications/MyEditor.app") == \
        "/Applications/.MyEditor.app.update"


# -- the scripts --------------------------------------------------------------------

AWKWARD = "/Users/Jo O'Neil/Apps & Tools/MyEditor.app"


def test_the_staging_script_mounts_privately_and_checks_the_signature():
    script = updater.mac_stage_script("/tmp/My Editor.dmg", updater.mac_staging_path(AWKWARD))
    assert "-nobrowse" in script and "-readonly" in script
    assert "codesign --verify --deep --strict" in script
    assert shlex.quote("/tmp/My Editor.dmg") in script
    assert shlex.quote(updater.mac_staging_path(AWKWARD)) in script
    for code in updater._MAC_STAGE_ERRORS:
        assert f"exit {code}" in script


def test_the_swap_script_waits_puts_the_old_app_back_on_failure_and_opens_it():
    staged = updater.mac_staging_path(AWKWARD)
    script = updater.mac_swap_script(4242, staged, AWKWARD)
    assert script.startswith('p=4242; while kill -0 "$p"')
    old = shlex.quote(os.path.join(os.path.dirname(AWKWARD), ".MyEditor.app.previous"))
    # Old app out, new app in; if the second move fails, the first is undone.
    assert f"mv -f {shlex.quote(AWKWARD)} {old}" in script
    assert f"else mv -f {old} {shlex.quote(AWKWARD)}" in script
    assert script.rstrip().endswith(f"open {shlex.quote(AWKWARD)}")


def test_the_relaunch_script_waits_for_this_process():
    script = updater.relaunch_script(7, "/opt/my-editor/my-editor")
    assert script.startswith('p=7; while kill -0 "$p"')
    assert script.endswith("exec /opt/my-editor/my-editor")


# -- preparing ------------------------------------------------------------------------

def prepare_with_exit_code(kind, code, tmp_path, monkeypatch, executable=None):
    installer = updater.UpdateInstaller(kind, executable=executable)
    calls = []

    def fake_run(program, args, on_exit):
        calls.append([program, *args])
        on_exit(code)

    monkeypatch.setattr(installer, "_run", fake_run)
    seen = {"prepared": [], "declined": [], "failed": []}
    for name in seen:
        getattr(installer, name).connect(lambda v, n=name: seen[n].append(v))
    downloaded = tmp_path / "update.bin"
    downloaded.write_bytes(b"x")
    installer.prepare(str(downloaded))
    return seen, calls, downloaded


def test_windows_and_appimage_have_nothing_to_prepare(tmp_path, monkeypatch):
    for kind in (updater.WINDOWS_INSTALLER, updater.APPIMAGE):
        seen, calls, path = prepare_with_exit_code(kind, 0, tmp_path, monkeypatch)
        assert seen["prepared"] == [str(path)] and calls == []


def test_the_deb_is_installed_with_a_password_prompt(tmp_path, monkeypatch):
    seen, calls, path = prepare_with_exit_code(
        updater.DEB, 0, tmp_path, monkeypatch, executable="/opt/my-editor/my-editor")
    assert calls == [["pkexec", "apt-get", "install", "-y", str(path)]]
    assert seen["prepared"] == [os.path.realpath("/opt/my-editor/my-editor")]
    assert not path.exists()   # the package file is not left in the temp folder


def test_a_closed_password_prompt_is_declined_not_failed(tmp_path, monkeypatch):
    seen, _, _ = prepare_with_exit_code(updater.DEB, 126, tmp_path, monkeypatch)
    assert seen["failed"] == [] and "Nothing was changed" in seen["declined"][0]


def test_no_password_prompt_at_all_points_to_the_guide(tmp_path, monkeypatch):
    seen, _, _ = prepare_with_exit_code(updater.DEB, 127, tmp_path, monkeypatch)
    assert "update guide" in seen["failed"][0]


def test_a_mac_staging_failure_is_explained(tmp_path, monkeypatch):
    exe = make_bundle(tmp_path)
    seen, calls, _ = prepare_with_exit_code(updater.MACOS_APP, 14, tmp_path, monkeypatch,
                                            executable=exe)
    assert calls[0][:2] == ["sh", "-c"]
    assert "integrity check" in seen["failed"][0]


def test_a_staged_mac_app_is_what_gets_applied(tmp_path, monkeypatch):
    exe = make_bundle(tmp_path)
    seen, _, _ = prepare_with_exit_code(updater.MACOS_APP, 0, tmp_path, monkeypatch,
                                        executable=exe)
    bundle = updater.mac_bundle_path(exe)
    assert seen["prepared"] == [updater.mac_staging_path(bundle)]


def test_calling_off_the_restart_removes_what_was_prepared(tmp_path):
    staged = tmp_path / ".MyEditor.app.update"
    (staged / "Contents").mkdir(parents=True)
    updater.UpdateInstaller(updater.MACOS_APP).discard_prepared(str(staged))
    assert not staged.exists()

    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"x")
    updater.UpdateInstaller(updater.WINDOWS_INSTALLER).discard_prepared(str(setup))
    assert not setup.exists()

    # Never remove a folder that is not a staging copy.
    other = tmp_path / "MyEditor.app"
    other.mkdir()
    updater.UpdateInstaller(updater.MACOS_APP).discard_prepared(str(other))
    assert other.exists()


def test_messages_use_no_em_dashes():
    texts = list(updater._MAC_STAGE_ERRORS.values())
    assert all("\u2014" not in t for t in texts)


def test_downloads_land_in_a_private_folder_that_goes_away_with_them():
    asset = SimpleNamespace(name="my-editor_3.4_amd64.deb")
    dest = updater._download_destination(updater.DEB, asset)
    folder = os.path.dirname(dest)
    try:
        assert os.path.basename(dest) == "my-editor_3.4_amd64.deb"
        assert os.path.basename(folder).startswith("my-editor-update-")
        if os.name == "posix":
            assert os.stat(folder).st_mode & 0o077 == 0   # nobody else can enter
        with open(dest, "wb") as f:
            f.write(b"x")
        updater._discard(dest)
        assert not os.path.exists(folder)
    finally:
        if os.path.isdir(folder):
            os.rmdir(folder)
