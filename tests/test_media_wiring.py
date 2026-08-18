# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The media seams the application actually connects on startup.

Every object in the media stack is injected, which is what makes the
rest of the suite able to test it. The cost is that a seam can be left
unplugged without a single test noticing: the mechanism keeps its
coverage, the app quietly loses the behaviour, and the symptom only
shows up on a machine that is offline.

That is exactly what happened to the blob cache. ``MediaStore`` seeds
the cache it is given and ``tests/test_blossom_store.py`` pins that, but
nothing checked that the running app gives it one. Removing the keyword
argument from ``MainWindow`` left the whole suite green while an
uploaded image went back to being downloaded from a server to be shown.

``MainWindow`` cannot be constructed here to check it the direct way. Its
``__init__`` reads the real settings file, builds a relay pool, and for
a profile from a previous session starts a metadata fetch, so a test
that built one would touch the user's configuration and the network,
which AD-8 forbids. ``tests/test_media_document.py`` works around the
same problem by lifting unbound methods off the class. Its constructor
has no methods to lift, so the wiring is read out of the source instead:
narrower than running it, and it does catch a dropped argument and a
reordered construction, which is the whole failure mode.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from main_window import MainWindow


@pytest.fixture(scope="module")
def init_body():
    """The statements of ``MainWindow.__init__``, in order."""
    source = textwrap.dedent(inspect.getsource(MainWindow.__init__))
    return ast.parse(source).body[0].body


def statement_index(body, matches) -> int:
    """Position of the first top-level statement containing a match."""
    for index, statement in enumerate(body):
        if any(matches(node) for node in ast.walk(statement)):
            return index
    return -1


def constructor(body, name: str) -> ast.Call:
    """The one ``name(...)`` call in ``body``."""
    calls = [
        node for statement in body for node in ast.walk(statement)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == name
    ]
    assert len(calls) == 1, f"expected exactly one {name}(...), got {len(calls)}"
    return calls[0]


def attribute_argument(call: ast.Call, keyword: str) -> str:
    """The ``self.<attr>`` a keyword argument names, or an empty string."""
    for kwarg in call.keywords:
        if kwarg.arg != keyword:
            continue
        value = kwarg.value
        if (isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id == "self"):
            return value.attr
    return ""


def assigns(attr: str):
    def matches(node) -> bool:
        return (isinstance(node, ast.Attribute)
                and node.attr == attr
                and isinstance(node.ctx, ast.Store))
    return matches


def calls(name: str):
    def matches(node) -> bool:
        return isinstance(node, ast.Call) and getattr(node.func, "id", "") == name
    return matches


def test_the_media_store_is_given_a_blob_cache(init_body):
    """AD-2: an uploaded image is displayable with the network gone.

    The store writes the bytes into this cache before it sends them, so
    a failed upload, a retry and an offline session all still have
    something to show. Without the argument the store keeps uploading
    and silently caches nothing.
    """
    assert attribute_argument(constructor(init_body, "MediaStore"),
                              "blob_cache") == "_media_image_loader"


def test_the_cache_the_store_seeds_is_the_one_the_editor_reads_from(init_body):
    """One cache, or the seeding writes where nothing ever reads.

    ``AssetManager`` resolves a document's images through the blob store
    it is handed. Seeding a different object would be a write with no
    reader and the offline guarantee would still be broken.
    """
    store = constructor(init_body, "MediaStore")
    assets = constructor(init_body, "AssetManager")
    assert attribute_argument(store, "blob_cache") == "_media_image_loader"
    assert attribute_argument(assets, "blob_store") == "_media_image_loader"


def test_the_cache_is_built_before_the_store_that_seeds_it(init_body):
    """Construction order is load bearing, so it is pinned.

    The cache used to be created after the store. Passing it in means it
    has to exist first, and moving it back is an ``AttributeError`` at
    startup rather than anything a running test would report.
    """
    cache_at = statement_index(init_body, assigns("_media_image_loader"))
    store_at = statement_index(init_body, calls("MediaStore"))
    assert cache_at >= 0 and store_at >= 0
    assert cache_at < store_at
