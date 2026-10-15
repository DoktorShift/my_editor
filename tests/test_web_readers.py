# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins where a published post opens on the web (nostr/web_readers.py).

What must hold:

  Standup Discover comes first and is the default for articles and
  curations, and is never offered for a short note, where njump is
  first. Each app's link is the one its own code defines. Standup's
  addresses are the ones its server serves (backend/app.py), the most
  precise first: an article a site lists on the site's page (the
  owner-qualified address from Standup 1.5, then the site's short one),
  then Discover's page for the article by author and identifier; never
  /a/<naddr>, which the live Discover does not serve. Identifiers go in
  as Standup writes them. The first address that is there is opened.
  Every link is one a browser may be handed.
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
AUTHOR = "b" * 64
SITES = "https://media.einundzwanzig.space"
DISCOVER = "https://discover.einundzwanzig.space"


def test_discover_comes_first_for_articles_and_curations_and_never_for_notes():
    assert [r.key for r in wr.readers_for("article")] == [
        "discover", "njump", "yakihonne", "primal", "nostrudel"]
    assert [r.key for r in wr.readers_for("curation")] == ["discover", "njump", "yakihonne"]
    notes = [r.key for r in wr.readers_for("note")]
    assert "discover" not in notes and notes[0] == "njump"


@pytest.mark.parametrize("kind, key, expected", [
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
    post = wr.Post(kind, entity=NEVENT if kind == "note" else NADDR, author=AUTHOR,
                   identifier="x")
    link = wr.link_for(wr.reader(key), post)
    assert link == expected and url_safety.is_safe_external_url(link)


def test_an_article_in_no_site_is_read_in_discover_by_author_and_identifier():
    post = wr.Post("article", entity=NADDR, author=AUTHOR.upper(), identifier="my-post")
    assert wr.links_for(wr.reader("discover"), post) == (f"{DISCOVER}/a/{AUTHOR}/my-post",)


def test_an_article_in_a_site_is_read_on_the_sites_page_first():
    post = wr.Post("article", entity=NADDR, author=AUTHOR, identifier="my-post",
                   site=wr.Site(OWNER, "my-blog"))
    assert wr.links_for(wr.reader("discover"), post) == (
        f"{SITES}/s/by/{OWNER}/bXktYmxvZw/my-post",    # Standup 1.5: unambiguous
        f"{SITES}/s/my-blog/my-post",                  # served today
        f"{DISCOVER}/a/{AUTHOR}/my-post")              # any article Standup has


def test_a_curation_is_read_on_its_sites_page():
    post = wr.Post("curation", entity=NADDR, author=OWNER.upper(), identifier="my-blog")
    assert wr.links_for(wr.reader("discover"), post) == (
        f"{SITES}/s/by/{OWNER}/bXktYmxvZw", f"{SITES}/s/my-blog")


def test_identifiers_go_in_as_standup_writes_them():
    # An identifier can be a whole web address (RSS imports).
    post = wr.Post("article", author=AUTHOR, identifier="https://blog.example/p?id=1",
                   site=wr.Site(OWNER, "a/b"))
    links = wr.links_for(wr.reader("discover"), post)
    assert links[1] == f"{SITES}/s/a-b/https:--blog.example-p-id-1"
    assert links[0].split("/")[-2] == "YS9i"            # "a/b", URL-safe base64


def test_the_default_is_the_chosen_app_where_it_can_show_the_post():
    assert wr.default_reader("article", "").key == "discover"
    assert wr.default_reader("article", "primal").key == "primal"
    assert wr.default_reader("note", "discover").key == "njump"
    assert wr.default_reader("note", "primal").key == "primal"
    assert wr.default_reader("curation", "primal").key == "discover"
    assert wr.default_reader("article", "no-such-app").key == "discover"


def test_no_link_without_an_address():
    assert wr.link_for(wr.reader("njump"), wr.Post("note")) == ""
    assert wr.links_for(wr.reader("discover"), wr.Post("note", entity=NEVENT)) == ()
    assert wr.links_for(wr.reader("discover"), wr.Post("article", entity=NADDR)) == ()


@pytest.mark.parametrize("answers, chosen", [
    ({"a": True}, ("a", True)),                          # the first is there
    ({"a": False, "b": True}, ("b", True)),              # the next that is
    ({"a": False, "b": None}, ("b", None)),              # no telling: tried as it is
    ({"a": False, "b": False, "c": False}, ("c", False)),  # none is
])
def test_the_first_address_that_is_there_is_chosen(answers, chosen):
    check = wr.PageCheck()
    asked = []
    check.check = lambda url, done: (asked.append(url), done(answers.get(url, False)))
    found = []
    check.first_found(["a", "b", "c"], lambda url, there: found.append((url, there)))
    assert found == [chosen] and asked == list("abc")[:len(asked)]
