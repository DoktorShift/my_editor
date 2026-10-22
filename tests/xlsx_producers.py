# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Excel workbooks as the apps people use write them, built by hand.

Each function writes an ordinary workbook the way one producer writes
it: Excel 365 for Windows (printer settings, custom views, ignored error
checks, data bars linked to their newer part, sparklines, validation
from another sheet, threaded comments with their people, an add-in, the
calculation chain, revision ids and mc:Ignorable namespaces), Excel for
Mac, Excel on the web, a Google Sheets export, LibreOffice's "Save as
xlsx" and a Numbers export. openpyxl cannot write most of these
structures, so the XML is written here, after what those apps write.

``check_package`` checks a saved package the way Excel opens one: the
zip, a content type for every part, every relationship's target, and
the order of the elements of every sheet and of the workbook.
"""

from __future__ import annotations

import re
import zipfile

from lxml import etree

MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_R = "http://schemas.openxmlformats.org/package/2006/relationships"

MC_ATTRS = ('xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'mc:Ignorable="x14ac xr xr2 xr3" '
            'xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" '
            'xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision" '
            'xmlns:xr2="http://schemas.microsoft.com/office/spreadsheetml/2015/revision2" '
            'xmlns:xr3="http://schemas.microsoft.com/office/spreadsheetml/2016/revision3"')
PLAIN_ATTRS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

STYLES = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{MAIN}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
<dxfs count="1"><dxf><font><color rgb="FF9C0006"/></font></dxf></dxfs>
<tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>'''

THEME = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme"><a:themeElements><a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2><a:accent1><a:srgbClr val="4472C4"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>'''

CORE = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>Ann</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">2026-01-05T09:00:00Z</dcterms:created></cp:coreProperties>'''


def _app(name: str) -> str:
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties '
            'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
            f'<Application>{name}</Application></Properties>')


CUSTOM = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="Project"><vt:lpwstr>Budget</vt:lpwstr></property></Properties>'''

PRINTER = bytes(range(256)) * 8      # a DEVMODE: binary, kept byte for byte

GUID_CF = "{6B1E33C2-4D7A-4E0F-9A11-2C3D4E5F6A7B}"
GUID_VIEW = "{9D9C1A6E-31B0-4D33-8E77-6A1C1C2A2B3C}"
GUID_PERSON = "{3F2A1B0C-1D2E-4F50-8A9B-0C1D2E3F4A5B}"
GUID_THREAD = "{0A1B2C3D-4E5F-4A6B-8C7D-9E0F1A2B3C4D}"


def _cells(rows):
    """sheetData rows: ``rows`` is a list of lists of (ref, kind, text).
    As every producer writes them, they hold self-closing elements: a
    styled empty cell (``<c r="E1" s="0"/>``) and an empty row with only a
    height (``<row r="..." ht="20" customHeight="1"/>``) after the data."""
    out = []
    for index, cells in enumerate(rows, start=1):
        items = []
        for ref, kind, text in cells:
            if kind == "f":
                items.append(f'<c r="{ref}"><f>{text}</f><v>0</v></c>')
            elif kind == "s":
                items.append(f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>')
            else:
                items.append(f'<c r="{ref}"><v>{text}</v></c>')
        if index == 1:
            items.append('<c r="E1" s="0"/>')
        out.append(f'<row r="{index}">{"".join(items)}</row>')
    out.append(f'<row r="{len(rows) + 2}" ht="20" customHeight="1"/>')
    return "<sheetData>" + "".join(out) + "</sheetData>"


EMPTY_SHEET = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet '
               f'xmlns="{MAIN}" {PLAIN_ATTRS}><sheetViews><sheetView workbookViewId="0"/>'
               "</sheetViews><sheetData/><pageMargins left=\"0.7\" right=\"0.7\" "
               'top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>')


def _empty_sheet(package, sheet_id):
    """A sheet with no cells at all: an empty, self-closing sheetData."""
    package.add(f"xl/worksheets/sheet{sheet_id}.xml", EMPTY_SHEET, SHEET_TYPE)


DATA_ROWS = [[("A1", "s", "Item"), ("B1", "s", "Amount"), ("C1", "s", "Trend")],
             [("A2", "s", "Rent"), ("B2", "n", "1200")],
             [("A3", "s", "Food"), ("B3", "n", "350")],
             [("A4", "s", "Power"), ("B4", "n", "80")],
             [("A5", "s", "Total"), ("B5", "f", "SUM(B2:B4)")]]
LIST_ROWS = [[("A1", "s", "yes")], [("A2", "s", "no")]]


class Package:
    """A workbook package written part by part."""

    def __init__(self) -> None:
        self.parts = {}
        self.types = {}          # part name -> content type
        self.defaults = {"rels": "application/vnd.openxmlformats-package.relationships+xml",
                         "xml": "application/xml"}
        self.rels = {}           # part -> list of (id, type, target)

    def add(self, name, data, content_type=None):
        self.parts[name] = data.encode("utf-8") if isinstance(data, str) else data
        if content_type:
            self.types[name] = content_type

    def relate(self, source, rel_id, rel_type, target):
        self.rels.setdefault(source, []).append((rel_id, rel_type, target))

    def write(self, path):
        types = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">']
        for ext, ctype in self.defaults.items():
            types.append(f'<Default Extension="{ext}" ContentType="{ctype}"/>')
        for name, ctype in self.types.items():
            types.append(f'<Override PartName="/{name}" ContentType="{ctype}"/>')
        types.append("</Types>")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "".join(types))
            for source, rels in self.rels.items():
                folder, _slash, name = source.rpartition("/")
                rels_name = f"{folder}/_rels/{name}.rels" if folder else "_rels/.rels"
                body = "".join(f'<Relationship Id="{i}" Type="{t}" Target="{g}"/>'
                               for i, t, g in rels)
                archive.writestr(rels_name, '<?xml version="1.0" encoding="UTF-8" '
                                 f'standalone="yes"?><Relationships xmlns="{PKG_R}">{body}'
                                 "</Relationships>")
            for name, data in self.parts.items():
                archive.writestr(name, data)


SHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
OD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _base(app: str, *, theme=True, custom=False) -> Package:
    package = Package()
    package.add("docProps/core.xml", CORE,
                "application/vnd.openxmlformats-package.core-properties+xml")
    package.add("docProps/app.xml", _app(app),
                "application/vnd.openxmlformats-officedocument.extended-properties+xml")
    package.relate("", "rId1", OD + "/officeDocument", "xl/workbook.xml")
    package.relate("", "rId2",
                   "http://schemas.openxmlformats.org/package/2006/relationships/metadata/"
                   "core-properties", "docProps/core.xml")
    package.relate("", "rId3", OD + "/extended-properties", "docProps/app.xml")
    if custom:
        package.add("docProps/custom.xml", CUSTOM,
                    "application/vnd.openxmlformats-officedocument.custom-properties+xml")
        package.relate("", "rId4", OD + "/custom-properties", "docProps/custom.xml")
    package.add("xl/styles.xml", STYLES,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml")
    package.relate("xl/workbook.xml", "rIdS", OD + "/styles", "styles.xml")
    if theme:
        package.add("xl/theme/theme1.xml", THEME,
                    "application/vnd.openxmlformats-officedocument.theme+xml")
        package.relate("xl/workbook.xml", "rIdT", OD + "/theme", "theme/theme1.xml")
    return package


def _workbook(package, sheets, *, attrs=PLAIN_ATTRS, before="", after="", ext=""):
    entries = "".join(f'<sheet name="{name}" sheetId="{sheet_id}" r:id="rIdW{sheet_id}"/>'
                      for name, sheet_id in sheets)
    xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="{MAIN}" '
           f'{attrs}>{before}<sheets>{entries}</sheets>{after}{ext}</workbook>')
    package.add("xl/workbook.xml", xml,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml")
    for _name, sheet_id in sheets:
        package.relate("xl/workbook.xml", f"rIdW{sheet_id}", OD + "/worksheet",
                       f"worksheets/sheet{sheet_id}.xml")


def excel_windows(path):
    """Excel 365 for Windows: everything an ordinary, lived-in workbook
    gathers."""
    package = _base("Microsoft Excel", custom=True)
    _workbook(
        package, [("Data", 1), ("Empty", 9), ("Lists", 2)], attrs=MC_ATTRS + ' xr:revisionPtr=""',
        before=('<fileVersion appName="xl" lastEdited="7" lowestEdited="7" rupBuild="27425"/>'
                '<workbookPr defaultThemeVersion="166925"/>'
                '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-'
                'compatibility/2006"><mc:Choice Requires="x15"><x15ac:absPath url="C:\\Users\\'
                'ann\\" xmlns:x15ac="http://schemas.microsoft.com/office/spreadsheetml/2010/11/'
                'ac"/></mc:Choice></mc:AlternateContent>'
                '<bookViews><workbookView xWindow="-120" yWindow="-120" windowWidth="29040" '
                'windowHeight="15840" xr2:uid="{00000000-000D-0000-FFFF-FFFF00000000}"/>'
                "</bookViews>"),
        after=('<calcPr calcId="191029"/>'
               f'<customWorkbookViews><customWorkbookView name="Print" guid="{GUID_VIEW}" '
               'windowWidth="1200" windowHeight="800" activeSheetId="2"/></customWorkbookViews>'),
        ext=('<extLst><ext uri="{140A7094-0E35-4892-8432-C4D2E57EDEB5}" '
             'xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main">'
             '<x15:workbookPr chartTrackingRefBase="1"/></ext></extLst>'))
    data_bar = ('<conditionalFormatting sqref="B2:B4"><cfRule type="dataBar" priority="1">'
                '<dataBar><cfvo type="min"/><cfvo type="max"/><color rgb="FF638EC6"/></dataBar>'
                '<extLst><ext uri="{B025F937-C7B1-47D3-B67F-A62EFF666E3E}" '
                'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
                f"<x14:id>{GUID_CF}</x14:id></ext></extLst></cfRule></conditionalFormatting>"
                '<conditionalFormatting sqref="A2:A4"><cfRule type="expression" dxfId="0" '
                'priority="2"><formula>$B2&gt;1000</formula></cfRule></conditionalFormatting>')
    extensions = (
        '<extLst><ext uri="{78C0D931-6437-407d-A8EE-F0AAD7539E65}" '
        'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        '<x14:conditionalFormattings><x14:conditionalFormatting '
        'xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main"><x14:cfRule '
        f'type="dataBar" id="{GUID_CF}"><x14:dataBar minLength="0" maxLength="100" '
        'gradient="0"><x14:cfvo type="autoMin"/><x14:cfvo type="autoMax"/>'
        '<x14:negativeFillColor rgb="FFFF0000"/><x14:axisColor rgb="FF000000"/>'
        "</x14:dataBar></x14:cfRule><xm:sqref>B2:B4</xm:sqref></x14:conditionalFormatting>"
        "</x14:conditionalFormattings></ext>"
        '<ext uri="{CCE6A557-97BC-4b89-ADB6-D9C93CAAB3DF}" '
        'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        '<x14:dataValidations count="1" xmlns:xm="http://schemas.microsoft.com/office/excel/'
        '2006/main"><x14:dataValidation type="list" allowBlank="1" showInputMessage="1" '
        'showErrorMessage="1"><x14:formula1><xm:f>Lists!$A$1:$A$2</xm:f></x14:formula1>'
        "<xm:sqref>D2:D4</xm:sqref></x14:dataValidation></x14:dataValidations></ext>"
        '<ext uri="{05C60535-1F16-4fd2-B633-F4F36F0B64E0}" '
        'xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main">'
        '<x14:sparklineGroups xmlns:xm="http://schemas.microsoft.com/office/excel/2006/main">'
        '<x14:sparklineGroup displayEmptyCellsAs="gap"><x14:colorSeries rgb="FF376092"/>'
        "<x14:sparklines><x14:sparkline><xm:f>Data!B2:B4</xm:f><xm:sqref>C2</xm:sqref>"
        "</x14:sparkline></x14:sparklines></x14:sparklineGroup></x14:sparklineGroups></ext>"
        "</extLst>")
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{MAIN}" '
             f'{MC_ATTRS} xr:uid="{{00000000-0001-0000-0000-000000000000}}">'
             '<dimension ref="A1:D5"/><sheetViews><sheetView tabSelected="1" workbookViewId="0">'
             '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
             '<selection pane="bottomLeft" activeCell="B3" sqref="B3"/></sheetView></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15" x14ac:dyDescent="0.25"/>' + _cells(DATA_ROWS) +
             f'<customSheetViews><customSheetView guid="{GUID_VIEW}" scale="90">'
             '<pane ySplit="1" topLeftCell="A3" activePane="bottomLeft" state="frozen"/>'
             '<selection pane="bottomLeft" activeCell="B4" sqref="B4"/>'
             '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" '
             'footer="0.3"/><pageSetup paperSize="9" orientation="landscape" r:id="rId1"/>'
             "</customSheetView></customSheetViews>" + data_bar +
             '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" '
             'footer="0.3"/><pageSetup paperSize="9" orientation="portrait" r:id="rId1"/>'
             '<ignoredErrors><ignoredError sqref="B2:B4" numberStoredAsText="1"/>'
             '<ignoredError sqref="B5" formulaRange="1"/></ignoredErrors>'
             '<legacyDrawing r:id="rId3"/>' + extensions + "</worksheet>")
    package.add("xl/worksheets/sheet1.xml", sheet, SHEET_TYPE)
    _empty_sheet(package, 9)
    package.add("xl/worksheets/sheet2.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{MAIN}"'
                f' {PLAIN_ATTRS}>' + _cells(LIST_ROWS) +
                '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" '
                'footer="0.3"/></worksheet>', SHEET_TYPE)
    package.defaults["bin"] = ("application/vnd.openxmlformats-officedocument.spreadsheetml."
                               "printerSettings")
    package.defaults["vml"] = "application/vnd.openxmlformats-officedocument.vmlDrawing"
    package.add("xl/printerSettings/printerSettings1.bin", PRINTER)
    package.relate("xl/worksheets/sheet1.xml", "rId1", OD + "/printerSettings",
                   "../printerSettings/printerSettings1.bin")
    # A threaded comment, its legacy note, and the person who wrote it.
    package.add("xl/comments1.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><comments xmlns="{MAIN}">'
                f"<authors><author>tc={GUID_THREAD}</author></authors><commentList>"
                '<comment ref="B3" authorId="0"><text><t>[Threaded comment]\n\nYour version of '
                "Excel allows you to read this threaded comment. Comment:\n    Check the amount"
                "</t></text></comment></commentList></comments>",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml")
    package.add("xl/drawings/vmlDrawing1.vml",
                '<xml xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:'
                'office:office" xmlns:x="urn:schemas-microsoft-com:office:excel"><v:shape '
                'type="#_x0000_t202" style="width:108pt;height:59.25pt"><x:ClientData '
                'ObjectType="Note"><x:Row>2</x:Row><x:Column>1</x:Column></x:ClientData>'
                "</v:shape></xml>")
    package.relate("xl/worksheets/sheet1.xml", "rId2", OD + "/comments", "../comments1.xml")
    package.relate("xl/worksheets/sheet1.xml", "rId3", OD + "/vmlDrawing",
                   "../drawings/vmlDrawing1.vml")
    package.add("xl/threadedComments/threadedComment1.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><ThreadedComments '
                'xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments" '
                'xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<threadedComment ref="B3" dT="2026-01-05T09:30:00.00" personId="{GUID_PERSON}" '
                f'id="{GUID_THREAD}"><text>Check the amount</text></threadedComment>'
                "</ThreadedComments>", "application/vnd.ms-excel.threadedcomments+xml")
    package.relate("xl/worksheets/sheet1.xml", "rId4",
                   "http://schemas.microsoft.com/office/2017/10/relationships/threadedComment",
                   "../threadedComments/threadedComment1.xml")
    package.add("xl/persons/person.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><personList '
                'xmlns="http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments" '
                'xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><person '
                f'displayName="Ann" id="{GUID_PERSON}" userId="ann@example.org" '
                'providerId="AD"/></personList>', "application/vnd.ms-excel.person+xml")
    package.relate("xl/workbook.xml", "rIdP",
                   "http://schemas.microsoft.com/office/2017/10/relationships/person",
                   "persons/person.xml")
    # An Office add-in's task pane.
    package.add("xl/webextensions/taskpanes.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><wetp:taskpanes '
                'xmlns:wetp="http://schemas.microsoft.com/office/webextensions/taskpanes/2010/11"'
                ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<wetp:taskpane dockstate="right" visibility="0" width="350" row="4">'
                '<wetp:webextensionref r:id="rId1"/></wetp:taskpane></wetp:taskpanes>',
                "application/vnd.ms-office.webextensiontaskpanes+xml")
    package.add("xl/webextensions/webextension1.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><we:webextension '
                'xmlns:we="http://schemas.microsoft.com/office/webextensions/webextension/2010/11"'
                ' id="{11111111-2222-3333-4444-555555555555}"><we:reference '
                'id="wa104381222" version="1.0.0.0" store="en-US" storeType="OMEX"/>'
                "<we:alternateReferences/><we:properties/><we:bindings/><we:snapshot/>"
                "</we:webextension>", "application/vnd.ms-office.webextension+xml")
    package.relate("xl/webextensions/taskpanes.xml", "rId1",
                   "http://schemas.microsoft.com/office/2011/relationships/webextension",
                   "webextension1.xml")
    package.relate("", "rId5",
                   "http://schemas.microsoft.com/office/2011/relationships/webextensiontaskpanes",
                   "xl/webextensions/taskpanes.xml")
    package.add("xl/calcChain.xml",
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><calcChain xmlns="{MAIN}">'
                '<c r="B5" i="1"/></calcChain>',
                "application/vnd.openxmlformats-officedocument.spreadsheetml.calcChain+xml")
    package.relate("xl/workbook.xml", "rIdC", OD + "/calcChain", "calcChain.xml")
    package.write(path)


def excel_mac(path):
    """Excel for Mac: printer settings on every sheet, a custom view."""
    package = _base("Microsoft Macintosh Excel")
    _workbook(package, [("Sheet1", 1), ("Empty", 9)], attrs=MC_ATTRS,
              before='<workbookPr defaultThemeVersion="124226"/><bookViews><workbookView '
                     'xWindow="0" yWindow="500" windowWidth="28800" windowHeight="16000"/>'
                     "</bookViews>",
              after='<calcPr calcId="191029"/>')
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{MAIN}" '
             f'{MC_ATTRS}><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
             '<sheetViews><sheetView workbookViewId="0"/></sheetViews>' + _cells(DATA_ROWS) +
             '<pageMargins left="0.75" right="0.75" top="1" bottom="1" header="0.5" '
             'footer="0.5"/><pageSetup paperSize="9" fitToHeight="0" orientation="portrait" '
             'r:id="rId1"/><headerFooter><oddFooter>&amp;P</oddFooter></headerFooter>'
             '<ignoredErrors><ignoredError sqref="B2:B4" numberStoredAsText="1"/></ignoredErrors>'
             "</worksheet>")
    package.add("xl/worksheets/sheet1.xml", sheet, SHEET_TYPE)
    _empty_sheet(package, 9)
    package.defaults["bin"] = ("application/vnd.openxmlformats-officedocument.spreadsheetml."
                               "printerSettings")
    package.add("xl/printerSettings/printerSettings1.bin", PRINTER[:600])
    package.relate("xl/worksheets/sheet1.xml", "rId1", OD + "/printerSettings",
                   "../printerSettings/printerSettings1.bin")
    package.write(path)


def excel_web(path):
    """Excel on the web: revision ids, no printer settings, a threaded comment."""
    package = _base("Microsoft Excel Online")
    _workbook(package, [("Budget", 1), ("Empty", 9)], attrs=MC_ATTRS + ' xr:revisionPtr=""',
              before='<bookViews><workbookView xWindow="-110" yWindow="-110" windowWidth="1000"'
                     ' windowHeight="800" xr2:uid="{0}"/></bookViews>'.replace(
                         "{0}", "{00000000-000D-0000-FFFF-FFFF00000000}"),
              after='<calcPr calcId="191028"/>')
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{MAIN}" '
             f'{MC_ATTRS} xr:uid="{{AB5D5E4C-3E3E-4A44-8C5E-1D9A2C6B7E21}}">'
             '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
             '<sheetFormatPr defaultRowHeight="15" x14ac:dyDescent="0.25"/>' + _cells(DATA_ROWS) +
             '<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" '
             'footer="0.3"/></worksheet>')
    package.add("xl/worksheets/sheet1.xml", sheet, SHEET_TYPE)
    _empty_sheet(package, 9)
    package.write(path)


def google_sheets(path):
    """A Google Sheets download: plain, no theme."""
    package = _base("", theme=False)
    _workbook(package, [("Sheet1", 1), ("Empty", 9)], before="<workbookPr/>",
              after="<definedNames/><calcPr/>")
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{MAIN}" '
             f'{PLAIN_ATTRS}><sheetPr><outlinePr summaryBelow="0" summaryRight="0"/></sheetPr>'
             '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
             '<sheetFormatPr customHeight="1" defaultColWidth="12.63" defaultRowHeight="15.75"/>'
             + _cells(DATA_ROWS) + "</worksheet>")
    package.add("xl/worksheets/sheet1.xml", sheet, SHEET_TYPE)
    _empty_sheet(package, 9)
    package.write(path)


def libreoffice(path):
    """LibreOffice Calc's "Save as xlsx"."""
    package = _base("LibreOffice/24.2.5.2$MacOSX_AARCH64 LibreOffice_project/x")
    _workbook(package, [("Sheet1", 1), ("Empty", 9)],
              before='<fileVersion appName="Calc"/><workbookPr backupFile="false" '
                     'showObjects="all" date1904="false"/><workbookProtection/><bookViews>'
                     '<workbookView showHorizontalScroll="true" showVerticalScroll="true" '
                     'showSheetTabs="true" xWindow="0" yWindow="0" windowWidth="16384" '
                     'windowHeight="8192" tabRatio="500" firstSheet="0" activeTab="0"/>'
                     "</bookViews>",
              after='<calcPr iterateCount="100" refMode="A1" iterate="false" '
                    'iterateDelta="0.001"/>')
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{MAIN}" '
             f'{PLAIN_ATTRS}><sheetPr filterMode="false"><pageSetUpPr fitToPage="false"/>'
             '</sheetPr><dimension ref="A1:B5"/><sheetViews><sheetView showFormulas="false" '
             'showGridLines="true" zoomScale="100" workbookViewId="0"><selection pane="topLeft" '
             'activeCell="A1" sqref="A1"/></sheetView></sheetViews><sheetFormatPr '
             'defaultRowHeight="12.8" outlineLevelRow="0" outlineLevelCol="0"/>'
             + _cells(DATA_ROWS) + '<printOptions headings="false" gridLines="false"/>'
             '<pageMargins left="0.7875" right="0.7875" top="1.05277777777778" '
             'bottom="1.05277777777778" header="0.7875" footer="0.7875"/><pageSetup '
             'paperSize="9" scale="100" orientation="portrait" pageOrder="downThenOver"/>'
             '<headerFooter differentFirst="false" differentOddEven="false"><oddHeader>'
             "&amp;C&amp;&quot;Times New Roman,Regular&quot;&amp;12&amp;A</oddHeader>"
             "</headerFooter></worksheet>")
    package.add("xl/worksheets/sheet1.xml", sheet, SHEET_TYPE)
    _empty_sheet(package, 9)
    package.write(path)


def numbers(path):
    """A Numbers export: a table per sheet, its name as the sheet's."""
    package = _base("Numbers")
    _workbook(package, [("Table 1", 1), ("Empty", 9)],
              before='<bookViews><workbookView activeTab="0"/></bookViews>')
    sheet = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="{MAIN}" '
             f'{PLAIN_ATTRS}><sheetPr><pageSetUpPr fitToPage="1"/></sheetPr><dimension '
             'ref="A1:B5"/><sheetViews><sheetView workbookViewId="0" showGridLines="0" '
             'defaultGridColor="0"/></sheetViews><sheetFormatPr defaultColWidth="16.3333" '
             'defaultRowHeight="18" customHeight="1" outlineLevelRow="0" outlineLevelCol="0"/>'
             + _cells(DATA_ROWS) + '<pageMargins left="0.5" right="0.5" top="0.75" '
             'bottom="0.75" header="0.25" footer="0.25"/><pageSetup firstPageNumber="1" '
             'fitToHeight="1" fitToWidth="1" scale="100" useFirstPageNumber="0" '
             'orientation="portrait" pageOrder="downThenOver"/><headerFooter><oddFooter>'
             "&amp;C&amp;&quot;Helvetica Neue,Regular&quot;&amp;12&amp;K000000&amp;P"
             "</oddFooter></headerFooter></worksheet>")
    package.add("xl/worksheets/sheet1.xml", sheet, SHEET_TYPE)
    _empty_sheet(package, 9)
    package.write(path)


PRODUCERS = {"excel_windows": excel_windows, "excel_mac": excel_mac, "excel_web": excel_web,
             "google_sheets": google_sheets, "libreoffice": libreoffice, "numbers": numbers}

SHEET_ORDER = (
    "sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData", "sheetCalcPr",
    "sheetProtection", "protectedRanges", "scenarios", "autoFilter", "sortState",
    "dataConsolidate", "customSheetViews", "mergeCells", "phoneticPr", "conditionalFormatting",
    "dataValidations", "hyperlinks", "printOptions", "pageMargins", "pageSetup", "headerFooter",
    "rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors", "smartTags",
    "drawing", "legacyDrawing", "legacyDrawingHF", "drawingHF", "picture", "oleObjects",
    "controls", "webPublishItems", "tableParts", "extLst")
BOOK_ORDER = (
    "fileVersion", "fileSharing", "workbookPr", "workbookProtection", "bookViews", "sheets",
    "functionGroups", "externalReferences", "definedNames", "calcPr", "oleSize",
    "customWorkbookViews", "pivotCaches", "smartTagPr", "smartTagTypes", "webPublishing",
    "fileRecoveryPr", "webPublishObjects", "extLst")


def _in_order(root, order):
    names = [etree.QName(c).localname for c in root if isinstance(c.tag, str)]
    names = [n for n in names if n != "AlternateContent"]
    unknown = [n for n in names if n not in order]
    ranks = [order.index(n) for n in names if n in order]
    return not unknown and ranks == sorted(ranks), names


def _target(source, target):
    if target.startswith("/"):
        return target.lstrip("/")
    folder = source.rsplit("/", 1)[0] if "/" in source else ""
    parts = []
    for piece in (folder + "/" + target).split("/"):
        if piece == "..":
            parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def check_package(path):
    """Problems Excel would have opening the package at ``path``."""
    problems = []
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            problems.append("zip")
        names = set(archive.namelist())
        types = etree.fromstring(archive.read("[Content_Types].xml"))
        overrides = {o.get("PartName").lstrip("/") for o in types
                     if etree.QName(o).localname == "Override"}
        defaults = {d.get("Extension").lower() for d in types
                    if etree.QName(d).localname == "Default"}
        for name in names:
            if name == "[Content_Types].xml" or name.endswith("/"):
                continue
            if name not in overrides and name.rsplit(".", 1)[-1].lower() not in defaults:
                problems.append(f"no content type: {name}")
        for override in overrides:
            if override not in names:
                problems.append(f"content type for a missing part: {override}")
        for name in names:
            if not name.endswith(".rels"):
                continue
            folder = name[:-len(".rels")].replace("_rels/", "")
            source = folder if folder else ""
            for rel in etree.fromstring(archive.read(name)):
                if rel.get("TargetMode") == "External":
                    continue
                target = _target(source, rel.get("Target"))
                if target not in names:
                    problems.append(f"{name}: {rel.get('Id')} points to missing {target}")
        for name in names:
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                ok, seen = _in_order(etree.fromstring(archive.read(name)), SHEET_ORDER)
                if not ok:
                    problems.append(f"{name} out of order: {seen}")
                rels_name = f"xl/worksheets/_rels/{name.rsplit('/', 1)[1]}.rels"
                ids = set()
                if rels_name in names:
                    ids = {r.get("Id") for r in etree.fromstring(archive.read(rels_name))}
                for item in etree.fromstring(archive.read(name)).iter():
                    rel_id = item.get(f"{{{R}}}id") if isinstance(item.tag, str) else None
                    if rel_id and rel_id not in ids:
                        problems.append(f"{name}: r:id {rel_id} has no relationship")
        ok, seen = _in_order(etree.fromstring(archive.read("xl/workbook.xml")), BOOK_ORDER)
        if not ok:
            problems.append(f"workbook out of order: {seen}")
    return problems
