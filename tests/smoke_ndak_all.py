# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run every live ndak smoke test, one after another, and sum them up.

Not run by pytest (no ``test_`` prefix). Run with the ndak relays up:

    (cd ~/projects/ndak && docker compose up -d)
    .venv/bin/python tests/smoke_ndak_all.py              # all areas
    .venv/bin/python tests/smoke_ndak_all.py drafts media # some areas

The media area also needs Docker for its Blossom servers
(``docker pull ghcr.io/hzrd149/blossom-server:master`` once); resilience
restarts ndak's nostr-rs-relay container once. Results are
written as JSON to $NDAK_OUT (default: <system temp>/ndak-results), one file
per area, and summed up here. Exits 1 when any check failed or anything
tried to reach a host that is not this computer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AREAS = ("accounts", "people", "publish", "drafts", "imports", "media", "membership", "app",
         "resilience")


def main(argv) -> int:
    areas = [a for a in argv if a in AREAS] or list(AREAS)
    out = Path(os.environ.get("NDAK_OUT") or Path(tempfile.gettempdir()) / "ndak-results")
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, NDAK_OUT=str(out))
    totals, failed = {}, []
    for area in areas:
        script = HERE / f"smoke_ndak_{area}.py"
        print(f"\n######## {area}")
        started = time.time()
        code = subprocess.call([sys.executable, "-u", str(script)], env=env, cwd=str(HERE.parent))
        result = out / f"{area}.json"
        if not result.exists() or result.stat().st_mtime < started:
            failed.append(f"{area}: exited {code} without results")
            continue
        data = json.loads(result.read_text())
        for check in data["checks"]:
            totals[check["status"]] = totals.get(check["status"], 0) + 1
            if check["status"] == "FAIL":
                failed.append(f"{area}: {check['check']}")
        if data.get("violations"):
            failed.append(f"{area}: contacted a host off this computer: {data['violations'][:3]}")
    print("\n######## summary: " + ", ".join(f"{v} {k}" for k, v in sorted(totals.items())))
    for line in failed:
        print("  FAIL " + line)
    print(f"results in {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
