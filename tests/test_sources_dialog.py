"""Sources dialog: layout and Add Source flow.

Regression coverage for the two visual bugs the user spotted in the
screenshots:

  1. Rows used to stretch vertically when there was room, because
     ``_list_layout`` had no bottom stretch. That made the checkbox
     float in the middle of the row and the URL drift far below the
     name. We pin a sensible upper bound on the row's height.
  2. The "Add source..." button stayed visible while the inline form
     was open, creating two duplicate ways to do the same thing. We
     verify the button hides when the form opens and restores when
     the form closes.
"""

from __future__ import annotations

import pytest

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from plugin_marketplace.models import RegistrySource
from plugin_marketplace.registry import DEFAULT_REGISTRY
from plugin_marketplace.ui.sources_dialog import SourcesDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture(autouse=True)
def _stub_message_boxes(monkeypatch):
    """``QMessageBox.warning`` is modal and blocks the offscreen event
    loop forever. Stub it to a no-op so validation failures can be
    asserted without hanging the test runner."""
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)


@pytest.fixture
def dialog(qapp):
    return SourcesDialog(None, sources=[DEFAULT_REGISTRY])


# ──────────────────────────────────────────────────────────────────────
# Row sizing
# ──────────────────────────────────────────────────────────────────────

def test_row_does_not_vertically_stretch(dialog):
    """Single-row case must yield a compact row, not a stretched one.

    Before the fix, ``_list_layout`` had no bottom stretch and Qt
    expanded the only row to fill the QScrollArea. The row would be
    300+ pixels tall and the checkbox would float in the middle.
    """
    assert len(dialog._rows) == 1
    height = dialog._rows[0].sizeHint().height()
    # Two text lines plus padding fit comfortably under 90px. 200+
    # would indicate the stretch regression.
    assert height < 90, f"row height {height} suggests vertical stretching"


def test_list_layout_has_bottom_stretch(dialog):
    """The QVBoxLayout must end with a stretch so additional rows pile
    at the top instead of getting expanded."""
    last_item = dialog._list_layout.itemAt(dialog._list_layout.count() - 1)
    # A QSpacerItem from addStretch reports its widget() as None.
    assert last_item.widget() is None


# ──────────────────────────────────────────────────────────────────────
# Add Source flow
# ──────────────────────────────────────────────────────────────────────

def test_add_source_button_hidden_when_form_open(dialog):
    """Both controls must never coexist on screen."""
    assert dialog._add_btn_holder.isVisibleTo(dialog)
    assert not dialog._add_form.isVisibleTo(dialog)
    dialog._show_add_form()
    assert not dialog._add_btn_holder.isVisibleTo(dialog)
    assert dialog._add_form.isVisibleTo(dialog)


def test_cancel_in_add_form_restores_button(dialog):
    dialog._show_add_form()
    dialog._hide_add_form()
    assert dialog._add_btn_holder.isVisibleTo(dialog)
    assert not dialog._add_form.isVisibleTo(dialog)


def test_focus_lands_on_name_input_when_form_opens(dialog):
    """Focus must NOT land on Cancel - on macOS that gives Cancel the
    blue focus ring and makes it look like the primary action."""
    dialog._show_add_form()
    focused = dialog.focusWidget()
    assert isinstance(focused, QLineEdit)
    assert focused is dialog._add_name


def test_add_button_is_default(dialog):
    """``setDefault(True)`` on the Add button means Return inside the
    form commits Add, not the dialog's outer OK button."""
    dialog._show_add_form()
    assert dialog._confirm_add_btn.isDefault()
    assert not dialog._cancel_add_btn.autoDefault()


def test_commit_appends_row_and_closes_form(dialog):
    dialog._show_add_form()
    dialog._add_name.setText("My Registry")
    dialog._add_url.setText("https://example.com/index.json")
    dialog._commit_add()
    # New row added, form closed, fields cleared.
    assert len(dialog._rows) == 2
    assert dialog._rows[-1].source.index_url == "https://example.com/index.json"
    assert not dialog._add_form.isVisibleTo(dialog)
    assert dialog._add_name.text() == ""
    assert dialog._add_url.text() == ""


def test_commit_rejects_non_https(dialog):
    dialog._show_add_form()
    dialog._add_name.setText("Insecure")
    dialog._add_url.setText("http://example.com/index.json")
    # QMessageBox would block in interactive mode; in offscreen runs it
    # returns immediately, so the row is NOT added and form stays open.
    dialog._commit_add()
    assert len(dialog._rows) == 1
    assert dialog._add_form.isVisibleTo(dialog)


def test_official_row_is_not_removable(dialog):
    """The default registry can be toggled but never removed."""
    from PySide6.QtWidgets import QPushButton
    row = dialog._rows[0]
    buttons = [w for w in row.findChildren(QPushButton)]
    assert buttons == [], "default registry row should have no Remove button"


def test_added_row_is_removable(dialog):
    dialog._show_add_form()
    dialog._add_name.setText("Community")
    dialog._add_url.setText("https://community.example/idx.json")
    dialog._commit_add()
    from PySide6.QtWidgets import QPushButton
    added_row = dialog._rows[-1]
    buttons = added_row.findChildren(QPushButton)
    assert any(b.text() == "Remove" for b in buttons)


def test_snapshot_reflects_toggle_state(dialog):
    """``result_sources()`` reports the post-toggle enabled state."""
    dialog._rows[0]._enabled.setChecked(False)
    snap = dialog.result_sources()
    assert snap[0].enabled is False
    # Trusted flag stays sticky for the official source even when off.
    assert snap[0].trusted is True
