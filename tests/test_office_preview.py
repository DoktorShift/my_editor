# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""office_preview: the preview picture stored in an office file, the
reading view of a Word or Writer document, and Import as Note.

The defect classes guarded here:
- a preview picture not found where an app stores it (ODF, OOXML, iWork
  zip and package folder), or found and decoded when it should be
  refused (too large, an SVG in disguise, a header claiming billions of
  pixels, a symlink out of a package),
- the plain reading (without pandoc) losing headings, lists, tables,
  links or pictures, or reading a file an XML entity points at,
- pandoc not used when it is there, or its failure leaving no view,
- Import as Note replacing a file, touching the original, or putting
  pictures anywhere but the note's own media folder.
"""

import os
import stat
import struct
import sys
import threading
import time
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import office_preview  # noqa: E402
import rmd_toolchain  # noqa: E402
from tests.office_samples import (  # noqa: E402
    PKG, R, W, giant_png_header, make_docx, make_odt, make_package, make_zip, picture_bytes,
)

posix_only = pytest.mark.skipif(sys.platform == "win32",
                                reason="the stand-in pandoc is a shell script")


@pytest.fixture(autouse=True)
def no_pandoc(monkeypatch):
    """No pandoc unless a test brings its own."""
    monkeypatch.setattr(rmd_toolchain, "find_pandoc", lambda: (None, "missing"))


def finishes(work, seconds=3.0):
    """``(done, result)``: whether ``work()`` returned within ``seconds``
    on a thread of its own (a read that blocks must not hang the tests)."""
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("result", work()), daemon=True)
    thread.start()
    thread.join(seconds)
    return not thread.is_alive(), box.get("result")


def raises(function, *args):
    """The exception ``function(*args)`` raised, or None."""
    try:
        function(*args)
    except Exception as error:  # noqa: BLE001
        return error
    return None


def fake_pandoc(tmp_path, monkeypatch, script: str, version: str = "3.6.4"):
    """A stand-in pandoc (a shell script) that the app finds as the system's.
    Asked for its version, it says ``version`` first, as pandoc does."""
    folder = tmp_path / "pandoc-bin"
    folder.mkdir()
    program = folder / "pandoc"
    program.write_text("#!/bin/sh\n"
                       f'if [ "$1" = "--version" ]; then echo "pandoc {version}"; exit 0; fi\n'
                       + script, encoding="utf-8")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(rmd_toolchain, "find_pandoc", lambda: (str(folder), "system"))
    return program


# --------------------------------------------------------------------------- #
# Preview pictures                                                            #
# --------------------------------------------------------------------------- #

def test_an_opendocument_file_shows_its_stored_thumbnail(tmp_path):
    path = make_zip(tmp_path / "Deck.odp", {"content.xml": "<x/>",
                                            "Thumbnails/thumbnail.png": picture_bytes(64, 48)})
    image = office_preview.package_thumbnail(path)
    assert image is not None and (image.width(), image.height()) == (64, 48)


def test_an_office_file_shows_its_docprops_thumbnail(tmp_path):
    path = make_zip(tmp_path / "Slides.pptx",
                    {"docProps/thumbnail.jpeg": picture_bytes(80, 60, fmt="JPEG")})
    image = office_preview.package_thumbnail(path)
    assert image is not None and image.width() == 80


def test_an_office_thumbnail_named_by_the_package_relationships_is_found(tmp_path):
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/package/2006/'
            'relationships/metadata/thumbnail" Target="/docProps/odd-name.png"/></Relationships>')
    path = make_zip(tmp_path / "Old.xlsx", {"_rels/.rels": rels,
                                            "docProps/odd-name.png": picture_bytes(20, 10)})
    assert office_preview.package_thumbnail(path).width() == 20


def test_an_iwork_zip_shows_its_preview_jpg(tmp_path):
    path = make_zip(tmp_path / "Budget.numbers",
                    {"Index/Document.iwa": b"\0", "preview.jpg": picture_bytes(90, 70, fmt="JPEG")})
    assert office_preview.package_thumbnail(path).width() == 90


def test_an_iwork_package_folder_shows_its_quicklook_preview(tmp_path):
    folder = make_package(tmp_path / "Talk.key",
                          {"QuickLook/Preview.png": picture_bytes(30, 20)})
    assert office_preview.package_thumbnail(folder).width() == 30


def test_a_file_without_a_preview_picture_has_none(tmp_path):
    assert office_preview.package_thumbnail(make_zip(tmp_path / "a.pptx", {"x": "y"})) is None
    (tmp_path / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0 not a zip")
    assert office_preview.package_thumbnail(str(tmp_path / "old.doc")) is None
    assert office_preview.package_thumbnail(str(tmp_path / "missing.key")) is None


def test_an_oversized_thumbnail_is_refused(tmp_path, monkeypatch):
    path = make_zip(tmp_path / "Big.odp", {"Thumbnails/thumbnail.png": picture_bytes(64, 48)})
    monkeypatch.setattr(office_preview, "THUMBNAIL_BYTES_LIMIT", 100)
    assert office_preview.package_thumbnail(path) is None


def test_an_svg_dressed_as_a_thumbnail_is_refused(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="/etc/passwd"/></svg>'
    path = make_zip(tmp_path / "Trick.odt", {"Thumbnails/thumbnail.png": svg})
    assert office_preview.package_thumbnail(path) is None


def test_a_thumbnail_claiming_billions_of_pixels_is_refused(tmp_path):
    path = make_zip(tmp_path / "Bomb.odp", {"Thumbnails/thumbnail.png": giant_png_header()})
    assert office_preview.package_thumbnail(path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_symlink_in_a_package_does_not_lead_out_of_it(tmp_path):
    outside = tmp_path / "secret.png"
    outside.write_bytes(picture_bytes(12, 12))
    folder = make_package(tmp_path / "Linked.pages", {"Index/x": b"\0"})
    os.symlink(outside, os.path.join(folder, "preview.jpg"))
    assert office_preview.package_thumbnail(folder) is None


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes")
def test_a_pipe_named_like_a_presentation_is_never_opened(tmp_path):
    os.mkfifo(tmp_path / "Slides.pptx")
    done, result = finishes(lambda: office_preview.package_thumbnail(
        str(tmp_path / "Slides.pptx")))
    assert done and result is None
    done, error = finishes(lambda: raises(office_preview.render_document,
                                          str(tmp_path / "Slides.pptx")))
    assert done


@pytest.mark.skipif(sys.platform == "win32", reason="named pipes")
def test_a_pipe_in_a_package_folder_is_never_opened(tmp_path):
    folder = make_package(tmp_path / "Talk.key", {"Index/x": b"\0"})
    os.mkfifo(os.path.join(folder, "preview.jpg"))
    done, result = finishes(lambda: office_preview.package_thumbnail(folder))
    assert done and result is None


def many_parts(path, count):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        for number in range(count):
            archive.writestr(f"x/{number:07d}", b"")
        archive.writestr("docProps/thumbnail.png", picture_bytes(20, 10))
    return str(path)


def test_a_zip_of_more_parts_than_an_office_file_has_is_not_listed(tmp_path, monkeypatch):
    path = many_parts(tmp_path / "many.pptx", 300)
    assert office_preview.package_thumbnail(path).width() == 20
    monkeypatch.setattr(office_preview, "ZIP_ENTRY_LIMIT", 100)
    listed = []
    real = zipfile.ZipFile._RealGetContents
    monkeypatch.setattr(zipfile.ZipFile, "_RealGetContents",
                        lambda self: listed.append(True) or real(self))
    assert office_preview.package_thumbnail(path) is None
    assert listed == []                      # refused from the end record alone
    document = many_parts(tmp_path / "many.docx", 300)
    with pytest.raises(office_preview.DocumentUnreadable) as caught:
        office_preview.render_document(document, use_pandoc=False)
    assert "too large" in str(caught.value)
    assert listed == []


def test_a_zip_whose_parts_unpack_past_the_budget_is_refused(tmp_path, monkeypatch):
    path = make_zip(tmp_path / "Deck.odp", {"Thumbnails/thumbnail.png": picture_bytes(64, 48),
                                            "big.bin": b"a" * 5000})
    monkeypatch.setattr(office_preview, "ZIP_TOTAL_LIMIT", 4000)
    assert office_preview.package_thumbnail(path) is None


def test_a_zip64_end_record_is_read_for_the_count(tmp_path, monkeypatch):
    path = tmp_path / "z64.pptx"
    with zipfile.ZipFile(path, "w", allowZip64=True) as archive:
        archive.writestr("docProps/thumbnail.png", picture_bytes(20, 10))
    data = bytearray(path.read_bytes())
    # The plain end record says "see the Zip64 one", which is written in front of it.
    at = bytes(data).rfind(b"PK\x05\x06")
    entries = 1
    directory_size, directory_offset = struct.unpack("<LL", data[at + 12:at + 20])
    record = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, entries, entries,
                         directory_size, directory_offset)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, at, 1)
    end = bytearray(data[at:])
    end[8:10] = struct.pack("<H", 0xFFFF)
    end[10:12] = struct.pack("<H", 0xFFFF)
    path.write_bytes(bytes(data[:at]) + record + locator + bytes(end))
    with open(path, "rb") as stream:
        assert office_preview._zip_directory(stream) == (1, directory_size)
    monkeypatch.setattr(office_preview, "ZIP_ENTRY_LIMIT", 0)
    with open(path, "rb") as stream:
        assert office_preview._zip_directory(stream)[0] > office_preview.ZIP_ENTRY_LIMIT


# --------------------------------------------------------------------------- #
# The reading view without pandoc                                             #
# --------------------------------------------------------------------------- #

def test_a_word_document_reads_with_headings_links_lists_and_tables(tmp_path):
    result = office_preview.render_document(make_docx(tmp_path / "Notes.docx"))
    html = result.html
    assert not result.with_pandoc and result.pandoc_missing
    assert "<h1>Meeting notes</h1>" in html
    assert '<a href="https://example.org/agenda">agenda</a>' in html
    assert "<ul><li>Bring coffee</li>" in html.replace("\n", "")
    assert "<th>Name</th><th>Amount</th>" in html
    assert "<td>Coffee</td><td>3</td>" in html


def docx_with_paragraph(path, runs: str) -> str:
    document = (f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body><w:p>{runs}</w:p>'
                '</w:body></w:document>')
    rels = (f'<Relationships xmlns="{PKG}">'
            f'<Relationship Id="rIdA" Type="{R}/hyperlink" Target="https://a.example/" '
            'TargetMode="External"/>'
            f'<Relationship Id="rIdB" Type="{R}/hyperlink" Target="https://b.example/?x=1&amp;y=2" '
            'TargetMode="External"/>'
            f'<Relationship Id="rIdJ" Type="{R}/hyperlink" Target="javascript:alert(1)" '
            'TargetMode="External"/></Relationships>')
    return make_zip(path, {"word/document.xml": document,
                           "word/_rels/document.xml.rels": rels})


def test_a_link_wraps_its_own_words_not_the_same_words_before_it(tmp_path):
    runs = ('<w:r><w:t xml:space="preserve">a then </w:t></w:r>'
            '<w:hyperlink r:id="rIdA"><w:r><w:t>a</w:t></w:r></w:hyperlink>'
            '<w:r><w:t xml:space="preserve"> and </w:t></w:r>'
            '<w:hyperlink r:id="rIdB"><w:r><w:t>a</w:t></w:r></w:hyperlink>'
            '<w:r><w:t xml:space="preserve"> and </w:t></w:r>'
            '<w:hyperlink r:id="rIdJ"><w:r><w:t>href</w:t></w:r></w:hyperlink>')
    html = office_preview.document_html(docx_with_paragraph(tmp_path / "Links.docx", runs))
    assert ('<p>a then <a href="https://a.example/">a</a> and '
            '<a href="https://b.example/?x=1&amp;y=2">a</a> and href</p>') in html


def test_a_writer_link_wraps_its_own_words(tmp_path):
    content = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:'
               'office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
               'xmlns:xlink="http://www.w3.org/1999/xlink"><office:body><office:text>'
               '<text:p>see <text:a xlink:href="https://a.example/">see</text:a> twice</text:p>'
               '</office:text></office:body></office:document-content>')
    html = office_preview.document_html(make_zip(tmp_path / "Links.odt",
                                                 {"content.xml": content}))
    assert '<p>see <a href="https://a.example/">see</a> twice</p>' in html


def test_a_paragraph_of_many_links_reads_in_one_pass(tmp_path):
    runs = '<w:hyperlink r:id="rIdA"><w:r><w:t>a</w:t></w:r></w:hyperlink>' \
        '<w:r><w:t xml:space="preserve"> </w:t></w:r>'
    path = docx_with_paragraph(tmp_path / "Many.docx", runs * 40_000)
    started = time.monotonic()
    html = office_preview.document_html(path)
    assert time.monotonic() - started < 3.0
    assert html.count('<a href="https://a.example/">a</a>') == 40_000


def test_a_writer_document_reads_with_spaces_lists_and_a_trimmed_table(tmp_path):
    html = office_preview.document_html(make_odt(tmp_path / "Trip.odt"))
    assert "<h2>Trip plan</h2>" in html
    assert "Leave at   eight." in html
    assert "<li>Tickets</li>" in html
    assert "<tr><td>Monday</td><td>Lyon</td></tr>" in html


def test_pictures_go_into_the_tabs_folder_or_ride_along_as_data(tmp_path):
    path = make_docx(tmp_path / "Pics.docx", picture=picture_bytes())
    media = tmp_path / "media"
    media.mkdir()
    html = office_preview.document_html(path, media_dir=str(media))
    written = list(media.iterdir())
    assert len(written) == 1 and str(written[0]) in html
    inline = office_preview.document_html(path)
    assert 'src="data:image/png;base64,' in inline


def test_a_picture_this_app_would_not_decode_is_left_out(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    html = office_preview.document_html(make_docx(tmp_path / "Svg.docx", picture=svg))
    assert "<img" not in html


def test_an_xml_entity_never_reads_another_file(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-WORDS", encoding="utf-8")
    doctype = f'<!DOCTYPE w:document [<!ENTITY leak SYSTEM "{secret.as_uri()}">]>'
    body = (f'<w:p xmlns:w="{W}">'
            '<w:r><w:t>&leak;</w:t></w:r></w:p>')
    path = make_docx(tmp_path / "Leak.docx", doctype=doctype, extra_body=body)
    try:
        html = office_preview.document_html(path)
    except office_preview.DocumentUnreadable:
        return
    assert "TOP-SECRET-WORDS" not in html


def test_a_broken_document_raises_a_sentence_to_show(tmp_path):
    (tmp_path / "Broken.docx").write_bytes(b"not a zip at all")
    with pytest.raises(office_preview.DocumentUnreadable) as caught:
        office_preview.document_html(str(tmp_path / "Broken.docx"))
    assert str(caught.value)
    path = make_zip(tmp_path / "Garbled.docx", {"word/document.xml": "<w:document"})
    with pytest.raises(office_preview.DocumentUnreadable):
        office_preview.document_html(path)


def test_a_document_without_text_says_so(tmp_path):
    path = make_zip(tmp_path / "Empty.odt", {
        "content.xml": '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:'
                       'xmlns:office:1.0"><office:body><office:text/></office:body>'
                       '</office:document-content>'})
    assert "This document has no text." in office_preview.document_html(path)


# --------------------------------------------------------------------------- #
# pandoc                                                                      #
# --------------------------------------------------------------------------- #

@posix_only
def test_pandoc_makes_the_reading_view_when_it_is_there(tmp_path, monkeypatch):
    log = tmp_path / "args.txt"
    fake_pandoc(tmp_path, monkeypatch, f'echo "$@" > "{log}"\necho "<p>FROM PANDOC</p>"\n')
    media = tmp_path / "media"
    media.mkdir()
    result = office_preview.render_document(make_docx(tmp_path / "A.docx"), media_dir=str(media))
    assert result.with_pandoc and not result.pandoc_missing
    assert "FROM PANDOC" in result.html
    args = log.read_text(encoding="utf-8")
    assert "--from docx --to html" in args and f"--extract-media {media}" in args


@posix_only
def test_a_failing_pandoc_falls_back_without_offering_an_install(tmp_path, monkeypatch):
    fake_pandoc(tmp_path, monkeypatch, "exit 3\n")
    result = office_preview.render_document(make_docx(tmp_path / "A.docx"))
    assert not result.with_pandoc and not result.pandoc_missing
    assert "<h1>Meeting notes</h1>" in result.html


@posix_only
def test_a_pandoc_that_hangs_is_stopped(tmp_path, monkeypatch):
    fake_pandoc(tmp_path, monkeypatch, "sleep 30\n")
    monkeypatch.setattr(office_preview, "READING_SECONDS", 0.5)
    result = office_preview.render_document(make_docx(tmp_path / "A.docx"))
    assert not result.with_pandoc and "Meeting notes" in result.html


@posix_only
def test_a_pandoc_older_than_3_1_6_is_not_asked_to_extract_pictures(tmp_path, monkeypatch):
    log = tmp_path / "args.txt"
    fake_pandoc(tmp_path, monkeypatch, f'echo "$@" >> "{log}"\necho "<p>FROM PANDOC</p>"\n',
                version="3.1.5")
    media = tmp_path / "media"
    media.mkdir()
    path = make_docx(tmp_path / "A.docx", picture=picture_bytes())
    result = office_preview.render_document(path, media_dir=str(media))
    assert not result.with_pandoc and not result.pandoc_missing
    assert "<h1>Meeting notes</h1>" in result.html
    note = office_preview.import_as_markdown(path)
    assert open(note, encoding="utf-8").read().startswith("# Meeting notes")
    assert (tmp_path / "A_media" / "image1.png").exists()
    assert not log.exists()                   # never run but to ask its version


@posix_only
def test_a_pandoc_that_does_not_say_its_version_is_not_asked_to_extract(tmp_path, monkeypatch):
    log = tmp_path / "args.txt"
    program = fake_pandoc(tmp_path, monkeypatch, f'echo "$@" >> "{log}"\necho "<p>X</p>"\n',
                          version="")
    program.write_text(program.read_text(encoding="utf-8").replace(
        'echo "pandoc "; exit 0', 'echo "nonsense"; exit 0'), encoding="utf-8")
    media = tmp_path / "media"
    media.mkdir()
    result = office_preview.render_document(make_docx(tmp_path / "A.docx"),
                                            media_dir=str(media))
    assert not result.with_pandoc and not log.exists()


@posix_only
def test_pandoc_is_asked_its_version_once(tmp_path, monkeypatch):
    asked = tmp_path / "asked.txt"
    program = fake_pandoc(tmp_path, monkeypatch, 'echo "<p>FROM PANDOC</p>"\n')
    program.write_text(program.read_text(encoding="utf-8").replace(
        'if [ "$1" = "--version" ]; then', f'if [ "$1" = "--version" ]; then echo x >> "{asked}";'),
        encoding="utf-8")
    media = tmp_path / "media"
    media.mkdir()
    for _ in range(3):
        office_preview.render_document(make_docx(tmp_path / "A.docx"), media_dir=str(media))
    assert asked.read_text(encoding="utf-8").count("x") == 1


# --------------------------------------------------------------------------- #
# Import as Note                                                              #
# --------------------------------------------------------------------------- #

def test_import_writes_a_note_next_to_the_original_and_never_replaces_one(tmp_path):
    path = make_docx(tmp_path / "Report.docx", picture=picture_bytes())
    original = open(path, "rb").read()
    first = office_preview.import_as_markdown(path)
    assert first == str(tmp_path / "Report.md")
    text = open(first, encoding="utf-8").read()
    assert text.startswith("# Meeting notes")
    assert "| Name | Amount |" in text and "| Coffee | 3 |" in text
    assert "- Bring coffee" in text
    assert "![](Report_media/image1.png)" in text
    assert (tmp_path / "Report_media" / "image1.png").read_bytes() == picture_bytes()

    second = office_preview.import_as_markdown(path)
    assert second == str(tmp_path / "Report 2.md")
    assert "](<Report 2_media/image1.png>)" in open(second, encoding="utf-8").read()
    assert open(first, encoding="utf-8").read() == text
    assert open(path, "rb").read() == original


def test_import_steps_around_a_media_folder_that_is_already_there(tmp_path):
    (tmp_path / "Trip_media").mkdir()
    (tmp_path / "Trip_media" / "keep.png").write_bytes(b"mine")
    note = office_preview.import_as_markdown(make_odt(tmp_path / "Trip.odt",
                                                      picture=picture_bytes()))
    assert note == str(tmp_path / "Trip 2.md")
    assert os.listdir(tmp_path / "Trip_media") == ["keep.png"]
    assert (tmp_path / "Trip 2_media" / "photo.png").exists()
    assert "Leave at   eight." in open(note, encoding="utf-8").read()


def test_import_refuses_files_it_cannot_read(tmp_path):
    (tmp_path / "a.pages").write_bytes(b"x")
    with pytest.raises(office_preview.DocumentUnreadable):
        office_preview.import_as_markdown(str(tmp_path / "a.pages"))
    assert sorted(os.listdir(tmp_path)) == ["a.pages"]


@posix_only
def test_import_uses_pandoc_in_the_documents_folder(tmp_path, monkeypatch):
    log = tmp_path / "args.txt"
    fake_pandoc(tmp_path, monkeypatch,
                f'echo "$PWD|$@" > "{log}"\necho "# From pandoc"\n'
                'echo "![](Doc_media/media/image1.png)"\n')
    folder = tmp_path / "docs"
    folder.mkdir()
    note = office_preview.import_as_markdown(make_docx(folder / "Doc.docx"))
    assert open(note, encoding="utf-8").read().startswith("# From pandoc")
    cwd, args = log.read_text(encoding="utf-8").strip().split("|", 1)
    assert os.path.realpath(cwd) == os.path.realpath(folder)
    assert "--to gfm" in args and "--extract-media Doc_media" in args


def test_pandoc_links_to_a_media_folder_with_a_space_take_the_apps_form():
    text = "![](My Doc_media/media/a.png) and [x](other.png)"
    assert office_preview._rewrite_pandoc_media(text, "My Doc_media") == \
        "![](<My Doc_media/media/a.png>) and [x](other.png)"


def test_zip_reads_stop_at_the_cap_even_when_the_header_lies(tmp_path):
    path = make_zip(tmp_path / "x.zip", {"big.bin": b"a" * 5000})
    with zipfile.ZipFile(path) as archive:
        assert office_preview._zip_member(archive, "big.bin", 100) is None
        assert office_preview._zip_member(archive, "BIG.BIN", 10000) == b"a" * 5000


def test_two_documents_imported_at_the_same_moment_never_share_a_note_or_its_pictures(tmp_path):
    from tests import office_samples
    for trial in range(15):
        folder = tmp_path / f"t{trial}"
        folder.mkdir()
        docx = office_samples.make_docx(folder / "Report.docx",
                                        picture=office_samples.picture_bytes(color="#F00"))
        odt = office_samples.make_odt(folder / "Report.odt",
                                      picture=office_samples.picture_bytes(color="#00F"))
        made = {}
        start = threading.Barrier(2)

        def run(path):
            start.wait()
            made[path] = office_preview.import_as_markdown(str(path), use_pandoc=False)

        threads = [threading.Thread(target=run, args=(p,)) for p in (docx, odt)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        notes = sorted(os.path.basename(made[p]) for p in (docx, odt))
        assert notes == ["Report 2.md", "Report.md"]
        texts = [open(made[p], encoding="utf-8").read() for p in (docx, odt)]
        media = [os.path.splitext(os.path.basename(made[p]))[0] + "_media" for p in (docx, odt)]
        for text, folder_name in zip(texts, media):
            assert folder_name in text.replace("%20", " ").replace("<", "").replace(">", "")


def test_an_import_that_fails_leaves_neither_a_note_nor_a_folder(tmp_path):
    broken = tmp_path / "Broken.docx"
    broken.write_bytes(b"not a zip")
    with pytest.raises((office_preview.DocumentUnreadable, OSError)):
        office_preview.import_as_markdown(str(broken), use_pandoc=False)
    assert sorted(os.listdir(tmp_path)) == ["Broken.docx"]
