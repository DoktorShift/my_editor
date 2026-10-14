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
    path.write_text("{}", encoding="utf-8")
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
    a, b = str(tmp_path / "a.md"), str(tmp_path / "b.md")
    (tmp_path / "recent.json").write_text(json.dumps([a, 5, None, b]), encoding="utf-8")
    assert recent_files.load_recent() == [a, b]


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
@pytest.mark.parametrize("existing_mode", [None, 0o600, 0o644])
def test_the_new_text_is_private_until_it_takes_the_documents_place(tmp_path, monkeypatch,
                                                                    existing_mode):
    """While it is written, and if a crash leaves it behind, the
    temporary file is readable by this account only; the document then
    gets its own permissions (a new one, a new file's)."""
    path = tmp_path / "Diary.md"
    if existing_mode is not None:
        path.write_bytes(b"old")
        os.chmod(path, existing_mode)
    seen = []
    real_fsync = os.fsync

    def fsync(fd):
        seen.append(stat.S_IMODE(os.fstat(fd).st_mode))
        real_fsync(fd)

    monkeypatch.setattr(atomic_file.os, "fsync", fsync)
    save_document(path, b"new private text")
    assert seen[0] == 0o600
    assert stat.S_IMODE(os.stat(path).st_mode) == (
        existing_mode if existing_mode is not None else atomic_file._NEW_FILE_MODE)


def test_a_temporary_file_says_which_document_it_belongs_to(tmp_path):
    """A crash can leave it behind: its name starts with the document's."""
    fd, temporary = atomic_file.new_document_file(tmp_path / "Diary.md")
    os.close(fd)
    name = os.path.basename(temporary)
    assert name.startswith(".Diary.md.") and name.endswith(".tmp")
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(temporary).st_mode) == 0o600


def test_a_temporary_file_of_the_longest_name_still_fits(tmp_path):
    long_name = "ü" * 125 + ".md"                    # 253 bytes in UTF-8
    fd, temporary = atomic_file.new_document_file(tmp_path / long_name)
    os.close(fd)
    assert len(os.path.basename(temporary).encode("utf-8")) <= 255
    assert os.path.basename(temporary).startswith(".üüü")


@posix_only
def test_a_document_keeps_its_group(tmp_path):
    """As the system's own editors keep it, not the folder's or the
    person's main group."""
    path = tmp_path / "team.md"
    path.write_bytes(b"old")
    other = [g for g in os.getgroups() if g != os.stat(path).st_gid]
    if not other:
        pytest.skip("this account is in no other group")
    os.chown(path, -1, other[0])
    save_document(path, b"new")
    assert os.stat(path).st_gid == other[0]
    assert path.read_bytes() == b"new"


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


def _in_place_case(tmp_path, kind):
    """A document saved in place: one with a second name (a hard link), or
    in a folder that can't take a new file."""
    folder = tmp_path / "docs"
    folder.mkdir()
    path = folder / "Essay.md"
    path.write_bytes(b"my whole essay\n" * 1000)
    if kind == "hard link":
        os.link(path, folder / "Other.md")
    else:
        os.chmod(folder, 0o555)
    return folder, path


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root may write into any folder")
@pytest.mark.parametrize("kind", ["hard link", "read-only folder"])
def test_a_document_saved_in_place_survives_a_disk_that_cant_take_the_new_text(tmp_path, kind):
    """The new text needs more room than is left: the save fails, and the
    document is as it was, not emptied or half written."""
    resource = pytest.importorskip("resource")
    folder, path = _in_place_case(tmp_path, kind)
    old = path.read_bytes()
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (len(old) + 4096, hard))
    try:
        with pytest.raises(OSError):
            save_document(path, b"a much longer new version\n" * 10_000)
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
        os.chmod(folder, 0o755)
    assert path.read_bytes() == old


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root may write into any folder")
@pytest.mark.parametrize("kind", ["hard link", "read-only folder"])
@pytest.mark.parametrize("new", [b"a much longer new version\n" * 10_000, b"short"],
                         ids=["longer", "shorter"])
def test_a_document_saved_in_place_is_put_back_when_writing_it_fails(tmp_path, monkeypatch,
                                                                    kind, new):
    """The room was there, and writing the new text over it failed all the
    same (a removed volume): the old text is written back."""
    folder, path = _in_place_case(tmp_path, kind)
    old = path.read_bytes()
    real_fsync = os.fsync

    def fsync(fd):
        if os.pread(fd, len(new), 0) == new:      # the new text, written over the old
            raise OSError(28, "No space left on device")
        real_fsync(fd)

    monkeypatch.setattr(atomic_file.os, "fsync", fsync)
    try:
        with pytest.raises(OSError):
            save_document(path, new)
    finally:
        os.chmod(folder, 0o755)
    assert path.read_bytes() == old


class _CopyOnWriteFile:
    """An open file on a copy-on-write disk with ``room`` bytes left and
    its blocks shared with a clone (btrfs, XFS, an APFS clone or
    snapshot): every byte written takes fresh room, over the old text
    too, and only cutting off what was written past the old end gives
    room back. A write that doesn't fit writes what does; the next fails."""

    def __init__(self, raw, room):
        self._raw, self._room = raw, room
        self._old_end = os.fstat(raw.fileno()).st_size
        self._written_past = bytearray()     # per byte past the old end: written?

    def __getattr__(self, name):
        return getattr(self._raw, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._raw.close()

    def write(self, data):
        if self._room <= 0 < len(data):
            raise OSError(28, "No space left on device")
        start = self._raw.tell()
        written = self._raw.write(data[:self._room])
        self._room -= written
        first, end = max(start, self._old_end) - self._old_end, start + written - self._old_end
        if end > first:
            self._written_past.extend(bytes(max(0, end - len(self._written_past))))
            self._written_past[first:end] = b"\1" * (end - first)
        return written

    def truncate(self, size):
        cut = max(size, self._old_end) - self._old_end
        self._room += self._written_past[cut:].count(1)
        del self._written_past[cut:]
        return self._raw.truncate(size)


@posix_only
@pytest.mark.parametrize("new_size", [30_000, 15_000, 5_000], ids=["longer", "same", "shorter"])
def test_a_document_saved_in_place_on_a_copy_on_write_disk_is_old_or_new(tmp_path, monkeypatch,
                                                                          new_size):
    """Writing over the old text takes fresh room there, whatever the new
    text's length. With any room left on the disk, the save either
    fails and leaves the document as it was or succeeds with the whole
    new text: never half of each, never the old text with zeros after it."""
    room = {"left": 0}
    real_open = open

    def cow_open(file, mode="r", *args, **kwargs):
        f = real_open(file, mode, *args, **kwargs)
        return _CopyOnWriteFile(f, room["left"]) if mode == "r+b" else f

    monkeypatch.setattr(atomic_file, "open", cow_open, raising=False)
    new = (b"the new version, line by line\n" * 1000)[:new_size]
    for left in sorted({*range(0, new_size + 8192, 2048), new_size - 1, new_size + 65536}):
        folder = tmp_path / str(left)
        folder.mkdir()
        path = folder / "Essay.md"
        path.write_bytes(b"my whole essay\n" * 1000)
        os.link(path, folder / "Other.md")
        room["left"] = left
        try:
            save_document(path, new)
            saved = True
        except OSError:
            saved = False
        assert path.read_bytes() == (new if saved else b"my whole essay\n" * 1000), left
        assert left >= new_size if saved else left < new_size + 65536, left


@posix_only
def test_a_shorter_text_saved_in_place_leaves_nothing_of_the_old(tmp_path):
    folder, path = _in_place_case(tmp_path, "hard link")
    save_document(path, b"short")
    assert path.read_bytes() == b"short" == (folder / "Other.md").read_bytes()


@pytest.mark.skipif(sys.platform != "darwin", reason="creation dates as macOS keeps them")
def test_on_macos_a_document_keeps_its_creation_date(tmp_path):
    """As TextEdit keeps it: Finder's "Created" is when the document was
    made, not when it was last saved."""
    path = tmp_path / "essay.md"
    path.write_bytes(b"old")
    made = 1_600_000_000                               # an earlier time moves it back
    os.utime(path, (made, made))
    assert int(os.stat(path).st_birthtime) == made
    save_document(path, b"new")
    assert int(os.stat(path).st_birthtime) == made
    assert path.read_bytes() == b"new"


@pytest.mark.skipif(sys.platform != "darwin", reason="the quarantine attribute is macOS's")
def test_on_macos_the_quarantine_mark_is_kept_as_it_was(tmp_path):
    """Where the file came from, as the system recorded it: carried over
    unchanged, not rewritten."""
    import subprocess
    path = tmp_path / "downloaded.md"
    path.write_bytes(b"old")
    mark = "0081;5f5e1000;Safari;2C6E1F2A-1111-2222-3333-444455556666"
    subprocess.run(["xattr", "-w", "com.apple.quarantine", mark, str(path)], check=True)
    save_document(path, b"new")
    shown = subprocess.run(["xattr", "-p", "com.apple.quarantine", str(path)],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert shown == mark


@pytest.mark.skipif(sys.platform != "darwin", reason="access lists as macOS keeps them")
def test_on_macos_a_document_nobody_may_delete_is_saved_in_place(tmp_path):
    """Replacing it would delete it, which its access list forbids: it is
    written in place, keeps its list, and leaves no file behind that
    nobody could delete either."""
    import subprocess
    path = tmp_path / "kept.md"
    path.write_bytes(b"old")
    subprocess.run(["chmod", "+a", "everyone deny delete", str(path)], check=True)
    try:
        save_document(path, b"new")
        assert path.read_bytes() == b"new"
        assert sorted(os.listdir(tmp_path)) == ["kept.md"]
        listed = subprocess.run(["ls", "-le", str(path)], capture_output=True, text=True,
                                check=True).stdout
        assert "deny delete" in listed
    finally:
        subprocess.run(["chmod", "-RN", str(tmp_path)], check=False)


@pytest.mark.skipif(sys.platform != "darwin", reason="access lists as macOS keeps them")
def test_on_macos_a_document_that_inherits_a_deny_delete_entry_is_saved_in_place(tmp_path):
    """The usual way to protect files: the folder passes the entry on, and
    macOS lists it as "deny,inherited". Saved in place all the same, with
    no file left beside it that nobody could delete."""
    import subprocess
    folder = tmp_path / "kept"
    folder.mkdir()
    subprocess.run(["chmod", "+a", "everyone deny delete,file_inherit", str(folder)], check=True)
    try:
        path = folder / "Essay.md"
        path.write_bytes(b"old")
        save_document(path, b"new")
        assert path.read_bytes() == b"new"
        assert sorted(os.listdir(folder)) == ["Essay.md"]
    finally:
        subprocess.run(["chmod", "-RN", str(tmp_path)], check=False)


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


# -- line endings ----------------------------------------------------------------

from atomic_file import newline_of, read_text_document  # noqa: E402


def test_a_documents_line_endings_are_read_and_written_back(tmp_path):
    for ending in ("\n", "\r\n"):
        path = tmp_path / "notes.md"
        path.write_bytes(ending.join(["# Title", "", "Text."]).encode() + ending.encode())
        text, newline = read_text_document(path)
        assert text == "# Title\n\nText.\n"
        assert newline == ending
        save_text_document(path, text + "More.\n", newline=newline)
        assert path.read_bytes() == ending.join(
            ["# Title", "", "Text.", "More.", ""]).encode()


def test_an_lf_file_stays_lf_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(atomic_file.os, "linesep", "\r\n")
    path = tmp_path / "README.md"
    path.write_bytes(b"one\ntwo\n")
    text, newline = read_text_document(path)
    save_text_document(path, text, newline=newline)
    assert path.read_bytes() == b"one\ntwo\n"


def test_most_lines_decide_a_mixed_file(tmp_path):
    assert newline_of("a\r\nb\r\nc\n") == "\r\n"
    assert newline_of("a\nb\nc\r\n") == "\n"


def test_a_file_without_line_breaks_gets_this_systems(monkeypatch):
    monkeypatch.setattr(atomic_file.os, "linesep", "\r\n")
    assert newline_of("one line") == "\r\n"


def test_old_mac_line_endings_read_as_lines(tmp_path):
    path = tmp_path / "old.txt"
    path.write_bytes(b"one\rtwo\r")
    assert read_text_document(path)[0] == "one\ntwo\n"


def test_text_that_is_not_utf8_is_refused(tmp_path):
    path = tmp_path / "latin1.txt"
    path.write_bytes("café".encode("latin-1"))
    with pytest.raises(ValueError):
        read_text_document(path)


def test_a_stray_windows_ending_in_the_text_is_not_doubled(tmp_path):
    path = tmp_path / "pasted.md"
    save_text_document(path, "a\r\nb\n", newline="\r\n")
    assert path.read_bytes() == b"a\r\nb\r\n"


@pytest.mark.parametrize("text, newline", [
    ("one\rtwo\rthree\r", "\r"), ("one\r\ntwo\rthree\n", "\n"), ("a\rb\r\nc\r\nd\r\n", "\r\n"),
])
def test_a_lone_cr_is_a_line_ending_of_its_own(text, newline):
    """Classic Mac OS files end their lines in a lone CR (round 10, M1)."""
    assert newline_of(text) == newline
