# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The live checks' report says every check on a line a runner counts.

The runners count the printed "[PASS]" and "[FAIL]" lines. The checks
the report adds at its end (nothing contacted a host off this computer,
no exception escaped into Qt) were only in its JSON and in a summary
line the runners' pattern did not match, so a failure there went
unseen.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import smoke_ndak_common as nd  # noqa: E402


def _finish(tmp_path, monkeypatch, capsys, violations):
    monkeypatch.setattr(nd, "OUT_DIR", tmp_path)
    monkeypatch.setattr(nd, "VIOLATIONS", list(violations))
    monkeypatch.setattr(nd, "SLOT_ERRORS", [])
    monkeypatch.setattr(nd, "audit_remote_names", lambda: [])
    report = nd.Report("probe")
    report.check("something", True)
    code = report.finish()
    return code, capsys.readouterr().out


def test_the_guard_check_is_a_counted_pass_line(tmp_path, monkeypatch, capsys):
    _code, out = _finish(tmp_path, monkeypatch, capsys, [])
    assert "  [PASS] nothing contacted a host off this computer" in out


def test_a_guard_failure_is_a_counted_fail_line(tmp_path, monkeypatch, capsys):
    code, out = _finish(tmp_path, monkeypatch, capsys, ["http get https://example.com/"])
    assert "  [FAIL] nothing contacted a host off this computer" in out
    assert code != 0
