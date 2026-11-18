# Phase 3 test files

One made-up company, Harbor Lights Coffee, with the same facts in every format, so each check in the testing plan has a
question with a known answer. Made by a script (seeded, so the files never change); nothing here is real data.

## `notebook/` — drop these on one new notebook ("Harbor Lights")

| File | What it is | Exercises |
|---|---|---|
| `harbor-lights-handbook.docx` | 7-section handbook: headings, prose, a 3-column table, a bullet list | Word reader, headings in chunks |
| `handbook-text.pdf` | the handbook as a 3-page text PDF | PDF reader, `=== Page N ===`, Quick Look |
| `pricing-and-accounts-tables.pdf` | the handbook plus two **ruled tables** on page 3, prose-only page 4 | PDF ruled tables (slice 11): Markdown tables after the page text, cell words not repeated above |
| `scanned-memo.pdf` | 2 pages of **pictures only**, no text layer (a memo from Rosa Alvarez about night-shift sleep consultations) | Vision OCR path for scanned PDFs |
| `quarterly-review.pptx` | 6 slides with bullets and speaker notes | PowerPoint reader, `=== Slide N ===` |
| `accounts.xlsx` | sheet **Accounts**: 240 rows (Account, State, Plan, ARR, Seats, Renewal Date, Active); sheet **Regions**: 5 rows | spreadsheet reader + the SQL question path (slice 14) |
| `accounts-analysis.ipynb` | 2 Markdown + 2 code cells with outputs | Jupyter reader |
| `harbor-lights-handbook.epub` | the handbook as an EPUB (pandoc) | EPUB reader, `#` chapter headings in spine order |
| `logo.svg` | a logo with two text elements | SVG reader |
| `shipping-policy.json`, `roast-schedule.yaml` | small structured text | text-type drops become notes |
| `whiteboard-roadmap.png` | a picture with four lines of text | picture drop → note with OCR text |

## `formats/` — the same content in other formats; use as the **watched folder** (second notebook, "Formats")

`accounts.csv` (CSV path into SQL), `accounts.xls` (legacy BIFF8, first 200 rows only, by design), `accounts.ods`,
`harbor-lights-handbook.odt` / `.rtf` / `.html` / `.md` / `.txt`, `quarterly-review.ppt` (legacy). Keeping these in a
separate notebook keeps the SQL path in "Harbor Lights" to one table, so the counts below are unambiguous.

## Questions with known answers

Prose (should cite the handbook, the memo or the slides):

| Ask | Answer |
|---|---|
| Who founded Harbor Lights Coffee, and where? | Rosa Alvarez, Portland, Maine, 2014 (roastery moved to Austin, Texas in 2019) |
| What credit does a late shipment get? | 5 percent of the order; late = more than two days after the promised date |
| Who is the regional manager for Colorado? | Priya Raman (Mountain region) |
| When is the caffeine cut-off on the night shift? | 2 am (scanned memo and handbook) |
| How many employees does the company have? | 63 (the table in the handbook and the PDF) |

Spreadsheet (should be computed by SQL over `accounts.xlsx`, a bold number, the SELECT in the status line):

| Ask | Answer |
|---|---|
| How many accounts are in Texas? | **103** |
| Total ARR by state | Texas 772,200 · Ohio 473,300 · Colorado 214,800 · Maine 188,200 · Oregon 130,800 |
| How many Enterprise accounts are there? | **30** |
| How many accounts renew in 2026? | **126** |
| Which account has the largest ARR? | Pine Roasters |
| How many accounts are inactive? | **24** |

`expected-answers.json` holds the same numbers. In the "Formats" notebook, `accounts.xls` has 200 rows, so its counts differ on purpose.

## Also here

`web-diff-urls.txt`: the 30 addresses for the web-page diff (part B of the plan). `../../scripts/ingestion_diff_export.py`
run on `notebook/` (and, if there is time, `formats/`) writes the ingestion fixture for the gate's diff; it skips the
picture, as it should.
