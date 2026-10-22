# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pins what kind of file the folder sidebar takes each file for.

What must hold: every type MyEditor opens as text is a note or text;
the office formats are spreadsheets, documents and presentations, each
named the way people know it; the leftovers of Finder, Explorer and
the office apps never show; and the lock file another app keeps for an
open document is found, for Excel's, Word's and LibreOffice's naming.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import file_kinds  # noqa: E402
import format_icons  # noqa: E402
import main_window  # noqa: E402
import media_files  # noqa: E402
from file_kinds import kind_of_path  # noqa: E402


def test_every_type_the_app_opens_as_text_is_a_note_or_text():
    for ext in main_window._SUPPORTED_EXTS - {".pdf"}:
        assert kind_of_path("x" + ext) in ("note", "text"), ext
    assert kind_of_path("x.pdf") == "pdf"


@pytest.mark.parametrize("name, kind", [
    ("Plan.md", "note"), ("Plan.MARKDOWN", "note"), ("Analysis.Rmd", "note"),
    ("read me.txt", "text"), ("server.log", "text"), ("data.json", "text"),
    ("page.html", "text"), ("notes.text", "text"),
    ("Paper.pdf", "pdf"),
    ("Beach.JPG", "image"), ("Logo.svg", "image"), ("Photo.heic", "image"),
    ("Film.mov", "video"), ("Clip.mkv", "video"),
    ("Song.m4a", "audio"), ("Voice.opus", "audio"),
    ("Budget.xlsx", "sheet"), ("Macros.xlsm", "sheet"), ("Old.xls", "sheet"),
    ("Calc.ods", "sheet"), ("Table.csv", "sheet"), ("Table.tsv", "sheet"),
    ("Home.numbers", "sheet"),
    ("Letter.docx", "document"), ("Letter.doc", "document"), ("Letter.odt", "document"),
    ("Letter.rtf", "document"), ("Letter.pages", "document"),
    ("Talk.pptx", "slides"), ("Talk.ppt", "slides"), ("Talk.odp", "slides"),
    ("Talk.key", "slides"),
    ("archive.zip", "other"), ("no extension", "other"), ("setup.exe", "other"),
])
def test_a_file_is_the_kind_its_extension_says(name, kind):
    assert kind_of_path(name) == kind
    assert kind in file_kinds.KINDS


def test_a_kind_follows_the_name_not_the_folder_it_is_in():
    assert kind_of_path("/Users/a/Folder.md/Picture.png") == "image"
    assert kind_of_path("C:\\Notes\\Plan.md") == "note"


def test_the_shared_type_table_knows_every_office_format():
    for ext in file_kinds.SHEET_EXTS | file_kinds.DOCUMENT_EXTS | file_kinds.SLIDES_EXTS:
        assert ext in media_files.TYPES, ext
    assert media_files.file_family(media_files.type_of("Macros.xlsm")) == "spreadsheet"


def test_editable_spreadsheets_and_reading_views_are_of_their_kind():
    assert all(kind_of_path("x" + ext) == "sheet" for ext in file_kinds.EDITABLE_SHEET_EXTS)
    assert all(kind_of_path("x" + ext) == "document" for ext in file_kinds.READING_VIEW_EXTS)
    assert ".numbers" not in file_kinds.EDITABLE_SHEET_EXTS


@pytest.mark.parametrize("name, label", [
    ("Budget.xlsx", "Excel Spreadsheet"), ("Old.xls", "Excel Spreadsheet (Old Format)"),
    ("Calc.ods", "OpenDocument Spreadsheet"), ("Home.numbers", "Numbers Spreadsheet"),
    ("Letter.docx", "Word Document"), ("Letter.odt", "OpenDocument Text"),
    ("Letter.pages", "Pages Document"), ("Talk.pptx", "PowerPoint Presentation"),
    ("Talk.key", "Keynote Presentation"), ("Paper.pdf", "PDF Document"),
    ("Beach.png", "PNG Image"), ("Film.mov", "MOV Video"), ("Song.mp3", "MP3 Audio"),
    ("Plan.md", "Note"), ("Analysis.rmd", "R Markdown Note"), ("a.txt", "Plain Text"),
    ("archive.zip", "ZIP Archive"), ("setup.exe", "EXE File"), ("README", "File"),
])
def test_a_format_is_named_the_way_people_know_it(name, label):
    assert file_kinds.format_label(name) == label


def test_every_kind_has_a_tint_and_a_glyph_the_app_can_draw():
    for kind in file_kinds.KINDS:
        assert file_kinds.kind_tint(kind).isValid()
        assert file_kinds.kind_icon_name(kind) in format_icons._GLYPHS, kind
    assert file_kinds.kind_icon_name("folder") == "folder"
    assert file_kinds.format_tint("Letter.pages").name() == "#ff9500"
    assert file_kinds.format_tint("Letter.docx") == file_kinds.kind_tint("document")


def test_a_note_is_a_written_page_and_an_archive_is_drawn_as_one():
    # The Nostr note's speech bubble is never a note on disk.
    assert file_kinds.kind_icon_name(file_kinds.NOTE) == "article"
    assert file_kinds.kind_icon_name(file_kinds.OTHER, "Backup.zip") == "archive"
    assert file_kinds.kind_icon_name(file_kinds.OTHER, "Thing.xyz") == "document"
    assert file_kinds.kind_icon_name(file_kinds.OTHER) == "document"


@pytest.mark.parametrize("name", [
    ".DS_Store", ".git", ".~lock.Budget.xlsx#", "~$Budget.xlsx", "~$tter.docx",
    "Thumbs.db", "thumbs.db", "desktop.ini", "Desktop.ini", "Icon\r", "",
])
def test_system_and_lock_files_are_hidden(name):
    assert file_kinds.is_hidden_name(name)


@pytest.mark.parametrize("name", [
    "Budget.xlsx", "Icon", "Icon.png", "notes", "a~$b.txt", "Thumbs.db.md", "Über.md",
])
def test_ordinary_names_are_shown(name):
    assert not file_kinds.is_hidden_name(name)


def test_no_lock_file_means_nobody_has_the_file_open(tmp_path):
    path = tmp_path / "Budget.xlsx"
    path.write_bytes(b"")
    assert file_kinds.lock_owner_file(str(path)) is None


def test_excel_owner_file_is_found_by_the_whole_name(tmp_path):
    path = tmp_path / "Quarterly Budget.xlsx"
    path.write_bytes(b"")
    (tmp_path / "~$Quarterly Budget.xlsx").write_bytes(b"owner")
    assert file_kinds.lock_owner_file(str(path)) == str(tmp_path / "~$Quarterly Budget.xlsx")


@pytest.mark.parametrize("name, owner", [
    ("Letter.docx", "~$Letter.docx"),          # six characters: all of the name
    ("Letters.docx", "~$etters.docx"),         # seven: the first one replaced
    ("Document.docx", "~$cument.docx"),        # eight and more: the first two
    ("Über den Fluss.docx", "~$er den Fluss.docx"),
])
def test_word_owner_file_follows_the_first_two_characters_rule(tmp_path, name, owner):
    path = tmp_path / name
    path.write_bytes(b"")
    (tmp_path / owner).write_bytes(b"owner")
    assert file_kinds.lock_owner_file(str(path)) == str(tmp_path / owner)


def test_libreoffice_lock_file_is_found(tmp_path):
    path = tmp_path / "Kalkulation (neu).ods"
    path.write_bytes(b"")
    lock = tmp_path / ".~lock.Kalkulation (neu).ods#"
    lock.write_text("Someone,host,...", encoding="utf-8")
    assert file_kinds.lock_owner_file(str(path)) == str(lock)


def test_a_short_name_is_not_mistaken_for_a_longer_ones_owner(tmp_path):
    (tmp_path / "~$port.docx").write_bytes(b"owner")    # Word has "port.docx" open
    path = tmp_path / "Report.docx"                     # six characters
    path.write_bytes(b"")
    assert file_kinds.lock_owner_file(str(path)) is None


def test_document_packages_are_files(tmp_path):
    package = tmp_path / "Home.numbers"
    (package / "Data").mkdir(parents=True)
    assert file_kinds.is_package(str(package))
    assert not file_kinds.is_package(str(tmp_path))
    (tmp_path / "Zipped.numbers").write_bytes(b"PK")
    assert not file_kinds.is_package(str(tmp_path / "Zipped.numbers"))
    assert {".numbers", ".pages", ".key", ".rtfd", ".app"} <= file_kinds.PACKAGE_EXTS
