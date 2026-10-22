# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spreadsheet files for the tests, made on the spot.

``realistic_ods`` writes an OpenDocument spreadsheet the way LibreOffice
Calc does: a mimetype stored first, a manifest, styles, settings, a
thumbnail, and in content.xml repeated rows and cells, automatic styles
with number formats, a formula with its stored result, a comment and the
long run of empty rows at the end that Calc always writes.
"""

from __future__ import annotations

import zipfile

import openpyxl

ODS_MIMETYPE = b"application/vnd.oasis.opendocument.spreadsheet"

_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:number="urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0" '
    'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2" '
    'xmlns:calcext="urn:org:documentfoundation:names:experimental:calc:xmlns:calcext:1.0" '
    'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"'
)

CONTENT = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content {_NS} office:version="1.3">
<office:automatic-styles>
<style:style style:name="co1" style:family="table-column"><style:table-column-properties fo:break-before="auto" style:column-width="2.258cm"/></style:style>
<style:style style:name="co2" style:family="table-column"><style:table-column-properties fo:break-before="auto" style:column-width="5cm"/></style:style>
<style:style style:name="ro1" style:family="table-row"><style:table-row-properties style:row-height="0.452cm" fo:break-before="auto" style:use-optimal-row-height="true"/></style:style>
<style:style style:name="ta1" style:family="table" style:master-page-name="Default"><style:table-properties table:display="true" style:writing-mode="lr-tb"/></style:style>
<number:currency-style style:name="N111P0" style:volatile="true"><number:number number:decimal-places="2" number:min-decimal-places="2" number:min-integer-digits="1" number:grouping="true"/><number:text> </number:text><number:currency-symbol number:language="de" number:country="DE">€</number:currency-symbol></number:currency-style>
<number:percentage-style style:name="N11"><number:number number:decimal-places="0" number:min-decimal-places="0" number:min-integer-digits="1"/><number:text>%</number:text></number:percentage-style>
<style:style style:name="ce1" style:family="table-cell" style:parent-style-name="Default"><style:text-properties fo:font-weight="bold"/></style:style>
<style:style style:name="ce2" style:family="table-cell" style:parent-style-name="Default" style:data-style-name="N111P0"/>
<style:style style:name="ce3" style:family="table-cell" style:parent-style-name="Default" style:data-style-name="N11"/>
<style:style style:name="ce4" style:family="table-cell" style:parent-style-name="Default"><style:table-cell-properties fo:background-color="#ffff00"/></style:style>
</office:automatic-styles>
<office:body>
<office:spreadsheet>
<table:calculation-settings table:automatic-find-labels="false"/>
<table:table table:name="Budget" table:style-name="ta1">
<table:table-column table:style-name="co2" table:default-cell-style-name="Default"/>
<table:table-column table:style-name="co1" table:number-columns-repeated="1023" table:default-cell-style-name="Default"/>
<table:table-row table:style-name="ro1"><table:table-cell table:style-name="ce1" office:value-type="string" calcext:value-type="string"><text:p>Item</text:p></table:table-cell><table:table-cell table:style-name="ce1" office:value-type="string" calcext:value-type="string"><text:p>Amount</text:p></table:table-cell><table:table-cell table:number-columns-repeated="1022"/></table:table-row>
<table:table-row table:style-name="ro1"><table:table-cell office:value-type="string" calcext:value-type="string"><office:annotation><dc:creator>Ann</dc:creator><text:p>Check this</text:p></office:annotation><text:p>Rent</text:p></table:table-cell><table:table-cell table:style-name="ce2" office:value-type="currency" office:currency="EUR" office:value="1200" calcext:value-type="currency"><text:p>1.200,00 €</text:p></table:table-cell><table:table-cell table:number-columns-repeated="1022"/></table:table-row>
<table:table-row table:style-name="ro1"><table:table-cell office:value-type="string" calcext:value-type="string"><text:p>Food</text:p></table:table-cell><table:table-cell table:style-name="ce2" office:value-type="currency" office:currency="EUR" office:value="350.5" calcext:value-type="currency"><text:p>350,50 €</text:p></table:table-cell><table:table-cell table:number-columns-repeated="1022"/></table:table-row>
<table:table-row table:style-name="ro1" table:number-rows-repeated="3"><table:table-cell table:style-name="ce4" table:number-columns-repeated="2"/><table:table-cell table:number-columns-repeated="1022"/></table:table-row>
<table:table-row table:style-name="ro1"><table:table-cell office:value-type="string" calcext:value-type="string"><text:p>Total</text:p></table:table-cell><table:table-cell table:style-name="ce2" table:formula="of:=SUM([.B2:.B5])" office:value-type="currency" office:currency="EUR" office:value="1550.5" calcext:value-type="currency"><text:p>1.550,50 €</text:p></table:table-cell><table:table-cell table:style-name="ce3" table:formula="of:=[.B3]/[.B7]" office:value-type="percentage" office:value="0.226056110931957" calcext:value-type="percentage"><text:p>23%</text:p></table:table-cell><table:table-cell table:number-columns-repeated="1021"/></table:table-row>
<table:table-row table:style-name="ro1" table:number-rows-repeated="1048569"><table:table-cell table:number-columns-repeated="1024"/></table:table-row>
</table:table>
<table:table table:name="Notes" table:style-name="ta1">
<table:table-column table:style-name="co1" table:default-cell-style-name="Default"/>
<table:table-row table:style-name="ro1"><table:table-cell table:formula="of:=[$Budget.B7]*2" office:value-type="float" office:value="3101" calcext:value-type="float"><text:p>3101</text:p></table:table-cell></table:table-row>
</table:table>
<table:named-expressions><table:named-range table:name="Amounts" table:base-cell-address="$Budget.$A$1" table:cell-range-address="$Budget.$B$2:.$B$5"/></table:named-expressions>
</office:spreadsheet>
</office:body>
</office:document-content>
"""

STYLES = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles {_NS} office:version="1.3"><office:styles><style:style style:name="Default" style:family="table-cell"/></office:styles></office:document-styles>
"""

SETTINGS = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-settings {_NS} office:version="1.3"><office:settings><config:config-item-set config:name="ooo:view-settings"><config:config-item-map-indexed config:name="Views"><config:config-item-map-entry><config:config-item-map-named config:name="Tables"><config:config-item-map-entry config:name="Budget"><config:config-item config:name="VerticalSplitMode" config:type="short">2</config:config-item><config:config-item config:name="VerticalSplitPosition" config:type="int">1</config:config-item></config:config-item-map-entry></config:config-item-map-named></config:config-item-map-entry></config:config-item-map-indexed></config:config-item-set></office:settings></office:document-settings>
"""

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.3">
 <manifest:file-entry manifest:full-path="/" manifest:version="1.3" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
 <manifest:file-entry manifest:full-path="Thumbnails/thumbnail.png" manifest:media-type="image/png"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="settings.xml" manifest:media-type="text/xml"/>
 <manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""

META = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta {_NS} office:version="1.3"><office:meta><dc:title>Budget</dc:title></office:meta></office:document-meta>
"""

# A tiny PNG (1x1), as Calc stores a picture of the first page.
THUMBNAIL = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082")


def realistic_ods(path, content: str = CONTENT) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), ODS_MIMETYPE, zipfile.ZIP_STORED)
        archive.writestr("content.xml", content, zipfile.ZIP_DEFLATED)
        archive.writestr("styles.xml", STYLES, zipfile.ZIP_DEFLATED)
        archive.writestr("settings.xml", SETTINGS, zipfile.ZIP_DEFLATED)
        archive.writestr("meta.xml", META, zipfile.ZIP_DEFLATED)
        archive.writestr("Thumbnails/thumbnail.png", THUMBNAIL, zipfile.ZIP_STORED)
        archive.writestr("META-INF/manifest.xml", MANIFEST, zipfile.ZIP_DEFLATED)


def minimal_ods(path, rows_xml: str) -> None:
    """An .ods with one sheet "Sheet1" whose rows are ``rows_xml``."""
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content {_NS} office:version="1.2"><office:body><office:spreadsheet>
<table:table table:name="Sheet1"><table:table-column table:number-columns-repeated="4"/>{rows_xml}</table:table>
</office:spreadsheet></office:body></office:document-content>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), ODS_MIMETYPE, zipfile.ZIP_STORED)
        archive.writestr("content.xml", content, zipfile.ZIP_DEFLATED)
        archive.writestr("META-INF/manifest.xml", MANIFEST, zipfile.ZIP_DEFLATED)


def simple_xlsx(path, *, title: str = "Data") -> None:
    """A small workbook: numbers, a formula, a bold heading, a percent."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    ws["A1"] = 1
    ws["A2"] = 2
    ws["A3"] = "=A1+A2"
    ws["B1"] = "Name"
    wb.save(path)


def styled_xlsx(path) -> None:
    """A workbook that shows what a file can say about looks: a filled
    header row with white bold centred text and a medium line under it,
    a yellow cell, red italic text, thin grey borders, a wrapped note, a
    theme colour, and the first row and column frozen."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.styles.colors import Color
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Plan"
    ws.append(["Item", "Q1", "Q2", "Q3", "Notes"])
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="1F4E78")
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center")
        cell.border = Border(bottom=Side("medium", color="000000"))
    for row in range(2, 60):
        ws.append([f"Row {row}", row * 10, row * 11.5, f"=B{row}+C{row}"])
    ws["E3"] = "A long note that wraps inside its cell"
    ws["E3"].alignment = Alignment(wrap_text=True)
    ws.row_dimensions[3].height = 32
    ws["B5"].fill = PatternFill("solid", fgColor="FFFF00")
    ws["C5"].font = Font(color="C00000", italic=True)
    thin = Side("thin", color="808080")
    ws["C3"].border = Border(left=thin, right=thin, top=thin, bottom=Side("thick"))
    ws["A6"].fill = PatternFill("solid", fgColor=Color(theme=4, tint=0.6))
    ws.freeze_panes = "B2"
    wb.save(path)
