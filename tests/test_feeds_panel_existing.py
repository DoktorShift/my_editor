# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Importing again never overwrites a draft (T-M3-1).

Re-running an import used to sign every item again under the same
identifier, which replaced the draft on the relays, edits and all. Now
the panel asks what exists first: an item with a draft, an article or a
deletion is shown as already imported and nothing of it is signed, and
when no relay answers the import does not start at all.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from nostr.rss.dtag import derive_identifier
from tests.imports_fakes import TWO_ITEM_FEED, FakeCatalogue, FakeFetcher
from tests.test_feeds_panel import make_panel

FIRST = derive_identifier(guid="g1", prefix="rss-")


def loaded(catalogue):
    fetcher = FakeFetcher({"https://example.com/feed": ("ok", TWO_ITEM_FEED)})
    panel, created, kwargs = make_panel(fetcher, catalogue=catalogue)
    panel._url_edit.setText("https://example.com/feed")
    panel._on_load_clicked()
    return panel, created


def test_an_item_already_imported_is_left_alone():
    catalogue = FakeCatalogue(existing={FIRST: "drafted"})
    panel, created = loaded(catalogue)
    panel._on_import_clicked()
    assert [job.identifier for job in created] == [derive_identifier(guid="g2",
                                                                     prefix="rss-")]
    texts = [panel._list.item(i).text() for i in range(panel._list.count())]
    assert any("First" in t and "already imported" in t for t in texts)
    assert "1 was already imported and left as it is." in panel._status_label.text()


def test_everything_already_imported():
    catalogue = FakeCatalogue(existing={FIRST: "drafted",
                                        derive_identifier(guid="g2", prefix="rss-"):
                                        "published"})
    panel, created = loaded(catalogue)
    panel._on_import_clicked()
    assert created == []
    assert "All 2 items were already imported" in panel._status_label.text()


def test_no_answer_means_no_import():
    catalogue = FakeCatalogue(unavailable="Couldn't check your existing drafts.")
    panel, created = loaded(catalogue)
    panel._list.item(1).setCheckState(Qt.Unchecked)
    panel._on_import_clicked()
    assert created == []
    assert panel._state == "preview"
    assert panel._status_label.text() == "Couldn't check your existing drafts."
    # The person's choice is kept for the next try.
    checks = [panel._list.item(i).checkState() for i in range(panel._list.count())]
    assert checks.count(Qt.Checked) == 1
