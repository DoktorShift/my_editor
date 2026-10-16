# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deleting the windows and panels a test made, after the test.

A widget without a parent that is left to Python's cycle collector can
crash the collector: Shiboken's ``tp_clear`` hands each child back to
Python, and a child can then be deleted during its own parent's
teardown (seen as SIGBUS and SIGSEGV after the Drafts panel and Imports
window tests). A test module that makes such widgets deletes them after
every test::

    @pytest.fixture(autouse=True)
    def _windows_deleted():
        yield from delete_new_windows()

Every window (a widget without a parent, or a sheet, menu or popover)
that appeared during the test is closed and deleted; those that were
there before it are left alone.
"""

from __future__ import annotations

from typing import Iterator

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


def delete_new_windows() -> Iterator[None]:
    before = set(QApplication.topLevelWidgets())
    yield
    for widget in QApplication.topLevelWidgets():
        if widget not in before:
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
