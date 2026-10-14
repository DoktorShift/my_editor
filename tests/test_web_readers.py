# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins where a published post opens on the web (nostr/web_readers.py).

What must hold:

  Standup Discover comes first and is the default for articles and
  curations, and is never offered for a short note, where njump is
  first. Each app's link is the one its own code defines (verified in
  each app's router, October 2026). A chosen default is used where that
  app can show the post, and otherwise the first app that can. Every
  link is one a browser may be handed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

import url_safety  # noqa: E402
from nostr import web_readers as wr  # noqa: E402

NADDR = "naddr1qqxnzdesxqmnxvpexqunzvpcqyt8wumn8ghj7un9d3shjtnwdaehgu3wvfskueqzyp"
NEVENT = "nevent1qqsfel9fdjee5mzhkft4wma95e9zd22jyzdchau4a8s3guva3jr94ysz"
OWNER = "a" * 64


def test_discover_comes_first_for_articles_and_curations_and_never_for_notes():
    assert [r.key for r in wr.readers_for("article")] == [
        "discover", "njump", "yakihonne", "primal", "nostrudel"]
    assert [r.key for r in wr.readers_for("curation")] == ["discover", "njump", "yakihonne"]
    notes = [r.key for r in wr.readers_for("note")]
    assert "discover" not in notes and notes[0] == "njump"


@pytest.mark.parametrize("kind, key, expected", [
    ("article", "discover", f"https://discover.einundzwanzig.space/a/{NADDR}"),
    ("article", "njump", f"https://njump.me/{NADDR}"),
    ("article", "yakihonne", f"https://yakihonne.com/article/{NADDR}"),
    ("article", "primal", f"https://primal.net/a/{NADDR}"),
    ("article", "nostrudel", f"https://nostrudel.ninja/articles/{NADDR}"),
    ("note", "njump", f"https://njump.me/{NEVENT}"),
    ("note", "yakihonne", f"https://yakihonne.com/note/{NEVENT}"),
    ("note", "primal", f"https://primal.net/e/{NEVENT}"),
    ("note", "nostrudel", f"https://nostrudel.ninja/n/{NEVENT}"),
    ("curation", "njump", f"https://njump.me/{NADDR}"),
    ("curation", "yakihonne", f"https://yakihonne.com/curation/{NADDR}"),
])
def test_each_app_has_the_link_its_own_code_defines(kind, key, expected):
    entity = NEVENT if kind == "note" else NADDR
    link = wr.link_for(wr.reader(key), kind, entity=entity, owner=OWNER, identifier="x")
    assert link == expected
    assert url_safety.is_safe_external_url(link)


def test_a_curation_opens_in_the_standup_reader_by_owner_and_its_identifier():
    # The owner-qualified route Discover's own collection links use
    # (standup core/site_address.py): the d tag in URL-safe base64.
    link = wr.link_for(wr.reader("discover"), "curation", entity=NADDR, owner=OWNER.upper(),
                       identifier="my-blog")
    assert link == f"https://media.einundzwanzig.space/s/by/{OWNER}/bXktYmxvZw"


def test_the_default_is_the_chosen_app_where_it_can_show_the_post():
    assert wr.default_reader("article", "").key == "discover"
    assert wr.default_reader("article", "primal").key == "primal"
    assert wr.default_reader("note", "discover").key == "njump"
    assert wr.default_reader("note", "primal").key == "primal"
    assert wr.default_reader("curation", "primal").key == "discover"
    assert wr.default_reader("article", "no-such-app").key == "discover"


def test_no_link_without_an_address():
    assert wr.link_for(wr.reader("njump"), "note", entity="") == ""
    assert wr.link_for(wr.reader("discover"), "note", entity=NEVENT) == ""
