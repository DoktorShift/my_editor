# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Release builds install exact versions; the lock files must keep up.

requirements.txt says which versions the app works with; the lock files in
packaging/ say which ones a release build and the test workflow install
(compiled with uv, see packaging/README.md). A requirement added to
requirements.txt but not compiled into the locks would be missing from
every installer, so this fails until the locks are compiled again.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def names(path, *, pinned=False):
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "\\")):
            continue
        if pinned and "==" not in line:
            continue
        match = _NAME.match(line)
        if match:
            found.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return found


def test_every_requirement_is_in_the_build_lock():
    missing = names(ROOT / "requirements.txt") - names(
        ROOT / "packaging" / "requirements-build.lock", pinned=True)
    assert missing == set(), "compile packaging/requirements-build.lock again"


def test_every_requirement_and_sidecar_requirement_is_in_the_test_lock():
    wanted = names(ROOT / "requirements.txt") | names(ROOT / "sidecar" / "requirements.txt")
    missing = wanted - names(ROOT / "packaging" / "requirements-test.lock", pinned=True)
    assert missing == set(), "compile packaging/requirements-test.lock again"


def test_the_bundler_is_pinned_for_release_builds():
    assert "pyinstaller" in names(ROOT / "packaging" / "requirements-build.lock", pinned=True)


def test_every_pinned_package_carries_a_hash():
    for lock in ("requirements-build.lock", "requirements-test.lock"):
        text = (ROOT / "packaging" / lock).read_text(encoding="utf-8")
        for block in re.split(r"\n(?=[a-z0-9])", text):
            if "==" in block.split("\n", 1)[0]:
                assert "--hash=sha256:" in block, f"{lock}: {block.splitlines()[0]}"
