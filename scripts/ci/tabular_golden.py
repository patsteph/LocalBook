"""Write the tabular-text golden fixture the Swift port is tested against.

The old app turns a spreadsheet, CSV or slide table into sentences (``DocumentProcessor._dataframe_to_sentences``
over a pandas frame) before chunking. ``TabularText`` in LocalBookCore does the same from a parsed table; this script
records what the Python code makes of a set of CSV texts (pandas' reading included: empty headers become
``Unnamed: N``, duplicates get ``.1``, a numeric column with blanks prints as floats). Run from ``backend/``:

    python ../scripts/ci/tabular_golden.py ../swift/LocalBookCore/Tests/LocalBookCoreTests/Fixtures/tabular_golden.json
"""
import io
import json
import sys

import pandas as pd

sys.path.insert(0, ".")
from services.document_processor import DocumentProcessor  # noqa: E402

dp = DocumentProcessor()
cases = []


def case(name, csv, sheet="CSV Data"):
    df = pd.read_csv(io.StringIO(csv))
    cases.append({"name": name, "csv": csv, "sheet": sheet, "sentences": dp._dataframe_to_sentences(df, sheet)})


case("plain rows", "Name,Score,Team\nAnn,7,Red\nBob,12,Blue\nCy,3,Red\n")
case("blanks make floats", "Name,Score,Team\nAnn,7,Red\nBob,,Blue\nCy,3,\n")
case("empty and duplicate headers", "Name,Score,Team,,Score\nAnn,7,Red,,1\nBob,,Blue,x,2\n")
case("pivot by quarter", "Person,Activity,Q1 FY2025,Q2 FY2025,Total\nAnn,Calls,7,9,16\n,Visits,2,0,2\nBob,Calls,4,5,9\n,Visits,,1,1\n")
case("quoted fields", 'Title,Notes,Count\n"Smith, J","Said ""hello"", left",2\n"Lee","Line one\nline two",5\n')
case("decimals and negatives", "Item,Delta,Ratio\nA,-3,0.5\nB,4,1.25\nC,0,2\n")
case("numbers with leading zeros", "Code,Qty\n007,10\n042,20\n")
case("one header only", "A,B,C\n")
case("mixed text and numbers", "Key,Value\nx,1\ny,two\nz,3.5\n")

with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump({"pandas": pd.__version__, "cases": cases}, f, indent=1, ensure_ascii=False)
    f.write("\n")
