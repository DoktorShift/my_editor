# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A file's access list on Windows, read and changed with icacls, the
system's own tool: what Windows has where macOS and Linux have a file's
permission bits."""

from __future__ import annotations

import subprocess
from typing import List

# Everyone, named by its well-known security identifier, so the entry is
# made the same whatever the system's language.
EVERYONE = "*S-1-1-0"


def access_list(path) -> List[str]:
    """The entries of the file's access list, as icacls lists them
    ("Everyone:(R)", "NT AUTHORITY\\SYSTEM:(I)(F)"), in their order."""
    out = subprocess.run(["icacls", str(path)], capture_output=True, text=True,
                         check=True).stdout
    entries = []
    for line in out.replace(str(path), "", 1).splitlines():
        line = line.strip()
        if line and not line.startswith("Successfully processed"):
            entries.append(line)
    return entries


def let_everyone_read(path) -> None:
    """Give Everyone read access to the file, an entry of its own (not one
    its folder passes on), as sharing a file with others does."""
    subprocess.run(["icacls", str(path), "/grant", EVERYONE + ":(R)"], capture_output=True,
                   check=True)
