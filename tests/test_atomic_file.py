# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The app's own files are written in one step and read without surprises."""

import json
import logging
import os
import stat
import sys

import pytest

import atomic_file
from atomic_file import read_json, write_bytes, write_json, write_text


def leftovers(folder):
    return sorted(name for name in os.listdir(folder) if name.endswith(".tmp"))


# -- writing ---------------------------------------------------------------------

def test_a_written_file_reads_back(tmp_path):
    path = tmp_path / "settings.json"
    assert write_json(path, {"theme": "dark", "fonts": ["Inter"]}, indent=2)
    assert read_json(path, dict) == {"theme": "dark", "fonts": ["Inter"]}
    assert leftovers(tmp_path) == []


def test_an_existing_file_is_replaced_whole(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"long": "x" * 1000})
    write_json(path, {"short": 1})
    assert path.read_text(encoding="utf-8") == '{"short": 1}'


def test_missing_folders_are_created(tmp_path):
    path = tmp_path / "a" / "b" / "c.json"
    assert write_json(path, [1, 2])
    assert read_json(path, list) == [1, 2]


def test_line_endings_are_the_same_on_every_system(tmp_path):
    path = tmp_path / "notes.txt"
    write_text(path, "one\ntwo\n")
    assert path.read_bytes() == b"one\ntwo\n"


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows has no owner-only file mode bits")
def test_the_file_is_readable_by_this_account_only(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text("{}")
    os.chmod(path, 0o644)
    write_bytes(path, b"{}")
    assert stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0


def test_a_failed_replace_keeps_the_old_file_and_leaves_nothing_behind(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    write_json(path, {"keep": True})

    def refuse(source, target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(atomic_file.os, "replace", refuse)
    with pytest.raises(OSError):
        write_bytes(path, b'{"keep": false}')
    assert read_json(path, dict) == {"keep": True}
    assert leftovers(tmp_path) == []


def test_write_json_reports_a_failure_instead_of_raising(tmp_path, monkeypatch, caplog):
    path = tmp_path / "settings.json"
    write_json(path, {"keep": True})
    monkeypatch.setattr(atomic_file.os, "replace",
                        lambda *_: (_ for _ in ()).throw(PermissionError(13, "read-only")))
    with caplog.at_level(logging.WARNING, logger="atomic_file"):
        assert write_json(path, {"keep": False}) is False
    assert "could not be written" in caplog.text
    assert read_json(path, dict) == {"keep": True}


def test_data_that_is_not_json_never_touches_the_disk(tmp_path, caplog):
    path = tmp_path / "settings.json"
    write_json(path, {"keep": True})
    with caplog.at_level(logging.ERROR, logger="atomic_file"):
        assert write_json(path, {"bad": {1, 2}}) is False
    assert "not JSON" in caplog.text
    assert read_json(path, dict) == {"keep": True}
    assert leftovers(tmp_path) == []


def test_an_interrupted_write_keeps_the_previous_version(tmp_path, monkeypatch):
    # The app is stopped (here: interrupted) after the new file was written
    # and before it was put in place.
    path = tmp_path / "session.json"
    write_json(path, {"paths": ["a.md"]})

    def stop(*_):
        raise KeyboardInterrupt

    monkeypatch.setattr(atomic_file, "_replace", stop)
    with pytest.raises(KeyboardInterrupt):
        write_json(path, {"paths": ["b.md"]})
    assert read_json(path, dict) == {"paths": ["a.md"]}
    assert leftovers(tmp_path) == []


def test_a_temporary_file_left_by_a_crash_does_not_disturb(tmp_path):
    # A crash of the whole process can leave a temporary file; it is never
    # read, and the next write still works.
    path = tmp_path / "settings.json"
    write_json(path, {"v": 1})
    (tmp_path / ".settings.json.abc123.tmp").write_text('{"v": 9', encoding="utf-8")
    assert read_json(path, dict) == {"v": 1}
    assert write_json(path, {"v": 2})
    assert read_json(path, dict) == {"v": 2}


def test_two_writes_never_share_a_temporary_file(tmp_path, monkeypatch):
    names = []
    real = atomic_file._replace
    monkeypatch.setattr(atomic_file, "_replace",
                        lambda source, target: (names.append(source), real(source, target)))
    write_json(tmp_path / "a.json", 1)
    write_json(tmp_path / "a.json", 2)
    assert len(set(names)) == 2


# -- Windows: a file held open for a moment ----------------------------------

def test_on_windows_a_briefly_locked_file_is_replaced_after_a_short_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_file.sys, "platform", "win32")
    waits = []
    monkeypatch.setattr(atomic_file.time, "sleep", waits.append)
    real = os.replace
    attempts = []

    def locked_twice(source, target):
        attempts.append(target)
        if len(attempts) <= 2:
            raise PermissionError(13, "The process cannot access the file")
        real(source, target)

    monkeypatch.setattr(atomic_file.os, "replace", locked_twice)
    path = tmp_path / "settings.json"
    write_bytes(path, b"{}")
    assert path.read_bytes() == b"{}"
    assert len(attempts) == 3
    assert waits == list(atomic_file._WINDOWS_REPLACE_WAITS_S[:2])


def test_on_windows_a_file_that_stays_locked_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_file.sys, "platform", "win32")
    monkeypatch.setattr(atomic_file.time, "sleep", lambda _s: None)

    def locked(source, target):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(atomic_file.os, "replace", locked)
    with pytest.raises(PermissionError):
        write_bytes(tmp_path / "settings.json", b"{}")
    assert leftovers(tmp_path) == []
    assert sum(atomic_file._WINDOWS_REPLACE_WAITS_S) < 0.5


def test_elsewhere_a_permission_error_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_file.sys, "platform", "linux")
    attempts = []

    def locked(source, target):
        attempts.append(target)
        raise PermissionError(13, "denied")

    monkeypatch.setattr(atomic_file.os, "replace", locked)
    with pytest.raises(PermissionError):
        write_bytes(tmp_path / "settings.json", b"{}")
    assert len(attempts) == 1


# -- reading -----------------------------------------------------------------

def test_a_missing_file_reads_as_empty_without_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="atomic_file"):
        assert read_json(tmp_path / "none.json", dict) == {}
        assert read_json(tmp_path / "none.json", list) == []
    assert caplog.text == ""


@pytest.mark.parametrize("content", [
    b'{"theme": "dark"',          # cut off in the middle (an old, non-atomic write)
    b"",                          # empty
    b"not json at all",
    b'\xff\xfe{"a": 1}',          # not UTF-8
])
def test_a_damaged_file_reads_as_empty_and_is_logged(tmp_path, caplog, content):
    path = tmp_path / "settings.json"
    path.write_bytes(content)
    with caplog.at_level(logging.WARNING, logger="atomic_file"):
        assert read_json(path, dict) == {}
    assert "could not be read" in caplog.text


def test_a_file_of_the_wrong_shape_reads_as_empty(tmp_path):
    path = tmp_path / "recent.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    assert read_json(path, list) == []
    path.write_text("[1, 2]", encoding="utf-8")
    assert read_json(path, dict) == {}


def test_a_folder_where_the_file_should_be_reads_as_empty(tmp_path):
    (tmp_path / "settings.json").mkdir()
    assert read_json(tmp_path / "settings.json", dict) == {}


def test_a_byte_order_mark_is_accepted(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"a": 1}).encode("utf-8"))
    assert read_json(path, dict) == {"a": 1}


# -- the app's files use it ----------------------------------------------------

def test_settings_survive_a_damaged_or_unreadable_file(tmp_path, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    (tmp_path / "settings.json").write_text('{"theme": "da', encoding="utf-8")
    assert settings.load_settings() == {}
    settings.save_setting("theme", "dark")
    assert settings.load_settings() == {"theme": "dark"}


def test_settings_that_cannot_be_saved_keep_the_old_ones(tmp_path, monkeypatch):
    import settings
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    settings.save_setting("theme", "dark")
    monkeypatch.setattr(atomic_file.os, "replace",
                        lambda *_: (_ for _ in ()).throw(OSError(30, "read-only")))
    assert settings.save_settings({"theme": "light"}) is False
    monkeypatch.undo()
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    assert settings.load_settings() == {"theme": "dark"}


def test_recent_files_ignore_entries_that_are_not_paths(tmp_path, monkeypatch):
    import recent_files
    monkeypatch.setattr(recent_files, "_RECENT_FILE", str(tmp_path / "recent.json"))
    (tmp_path / "recent.json").write_text('["/a.md", 5, null, "/b.md"]', encoding="utf-8")
    assert recent_files.load_recent() == ["/a.md", "/b.md"]


# -- the person's own files ------------------------------------------------------

from atomic_file import save_document, save_text_document  # noqa: E402

posix_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="Windows has no POSIX file modes or symlinks by default")


def test_a_document_is_saved_and_replaced(tmp_path):
    path = tmp_path / "essay.md"
    save_document(path, b"first")
    save_document(path, b"second")
    assert path.read_bytes() == b"second"
    assert leftovers(tmp_path) == []


def test_a_full_disk_leaves_the_document_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "essay.md"
    path.write_bytes(b"my whole essay")

    def full(*_):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(atomic_file, "_replace", full)
    with pytest.raises(OSError):
        save_document(path, b"the new version")
    assert path.read_bytes() == b"my whole essay"
    assert leftovers(tmp_path) == []


@posix_only
def test_a_document_keeps_its_permissions(tmp_path):
    path = tmp_path / "shared.md"
    path.write_bytes(b"old")
    os.chmod(path, 0o640)
    save_document(path, b"new")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640


@posix_only
def test_a_new_document_gets_the_usual_permissions(tmp_path):
    path = tmp_path / "new.md"
    save_document(path, b"text")
    assert stat.S_IMODE(os.stat(path).st_mode) == atomic_file._NEW_FILE_MODE
    assert atomic_file._NEW_FILE_MODE & 0o044, "readable like any new file, not private"


@posix_only
def test_a_link_stays_a_link(tmp_path):
    real = tmp_path / "real.md"
    real.write_bytes(b"old")
    link = tmp_path / "link.md"
    link.symlink_to(real)
    save_document(link, b"new")
    assert link.is_symlink()
    assert real.read_bytes() == b"new"


@posix_only
def test_a_file_with_several_names_is_written_in_place(tmp_path):
    first = tmp_path / "a.md"
    first.write_bytes(b"old")
    second = tmp_path / "b.md"
    os.link(first, second)
    save_document(first, b"new")
    assert second.read_bytes() == b"new"
    assert os.stat(first).st_ino == os.stat(second).st_ino


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root may write into any folder")
def test_a_document_in_a_folder_that_cannot_take_new_files_is_still_saved(tmp_path):
    folder = tmp_path / "locked"
    folder.mkdir()
    path = folder / "essay.md"
    path.write_bytes(b"old")
    os.chmod(folder, 0o555)
    try:
        save_document(path, b"new")
        assert path.read_bytes() == b"new"
    finally:
        os.chmod(folder, 0o755)


@pytest.mark.skipif(sys.platform != "darwin", reason="Finder tags exist on macOS only")
def test_on_macos_finder_tags_and_attributes_are_kept(tmp_path):
    import subprocess
    path = tmp_path / "tagged.md"
    path.write_bytes(b"old")
    subprocess.run(["xattr", "-w", "com.example.note", "keep me", str(path)], check=True)
    save_document(path, b"new")
    shown = subprocess.run(["xattr", "-p", "com.example.note", str(path)],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert shown == "keep me"
    assert path.read_bytes() == b"new"


@pytest.mark.skipif(not hasattr(os, "setxattr"), reason="Linux extended attributes")
def test_on_linux_user_attributes_are_kept(tmp_path):
    path = tmp_path / "tagged.md"
    path.write_bytes(b"old")
    try:
        os.setxattr(path, "user.note", b"keep me")
    except OSError:
        pytest.skip("this file system has no user attributes")
    save_document(path, b"new")
    assert os.getxattr(path, "user.note") == b"keep me"


def test_text_documents_get_this_systems_line_endings(tmp_path):
    path = tmp_path / "notes.txt"
    save_text_document(path, "one\ntwo\n")
    assert path.read_bytes() == ("one" + os.linesep + "two" + os.linesep).encode()


def test_text_documents_can_use_another_encoding(tmp_path):
    path = tmp_path / "letter.rtf"
    save_text_document(path, "{\\rtf1 hi}", encoding="ascii")
    assert path.read_bytes() == b"{\\rtf1 hi}"
    with pytest.raises(UnicodeEncodeError):
        save_text_document(path, "café", encoding="ascii")
    assert path.read_bytes() == b"{\\rtf1 hi}"
