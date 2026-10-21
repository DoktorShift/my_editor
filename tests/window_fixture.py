# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A main window built in the test run's own process, with everything it
would read or write of the person's kept in the test's folder: settings,
recent files, the last session, Nostr accounts. Updates are never asked
for, and the apps that open a file are "Preview" without asking the
system."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent


def make_window(tmp_path, monkeypatch, *, settings_values=None):
    import default_apps
    import main_window
    import recent_files
    import settings
    from nostr.profiles import ProfileStore
    monkeypatch.setattr(settings, "_SETTINGS_FILE", str(tmp_path / "settings.json"))
    for key, value in (settings_values or {}).items():
        settings.save_setting(key, value)
    monkeypatch.setattr(recent_files, "_RECENT_FILE", str(tmp_path / "recent.json"))
    monkeypatch.setattr(main_window.MainWindow, "_SESSION_FILE", str(tmp_path / "session.json"))
    monkeypatch.setattr(main_window.MainWindow, "_resume_workspace",
                        lambda self: (None, frozenset()))
    monkeypatch.setattr(main_window.MainWindow, "_maybe_auto_check_for_updates",
                        lambda self: None)
    monkeypatch.setattr(main_window, "ProfileStore",
                        lambda: ProfileStore(tmp_path / "profiles.json"))
    # The apps that open a file, never asked of this computer.
    monkeypatch.setattr(default_apps, "default_app_name", lambda path: "Preview")
    monkeypatch.setattr(default_apps, "other_apps", lambda path: [
        default_apps.AppChoice("Preview", "/Applications/Preview.app")])
    return main_window.MainWindow()


def close_window(window) -> None:
    """Closed, not deleted: the application's signals still reach it."""
    for ed in window._all_editors():
        ed.document().setModified(False)
    for i in range(window.tabs.count()):
        sheet = window._sheet_from_widget(window.tabs.widget(i))
        if sheet is not None:
            sheet.undo_stack.setClean()
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
