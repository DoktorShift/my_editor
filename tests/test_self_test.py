# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The self-test a release build runs before it becomes an installer."""

import pytest

import self_test


def test_every_check_but_spelling_passes_from_source(capsys):
    # Spelling depends on the system's checker, so it has its own test.
    checks = [c for c in self_test.CHECKS if c[0] != "spelling"]
    assert self_test.run(checks) == 0
    out = capsys.readouterr().out
    assert "all checks passed" in out
    assert "FAILED" not in out


def test_a_failing_check_is_reported_and_fails_the_run(capsys):
    def broken():
        raise ImportError("No module named 'segno'")

    code = self_test.run([("fine", lambda: "ok"), ("modules", broken)])
    out = capsys.readouterr().out
    assert code == 1
    assert "ok      fine: ok" in out
    assert "FAILED  modules: ImportError: No module named 'segno'" in out
    assert "1 check(s) failed" in out


def test_the_report_is_written_where_a_console_is_missing(tmp_path, monkeypatch):
    # A Windows build has no console; the workflow reads the report file.
    report = tmp_path / "report.txt"
    monkeypatch.setenv("MYEDITOR_SELF_TEST_REPORT", str(report))
    monkeypatch.setattr(self_test.sys, "stdout", None)
    assert self_test.run([("fine", lambda: "ok")]) == 0
    assert "all checks passed" in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("platform, fails", [("darwin", True), ("win32", True), ("linux", False)])
def test_a_missing_spell_checker_fails_only_where_one_always_exists(monkeypatch, platform, fails):
    class Backend:
        problem = "no checker"

    class Checker:
        backend = Backend()

        def is_available(self):
            return False

    import spelling
    monkeypatch.setattr(spelling, "SpellChecker", Checker)
    monkeypatch.setattr(self_test.sys, "platform", platform)
    if fails:
        with pytest.raises(RuntimeError, match="no checker"):
            self_test._spelling()
    else:
        assert "not offered" in self_test._spelling()
