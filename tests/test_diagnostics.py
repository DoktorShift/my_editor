# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The log of what went wrong, kept for bug reports."""

import logging
import os
import sys
import threading

import pytest
from PySide6.QtCore import QUrl, qWarning

import diagnostics


@pytest.fixture
def folder(tmp_path):
    hook, thread_hook = sys.excepthook, threading.excepthook
    yield str(tmp_path / "logs")
    diagnostics.uninstall()
    sys.excepthook, threading.excepthook = hook, thread_hook


def log_text(folder):
    with open(os.path.join(folder, diagnostics.LOG_NAME), encoding="utf-8") as f:
        return f.read()


def test_each_start_says_which_version_ran(folder):
    assert diagnostics.install("9.8", folder=folder)
    assert "MyEditor 9.8 started (Python" in log_text(folder)
    assert os.path.exists(os.path.join(folder, diagnostics.CRASH_NAME))


def test_warnings_and_errors_are_kept_but_not_everyday_notes(folder):
    diagnostics.install("1.0", folder=folder)
    log = logging.getLogger("nostr.test")
    log.info("connected to a relay")
    log.warning("relay list lookup failed")
    log.error("upload refused")
    text = log_text(folder)
    assert "connected to a relay" not in text
    assert "WARNING nostr.test: relay list lookup failed" in text
    assert "ERROR nostr.test: upload refused" in text


def test_an_unexpected_error_is_kept_with_its_trace(folder):
    shown = []
    sys.excepthook = lambda *exc: shown.append(exc[0])
    diagnostics.install("1.0", folder=folder)
    try:
        raise ValueError("a slot went wrong")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    text = log_text(folder)
    assert "Unexpected error" in text and "ValueError: a slot went wrong" in text
    assert "Traceback" in text
    assert shown == [ValueError]        # still shown where it was before


# pytest notes the thread's error too, after the log has it.
@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_an_error_in_a_thread_is_kept(folder):
    diagnostics.install("1.0", folder=folder)

    def work():
        raise RuntimeError("worker failed")

    worker = threading.Thread(target=work, name="importer")
    worker.start()
    worker.join()
    text = log_text(folder)
    assert "Unexpected error in thread importer" in text
    assert "RuntimeError: worker failed" in text


def test_qts_own_warnings_are_kept(folder, capsys):
    diagnostics.install("1.0", folder=folder)
    qWarning("a painter is not active")
    assert "WARNING qt: default: a painter is not active" in log_text(folder)
    assert "a painter is not active" in capsys.readouterr().err


def test_the_log_stays_small(folder):
    diagnostics.install("1.0", folder=folder)
    handler = diagnostics._handler
    assert handler.maxBytes == diagnostics.MAX_BYTES == 1024 * 1024
    assert handler.backupCount == diagnostics.KEPT == 1


def test_a_folder_that_cannot_be_written_leaves_the_app_as_it_was(tmp_path, folder):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder", encoding="utf-8")
    hook = sys.excepthook
    assert diagnostics.install("1.0", folder=str(blocked / "logs")) is False
    assert sys.excepthook is hook
    assert diagnostics._handler is None


def test_installing_twice_writes_one_log(folder):
    diagnostics.install("1.0", folder=folder)
    diagnostics.install("1.0", folder=folder)
    handlers = [h for h in logging.getLogger().handlers if h is diagnostics._handler]
    assert len(handlers) == 1


def test_help_show_log_files_opens_the_folder(tmp_path, monkeypatch):
    from main_window import MainWindow
    opened = []
    monkeypatch.setattr(diagnostics, "log_folder", lambda: str(tmp_path / "logs"))
    monkeypatch.setattr("main_window.QDesktopServices.openUrl",
                        lambda url: opened.append(url) or True)

    class Window:
        _show_log_files = MainWindow._show_log_files

    Window()._show_log_files()
    assert opened == [QUrl.fromLocalFile(str(tmp_path / "logs"))]
    assert (tmp_path / "logs").is_dir()


class _CrashReports:
    """Stands in for faulthandler: where crash traces go."""

    def __init__(self, target=None):
        self.target = target

    def is_enabled(self):
        return self.target is not None

    def enable(self, file, all_threads=True):
        self.target = file

    def disable(self):
        self.target = None


@pytest.mark.parametrize("before", [None, "a test run"])
def test_crash_traces_go_to_the_crash_log_unless_they_already_go_elsewhere(
        folder, monkeypatch, before):
    # Traces that already go somewhere (python -X faulthandler, a test
    # run) stay there: faulthandler cannot say where, so taking them
    # over could never be undone, and a test run lost every crash trace
    # after the first test that installed the log.
    reports = _CrashReports(before)
    monkeypatch.setattr(diagnostics, "faulthandler", reports)
    diagnostics.install("1.0", folder=folder)
    if before is None:
        assert reports.target.name == os.path.join(folder, diagnostics.CRASH_NAME)
    else:
        assert reports.target == before
    diagnostics.uninstall()
    assert reports.target == before
