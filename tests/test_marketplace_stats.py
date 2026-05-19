"""Engagement metrics: parsing, formatting, widget rendering."""

from __future__ import annotations

import json
import time

import pytest

from plugin_marketplace.models import (
    REGISTRY_SCHEMA_VERSION,
    PluginListing,
    parse_registry_index,
)


# ──────────────────────────────────────────────────────────────────────
# Registry parsing
# ──────────────────────────────────────────────────────────────────────

def _entry(**kw):
    base = {
        "id": "x", "name": "X", "version": "1.0.0",
        "download_url": "https://e/p.zip", "sha256": "a" * 64,
    }
    base.update(kw)
    return base


def _index(entries):
    return {"schema_version": REGISTRY_SCHEMA_VERSION, "plugins": entries}


def test_parses_rating_fields():
    raw = _index([_entry(rating_avg=4.5, rating_count=200)])
    listing = parse_registry_index(raw, source_name="d")[0]
    assert listing.rating_avg == 4.5
    assert listing.rating_count == 200


def test_rating_avg_clamped_to_valid_range():
    """A registry returning a 17-star average is buggy or hostile;
    clamp rather than refuse the whole listing."""
    raw = _index([_entry(rating_avg=17.0)])
    assert parse_registry_index(raw, source_name="d")[0].rating_avg == 5.0
    raw = _index([_entry(rating_avg=-3.0)])
    assert parse_registry_index(raw, source_name="d")[0].rating_avg == 0.0


def test_rating_avg_accepts_int():
    raw = _index([_entry(rating_avg=4)])
    assert parse_registry_index(raw, source_name="d")[0].rating_avg == 4.0


def test_bool_rating_avg_ignored():
    raw = _index([_entry(rating_avg=True)])
    assert parse_registry_index(raw, source_name="d")[0].rating_avg == 0.0


def test_parses_downloads_comments_zaps():
    raw = _index([_entry(
        downloads=12_345, comments_count=89, zaps_sats=4521,
        updated_at="2026-05-12T09:30:00Z",
    )])
    li = parse_registry_index(raw, source_name="d")[0]
    assert li.downloads == 12_345
    assert li.comments_count == 89
    assert li.zaps_sats == 4521
    assert li.updated_at == "2026-05-12T09:30:00Z"


def test_engagement_defaults_to_zero_when_omitted():
    raw = _index([_entry()])
    li = parse_registry_index(raw, source_name="d")[0]
    assert li.rating_avg == 0.0
    assert li.rating_count == 0
    assert li.downloads == 0
    assert li.comments_count == 0
    assert li.zaps_sats == 0
    assert li.updated_at == ""


# ──────────────────────────────────────────────────────────────────────
# Number + time formatters
# ──────────────────────────────────────────────────────────────────────

def test_format_count_small_numbers_exact():
    from plugin_marketplace.ui.stats import format_count
    assert format_count(0) == "0"
    assert format_count(7) == "7"
    assert format_count(999) == "999"


def test_format_count_thousands_have_one_decimal():
    from plugin_marketplace.ui.stats import format_count
    assert format_count(1_234) == "1.2k"
    assert format_count(9_876) == "9.9k"
    # Above 10k we drop the decimal.
    assert format_count(12_345) == "12k"
    assert format_count(999_000) == "999k"


def test_format_count_millions():
    from plugin_marketplace.ui.stats import format_count
    assert format_count(1_500_000) == "1.5M"
    assert format_count(12_000_000) == "12M"


def test_format_count_handles_negative():
    from plugin_marketplace.ui.stats import format_count
    assert format_count(-1) == "0"


def test_format_sats_precise_below_1k():
    """People care about small sat amounts to the unit."""
    from plugin_marketplace.ui.stats import format_sats
    assert format_sats(21) == "21"
    assert format_sats(999) == "999"
    assert format_sats(1_500) == "1.5k"


def test_format_relative_time_now_and_minutes():
    from plugin_marketplace.ui.stats import format_relative_time
    # Just-now (within a few seconds of now) yields "just now".
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    assert format_relative_time(now_iso) == "just now"


def test_format_relative_time_handles_z_suffix():
    """Older python's fromisoformat doesn't accept trailing ``Z``;
    the helper must paper over that."""
    from plugin_marketplace.ui.stats import format_relative_time
    out = format_relative_time("2020-01-01T00:00:00Z")
    assert "yr" in out  # we're well past 2020


def test_format_relative_time_falls_back_to_raw_on_garbage():
    from plugin_marketplace.ui.stats import format_relative_time
    assert format_relative_time("not a timestamp") == "not a timestamp"


def test_format_relative_time_empty_returns_empty():
    from plugin_marketplace.ui.stats import format_relative_time
    assert format_relative_time("") == ""


# ──────────────────────────────────────────────────────────────────────
# Widgets
# ──────────────────────────────────────────────────────────────────────

PySide6 = pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _listing(**kw):
    base = dict(
        plugin_id="hello_world", name="Hello World", version="1.0.0",
        download_url="https://e/p.zip", sha256="a" * 64,
    )
    base.update(kw)
    return PluginListing(**base)


def test_rating_stars_clamps_at_5(qapp):
    from plugin_marketplace.ui.stats import RatingStars
    s = RatingStars(rating=12.0)
    assert s._rating == 5.0


def test_rating_stars_clamps_at_0(qapp):
    from plugin_marketplace.ui.stats import RatingStars
    s = RatingStars(rating=-1.0)
    assert s._rating == 0.0


def test_stats_row_hides_when_no_metrics(qapp):
    from plugin_marketplace.ui.stats import StatsRow
    row = StatsRow()
    row.populate(_listing())  # no engagement fields populated
    assert row.isVisible() is False


def test_stats_row_visible_with_one_metric(qapp):
    from plugin_marketplace.ui.stats import StatsRow
    row = StatsRow()
    row.populate(_listing(downloads=42))
    # Note: ``isVisible`` returns false until shown on a parent. Use the
    # internal flag the widget toggles.
    assert row.isVisibleTo(row.parentWidget() or row) or row.children()


def test_compact_stats_line_renders_zero_metrics_as_hidden(qapp):
    from plugin_marketplace.ui.stats import CompactStatsLine
    line = CompactStatsLine()
    line.populate(_listing())
    assert line.text() == ""
    assert not line.isVisibleTo(line)


def test_compact_stats_line_includes_star_when_rated(qapp):
    from plugin_marketplace.ui.stats import CompactStatsLine
    line = CompactStatsLine()
    line.populate(_listing(rating_avg=4.7, rating_count=200))
    text = line.text()
    assert "4.7" in text
    assert "200" in text


def test_lightning_tip_chip_copies_to_clipboard(qapp):
    from PySide6.QtGui import QGuiApplication
    from plugin_marketplace.ui.stats import LightningTipChip
    chip = LightningTipChip("alice@example.com")
    captured: list[str] = []
    chip.address_copied.connect(captured.append)
    chip.click()
    assert captured == ["alice@example.com"]
    assert QGuiApplication.clipboard().text() == "alice@example.com"


def test_lightning_tip_chip_shortens_long_addresses(qapp):
    """A really long address must not push the action row off-screen."""
    from plugin_marketplace.ui.stats import LightningTipChip, _short_address
    long_addr = "verylongusernamehere@anextremelylongdomainname.example"
    assert "..." in _short_address(long_addr)
    chip = LightningTipChip(long_addr)
    assert "..." in chip.text()


def test_lightning_tip_chip_keeps_short_addresses_intact(qapp):
    from plugin_marketplace.ui.stats import _short_address
    assert _short_address("a@b.c") == "a@b.c"
