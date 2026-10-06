# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Drafts panel guarantees that a restyle must not quietly break.

The states carrying the risk are the ones a person restyling the panel
never looks at: no profile connected, no drafts, a first refresh still
in flight, an unknown-kind draft (the wrap's ``k`` tag is optional, so
this is the normal state of every draft that has not decrypted yet), and
a very narrow panel. The colour tokens and the accessible names are
pinned here too, because both are the kind of thing a later refactor
drops without anyone noticing until someone cannot read the panel.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFont,
    QFontMetrics,
    QImage,
    QKeySequence,
    QPainter,
    QPalette,
    QRegion,
)
from PySide6.QtWidgets import (
    QApplication,
    QPushButton,
    QToolButton,
    QWidget,
)

from nostr.draft_store import DraftRecord, DraftState, DraftStore
from nostr.drafts import DraftWrapMeta
from nostr.profiles import Profile
import nostr.ui.drafts_panel as dp
from nostr.ui.drafts_panel import (
    _AGE_SAMPLES,
    _THEME_TOKENS,
    MIN_PANEL_WIDTH,
    DraftsPanel,
    _DraftRowWidget,
    _accessible_row_text,
    _format_relative_time,
    _menu_css,
    _panel_css,
    _row_metrics,
    _secondary_font,
)
from nostr.ui.profile_chip import ProfileChip
from tests.accessibility import unnamed_controls


PK = "a" * 64


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def app_font():
    """Restore the application font, it is process-wide state."""
    app = QApplication.instance()
    original = app.font()
    yield app
    app.setFont(original)


def make_profile(display_name: str = "Alice") -> Profile:
    return Profile(
        user_pubkey=PK,
        bunker_pubkey="b" * 64,
        bunker_relays=["wss://relay.example"],
        local_secret_hex="c" * 64,
        display_name=display_name,
    )


def wrap_meta(identifier: str, *, inner_kind: int = 0, created_at: int = 1700000000):
    """A skeleton wrap. ``inner_kind`` 0 is the undecrypted default."""
    return DraftWrapMeta(
        identifier=identifier,
        inner_kind=inner_kind,
        event_id=identifier + "e",
        pubkey=PK,
        created_at=created_at,
        expiration=None,
        ciphertext="ciphertext",
    )


def populated_store() -> DraftStore:
    """Four drafts, every one of them ``inner_kind == 0``.

    One stays LOADING, one FAILED, two decrypt: exactly the mix the kind
    filter used to hide, since a draft only learns its kind after the
    inner event is read.
    """
    store = DraftStore()
    store.bind_profile(PK)
    for i in range(4):
        store.upsert_skeleton(wrap_meta(f"d{i}", created_at=1700000000 + i))
    store.set_decrypted("d2", inner={"kind": 1, "content": "Pineapple notes", "tags": []})
    store.set_decrypted("d3", inner={"kind": 1, "content": "Second body", "tags": []})
    store.set_failed("d1", "signer timed out")
    return store


def healthy_store() -> DraftStore:
    """Two drafts, neither of them failed, so no error outranks a status."""
    store = DraftStore()
    store.bind_profile(PK)
    for i in range(2):
        store.upsert_skeleton(wrap_meta(f"h{i}", created_at=1700000000 + i))
    store.set_decrypted("h0", inner={"kind": 1, "content": "First body", "tags": []})
    return store


def bound_panel(store: DraftStore | None = None) -> DraftsPanel:
    panel = DraftsPanel(is_dark=True)
    panel.set_active_profile(make_profile())
    if store is not None:
        panel.bind_store(store)
    return panel


def visible_identifiers(panel: DraftsPanel):
    return [
        panel._list.item(i).data(Qt.UserRole)
        for i in range(panel._list.count())
    ]


def relative_luminance(hex_colour: str) -> float:
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        for c in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


# --------------------------------------------------------------------------- #
# D1: one profile control, and the panel still says whose drafts these are    #
# --------------------------------------------------------------------------- #

def test_panel_builds_with_no_profile_and_no_store():
    # The riskiest state: nothing bound at all.
    panel = DraftsPanel(is_dark=True)
    panel.set_active_profile(None)
    assert panel._body_stack.currentIndex() == 1
    assert "Connect a signer" in panel._empty_body.text()
    assert panel._status_label.full_text() == ""
    # An empty line collapses instead of leaving a blank strip of chrome.
    assert not panel._status_label.isVisibleTo(panel)


def test_panel_carries_no_second_profile_control():
    panel = DraftsPanel(is_dark=True)
    assert not hasattr(panel, "_profile_chip")
    assert not hasattr(panel, "_refresh_profile_chip")
    assert not hasattr(DraftsPanel, "switch_profile_requested")
    assert not hasattr(panel, "set_avatar_store")
    # Only the refresh and close buttons remain in the header.
    buttons = [
        b for b in panel.findChildren(QToolButton)
        if b.objectName() == "drafts_panel_icon_btn"
    ]
    assert {b.toolTip() for b in buttons} == {"Refresh drafts", "Close drafts panel"}


def test_status_line_names_the_bound_account():
    # This is what replaces the deleted chip: the account is still
    # legible, as a label rather than a second control.
    panel = DraftsPanel(is_dark=True)
    panel.set_active_profile(make_profile("Alice"))
    assert panel._status_label.full_text() == "Drafts for Alice"
    assert panel._status_label.property("error") == "false"


def test_status_line_falls_back_to_the_short_npub():
    panel = DraftsPanel(is_dark=True)
    profile = make_profile("")
    panel.set_active_profile(profile)
    assert panel._status_label.full_text() == f"Drafts for {profile.npub_short()}"


# --------------------------------------------------------------------------- #
# D2: no draft may be unreachable                                             #
# --------------------------------------------------------------------------- #

def test_no_kind_filter_controls_remain():
    panel = DraftsPanel(is_dark=True)
    chips = [
        b for b in panel.findChildren(QPushButton)
        if b.objectName() == "drafts_panel_chip"
    ]
    assert chips == []
    assert not hasattr(panel, "_kind_filter")


def test_unknown_kind_drafts_are_all_visible():
    # Four records, all inner_kind 0, in three different states. The old
    # chips showed 4 under All, 0 under Notes and 0 under Articles.
    store = populated_store()
    panel = bound_panel(store)
    assert all(r.inner_kind == 0 for r in store)
    assert sorted(visible_identifiers(panel)) == ["d0", "d1", "d2", "d3"]
    assert panel._body_stack.currentIndex() == 0


def test_clearing_the_search_restores_every_record():
    store = populated_store()
    panel = bound_panel(store)

    panel._search_edit.setText("pineapple")
    assert visible_identifiers(panel) == ["d2"]

    panel._search_edit.setText("zzz-no-such-draft")
    assert visible_identifiers(panel) == []

    panel._search_edit.clear()
    assert sorted(visible_identifiers(panel)) == ["d0", "d1", "d2", "d3"]
    assert len(visible_identifiers(panel)) == len(store)


def test_search_reaches_the_body_of_a_draft():
    # The panel's only filter has to be able to find what is plainly in
    # the draft. "watermelon" appears in the body, past the 140-char
    # snippet, and nowhere in the title.
    store = DraftStore()
    store.bind_profile(PK)
    store.upsert_skeleton(wrap_meta("d0"))
    body = "Title line\n\n" + ("filler " * 40) + "watermelon"
    store.set_decrypted("d0", inner={"kind": 1, "content": body, "tags": []})
    panel = bound_panel(store)

    assert "watermelon" not in store.get("d0").snippet
    panel._search_edit.setText("watermelon")
    assert visible_identifiers(panel) == ["d0"]


def test_a_failed_draft_stays_reachable():
    # The draft that most needs attention was the most reliably hidden:
    # a failed decryption never learns its kind.
    store = populated_store()
    panel = bound_panel(store)
    assert store.get("d1").state is DraftState.FAILED
    assert "d1" in visible_identifiers(panel)


# --------------------------------------------------------------------------- #
# D3: the row's own truncation                                                #
# --------------------------------------------------------------------------- #

def render_row(record, width: int) -> _DraftRowWidget:
    row = _DraftRowWidget()
    row.set_record(record)
    # Shown first: a hidden widget does not re-run its layout on resize,
    # so the label would keep a width the test never asked for.
    row.show()
    row.resize(width, row.height())
    row.layout().activate()
    return row


def ready_record(title: str, snippet: str = "") -> DraftRecord:
    return DraftRecord(
        identifier="d0",
        inner_kind=0,
        state=DraftState.READY,
        title=title,
        snippet=snippet,
        created_at=1700000000,
    )


def decrypted_row(body: str, width: int) -> _DraftRowWidget:
    """A row built the long way round, through the store's derivation."""
    store = DraftStore()
    store.bind_profile(PK)
    store.upsert_skeleton(wrap_meta("d0"))
    store.set_decrypted("d0", inner={"kind": 1, "content": body, "tags": []})
    return render_row(store.get("d0"), width)


def test_a_long_title_elides_inside_the_label():
    # 90 characters at the narrowest the panel goes. Before, QLabel
    # clipped the string from the right and took the ellipsis with it,
    # so the row read "One Class, On" with no sign it had been cut.
    long_title = "One Class, One Purpose: Refactoring the Whole Import Pipeline Without Breaking It"
    row = render_row(ready_record(long_title), MIN_PANEL_WIDTH)
    label = row._title
    width = label.contentsRect().width()
    painted = label.fontMetrics().elidedText(label.full_text(), Qt.ElideRight, width)
    assert label.fontMetrics().horizontalAdvance(painted) <= width
    assert painted.endswith("…")
    assert len(painted) < len(long_title)
    # And the row really paints, ellipsis and all, at that width.
    assert not row.grab().isNull()
    # The label never mutates the string it was handed. It holds no
    # QLabel text of its own at all, which is what stops elision from
    # feeding back into the layout.
    assert label.text() == ""
    assert label.full_text() == long_title
    # The whole row is readable from any point on it, so the full title
    # survives at every width.
    assert long_title in row.toolTip()

    # Widening the row re-elides for free, no setText and no resizeEvent.
    row.resize(520, row.height())
    row.layout().activate()
    wider = label.fontMetrics().elidedText(
        label.full_text(), Qt.ElideRight, label.contentsRect().width(),
    )
    assert len(wider) > len(painted)


def paint_to_image(widget) -> QImage:
    """What the widget's own paintEvent puts on screen.

    ``DrawChildren`` alone, because the default flags also fill the
    window background, which would swamp the glyphs being compared.
    """
    image = QImage(widget.size(), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    widget.render(image, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
    return image


def paint_label_string(label, text: str) -> QImage:
    """The label's own draw call, with a string of our choosing.

    The reference the painted row is compared against. It has to be the
    same call ``_ElidingLabel.paintEvent`` makes, through the same style
    and palette, or the comparison would be measuring something else.
    """
    image = QImage(label.size(), QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    # drawItemText paints in the painter's font, and a QPainter opened on
    # an image starts from the application font rather than the label's,
    # which would compare the title against a lighter weight.
    painter.setFont(label.font())
    label.style().drawItemText(
        painter,
        label.contentsRect(),
        int(label.alignment()),
        label.palette(),
        label.isEnabled(),
        text,
        label.foregroundRole(),
    )
    painter.end()
    return image


def test_the_row_paints_the_elided_title_and_not_the_full_one():
    """Pins the pixels, not a recomputation of the elision.

    ``test_a_long_title_elides_inside_the_label`` above asserts against
    a string it elides itself, so it exercises QFontMetrics rather than
    the widget: deleting the elision from ``paintEvent`` leaves it
    passing while the row visibly goes back to a title cut mid-word with
    no ellipsis. This compares the painted output with the same draw
    call fed the two candidate strings, so only one of them can match.
    """
    long_title = (
        "One Class, One Purpose: Refactoring the Whole Import Pipeline "
        "Without Breaking It"
    )
    row = render_row(ready_record(long_title), MIN_PANEL_WIDTH)
    label = row._title
    expected = label.fontMetrics().elidedText(
        long_title, Qt.ElideRight, label.contentsRect().width(),
    )
    # The premise: at this width the two candidates really are different.
    assert expected.endswith("…")
    assert len(expected) < len(long_title)

    painted = paint_to_image(label)
    assert painted == paint_label_string(label, expected)
    assert painted != paint_label_string(label, long_title)


def test_a_title_never_widens_the_panel():
    row = render_row(ready_record("x" * 400), MIN_PANEL_WIDTH)
    assert row._title.minimumSizeHint().width() == 0
    assert row._meta.minimumSizeHint().width() == 0
    panel = DraftsPanel(is_dark=True)
    assert panel.minimumSizeHint().width() <= MIN_PANEL_WIDTH


def test_an_image_led_body_never_reaches_the_row_as_markdown():
    body = "![One Class, One Purpose](https://x/hero.png)\n\n# One Class, One Purpose\n\nBody."
    row = decrypted_row(body, MIN_PANEL_WIDTH)
    assert row._title.full_text() == "One Class, One Purpose"
    assert "![" not in row._meta.full_text()
    assert "https://" not in row._meta.full_text()


# --------------------------------------------------------------------------- #
# Row layout: derived geometry, one inline item                               #
# --------------------------------------------------------------------------- #

def test_row_height_scales_with_the_application_font(app_font):
    """The row and its list item must read one height, at any font size.

    A QSS ``font-size`` would have pinned both; the sizes are set in
    Python from the application font precisely so this holds.
    """
    heights = {}
    for point_size in (9, 18):
        app_font.setFont(QFont("Helvetica", point_size))
        store = healthy_store()
        panel = bound_panel(store)
        item = panel._list.item(0)
        widget = panel._list.itemWidget(item)
        assert item.sizeHint().height() == widget.row_height()
        assert widget.height() == widget.row_height()
        heights[point_size] = widget.row_height()
    assert heights[18] > heights[9]


def test_the_age_column_fits_every_age_it_can_show(app_font):
    # "just now" needed 46 px inside a 36 px label and was the only
    # string wide enough to give one row a different column width.
    assert _format_relative_time(1_700_000_000, now=1_700_000_010) == "now"
    for point_size in (9, 13, 18):
        app_font.setFont(QFont("Helvetica", point_size))
        metrics = _row_metrics()
        fm = QFontMetrics(metrics.secondary_font)
        for sample in _AGE_SAMPLES:
            assert fm.horizontalAdvance(sample) <= metrics.age_width


def test_the_secondary_font_never_overtakes_the_title(app_font):
    for point_size in (8, 9, 10, 13, 26):
        app_font.setFont(QFont("Helvetica", point_size))
        secondary = _secondary_font().pointSizeF()
        assert secondary <= QApplication.font().pointSizeF()
        # The macOS minimum readable size, unless the app itself is below it.
        assert secondary >= min(10.0, QApplication.font().pointSizeF())


def test_the_row_has_one_inline_item_beside_the_title():
    # The lock glyph and the kind pill are gone; they discriminated
    # nothing and cost the title column 80 px.
    row = render_row(ready_record("Title"), MIN_PANEL_WIDTH)
    assert not hasattr(row, "_lock")
    assert not hasattr(row, "_kind")
    title_width = row._title.contentsRect().width()
    assert title_width > 150, title_width


def test_the_meta_line_carries_the_source_host_not_a_pill():
    record = ready_record("Title", snippet="First words of the body")
    record.inner_tags = [["source", "https://www.blog.example.com/posts/1"]]
    row = render_row(record, MIN_PANEL_WIDTH)
    assert row._meta.full_text() == "blog.example.com · First words of the body"


def test_a_file_import_source_does_not_become_a_bare_drive_letter():
    record = ready_record("Title", snippet="Body")
    record.inner_tags = [["source", r"C:\exports\wordpress.xml"]]
    row = render_row(record, MIN_PANEL_WIDTH)
    assert row._meta.full_text().startswith("wordpress.xml · ")


def test_a_pathological_source_cannot_eat_the_meta_line():
    record = ready_record("Title", snippet="Body")
    record.inner_tags = [
        ["source", "https://" + "a-very-long-subdomain." * 6 + "example.org/x"]
    ]
    row = render_row(record, MIN_PANEL_WIDTH)
    host = row._meta.full_text().split(" · ")[0]
    assert len(host) <= 28
    # Elided in the middle, because a host is identified by its tail.
    assert host.endswith("example.org")
    assert "…" in host


# --------------------------------------------------------------------------- #
# Row state: progress and failure do not share a channel                      #
# --------------------------------------------------------------------------- #

def test_loading_and_failed_rows_are_told_apart():
    store = populated_store()
    panel = bound_panel(store)
    rows = {
        panel._list.item(i).data(Qt.UserRole): panel._list.itemWidget(panel._list.item(i))
        for i in range(panel._list.count())
    }
    # d0 never decrypted, d1 failed, d2 is ready.
    assert rows["d0"]._title.property("state") == "loading"
    assert rows["d1"]._title.property("state") == "failed"
    assert rows["d2"]._title.property("state") == "ready"
    # The old boolean lumped LOADING in with FAILED, so a cold load of a
    # whole library rendered as a library of broken drafts.
    assert rows["d0"]._title.property("failed") is None
    assert rows["d0"]._meta.full_text() == "Decrypting"
    assert "retry" in rows["d1"]._meta.full_text().lower()


def test_selecting_a_row_moves_the_selected_look_with_it():
    store = healthy_store()
    panel = bound_panel(store)
    first = panel._list.itemWidget(panel._list.item(0))
    second = panel._list.itemWidget(panel._list.item(1))
    # A populated list always has a current row, so the arrow keys have
    # an anchor the moment focus arrives.
    assert panel._list.currentRow() == 0
    assert first._title.property("sel") == "true"
    assert second._title.property("sel") == "false"

    panel._list.setCurrentRow(1)
    assert first._title.property("sel") == "false"
    assert second._title.property("sel") == "true"
    assert second._age.property("sel") == "true"
    assert second._meta.property("sel") == "true"
    assert not second.grab().isNull()


def test_hovering_a_row_fills_it():
    """The row paints its own hover fill, and it has to.

    The list's ``::item:hover`` rule was dead code: the row widget covers
    the item rect exactly and is not mouse transparent, so the viewport
    never saw the pointer. Reviving it by making the row transparent
    would cost the row its tooltip.
    """
    store = healthy_store()
    panel = bound_panel(store)
    panel.resize(320, 400)
    panel.show()
    panel.layout().activate()
    try:
        # Row 1, because row 0 is the current row and shows selection.
        row = panel._list.itemWidget(panel._list.item(1))
        centre = row.mapTo(panel, row.rect().center())
        # Well right of the text, so only the fill is under the sample.
        sample = lambda: QColor(panel.grab().toImage().pixel(300, centre.y())).name().upper()
        tokens = _THEME_TOKENS[True]
        assert sample() == tokens["panel_bg"].upper()
        row.enterEvent(QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        assert sample() == tokens["hover_bg"].upper()
        row.leaveEvent(QEvent(QEvent.Leave))
        assert sample() == tokens["panel_bg"].upper()
    finally:
        panel.hide()


def test_the_selected_row_carries_a_shape_cue_not_only_a_fill():
    # accessibility.md: "Offer visual indicators, like distinct shapes or
    # icons, in addition to color to help people perceive differences in
    # function and changes in state."
    for is_dark in (True, False):
        store = healthy_store()
        panel = DraftsPanel(is_dark=is_dark)
        panel.set_active_profile(make_profile())
        panel.bind_store(store)
        panel.resize(320, 400)
        panel.show()
        panel.layout().activate()
        try:
            row = panel._list.itemWidget(panel._list.item(0))
            centre = row.mapTo(panel, row.rect().center())
            image = panel.grab().toImage()
            tokens = _THEME_TOKENS[is_dark]
            bar = QColor(image.pixel(1, centre.y())).name().upper()
            fill = QColor(image.pixel(40, centre.y())).name().upper()
            assert bar == tokens["selection_bar"].upper()
            assert fill == tokens["selected_bg"].upper()
        finally:
            panel.hide()


@pytest.mark.parametrize("control_name", ["_launcher", "_close_btn", "_refresh_btn",
                                          "_search_edit"])
def test_focus_is_visible_on_every_keyboard_reachable_control(control_name):
    """Measured before this change: focusing changed zero pixels.

    Setting a background and a border in QSS makes Qt paint the control
    entirely from the stylesheet box model and skip the native focus
    ring, so every control in the panel except the search field was
    invisible to Full Keyboard Access.
    """
    panel = DraftsPanel(is_dark=True)
    panel.set_imports_state(available=True, new_posts=2)
    panel._launcher = panel._imports_row.button
    panel.resize(320, 400)
    panel.show()
    panel.layout().activate()
    try:
        control = getattr(panel, control_name)
        # Focus elsewhere first: a shown panel hands its focus to the first
        # control in the chain, and a cleared focus can come straight back.
        elsewhere = panel._close_btn if control is panel._search_edit else panel._search_edit
        elsewhere.setFocus(Qt.OtherFocusReason)
        QApplication.processEvents()
        assert not control.hasFocus()
        before = control.grab().toImage()
        control.setFocus(Qt.OtherFocusReason)
        QApplication.processEvents()
        after = control.grab().toImage()
        assert before != after, f"{control_name} looks identical focused"
    finally:
        panel.hide()


def test_the_context_menu_resolves_a_row_from_the_keyboard():
    # The Menu key delivers a position that may hit no row. Resolving by
    # position alone, the key did nothing at all.
    store = healthy_store()
    panel = bound_panel(store)
    far_below = QPoint(10, 10_000)
    assert panel._list.itemAt(far_below) is None
    assert panel._item_for_context(far_below) is panel._list.currentItem()
    assert panel._item_for_context(far_below) is not None


def test_the_context_menu_resolves_nothing_when_the_list_is_empty():
    panel = bound_panel(DraftStore())
    assert panel._item_for_context(QPoint(10, 10)) is None


def test_menu_commands_use_title_style_capitalisation():
    # menus.md: "To be consistent with platform experiences, use
    # title-style capitalization."
    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "nostr" / "ui" / "drafts_panel.py"
    ).read_text(encoding="utf-8")
    for command in (
        "Retry Decryption", "Open in New Tab", "Publish…",
        "Copy Event ID", "Delete Draft",
    ):
        assert f'QAction(_("{command}")' in source


def test_every_chrome_control_is_reachable_by_keyboard():
    panel = DraftsPanel(is_dark=True)
    panel.add_view("published", "Published", "", QWidget())
    for control in (
        *panel._view_switch.segments, panel._view_switch.popup, panel._refresh_btn,
        panel._close_btn, panel._imports_row.button, panel._imports_row.action,
        panel._search_edit, panel._list,
    ):
        assert control.focusPolicy() != Qt.NoFocus


def test_rebuilding_the_list_while_a_row_is_selected_does_not_crash():
    # currentItemChanged fires while the items are being destroyed.
    store = populated_store()
    panel = bound_panel(store)
    panel._list.setCurrentRow(2)
    panel._search_edit.setText("pineapple")
    panel._search_edit.clear()
    assert len(visible_identifiers(panel)) == 4


# --------------------------------------------------------------------------- #
# Empty, loading and error states                                             #
# --------------------------------------------------------------------------- #

def test_an_empty_store_names_the_shortcut_in_platform_notation():
    panel = bound_panel(DraftStore())
    assert panel._body_stack.currentIndex() == 1
    assert panel._empty_title.text() == "No private drafts yet"
    native = QKeySequence("Ctrl+Shift+S").toString(QKeySequence.NativeText)
    assert native in panel._empty_body.text()
    # No action button: the panel does not own the remedy here.
    assert not panel._empty_action.isVisibleTo(panel._empty_widget)


def test_a_search_with_no_matches_offers_a_way_out():
    # Verified before this change: 3 drafts and a query of "zzz" left the
    # stack on the list with zero rows, so the user faced a blank
    # rectangle with no hint that the search was the cause.
    store = populated_store()
    panel = bound_panel(store)
    panel._search_edit.setText("zzz-no-such-draft")

    assert panel._body_stack.currentIndex() == 1
    assert panel._empty_title.text() == "No matching drafts"
    assert '"zzz-no-such-draft"' in panel._empty_body.text()
    assert panel._empty_action.isVisibleTo(panel._empty_widget)

    panel._empty_action.click()
    assert panel._search_edit.text() == ""
    assert panel._body_stack.currentIndex() == 0
    assert len(visible_identifiers(panel)) == 4


def test_a_first_refresh_is_not_reported_as_an_empty_library():
    store = DraftStore()
    panel = bound_panel(store)
    assert panel._empty_title.text() == "No private drafts yet"

    store.set_loading(True)
    assert panel._body_stack.currentIndex() == 0
    assert panel._status_label.full_text() == "Refreshing drafts"

    store.set_loading(False)
    assert panel._body_stack.currentIndex() == 1
    assert panel._empty_title.text() == "No private drafts yet"


def test_an_unsupported_signer_outranks_everything_else():
    store = populated_store()
    panel = bound_panel(store)
    panel.set_signer_unsupported(True)
    assert panel._status_label.full_text() == "Signer cannot decrypt drafts (no NIP-44)"
    assert panel._status_label.property("error") == "true"
    assert panel._empty_title.text() == "Signer does not support NIP-44"


def test_failed_drafts_are_counted_on_the_status_line():
    store = populated_store()
    panel = bound_panel(store)
    assert panel._status_label.full_text() == "1 draft could not be decrypted"
    assert panel._status_label.property("error") == "true"
    store.set_failed("d0", "signer timed out")
    assert panel._status_label.full_text() == "2 drafts could not be decrypted"


def test_a_sync_message_survives_the_records_that_arrive_after_it():
    # The measured race: set_status and the store-progress refresh both
    # wrote the label, and the refresh ran on every record_added, so the
    # narration was wiped within milliseconds of being set.
    store = healthy_store()
    panel = bound_panel(store)
    panel.set_status("Refreshing drafts…")
    store.upsert_skeleton(wrap_meta("h9", created_at=1700000009))
    assert panel._status_label.full_text() == "Refreshing drafts…"


def test_the_resting_status_returns_once_a_narration_expires():
    store = healthy_store()
    panel = bound_panel(store)
    panel.set_status("Loaded 2 drafts.")
    assert panel._status_label.full_text() == "Loaded 2 drafts."
    # The timer is what hands the line back; fire it directly rather
    # than sleeping for its whole lifetime.
    panel._on_status_expired()
    assert panel._status_label.full_text() == "Decrypting 1 of 2"


def test_a_long_display_name_neither_clips_nor_widens_the_panel():
    panel = DraftsPanel(is_dark=True)
    panel.set_active_profile(make_profile("A" * 120))
    panel.resize(MIN_PANEL_WIDTH, 400)
    panel.show()
    panel.layout().activate()
    try:
        label = panel._status_label
        assert label.full_text().startswith("Drafts for AAA")
        # The whole string stays reachable even though the painted one is
        # cut, because it never went through setText.
        assert label.accessibleName() == label.full_text()
        painted = label.fontMetrics().elidedText(
            label.full_text(), Qt.ElideRight, label.contentsRect().width(),
        )
        assert painted.endswith("…")
        assert panel.minimumSizeHint().width() <= MIN_PANEL_WIDTH
    finally:
        panel.hide()


def test_decrypt_progress_only_shows_while_work_is_outstanding():
    store = healthy_store()
    panel = bound_panel(store)
    assert panel._status_label.full_text() == "Decrypting 1 of 2"
    store.set_decrypted("h1", inner={"kind": 1, "content": "Second body", "tags": []})
    # "2/2 decrypted" said nothing, and it said it for the whole time the
    # user was actually reading the list.
    assert panel._status_label.full_text() == "Drafts for Alice"


# --------------------------------------------------------------------------- #
# Accessibility                                                               #
# --------------------------------------------------------------------------- #

def test_every_icon_only_control_has_an_accessible_name():
    # Measured before this change: all eleven controls returned "", and
    # the two icon buttons were announced as the glyphs "⟲" and "×".
    panel = DraftsPanel(is_dark=True)
    panel.add_view("published", "Published", "", QWidget())
    segments = panel._view_switch.segments
    names = {
        panel._refresh_btn: "Refresh drafts",
        panel._close_btn: "Close drafts panel",
        panel._search_edit: "Search drafts",
        segments[0]: "Drafts",
        segments[1]: "Published",
        panel._view_switch.popup: "View: Drafts",
        panel._imports_row.button: "Imports",
    }
    for widget, expected in names.items():
        assert widget.accessibleName() == expected
    # The two icon buttons carry one string for both channels, so the
    # spoken label and the hovered label cannot drift apart.
    for button in (panel._refresh_btn, panel._close_btn):
        assert button.accessibleName() == button.toolTip()


def test_the_surviving_profile_chip_is_named():
    # Icon-only, and the control the panel's empty state points at.
    assert ProfileChip().accessibleName() == "Nostr profile"


def test_a_screen_reader_hears_the_row_and_not_a_blank():
    store = populated_store()
    panel = bound_panel(store)
    for i in range(panel._list.count()):
        item = panel._list.item(i)
        spoken = item.data(Qt.AccessibleTextRole)
        assert spoken
        # Words, not glyphs, and an absolute time the row itself cannot
        # show.
        assert "…" not in spoken
        assert "2023" in spoken or "2024" in spoken

    failed = panel._items["d1"]
    spoken = failed.data(Qt.AccessibleTextRole)
    assert "signer timed out" in spoken
    assert "Press Return to retry" in spoken


def test_the_announcement_tracks_the_row_through_a_state_change():
    store = DraftStore()
    store.bind_profile(PK)
    store.upsert_skeleton(wrap_meta("d0"))
    panel = bound_panel(store)
    assert "Decrypting" in panel._items["d0"].data(Qt.AccessibleTextRole)

    store.set_decrypted("d0", inner={"kind": 1, "content": "Real title\n\nbody", "tags": []})
    spoken = panel._items["d0"].data(Qt.AccessibleTextRole)
    assert spoken.startswith("Real title")
    assert "Decrypting" not in spoken


def test_row_announcements_are_composed_from_the_same_text_the_row_shows():
    record = ready_record("A title", snippet="Body words")
    record.inner_tags = [["source", "https://blog.example.com/x"]]
    spoken = _accessible_row_text(record)
    assert spoken.startswith("A title. Imported from blog.example.com. Saved ")
    assert spoken.endswith(".")


# --------------------------------------------------------------------------- #
# Theme tokens                                                                #
# --------------------------------------------------------------------------- #

def hexes_in(css: str) -> set:
    return {m.upper() for m in re.findall(r"#[0-9A-Fa-f]{6}", css)}


# Roles the row widget paints directly, so they never reach the QSS.
_PAINT_ONLY_ROLES = {"selection_bar"}


@pytest.mark.parametrize("is_dark", [True, False])
def test_the_token_table_is_the_only_source_of_colour(is_dark):
    """One definition per colour, so a contrast fix is a single edit.

    The two hand-maintained sheets this replaced carried about sixty
    literal hexes between them, which is why fixing one theme's contrast
    left the other's alone.
    """
    tokens = _THEME_TOKENS[is_dark]
    defined = {v.upper() for v in tokens.values()}
    used = hexes_in(_panel_css(is_dark)) | hexes_in(_menu_css(is_dark))
    assert used <= defined, f"literal hexes outside the token table: {used - defined}"
    # And nothing in the table is dead weight.
    paint_only = {tokens[role].upper() for role in _PAINT_ONLY_ROLES}
    assert defined - used <= paint_only, f"tokens nothing references: {defined - used}"


def test_both_themes_come_from_one_template():
    # The sheets carry comments explaining why some rules are absent, so
    # strip those before asserting that the rules really are absent.
    strip = lambda css: re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    dark, light = strip(_panel_css(True)), strip(_panel_css(False))
    assert dark != light
    for css in (dark, light):
        # Qt treats both px and pt in a stylesheet as absolute and
        # discards em, so any font-size here would freeze the panel
        # while the rest of the app scaled around it. The point sizes
        # are set in Python, from the application font.
        assert "font-size" not in css
        # Dead rules deleted with the controls they styled.
        assert "drafts_panel_chip" not in css
        assert "drafts_panel_heading" not in css
        assert "drafts_row_lock" not in css
        # ::item:hover could never fire: the row widget covers the item
        # rect and is not mouse transparent.
        assert "::item:hover" not in css
        # Removed so the keyboard's current row keeps its indicator.
        assert "outline" not in css

    # Every selector in one theme exists in the other: the two sheets
    # cannot drift, because structurally there is only one sheet.
    def selectors(css):
        return sorted(
            line.strip()
            for line in css.splitlines()
            if line.rstrip().endswith("{") or line.rstrip().endswith(",")
        )

    assert selectors(dark) == selectors(light)


@pytest.mark.parametrize("is_dark", [True, False])
def test_every_text_pair_clears_the_minimum_contrast(is_dark):
    """accessibility.md: up to 17 pt, all weights, 4.5:1.

    Checked in both appearances, and on all three row backgrounds, which
    is where the two hand-maintained sheets had drifted: five text pairs
    failed in light and four in dark.
    """
    t = _THEME_TOKENS[is_dark]
    pairs = [
        ("row title on list", t["row_fg"], t["panel_bg"]),
        ("muted on list", t["muted"], t["panel_bg"]),
        ("muted on hover", t["muted"], t["hover_bg"]),
        ("muted on chrome", t["muted"], t["chrome_bg"]),
        ("field text on list", t["field_fg"], t["panel_bg"]),
        ("chrome text on chrome", t["chrome_fg"], t["chrome_bg"]),
        ("selected title on selection", t["selected_fg"], t["selected_bg"]),
        ("selected muted on selection", t["selected_muted"], t["selected_bg"]),
        ("error on chrome", t["error_fg"], t["chrome_bg"]),
    ]
    for name, fg, bg in pairs:
        ratio = contrast(fg, bg)
        assert ratio >= 4.5, f"{name}: {fg} on {bg} is {ratio:.2f}:1"

    # Disabled text stays deliberately below 4.5 so it still reads as
    # unavailable, but must not fall back to the illegible 2.05 / 1.81 it
    # measured before. labels.md: "Tertiary label: Text that describes an
    # unavailable item or behavior."
    disabled = contrast(t["disabled"], t["chrome_bg"])
    assert 3.0 <= disabled < 4.5, f"{disabled:.2f}:1"

    # The selection bar is the shape cue that carries selection without
    # colour, so it has to be legible on the fill it sits on. The accent
    # is not usable here: #007ACC on #094771 is 2.16:1.
    bar = contrast(t["selection_bar"], t["selected_bg"])
    assert bar >= 3.0, f"selection bar {bar:.2f}:1"


@pytest.mark.parametrize("is_dark", [True, False])
def test_the_search_placeholder_clears_the_minimum_contrast(is_dark):
    """The placeholder is a runtime colour, so the tokens cannot pin it.

    Qt derives PlaceholderText from the stylesheet's ``color`` at half
    alpha, which put the only visible label on the panel's only filter
    at 3.83:1 dark and 2.85:1 light, both under the 4.5:1 that
    accessibility.md requires in "both light and dark appearances".
    """
    tokens = _THEME_TOKENS[is_dark]
    panel = DraftsPanel(is_dark=is_dark)
    # A theme switch is the path that used to reinstate the half-alpha
    # colour, because polishing after setStyleSheet re-derives it.
    panel.apply_theme(not is_dark)
    panel.apply_theme(is_dark)

    field = panel._search_edit
    field.ensurePolished()
    placeholder = field.palette().color(QPalette.PlaceholderText)
    assert placeholder.alpha() == 255
    assert placeholder.name().upper() == tokens["muted"].upper()

    ratio = contrast(placeholder.name(), tokens["panel_bg"])
    assert ratio >= 4.5, f"placeholder {ratio:.2f}:1"
    # The field still has a placeholder to colour, and no other label.
    assert field.placeholderText()


def test_switching_theme_repaints_existing_rows():
    store = populated_store()
    panel = bound_panel(store)
    panel.apply_theme(False)
    for i in range(panel._list.count()):
        widget = panel._list.itemWidget(panel._list.item(i))
        assert widget._is_dark is False
        assert not widget.grab().isNull()
    panel.apply_theme(True)
    assert panel._list.itemWidget(panel._list.item(0))._is_dark is True


# --------------------------------------------------------------------------- #
# The narrow panel                                                            #
# --------------------------------------------------------------------------- #

def test_the_chrome_fits_at_the_minimum_width():
    store = populated_store()
    panel = bound_panel(store)
    panel.set_imports_state(available=True, new_posts=14)
    panel.resize(MIN_PANEL_WIDTH, 600)
    panel.show()
    panel.layout().activate()
    try:
        # The close button is the last thing in the band; if it fits,
        # nothing before it has overflowed.
        right_edge = panel._close_btn.mapTo(panel, panel._close_btn.rect().topRight()).x()
        assert right_edge <= MIN_PANEL_WIDTH, right_edge
        # One view: a title, not a switch.
        assert panel._title.isVisibleTo(panel)
        assert not panel._view_switch.isVisibleTo(panel)
        # accessibility.md: macOS default control size is 28x28 pt.
        for control in (panel._refresh_btn, panel._close_btn):
            assert control.height() >= 28
            assert control.width() >= 28
        assert panel._imports_row.button.height() >= 28
    finally:
        panel.hide()


def corner_in_panel(panel, widget) -> QPoint:
    """A widget's top left in panel coordinates.

    ``x()`` and ``y()`` are relative to each widget's own parent, and
    the switch and the icon buttons sit in different holders.
    """
    return widget.mapTo(panel, widget.rect().topLeft())


# --------------------------------------------------------------------------- #
# Views, and the switch between them                                          #
# --------------------------------------------------------------------------- #

ENGLISH = ("Drafts", "Published")
# The German words are the longest the switch is known to need:
# "Entwürfe | Veröffentlicht".
GERMAN = ("Entwürfe", "Veröffentlicht")


def with_views(labels, width: int) -> DraftsPanel:
    """A panel with a second view, its two views named ``labels``."""
    panel = DraftsPanel(is_dark=True)
    panel._views[0] = (panel.DRAFTS_VIEW, labels[0], "")
    panel._title.set_full_text(labels[0])
    panel.add_view("published", labels[1], "Your published notes and articles", QWidget())
    panel.resize(width, 600)
    panel.show()
    panel.layout().activate()
    return panel


def default_size() -> float:
    """The platform's own text size: what 100 percent means here."""
    return QApplication.font().pointSizeF()


def scaled(app_font, scale: float) -> None:
    font = QFont(app_font.font())
    font.setPointSizeF(app_font.base * scale)
    app_font.setFont(font)


@pytest.fixture
def sized(app_font):
    app_font.base = default_size()
    return app_font


def test_one_view_shows_its_title():
    panel = DraftsPanel(is_dark=True)
    assert panel.views() == ["drafts"]
    assert panel._title.full_text() == "Drafts"
    assert not panel._view_switch.has_choice()


def test_a_second_view_brings_the_switch():
    panel = with_views(ENGLISH, 320)
    try:
        switch = panel._view_switch
        assert not panel._title.isVisibleTo(panel)
        assert switch.isVisibleTo(panel)
        changed = []
        panel.view_changed.connect(changed.append)
        if switch.is_popup():
            switch.popup.menu().actions()[1].trigger()
        else:
            switch.segments[1].click()
        assert changed == ["published"]
        assert panel.current_view() == "published"
        assert switch.popup.text() == "Published"
        panel.show_view("drafts")
        assert panel.current_view() == "drafts"
    finally:
        panel.hide()


def test_a_view_is_added_once():
    panel = DraftsPanel(is_dark=True)
    panel.add_view("published", "Published", "", QWidget())
    with pytest.raises(ValueError):
        panel.add_view("published", "Published", "", QWidget())


def shown_words(panel) -> list:
    """What the switch paints, as words."""
    switch = panel._view_switch
    if switch.is_popup():
        return [switch.popup.painted_text()]
    return [segment.painted_text() for segment in switch.segments]


def wanted_words(panel, labels) -> list:
    switch = panel._view_switch
    return [labels[0]] if switch.is_popup() else list(labels)


# 100 to 200 percent of the platform's own text size: accessibility.md
# asks apps to let text grow to at least 200 percent.
@pytest.mark.parametrize("scale", [1.0, 1.3, 1.7, 2.0])
@pytest.mark.parametrize("width", [MIN_PANEL_WIDTH, 320])
@pytest.mark.parametrize("labels", [ENGLISH, GERMAN])
def test_the_switch_never_cuts_a_word(sized, scale, width, labels):
    """Segments where they fit, else a pop-up button naming the current
    view: either way every word is painted whole, at every size up to
    200 percent, on the narrowest panel."""
    scaled(sized, scale)
    panel = with_views(labels, width)
    try:
        assert shown_words(panel) == wanted_words(panel, labels), (
            f"{labels} at {scale:.0%} on {width}px painted {shown_words(panel)}")
        switch = panel._view_switch
        if not switch.is_popup():
            for segment in switch.segments:
                assert segment.width() >= segment.sizeHint().width()
        right = panel._close_btn.mapTo(panel, panel._close_btn.rect().topRight()).x()
        assert right <= width
    finally:
        panel.hide()


@pytest.mark.parametrize("labels", [ENGLISH, GERMAN])
def test_the_band_stays_on_one_line_at_the_default_font(labels):
    # Changing control costs nothing; a second line of chrome costs every
    # normal user space, so the default size is pinned as firmly as the
    # enlarged one.
    for panel in (DraftsPanel(is_dark=True), with_views(labels, MIN_PANEL_WIDTH)):
        panel.resize(MIN_PANEL_WIDTH, 600)
        panel.show()
        try:
            panel.layout().activate()
            assert not panel._top_band.is_stacked()
            leading = (panel._view_switch if panel._view_switch.has_choice()
                       else panel._title)
            assert (corner_in_panel(panel, panel._refresh_btn).y()
                    == corner_in_panel(panel, leading).y())
        finally:
            panel.hide()


def test_the_band_stacks_only_when_the_popup_cannot_sit_beside_the_buttons(sized):
    """typography.md: "Consider adjusting your layout at large font
    sizes." The pop-up button comes first; the buttons only move down
    when even it does not fit beside them, and then the word stays
    whole on its own line."""
    stacked = None
    for scale in (1.0, 1.5, 2.0, 2.5, 3.0):
        scaled(sized, scale)
        panel = with_views(GERMAN, MIN_PANEL_WIDTH)
        try:
            switch = panel._view_switch
            if panel._top_band.is_stacked():
                assert switch.is_popup()
                assert (corner_in_panel(panel, panel._refresh_btn).y()
                        > corner_in_panel(panel, switch).y())
                stacked = scale
                break
            if switch.is_popup():
                assert (switch.popup_width() + panel._top_band._icons.sizeHint().width()
                        + 6 <= MIN_PANEL_WIDTH - dp.GUTTER - 8)
        finally:
            panel.hide()
    # Somewhere up to three times the platform's size, the buttons do move.
    assert stacked is not None


def test_past_every_size_the_popup_elides_and_keeps_its_name(app_font):
    """Far beyond 200 percent no control fits: the pop-up button then
    paints an ellipsis, and the whole name stays in its tooltip and its
    accessible name. Pins the pixels, not just ``painted_text``."""
    font = QFont(app_font.font())
    font.setPointSizeF(64)
    app_font.setFont(font)
    panel = with_views(GERMAN, MIN_PANEL_WIDTH)
    try:
        popup = panel._view_switch.popup
        assert panel._view_switch.is_popup()
        painted = popup.painted_text()
        assert painted != "Entwürfe" and painted.endswith("…")
        assert popup.toolTip() == "Entwürfe"
        assert popup.accessibleName() == "View: Entwürfe"
    finally:
        panel.hide()


@pytest.mark.parametrize("labels", [ENGLISH, GERMAN])
def test_long_view_names_never_widen_the_panel(sized, labels):
    scaled(sized, 2.0)
    panel = with_views(labels, MIN_PANEL_WIDTH)
    try:
        assert panel.minimumSizeHint().width() <= MIN_PANEL_WIDTH
    finally:
        panel.hide()


def test_the_whole_panel_paints_at_the_minimum_width():
    for is_dark in (True, False):
        store = populated_store()
        panel = DraftsPanel(is_dark=is_dark)
        panel.set_active_profile(make_profile())
        panel.bind_store(store)
        panel.set_imports_state(available=True, new_posts=3, activity="Creating drafts, 1 of 3",
                                action="pause")
        panel.resize(MIN_PANEL_WIDTH, 600)
        assert not panel.grab().isNull()


# --------------------------------------------------------------------------- #
# The Imports row (D-2)                                                       #
# --------------------------------------------------------------------------- #

def test_the_imports_row_waits_for_an_account():
    panel = DraftsPanel(is_dark=True)
    assert panel._imports_row.isHidden()
    panel.set_imports_state(available=True, new_posts=0)
    assert not panel._imports_row.isHidden()
    assert panel._imports_row.button.accessibleName() == "Imports"
    assert panel._imports_row.button.count_text() == ""


def test_the_imports_row_says_how_many_and_opens_the_window():
    panel = DraftsPanel(is_dark=True)
    opened = []
    panel.open_imports.connect(lambda: opened.append(True))
    panel.set_imports_state(available=True, new_posts=14)
    button = panel._imports_row.button
    assert button.count_text() == "14 new"
    assert button.accessibleName() == "Imports, 14 new posts"
    button.click()
    assert opened == [True]


def test_the_imports_row_pauses_and_resumes():
    panel = DraftsPanel(is_dark=True)
    asked = []
    panel.pause_import.connect(lambda: asked.append("pause"))
    panel.resume_import.connect(lambda: asked.append("resume"))
    row = panel._imports_row
    panel.set_imports_state(available=True, new_posts=2,
                            activity="Creating drafts, 1 of 3", action="pause")
    assert row.activity.text() == "Creating drafts, 1 of 3"
    assert row.action.text() == "Pause"
    row.action.click()
    panel.set_imports_state(available=True, activity="Import paused, 1 of 3",
                            action="resume")
    assert row.action.text() == "Resume"
    row.action.click()
    panel.set_imports_state(available=True, activity="1 post couldn't be imported",
                            action="retry")
    assert row.action.text() == "Try Again"
    row.action.click()
    assert asked == ["pause", "resume", "resume"]
    panel.set_imports_state(available=True)
    assert row._activity_line.isHidden()


@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_the_imports_row_reads_whole(sized, scale):
    """The row that replaced the Feeds segment fits the narrowest panel
    at up to 200 percent with its words whole (the Linux fonts made the
    old switch clip here)."""
    scaled(sized, scale)
    panel = DraftsPanel(is_dark=True)
    panel.set_imports_state(available=True, new_posts=148,
                            activity="Creating drafts, 31 of 48", action="pause")
    panel.resize(MIN_PANEL_WIDTH, 600)
    panel.show()
    try:
        panel.layout().activate()
        row = panel._imports_row
        assert row.button.painted_title() == "Imports"
        # The count stays whole too: under the title where it does not fit
        # beside it, the button a line taller for it.
        if row.button.is_stacked():
            assert row.button.height() >= row.button.heightForWidth(row.button.width())
        right = row.action.mapTo(panel, row.action.rect().topRight()).x()
        assert right <= MIN_PANEL_WIDTH
        assert row.action.width() >= row.action.sizeHint().width()
        assert panel.minimumSizeHint().width() <= MIN_PANEL_WIDTH
    finally:
        panel.hide()


def test_every_control_has_a_name():
    panel = DraftsPanel(is_dark=True)
    panel.set_imports_state(available=True, new_posts=3, activity="Creating drafts, 1 of 3",
                            action="pause")
    assert unnamed_controls(panel) == []
    two = with_views(GERMAN, MIN_PANEL_WIDTH)
    try:
        assert unnamed_controls(two) == []
    finally:
        two.hide()
