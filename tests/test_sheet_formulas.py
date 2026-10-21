# SPDX-FileCopyrightText: 2026 rinbal
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Formulas: read, calculated as Excel does, and moved with their cells."""

import datetime as dt

import pytest

import formula_names
import sheet_formulas as sf


class _Cell:
    def __init__(self, value=None, formula=None):
        self.value, self.formula, self.state = value, formula, ""


class _Book:
    """Two sheets, "Sheet1" and "My Sheet", as the engine sees a workbook."""

    date1904 = False

    def __init__(self):
        self.sheets = {1: ("Sheet1", {}), 2: ("My Sheet", {})}
        self.names = {}

    def sheet_uids(self):
        return list(self.sheets)

    def sheet_uid(self, name):
        for uid, (sheet_name, _cells) in self.sheets.items():
            if sheet_name.casefold() == name.casefold():
                return uid
        return None

    def cells_of(self, uid):
        return self.sheets[uid][1]

    def store_result(self, uid, row, col, value, state):
        cell = self.sheets[uid][1][(row, col)]
        cell.value, cell.state = value, state

    def defined_name(self, name, uid):
        return self.names.get(name.upper())

    def used_size(self, uid):
        keys = self.sheets[uid][1]
        return (max([k[0] for k in keys], default=-1) + 1,
                max([k[1] for k in keys], default=-1) + 1)

    def put(self, ref, value=None, formula=None, uid=1):
        row, col = sf.parse_cell_name(ref)
        self.sheets[uid][1][(row, col)] = _Cell(value, formula)

    def get(self, ref, uid=1):
        row, col = sf.parse_cell_name(ref)
        return self.sheets[uid][1][(row, col)]


@pytest.fixture
def book():
    book = _Book()
    for ref, value in (("A1", 1), ("A2", 2), ("A3", 3), ("B1", "x"), ("B2", "y"),
                       ("B3", "z")):
        book.put(ref, value)
    book.put("A1", 10, uid=2)
    return book


def calc(book, formula):
    engine = sf.Engine(book)
    engine.rebuild()
    value, _state = engine.evaluate(formula, 1, 20, 20)
    return value


@pytest.mark.parametrize("formula, expected", [
    ("=1+2*3", 7),
    ("=(1+2)*3", 9),
    ("=-2^2", 4),
    ("=2^3^2", 64),
    ("=50%", 0.5),
    ("=10/4", 2.5),
    ('="a"&"b"&1', "ab1"),
    ("=1=1", True),
    ('="A"="a"', True),
    ('=1<"a"', True),
    ("=TRUE+1", 2),
    ("=A1+A2+A3", 6),
    ("=$A$1+A$2+$A3", 6),
    ("='My Sheet'!A1*2", 20),
    ("=SUM(A1:A3)", 6),
    ("=SUM(A:A)", 6),
    ("=SUM(1:1)", 1),
    ("=AVERAGE(A1:A3)", 2),
    ("=MIN(A1:A3,0)", 0),
    ("=MAX(A1:A3)", 3),
    ('=COUNT(A1:B3,1,"2","x")', 5),
    ("=COUNTA(A1:B3)", 6),
    ("=COUNTBLANK(A1:A5)", 2),
    ('=COUNTIF(A1:A3,">1")', 2),
    ('=COUNTIF(B1:B3,"?")', 3),
    ('=SUMIF(A1:A3,"<>2")', 4),
    ('=SUMIF(B1:B3,"y",A1:A3)', 2),
    ('=AVERAGEIF(A1:A3,">=2")', 2.5),
    ("=PRODUCT(A1:A3,2)", 12),
    ('=IF(A1>0,"yes","no")', "yes"),
    ("=IF(A1>5,1)", False),
    ('=IFERROR(1/0,"none")', "none"),
    ("=AND(TRUE,A1)", True),
    ("=OR(FALSE,0)", False),
    ("=NOT(A1)", False),
    ("=ROUND(2.5,0)", 3),
    ("=ROUND(-2.5,0)", -3),
    ("=ROUND(1.005,2)", 1.01),
    ("=ROUNDUP(1.21,1)", 1.3),
    ("=ROUNDDOWN(-1.29,1)", -1.2),
    ("=INT(-1.5)", -2),
    ("=ABS(-3)", 3),
    ("=MOD(-3,2)", 1),
    ("=POWER(2,10)", 1024),
    ("=SQRT(16)", 4),
    ('=CONCAT(B1:B3,"!")', "xyz!"),
    ('=CONCATENATE("a",1,TRUE)', "a1TRUE"),
    ('=TEXTJOIN(", ",TRUE,B1:B3,"")', "x, y, z"),
    ('=LEN("hello")', 5),
    ('=LEFT("hello",2)', "he"),
    ('=RIGHT("hello",3)', "llo"),
    ('=MID("hello",2,3)', "ell"),
    ('=UPPER("abc")', "ABC"),
    ('=LOWER("ABC")', "abc"),
    ('=TRIM("  a   b  ")', "a b"),
    ('=SUBSTITUTE("a-b-c","-","+")', "a+b+c"),
    ('=SUBSTITUTE("a-b-c","-","+",2)', "a-b+c"),
    ('=VALUE("1,234.5")', 1234.5),
    ("=DATE(2024,1,15)", 45306),
    ("=DATE(2024,14,1)", 45689),
    ("=YEAR(45306)", 2024),
    ("=MONTH(45306)", 1),
    ("=DAY(45306)", 15),
    ("=VLOOKUP(2,A1:B3,2,FALSE)", "y"),
    ("=VLOOKUP(2.5,A1:B3,2)", "y"),
    ('=HLOOKUP("y",B2:B3,2,FALSE)', "z"),
    ("=INDEX(A1:B3,3,2)", "z"),
    ("=INDEX(A1:A3,2)", 2),
    ("=MATCH(3,A1:A3,0)", 3),
    ('=MATCH("Y",B1:B3,0)', 2),
    ("=MATCH(2.5,A1:A3)", 2),
    ("=SUM(A1:A3)/COUNT(A1:A3)", 2),
    ('=1/3&""', "0.333333333333333"),
])
def test_a_formula_is_calculated_as_excel_calculates_it(book, formula, expected):
    value = calc(book, formula)
    if isinstance(expected, float):
        assert value == pytest.approx(expected)
    else:
        assert value == expected and type(value) is type(expected)


@pytest.mark.parametrize("formula, error", [
    ("=1/0", sf.DIV0),
    ('="a"+1', sf.VALUE),
    ("=SQRT(-1)", sf.NUM),
    ("=0^0", sf.NUM),
    ("=VLOOKUP(9,A1:B3,2,FALSE)", sf.NA),
    ("=VLOOKUP(1,A1:B3,3,FALSE)", sf.REF),
    ("=INDEX(A1:A3,5)", sf.REF),
    ("=Missing!A1", sf.REF),
    ("=#REF!+1", sf.REF),
    ("=AVERAGE(B1:B3)", sf.DIV0),
    ('=MID("a",0,1)', sf.VALUE),
    ("=A1:A3+B5:C6", sf.VALUE),
    ("=SUM(1,1/0)", sf.DIV0),
])
def test_errors_follow_excel(book, formula, error):
    assert calc(book, formula) == error


def test_an_error_in_a_cell_travels_to_the_formulas_that_read_it(book):
    book.put("C1", sf.NA)
    assert calc(book, "=C1+1") == sf.NA
    assert calc(book, "=SUM(A1:C1)") == sf.NA
    assert calc(book, '=IFERROR(C1,"ok")') == "ok"


def test_text_that_cannot_be_read_is_a_name_error(book):
    assert calc(book, "=1+") == sf.NAME
    assert sf.formula_error("=SUM(1,") is not None
    assert sf.formula_error("=SUM(1,2)") is None


def test_dates_in_cells_count_as_their_serial_numbers(book):
    book.put("D1", dt.date(2024, 1, 15))
    book.put("D2", dt.datetime(2024, 1, 16, 12, 0))
    assert calc(book, "=D2-D1") == pytest.approx(1.5)
    assert calc(book, "=YEAR(D1)") == 2024
    assert sf.to_serial(dt.date(1900, 3, 1)) == 61
    assert sf.from_serial(45306) == dt.datetime(2024, 1, 15)


def test_an_unknown_function_keeps_the_result_the_app_stored(book):
    book.put("C1", 42, "=FORECAST.ETS(A1,A2:A3,A2:A3)")
    book.get("C1").state = sf.STATE_APP
    book.put("C2", None, "=C1+1")
    engine = sf.Engine(book)
    engine.rebuild()
    # As a file is opened: only the formula without a stored result.
    engine.calculate_all([(1, 1, 2)], follow=False)
    assert book.get("C1").value == 42
    assert book.get("C1").state == sf.STATE_APP
    assert book.get("C2").value == 43


def test_once_its_inputs_change_only_the_app_knows_such_a_result(book):
    book.put("C1", 42, "=FORECAST.ETS(A1,A2:A3,A2:A3)")
    book.get("C1").state = sf.STATE_APP
    book.put("C2", 43, "=C1+1")
    book.put("C3", 7, "=A2*7")
    engine = sf.Engine(book)
    engine.rebuild()
    book.get("A1").value = 5
    engine.recalculate([(1, 0, 0)])
    # Not the stale 42 and 43: no result, which the app works out on opening.
    assert book.get("C1").value is None and book.get("C1").state == sf.STATE_STALE
    assert book.get("C2").value is None and book.get("C2").state == sf.STATE_STALE
    assert book.get("C3").value == 7 and book.get("C3").state == ""


def test_array_constants_and_other_workbooks_are_left_to_the_app(book):
    engine = sf.Engine(book)
    assert engine.evaluate("=SUM({1,2,3})", 1, 0, 9)[1] == sf.STATE_APP
    assert engine.evaluate("=[1]Sheet1!A1", 1, 0, 9)[1] == sf.STATE_APP


def test_a_circular_reference_shows_an_error_and_never_hangs(book):
    book.put("C1", None, "=C2+1")
    book.put("C2", None, "=C1+1")
    book.put("C3", None, "=C3")
    book.put("C4", None, "=C1*2")
    engine = sf.Engine(book)
    engine.rebuild()
    engine.calculate_all()
    for ref in ("C1", "C2", "C3"):
        assert book.get(ref).value == sf.CIRCULAR
        assert book.get(ref).state == sf.STATE_CIRCULAR
    assert book.get("C4").value == sf.CIRCULAR


def test_an_edit_recalculates_only_what_depends_on_the_edited_cell(book):
    book.put("C1", None, "=A1*2")
    book.put("C2", None, "=C1+1")
    book.put("C3", None, "=SUM(A2:A3)")
    book.put("C4", None, "='My Sheet'!A1")
    engine = sf.Engine(book)
    engine.rebuild()
    engine.calculate_all()
    book.get("A1").value = 5
    recalculated = engine.recalculate([(1, 0, 0)])
    assert recalculated == {(1, 0, 2), (1, 1, 2)}
    assert book.get("C2").value == 11
    book.get("A1", uid=2).value = 7
    assert engine.recalculate([(2, 0, 0)]) == {(1, 3, 2)}
    assert book.get("C4").value == 7


def test_a_long_chain_of_formulas_is_calculated_without_running_out_of_stack(book):
    book.put("E1", 1)
    for row in range(2, 5001):
        book.put(f"E{row}", None, f"=E{row - 1}+1")
    engine = sf.Engine(book)
    engine.rebuild()
    engine.calculate_all()
    assert book.get("E5000").value == 5000
    book.get("E1").value = 10
    engine.recalculate([(1, 0, 4)])
    assert book.get("E5000").value == 5009


def test_today_is_calculated_again_on_every_edit(book):
    book.put("C1", 1, "=TODAY()")
    engine = sf.Engine(book)
    engine.rebuild()
    engine.recalculate([(1, 0, 0)])
    assert book.get("C1").value == sf.to_serial(dt.date.today())


def test_a_defined_name_reads_its_cells(book):
    book.names["TOTALS"] = "=Sheet1!$A$1:$A$3"
    assert calc(book, "=SUM(Totals)") == 6
    # A name the engine cannot find may live where it does not look (a
    # constant array, another workbook): the app's result is kept.
    assert sf.Engine(book).evaluate("=Unknown+1", 1, 0, 9)[1] == sf.STATE_APP


@pytest.mark.parametrize("formula, at, count, expected", [
    ("=SUM(A1:A3)+B5", 1, 2, "=SUM(A1:A5)+B7"),
    ("=A1+$A$4", 0, 1, "=A2+$A$5"),
    ("=SUM(A1:A3)+B2", 1, -1, "=SUM(A1:A2)+#REF!"),
    ("=SUM(A2:A3)", 1, -2, "=SUM(#REF!)"),
    ("=SUM(A1:A5)", 1, -2, "=SUM(A1:A3)"),
    ("=SUM(A:A)", 0, 3, "=SUM(A:A)"),
    ("=Other!A5+A5", 0, 1, "=Other!A5+A6"),
    ("='My Sheet'!A5", 0, 1, "='My Sheet'!A5"),
])
def test_references_follow_inserted_and_deleted_rows(formula, at, count, expected):
    assert sf.adjust_for_insert_delete(formula, home="Sheet1", target="Sheet1", axis="row",
                                       at=at, count=count) == expected


def test_references_from_another_sheet_follow_too():
    formula = "=SUM('My Sheet'!B1:B4)+'My Sheet'!C2+A2"
    assert sf.adjust_for_insert_delete(formula, home="Sheet1", target="My Sheet", axis="col",
                                       at=1, count=1) == \
        "=SUM('My Sheet'!C1:C4)+'My Sheet'!D2+A2"
    assert sf.adjust_for_insert_delete(formula, home="Sheet1", target="my sheet", axis="col",
                                       at=2, count=-1) == \
        "=SUM('My Sheet'!B1:B4)+'My Sheet'!#REF!+A2"


def test_the_rest_of_a_formula_keeps_its_spelling_when_references_move():
    formula = '=IF( a1 > 0 , "A1 stays" , sum(b1:b3) )'
    assert sf.adjust_for_insert_delete(formula, home="S", target="S", axis="row", at=0,
                                       count=1) == '=IF( A2 > 0 , "A1 stays" , sum(B2:B4) )'


def test_a_pasted_formula_moves_its_relative_references():
    assert sf.shift_formula("=A1+$A$1+A$1+$A1", 2, 1) == "=B3+$A$1+B$1+$A3"
    assert sf.shift_formula("=SUM(A:A)", 0, 2) == "=SUM(C:C)"
    assert sf.shift_formula("=A1", -1, 0) == "=#REF!"


def test_renaming_or_deleting_a_sheet_updates_formulas():
    assert sf.rename_sheet_in_formula("=Data!A1+data!B1:B2+A1", "Data", "Q1 Data") == \
        "='Q1 Data'!A1+'Q1 Data'!B1:B2+A1"
    assert sf.break_sheet_in_formula("=Data!A1+A1", "Data") == "=#REF!+A1"


@pytest.mark.parametrize("excel, openformula", [
    ("=SUM(A1:A3)", "of:=SUM([.A1:.A3])"),
    ("=Sheet2!A1*2", "of:=[$Sheet2.A1]*2"),
    ("=SUM('My Sheet'!$A$1:B2,3)", "of:=SUM([$'My Sheet'.$A$1:.B2];3)"),
    ('=IF(A1="a;b",TRUE,FALSE)', 'of:=IF([.A1]="a;b";TRUE();FALSE())'),
    ("=CONCAT(A1:A2)", "of:=COM.MICROSOFT.CONCAT([.A1:.A2])"),
])
def test_openformula_and_excel_translate_both_ways(excel, openformula):
    assert sf.to_openformula(excel) == openformula
    assert sf.from_openformula(openformula) == excel


def test_openformula_as_libreoffice_writes_it_is_read():
    assert sf.from_openformula("of:=[$Sheet2.A1:Sheet2.B3]") == "=Sheet2!A1:B3"
    assert sf.from_openformula("of:=[.#REF!]+1") == "=#REF!+1"
    assert sf.from_openformula("msoxl:=SUM(A1,B2)") == "=SUM(A1,B2)"


def test_cell_names_read_and_write():
    assert sf.parse_cell_name("C12") == (11, 2)
    assert sf.parse_cell_name("$AA$1") == (0, 26)
    assert sf.parse_cell_name("XFE1") is None
    assert sf.cell_name(0, 0) == "A1"
    assert sf.column_letters(16383) == "XFD"
    assert sf.quote_sheet_name("Plain") == "Plain"
    assert sf.quote_sheet_name("It's mine") == "'It''s mine'"
    assert sf.quote_sheet_name("A1") == "'A1'"


def test_numbers_become_text_as_a_formula_writes_them():
    assert sf.number_text(0.1 + 0.2) == "0.3"
    assert sf.number_text(1e21) == "1E+21"
    assert sf.number_text(5.0) == "5"


# --------------------------------------------------------------------------- #
# Formulas in the app's language                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("canonical, local", sorted(formula_names.FUNCTION_NAMES["de"].items()))
def test_every_german_function_name_translates_both_ways(canonical, local):
    formula = f"={canonical}(A1,B2)"
    shown = sf.localize_formula(formula, "de")
    assert shown == f"={local}(A1;B2)"
    assert sf.delocalize_formula(shown, "de") == formula
    assert sf.delocalize_formula(f"={local.lower()}(A1;B2)", "de") == formula


@pytest.mark.parametrize("canonical, local", [
    ("=ROUND(A1*1.19,2)", "=RUNDEN(A1*1,19;2)"),
    ('=IF(A1="1,5;x","TRUE",FALSE)', '=WENN(A1="1,5;x";"TRUE";FALSCH)'),
    ("='Übersicht 1'!A1+Übersicht!B2*2.5", "='Übersicht 1'!A1+Übersicht!B2*2,5"),
    ("=SUM($A$1:A3,1E+3,TRUE)", "=SUMME($A$1:A3;1E+3;WAHR)"),
    ("=VLOOKUP(\"Größe\",'Läden'!A:B,2,FALSE)", "=SVERWEIS(\"Größe\";'Läden'!A:B;2;FALSCH)"),
    ("=UNKNOWNFN(1.5)", "=UNKNOWNFN(1,5)"),
])
def test_german_formulas_keep_quotes_sheets_and_numbers(canonical, local):
    assert sf.localize_formula(canonical, "de") == local
    assert sf.delocalize_formula(local, "de") == canonical


def test_english_is_understood_in_any_language():
    assert sf.delocalize_formula("=SUM(A1,B1)+summe(1;2)", "de") == "=SUM(A1,B1)+SUM(1,2)"
    assert sf.delocalize_formula("=TEXTKETTE(A1;A2)", "de") == "=CONCAT(A1,A2)"
    assert sf.localize_formula("=SUM(A1,1.5)", "en") == "=SUM(A1,1.5)"
    assert sf.localize_formula("=SUM(A1,1.5)", "fr") == "=SUM(A1,1.5)"


def test_newer_functions_get_excels_prefix_in_files_and_lose_it_on_screen():
    assert sf.localize_formula("=_xlfn.CONCAT(A1:A3)", "en") == "=CONCAT(A1:A3)"
    assert sf.localize_formula("=_xlfn.CONCAT(A1:A3)", "de") == "=KONKATENIEREN(A1:A3)"
    assert sf.to_file_formula("=CONCAT(A1)&textjoin(\",\",TRUE,A1)") == \
        "=_xlfn.CONCAT(A1)&_xlfn.TEXTJOIN(\",\",TRUE,A1)"
    assert sf.to_file_formula("=_xlfn.CONCAT(A1)") == "=_xlfn.CONCAT(A1)"
    assert sf.localize_formula("=_xlfn.FORECAST.ETS(A1)", "de") == "=FORECAST.ETS(A1)"


def test_an_array_constant_is_shown_as_the_file_has_it():
    assert sf.localize_formula("=SUM({1,2;3,4})", "de") == "=SUM({1,2;3,4})"


# --------------------------------------------------------------------------- #
# References of every kind                                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("formula, expected", [
    # Another workbook's cells are never moved by a change in this one.
    ("=[1]Sheet1!A1+A1", "=[1]Sheet1!A1+A2"),
    ("='C:\\x\\[Book.xlsx]Data'!A1+A1", "='C:\\x\\[Book.xlsx]Data'!A1+A2"),
    ("=[1]!Total+A1", "=[1]!Total+A2"),
    # A table's structured reference stays; the cells around it move.
    ("=Table1[[#This Row],[Price]]*$H$1", "=Table1[[#This Row],[Price]]*$H$2"),
    ("=SUM(Table1[Amount])+B5", "=SUM(Table1[Amount])+B6"),
    ("=[@Price]*A1", "=[@Price]*A2"),
    ("=Table1[Col'[x']]+A1", "=Table1[Col'[x']]+A2"),
    # A reference to several sheets is not moved by one sheet's rows.
    ("=SUM(Sheet1:Sheet3!A5)+A5", "=SUM(Sheet1:Sheet3!A5)+A6"),
    # The intersection of two blocks moves with both.
    ("=SUM(A1:C3 B1:B5)", "=SUM(A2:C4 B2:B6)"),
])
def test_only_this_workbooks_cells_move_when_a_row_is_inserted(formula, expected):
    assert sf.adjust_for_insert_delete(formula, home="Sheet1", target="Sheet1", axis="row",
                                       at=0, count=1) == expected


def test_a_formula_with_a_table_reference_follows_a_renamed_sheet():
    assert sf.rename_sheet_in_formula("=SUM(Table1[Amount])*Rates!A1", "Rates", "Tax rates") == \
        "=SUM(Table1[Amount])*'Tax rates'!A1"


def test_references_to_several_sheets_follow_renames_and_deletions():
    order = ["Jan", "Feb", "Mar"]
    assert sf.rename_sheet_in_formula("=SUM(Jan:Mar!A5)", "Mar", "Q4") == "=SUM('Jan:Q4'!A5)"
    assert sf.rename_sheet_in_formula("=SUM(Jan:Mar!A5)", "Jan", "January") == \
        "=SUM(January:Mar!A5)"
    assert sf.break_sheet_in_formula("=SUM(Jan:Mar!A5)", "Mar", order) == "=SUM(Jan:Feb!A5)"
    assert sf.break_sheet_in_formula("=SUM(Jan:Feb!A5)", "Jan", order) == "=SUM(Feb!A5)"
    assert sf.break_sheet_in_formula("=Feb!A5+SUM(Jan:Mar!A1)", "Feb", order) == \
        "=#REF!+SUM(Jan:Mar!A1)"
    assert sf.rename_sheet_in_formula("=[1]Mar!A1+Mar!A1", "Mar", "M") == "=[1]Mar!A1+M!A1"


@pytest.mark.parametrize("name", ["R1C1", "RC", "R5", "C3", "r2c3", "Q4", "TRUE", "1st", "A b"])
def test_sheet_names_that_read_as_something_else_are_quoted(name):
    assert sf.quote_sheet_name(name).startswith("'")
    assert sf.rename_sheet_in_formula("=Data!A1", "Data", name) == \
        "=" + sf.quote_sheet_name(name) + "!A1"


@pytest.mark.parametrize("openformula", [
    "of:=INDEX({1;2|3;4};2;1)",
    "of:=SUM([.A1:.A2]~[.C1:.C2])",
    "of:=SUM([.A1:.C3]![.B1:.B5])",
    "of:=SUM([$Sheet1.A5:$Sheet3.A5])",
    "of:=['file:///C:/x/Book.ods'#$Data.A1]*2",
    "of:=[#REF!]",
    "of:=SUM(Table1[[#This Row];[Price]])",
])
def test_openformula_survives_the_way_through_excels_spelling(openformula):
    assert sf.to_openformula(sf.from_openformula(openformula)) == openformula


def test_what_only_the_app_calculates_keeps_its_stored_result():
    for formula in ("=SUM(Table1[Amount])", "=SUM(Jan:Mar!A1)", "=[1]Sheet1!A1",
                    "=SUM(A1:C3 B1:B5)", "=SUM(A1:A2~C1:C2)"):
        assert sf.needs_app(formula), formula


def test_a_german_number_without_its_leading_zero_is_read():
    assert sf.delocalize_formula("=,5+A1", "de") == "=.5+A1"
    assert sf.delocalize_formula("=SUMME(A1,5)", "de") == "=SUM(A1,5)"


def test_excel_files_get_the_spelling_excel_reads():
    assert sf.to_file_formula("=SUM(A1:A2~C1:C2~E1)+concat(A1)") == \
        "=SUM((A1:A2,C1:C2,E1))+_xlfn.CONCAT(A1)"
    assert sf.to_file_formula("=SORT(A1:A3)") == "=_xlfn._xlws.SORT(A1:A3)"
    assert sf.localize_formula("=_xlfn._xlws.SORT(A1)", "en") == "=SORT(A1)"
    assert sf.uses_spilling_function("=1+_xlfn._xlws.FILTER(A1:A3,B1:B3)") == "FILTER"
    assert sf.uses_spilling_function("=SUM(A1:A3)") is None


@pytest.mark.parametrize("formula, expected", [
    ("=0.1+0.2=0.3", True),
    ("=0.1+0.2<0.3", False),
    ("=1.1-1=0.1", True),
    ('=YEAR("2026-03-04")', 2026),
    ('=MONTH("2026-03-04 14:30")', 3),
    ('="2026-03-04"+1', 46086),
    ('=DAY("2026-03-04")', 4),
    ('="14:30"*24', 14.5),
    ('=YEAR("not a date")', sf.VALUE),
])
def test_numbers_compare_at_excels_precision_and_date_text_counts_as_a_date(book, formula,
                                                                           expected):
    result = calc(book, formula)
    if isinstance(expected, float):
        assert result == pytest.approx(expected)
    else:
        assert result == expected


def test_results_the_same_to_15_digits_are_the_same():
    assert sf.same_value(0.226056110931957, 0.22605611093195743)
    assert not sf.same_value(0.2260561109319, 0.2260561109320)
    assert not sf.same_value(1, True)
    assert sf.same_value("a", "a") and not sf.same_value("a", "A")
