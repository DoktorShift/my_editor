# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A post's date as Unix seconds (``nostr.rss.dates``)."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from nostr.rss.dates import to_unix


class ToUnixTests(unittest.TestCase):
    def test_how_sources_write_a_date(self) -> None:
        cases = {
            "2024-01-02T10:00:00Z": 1704189600,              # JSON Feed, JSON-LD, Ghost
            "2024-01-02T10:00:00.000Z": 1704189600,
            "2024-01-02T11:00:00+01:00": 1704189600,         # Open Graph
            "2024-01-02 10:00:00": 1704189600,               # WordPress export (UTC)
            "Tue, 02 Jan 2024 10:00:00 +0000": 1704189600,   # RSS pubDate
            "2024-01-02": 1704153600,                        # front matter, sitemap
            datetime(2024, 1, 2, 10, tzinfo=timezone.utc): 1704189600,
            date(2024, 1, 2): 1704153600,
        }
        for value, seconds in cases.items():
            self.assertEqual(to_unix(value), seconds, value)

    def test_no_date_is_none(self) -> None:
        for value in (None, "", "soon", "0000-00-00 00:00:00", "1970-01-01T00:00:00Z", 5):
            self.assertIsNone(to_unix(value), value)


if __name__ == "__main__":
    unittest.main()
