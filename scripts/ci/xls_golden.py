"""Write the legacy-Excel golden fixture the Swift BIFF8 reader is tested against.

Each case is an .xls written by xlwt (base64 in the fixture) and the text the old app made of it
(``DocumentProcessor._extract_from_excel(content, "xls")``: pandas over xlrd, then sentences). Run from ``backend/``:

    python ../scripts/ci/xls_golden.py ../swift/LocalBookCore/Tests/LocalBookCoreTests/Fixtures/xls_golden.json
"""
import asyncio
import base64
import datetime as dt
import io
import json
import sys

import xlwt

sys.path.insert(0, ".")
from services.document_processor import DocumentProcessor  # noqa: E402

dp = DocumentProcessor()
cases = []


def case(name, build):
    wb = xlwt.Workbook(encoding="utf-8")
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()
    text = asyncio.run(dp._extract_from_excel(content, "xls"))
    cases.append({"name": name, "xls": base64.b64encode(content).decode("ascii"), "text": text})


def plain(wb):
    ws = wb.add_sheet("People")
    for c, v in enumerate(["Name", "Score", "Team"]):
        ws.write(0, c, v)
    for r, row in enumerate([["Ann", 7, "Red"], ["Bob", 12, "Blue"], ["Cy", 3, "Red"]], 1):
        for c, v in enumerate(row):
            ws.write(r, c, v)


def two_sheets_blanks(wb):
    ws = wb.add_sheet("Scores")
    for c, v in enumerate(["Name", "Score", "Team"]):
        ws.write(0, c, v)
    ws.write(1, 0, "Ann"); ws.write(1, 1, 7); ws.write(1, 2, "Red")
    ws.write(2, 0, "Bob"); ws.write(2, 2, "Blue")
    ws.write(3, 0, "Cy"); ws.write(3, 1, 3)
    ws2 = wb.add_sheet("Notes")
    ws2.write(0, 0, "Key"); ws2.write(0, 1, "Value")
    ws2.write(1, 0, "x"); ws2.write(1, 1, 1.5)
    ws2.write(2, 0, "y"); ws2.write(2, 1, "two")


def pivot(wb):
    ws = wb.add_sheet("Pivot")
    for c, v in enumerate(["Person", "Activity", "Q1 FY2025", "Q2 FY2025", "Total"]):
        ws.write(0, c, v)
    rows = [["Ann", "Calls", 7, 9, 16], ["", "Visits", 2, 0, 2], ["Bob", "Calls", 4, 5, 9], ["", "Visits", None, 1, 1]]
    for r, row in enumerate(rows, 1):
        for c, v in enumerate(row):
            if v is None or v == "":
                continue
            ws.write(r, c, v)


def dates_bools_formulas(wb):
    ws = wb.add_sheet("Mixed")
    for c, v in enumerate(["Item", "When", "Done", "Sum", "Big"]):
        ws.write(0, c, v)
    date_style = xlwt.easyxf(num_format_str="YYYY-MM-DD")
    ws.write(1, 0, "A"); ws.write(1, 1, dt.datetime(2024, 3, 5), date_style); ws.write(1, 2, True); ws.write(1, 3, xlwt.Formula("1+2")); ws.write(1, 4, 123456789012.0)
    ws.write(2, 0, "B"); ws.write(2, 1, dt.datetime(2025, 12, 31, 13, 45), date_style); ws.write(2, 2, False); ws.write(2, 3, xlwt.Formula("2*21")); ws.write(2, 4, -0.5)


def long_strings_unicode(wb):
    ws = wb.add_sheet("Text")
    ws.write(0, 0, "Title"); ws.write(0, 1, "Body")
    ws.write(1, 0, "Café"); ws.write(1, 1, "日本語 and emoji 😀 " + "x" * 300)
    ws.write(2, 0, "Plain"); ws.write(2, 1, "a" * 5000)


def many_strings(wb):
    # Enough distinct strings to spill the shared string table into CONTINUE records.
    ws = wb.add_sheet("Many")
    ws.write(0, 0, "Index"); ws.write(0, 1, "Label")
    for r in range(1, 1200):
        ws.write(r, 0, r)
        ws.write(r, 1, "label number %d with some padding text" % r)


def empty_header_cells(wb):
    ws = wb.add_sheet("Gaps")
    ws.write(0, 0, "Name"); ws.write(0, 2, "Team")
    ws.write(1, 0, "Ann"); ws.write(1, 1, 7); ws.write(1, 2, "Red")
    ws.write(2, 0, "Bob"); ws.write(2, 1, 12); ws.write(2, 2, "Blue")


case("plain rows", plain)
case("two sheets with blanks", two_sheets_blanks)
case("pivot by quarter", pivot)
case("dates, booleans, formulas", dates_bools_formulas)
case("long strings and unicode", long_strings_unicode)
case("many strings", many_strings)
case("empty header cells", empty_header_cells)

with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({"cases": cases}, f, indent=1, ensure_ascii=False)
    f.write("\n")
