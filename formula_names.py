# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Spreadsheet function names and formula punctuation in each language.

Excel, Numbers and LibreOffice show formulas in the language of the app:
in German "=SUMME(A1:A3;1,5)" is what English writes "=SUM(A1:A3,1.5)".
MyEditor does the same in the formula bar and while a cell is edited.
Files always keep the English (canonical) spelling; sheet_formulas
translates between the two with this table (``localize_formula``,
``delocalize_formula``).

A language without an entry here shows formulas as English writes them.
Typing the English name of a function is accepted in every language.
"""

# Canonical (English) function name -> the name in the language.
FUNCTION_NAMES = {
    "de": {
        "SUM": "SUMME",
        "AVERAGE": "MITTELWERT",
        "MIN": "MIN",
        "MAX": "MAX",
        "COUNT": "ANZAHL",
        "COUNTA": "ANZAHL2",
        "COUNTBLANK": "ANZAHLLEEREZELLEN",
        "COUNTIF": "ZÄHLENWENN",
        "SUMIF": "SUMMEWENN",
        "AVERAGEIF": "MITTELWERTWENN",
        "PRODUCT": "PRODUKT",
        "IF": "WENN",
        "IFERROR": "WENNFEHLER",
        "AND": "UND",
        "OR": "ODER",
        "NOT": "NICHT",
        "ROUND": "RUNDEN",
        "ROUNDUP": "AUFRUNDEN",
        "ROUNDDOWN": "ABRUNDEN",
        "INT": "GANZZAHL",
        "ABS": "ABS",
        "MOD": "REST",
        "POWER": "POTENZ",
        "SQRT": "WURZEL",
        "CONCATENATE": "VERKETTEN",
        "CONCAT": "KONKATENIEREN",
        "TEXTJOIN": "TEXTVERKETTEN",
        "LEN": "LÄNGE",
        "LEFT": "LINKS",
        "RIGHT": "RECHTS",
        "MID": "TEIL",
        "UPPER": "GROSS",
        "LOWER": "KLEIN",
        "TRIM": "GLÄTTEN",
        "SUBSTITUTE": "WECHSELN",
        "VALUE": "WERT",
        "TODAY": "HEUTE",
        "NOW": "JETZT",
        "DATE": "DATUM",
        "YEAR": "JAHR",
        "MONTH": "MONAT",
        "DAY": "TAG",
        "VLOOKUP": "SVERWEIS",
        "HLOOKUP": "WVERWEIS",
        "INDEX": "INDEX",
        "MATCH": "VERGLEICH",
    },
}

# Other names people type for a function (Excel's German name for CONCAT
# is TEXTKETTE, LibreOffice's KONKATENIEREN): name -> canonical.
ALIASES = {
    "de": {
        "TEXTKETTE": "CONCAT",
    },
}

# TRUE and FALSE.
BOOLEANS = {
    "de": ("WAHR", "FALSCH"),
}

# The argument separator and the decimal mark inside formulas.
SYNTAX = {
    "de": {"separator": ";", "decimal": ","},
}

# Functions Excel writes into files with a prefix (added in Excel 2010
# and later): the name -> its prefix. Without it, Excel shows #NAME?.
FUTURE_FUNCTIONS = {name: "_xlfn." for name in (
    "CONCAT", "TEXTJOIN", "IFS", "SWITCH", "MAXIFS", "MINIFS", "IFNA", "XLOOKUP", "XMATCH",
    "SORTBY", "UNIQUE", "SEQUENCE", "RANDARRAY", "LET", "STDEV.S", "STDEV.P", "VAR.S",
    "VAR.P", "FORECAST.ETS", "FORECAST.LINEAR", "DAYS", "ISOWEEKNUM", "CEILING.MATH",
    "FLOOR.MATH")}
FUTURE_FUNCTIONS.update({"SORT": "_xlfn._xlws.", "FILTER": "_xlfn._xlws."})

# Functions whose result spills over several cells (Excel 365's dynamic
# arrays). A file marks such a formula in a part of its own
# (xl/metadata.xml) that MyEditor does not write, so it does not let them
# be typed.
SPILLING_FUNCTIONS = frozenset({"SORT", "SORTBY", "FILTER", "UNIQUE", "SEQUENCE", "RANDARRAY"})
