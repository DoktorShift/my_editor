# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins how MyEditor names and starts the app a file opens in.

What must hold, on every system: the module imports anywhere; the
system's answer is asked once per extension; whatever goes wrong comes
back as None, False or an empty list, never as an exception; each
system is asked its own way (NSWorkspace, the shell's associations,
xdg-mime and the desktop files), which the tests reach without the
real system by standing in for its calls.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import default_apps  # noqa: E402
from default_apps import AppChoice  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_cache():
    default_apps.clear_cache()
    yield
    default_apps.clear_cache()


def as_platform(monkeypatch, platform):
    monkeypatch.setattr(default_apps, "_platform", lambda: platform)


# -- every system ---------------------------------------------------------------


def test_the_name_is_asked_once_per_extension(monkeypatch):
    as_platform(monkeypatch, default_apps.MAC)
    asked = []
    monkeypatch.setattr(default_apps, "_mac_default_name",
                        lambda path: asked.append(path) or "Numbers")
    assert default_apps.default_app_name("/a/Budget.xlsx") == "Numbers"
    assert default_apps.default_app_name("/b/Other.XLSX") == "Numbers"
    assert asked == ["/a/Budget.xlsx"]


def test_failures_never_reach_the_caller(monkeypatch):
    def broken(*args):
        raise OSError("no system here")
    for platform in (default_apps.MAC, default_apps.WINDOWS, default_apps.LINUX):
        as_platform(monkeypatch, platform)
        default_apps.clear_cache()
        for name in ("_mac_default_name", "_windows_default_name", "_linux_default_name",
                     "_mac_apps", "_windows_apps", "_linux_apps", "_mac_choose",
                     "_windows_open_with_dialog", "_linux_portal_open", "_start",
                     "_linux_show_items", "_linux_launch"):
            monkeypatch.setattr(default_apps, name, broken)
        monkeypatch.setattr(default_apps, "open_with_default", lambda path: False)
        assert default_apps.default_app_name("/x/a.docx") is None
        assert default_apps.other_apps("/x/a.docx") == []
        assert default_apps.open_with("/x/a.docx", AppChoice("App", "app")) is False
        assert default_apps.choose_app_and_open("/x/a.docx") is False
        assert default_apps.reveal("/x/a.docx") is False


@pytest.mark.skipif(sys.platform == "win32", reason="Windows answers for itself")
def test_the_real_windows_branch_fails_quietly_without_windows(monkeypatch):
    as_platform(monkeypatch, default_apps.WINDOWS)
    assert default_apps.default_app_name("/x/a.docx") is None
    assert default_apps.other_apps("/x/a.docx") == []


@pytest.mark.skipif(sys.platform != "win32", reason="asks Windows itself")
def test_the_real_windows_branch_names_the_app_windows_opens_a_file_with(tmp_path):
    # Every Windows opens text files with an app it can name (Notepad).
    text = tmp_path / "a.txt"
    text.write_text("hello\n", encoding="utf-8")
    name = default_apps.default_app_name(str(text))
    assert isinstance(name, str) and name.strip()
    assert default_apps.other_apps(str(text))[0].name == name
    assert default_apps.default_app_name(str(tmp_path / "a.zzqqxx")) is None


def test_reveal_is_named_after_the_systems_file_manager(monkeypatch):
    for platform, words in ((default_apps.MAC, "Show in Finder"),
                            (default_apps.WINDOWS, "Show in Explorer"),
                            (default_apps.LINUX, "Show in File Manager")):
        as_platform(monkeypatch, platform)
        assert default_apps.reveal_label() == words


def test_other_apps_lists_each_app_once_default_first(monkeypatch):
    as_platform(monkeypatch, default_apps.MAC)
    monkeypatch.setattr(default_apps, "_mac_apps", lambda path: [
        AppChoice("Numbers", "/Applications/Numbers.app"),
        AppChoice("LibreOffice", "/Applications/LibreOffice.app"),
        AppChoice("Numbers", "/Applications/Numbers.app"), AppChoice("", "/x.app")])
    assert [c.name for c in default_apps.other_apps("/a.xlsx")] == ["Numbers", "LibreOffice"]


def test_a_choice_without_an_app_opens_in_the_default(monkeypatch):
    opened = []
    monkeypatch.setattr(default_apps, "open_with_default", lambda path: opened.append(path) or True)
    assert default_apps.open_with("/a.xlsx", AppChoice("Default", ""))
    assert opened == ["/a.xlsx"]


# -- macOS ------------------------------------------------------------------------


def test_mac_opens_with_and_reveals_through_open(monkeypatch):
    as_platform(monkeypatch, default_apps.MAC)
    started = []
    monkeypatch.setattr(default_apps, "_start",
                        lambda command, wait=False: started.append(command) or True)
    assert default_apps.open_with("/a b.xlsx", AppChoice("Numbers", "/Applications/Numbers.app"))
    assert default_apps.reveal("/a b.xlsx")
    assert started == [["open", "-a", "/Applications/Numbers.app", "/a b.xlsx"],
                       ["open", "-R", "/a b.xlsx"]]


def test_mac_choose_opens_with_the_app_picked(monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    as_platform(monkeypatch, default_apps.MAC)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("/Applications/Pages.app", "")))
    chosen = []
    monkeypatch.setattr(default_apps, "open_with",
                        lambda path, choice: chosen.append((path, choice)) or True)
    assert default_apps.choose_app_and_open("/Letter.docx")
    assert chosen == [("/Letter.docx", AppChoice("Pages", "/Applications/Pages.app"))]
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    assert default_apps.choose_app_and_open("/Letter.docx") is False


@pytest.mark.skipif(sys.platform != "darwin", reason="asks this Mac's NSWorkspace")
def test_this_mac_names_the_app_for_plain_text(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("hello", encoding="utf-8")
    name = default_apps.default_app_name(str(path))
    assert name and not name.endswith(".app")
    assert default_apps.other_apps(str(path))[0].name == name
    assert default_apps.default_app_name(str(tmp_path / "x.unknownext42")) is None


# -- Windows ----------------------------------------------------------------------


def test_windows_asks_the_shell_for_the_friendly_name(monkeypatch):
    as_platform(monkeypatch, default_apps.WINDOWS)
    asked = []

    def assoc(ext, what):
        asked.append((ext, what))
        return {default_apps._ASSOCSTR_FRIENDLYAPPNAME: "Excel",
                default_apps._ASSOCSTR_EXECUTABLE: r"C:\Office\EXCEL.EXE"}[what]
    monkeypatch.setattr(default_apps, "_windows_assoc", assoc)
    assert default_apps.default_app_name(r"C:\Data\Budget.XLSX") == "Excel"
    assert asked == [(".xlsx", default_apps._ASSOCSTR_FRIENDLYAPPNAME)]
    assert default_apps.other_apps(r"C:\Data\Budget.xlsx") == [
        AppChoice("Excel", r"C:\Office\EXCEL.EXE")]


def test_windows_opens_with_the_program_and_reveals_in_explorer(monkeypatch):
    as_platform(monkeypatch, default_apps.WINDOWS)
    started = []
    monkeypatch.setattr(default_apps, "_start",
                        lambda command, wait=False: started.append(command) or True)
    assert default_apps.open_with(r"C:\a b.xlsx", AppChoice("Excel", r"C:\Office\EXCEL.EXE"))
    assert default_apps.reveal("/data/a b.xlsx")
    assert started[0] == [r"C:\Office\EXCEL.EXE", r"C:\a b.xlsx"]
    assert started[1].startswith("explorer /select,\"") and started[1].endswith("a b.xlsx\"")


def test_windows_choose_uses_the_open_with_dialog(monkeypatch):
    as_platform(monkeypatch, default_apps.WINDOWS)
    monkeypatch.setattr(default_apps, "_windows_open_with_dialog", lambda path, parent: True)
    assert default_apps.choose_app_and_open("C:/a.xyz")


def test_windows_start_hides_the_console(monkeypatch):
    as_platform(monkeypatch, default_apps.WINDOWS)
    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs: seen.update(kwargs))
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert default_apps._start(["x"])
    assert seen["creationflags"] == 0x08000000


# -- Linux ------------------------------------------------------------------------


@pytest.fixture
def linux_desktop(tmp_path, monkeypatch):
    """A Linux desktop's folders, with LibreOffice Calc and Gnumeric."""
    data = tmp_path / "data"
    apps = data / "applications"
    (apps / "kde").mkdir(parents=True)
    (apps / "libreoffice-calc.desktop").write_text(
        "[Desktop Entry]\nName=LibreOffice Calc\nName[de]=LibreOffice Calc (de)\n"
        "Exec=libreoffice --calc %U\nType=Application\n", encoding="utf-8")
    (apps / "gnumeric.desktop").write_text(
        "[Desktop Entry]\nName=Gnumeric\nExec=gnumeric %f\n", encoding="utf-8")
    (apps / "hidden.desktop").write_text(
        "[Desktop Entry]\nName=Hidden\nExec=hidden %f\nNoDisplay=true\n", encoding="utf-8")
    (apps / "kde" / "calligra.desktop").write_text(
        "[Desktop Entry]\nName=Calligra Sheets\nExec=calligra %u\n", encoding="utf-8")
    (apps / "mimeinfo.cache").write_text(
        "[MIME Cache]\ntext/csv=gnumeric.desktop;hidden.desktop;kde-calligra.desktop;"
        "libreoffice-calc.desktop;\n", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "mimeapps.list").write_text(
        "[Default Applications]\ntext/csv=libreoffice-calc.desktop;\n"
        "[Removed Associations]\ntext/csv=gnumeric.desktop;\n", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "none"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(tmp_path / "none"))
    as_platform(monkeypatch, default_apps.LINUX)
    answers = {("xdg-mime", "query", "filetype"): "text/csv",
               ("xdg-mime", "query", "default"): "libreoffice-calc.desktop"}
    monkeypatch.setattr(default_apps, "_output", lambda command: answers.get(tuple(command[:3]), ""))
    return answers


def test_linux_names_the_default_from_its_desktop_file(linux_desktop):
    assert default_apps.default_app_name("/home/a/Table.csv") == "LibreOffice Calc"


def test_linux_names_the_app_in_the_apps_language(linux_desktop, monkeypatch):
    import i18n
    monkeypatch.setattr(i18n, "language", lambda: "de")
    assert default_apps.default_app_name("/home/a/Table.csv") == "LibreOffice Calc (de)"


def test_linux_without_xdg_mime_reads_mimeapps_list(linux_desktop, monkeypatch):
    monkeypatch.setattr(default_apps, "_output", lambda command: "")
    assert default_apps.default_app_name("/home/a/Table.csv") == "LibreOffice Calc"


def test_linux_lists_the_apps_for_the_type(linux_desktop):
    choices = default_apps.other_apps("/home/a/Table.csv")
    assert choices == [AppChoice("LibreOffice Calc", "libreoffice-calc.desktop"),
                       AppChoice("Calligra Sheets", "kde-calligra.desktop")]


def home_file(name):
    """A file in /home/a, spelled as this computer spells an absolute path
    (the tests run the Linux branch on every system; on Windows a path
    from the root has a drive)."""
    return os.path.join(os.path.abspath(os.sep), "home", "a", name)


def test_linux_opens_with_the_desktop_files_command(linux_desktop, monkeypatch):
    started = []
    monkeypatch.setattr(default_apps, "_start",
                        lambda command, wait=False: started.append(command) or True)
    table = home_file("My Table.csv")
    assert default_apps.open_with(table, AppChoice("Gnumeric", "gnumeric.desktop"))
    assert default_apps.open_with(table, AppChoice("LibreOffice Calc", "libreoffice-calc.desktop"))
    # %f gets the path, %U the file's URL, its space encoded: on Linux
    # "file:///home/a/My%20Table.csv".
    url = "file://" + ("" if table.startswith("/") else "/") \
        + table.replace(os.sep, "/").replace(" ", "%20")
    assert started == [["gnumeric", table], ["libreoffice", "--calc", url]]
    assert not default_apps.open_with("/x.csv", AppChoice("Gone", "gone.desktop"))


def test_linux_reveal_falls_back_to_opening_the_folder(monkeypatch):
    as_platform(monkeypatch, default_apps.LINUX)
    monkeypatch.setattr(default_apps, "_linux_show_items", lambda path: False)
    opened = []
    monkeypatch.setattr(default_apps, "open_with_default", lambda path: opened.append(path) or True)
    table = home_file("Table.csv")
    assert default_apps.reveal(table)
    assert opened == [os.path.dirname(table)]
    monkeypatch.setattr(default_apps, "_linux_show_items", lambda path: True)
    assert default_apps.reveal(table)
    assert opened == [os.path.dirname(table)]


def test_linux_choose_without_a_portal_opens_nothing(monkeypatch):
    as_platform(monkeypatch, default_apps.LINUX)
    import builtins
    real_import = builtins.__import__

    def no_dbus(name, *args, **kwargs):
        if name == "PySide6.QtDBus":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_dbus)
    assert default_apps.choose_app_and_open("/home/a/Table.csv") is False
