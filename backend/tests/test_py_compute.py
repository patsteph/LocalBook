"""CI-pure tests for the py_compute sandbox (P0 of the Python hard-compute tier).

Each `run()` spawns a real isolated child process (multiprocessing 'spawn'), so these verify the
actual isolation boundary: stdout capture, artifact emit, exception containment, wall-clock timeout,
network block, and resource-limit fallback. No model, no network, no app data.
"""
from services import py_compute as pc
from services.py_compute import SandboxLimits


def test_stdout_captured():
    res = pc.run("print('hello'); print('world')")
    assert res["ok"] is True
    assert "hello" in res["stdout"] and "world" in res["stdout"]
    assert res["artifact"] is None and res["error"] is None


def test_emit_artifact_dict():
    code = "emit({'id': 'a1', 'type': 'markdown', 'payload': 'the answer is 42'})"
    res = pc.run(code)
    assert res["ok"] is True
    assert res["artifact"] == {"id": "a1", "type": "markdown", "payload": "the answer is 42"}


def test_emit_accepts_object_with_model_dump():
    # emit() accepts anything with .model_dump() (e.g. a pydantic Artifact) and serializes it.
    code = (
        "class A:\n"
        "    def model_dump(self):\n"
        "        return {'id': 'm', 'type': 'markdown', 'payload': 'hi', 'title': 'T'}\n"
        "emit(A())"
    )
    res = pc.run(code)
    assert res["ok"] is True
    assert res["artifact"]["type"] == "markdown" and res["artifact"]["payload"] == "hi"
    assert res["artifact"]["title"] == "T"


def test_exception_is_contained():
    res = pc.run("x = 1 / 0")
    assert res["ok"] is False
    assert "ZeroDivisionError" in (res["error"] or "")


def test_computation_then_emit():
    # The intended shape: compute in Python, then emit a chart payload via the raw emit() escape
    # hatch. Payload must be a VALID ChartConfig — P1 validates json:chart in the parent.
    code = (
        "vals = [i * i for i in range(5)]\n"
        "print('sum', sum(vals))\n"
        "emit({'id': 'c', 'type': 'json:chart', 'payload': {\n"
        "    'chart_type': 'line', 'series': [{'key': 'y'}],\n"
        "    'data': [{'x': i, 'y': v} for i, v in enumerate(vals)]}})"
    )
    res = pc.run(code)
    assert res["ok"] is True, res["error"]
    assert [d["y"] for d in res["artifact"]["payload"]["data"]] == [0, 1, 4, 9, 16]
    assert "sum 30" in res["stdout"]


def test_timeout_kills_infinite_loop():
    # Busy loop: RLIMIT_CPU (1s) or the wall-clock backstop (1.5s) terminates it; either way ok False.
    res = pc.run("while True:\n    pass", limits=SandboxLimits(timeout_s=1.5, cpu_s=1))
    assert res["ok"] is False
    assert res["error"]  # "timeout ..." or "sandbox died ..."


def test_network_is_blocked():
    # A socket can be constructed (ssl subclasses it) but must never connect out.
    code = (
        "import socket\n"
        "try:\n"
        "    s = socket.socket()\n"
        "    s.connect(('1.1.1.1', 80))\n"
        "    print('CONNECTED')\n"
        "except Exception:\n"
        "    print('BLOCKED')"
    )
    res = pc.run(code)
    assert res["ok"] is True
    assert "BLOCKED" in res["stdout"] and "CONNECTED" not in res["stdout"]


def test_data_files_exposed_readonly_convention():
    res = pc.run("print(DATA_FILES.get('db'))", data_files={"db": "/some/path.db"})
    assert res["ok"] is True
    assert "/some/path.db" in res["stdout"]


def test_empty_code_rejected():
    res = pc.run("   ")
    assert res["ok"] is False and res["error"] == "empty code"


def test_oversized_artifact_rejected():
    # Emit an artifact larger than the 2MB cap → ok flips False with a clear error.
    code = "emit({'id': 'big', 'type': 'markdown', 'payload': 'x' * 3_000_000})"
    res = pc.run(code)
    assert res["ok"] is False
    assert "too large" in (res["error"] or "")


# ── P1: data access + emit contract ──────────────────────────────────────────────────
import sqlite3

import pytest


@pytest.fixture
def sales_db(tmp_path):
    """A tiny fixture DB — generic identifiers only (no deployment specifics)."""
    p = tmp_path / "sales.db"
    c = sqlite3.connect(str(p))
    c.executescript(
        "CREATE TABLE orders(region TEXT, quarter TEXT, amount REAL);"
        "INSERT INTO orders VALUES"
        " ('north','Q1',100),('north','Q2',150),"
        " ('south','Q1',80),('south','Q2',120);"
        "CREATE VIEW v_totals AS SELECT region, SUM(amount) AS total FROM orders GROUP BY region;"
    )
    c.commit()
    c.close()
    return str(p)


def test_query_reads_real_rows(sales_db):
    code = ("rows = query('db', 'SELECT region, SUM(amount) AS total FROM orders GROUP BY region "
            "ORDER BY region')\nprint(rows)")
    res = pc.run(code, data_files={"db": sales_db})
    assert res["ok"] is True, res["error"]
    assert "'region': 'north'" in res["stdout"] and "250.0" in res["stdout"]  # 100 + 150


def test_tables_lists_tables_and_views(sales_db):
    res = pc.run("print(tables('db'))", data_files={"db": sales_db})
    assert res["ok"] is True
    assert "orders" in res["stdout"] and "v_totals" in res["stdout"]


def test_db_is_read_only(sales_db):
    # The whole point: the sandbox physically cannot mutate a real store.
    res = pc.run("query('db', \"INSERT INTO orders VALUES ('east','Q1',1)\")",
                 data_files={"db": sales_db})
    assert res["ok"] is False
    assert "readonly" in (res["error"] or "").lower()
    # ...and the file is genuinely untouched.
    c = sqlite3.connect(sales_db)
    assert c.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 4
    c.close()


def test_unknown_data_file_is_a_clear_error(sales_db):
    res = pc.run("query('nope', 'SELECT 1')", data_files={"db": sales_db})
    assert res["ok"] is False
    assert "unknown data file" in (res["error"] or "")


def test_read_df_returns_dataframe(sales_db):
    code = ("df = read_df('db', 'SELECT * FROM orders')\n"
            "print(df.shape)\nprint(float(df['amount'].sum()))")
    res = pc.run(code, data_files={"db": sales_db})
    assert res["ok"] is True, res["error"]
    assert "(4, 3)" in res["stdout"] and "450.0" in res["stdout"]


def test_emit_chart_computed_from_real_data(sales_db):
    """The flagship shape: numbers DERIVED from the data, not guessed by a model."""
    code = (
        "rows = query('db', 'SELECT region, SUM(amount) AS total FROM orders "
        "GROUP BY region ORDER BY region')\n"
        "emit_chart('bar', rows, ['total'], title='Total by region', x_key='region')"
    )
    res = pc.run(code, data_files={"db": sales_db})
    assert res["ok"] is True, res["error"]
    art = res["artifact"]
    assert art["type"] == "json:chart" and art["title"] == "Total by region"
    p = art["payload"]
    assert p["chart_type"] == "bar"
    assert p["series"] == [{"key": "total"}]
    assert p["x_axis"] == {"key": "region"}
    assert p["data"] == [{"region": "north", "total": 250.0}, {"region": "south", "total": 200.0}]


def test_emit_chart_accepts_dataframe(sales_db):
    code = ("df = read_df('db', 'SELECT quarter, amount FROM orders WHERE region = \"north\"')\n"
            "emit_chart('line', df, [{'key': 'amount', 'label': 'North'}], x_key='quarter')")
    res = pc.run(code, data_files={"db": sales_db})
    assert res["ok"] is True, res["error"]
    p = res["artifact"]["payload"]
    assert p["data"] == [{"quarter": "Q1", "amount": 100.0}, {"quarter": "Q2", "amount": 150.0}]
    assert p["series"][0]["label"] == "North"


def test_invalid_chart_is_rejected_by_parent_validation():
    # 'donut' is not in the ChartConfig literal set -> caught before it reaches the frontend.
    res = pc.run("emit_chart('donut', [{'a': 1}], ['a'])")
    assert res["ok"] is False
    assert "ChartConfig validation" in (res["error"] or "")


def test_multiple_artifacts_for_multi_chart_dashboards(sales_db):
    code = (
        "rows = query('db', 'SELECT region, SUM(amount) AS total FROM orders GROUP BY region')\n"
        "emit_chart('bar', rows, ['total'], x_key='region', title='By region')\n"
        "emit_table(rows, title='Raw')\n"
        "emit_markdown('**450 total**')"
    )
    res = pc.run(code, data_files={"db": sales_db})
    assert res["ok"] is True, res["error"]
    assert [a["type"] for a in res["artifacts"]] == ["json:chart", "markdown", "markdown"]
    assert res["artifact"] is res["artifacts"][0]
    assert "| region | total |" in res["artifacts"][1]["payload"]


def test_emit_html_interactive_flag():
    res = pc.run("emit_html('<b>hi</b>')\nemit_html('<b>hi</b>', interactive=True)")
    assert res["ok"] is True
    assert [a["type"] for a in res["artifacts"]] == ["html", "interactive-html"]


def test_emitted_artifacts_carry_provenance():
    res = pc.run("emit_markdown('x')")
    assert res["artifact"]["metadata"]["source"] == "py_compute"


def test_failure_shape_includes_artifacts_key():
    assert pc.run("  ")["artifacts"] == []
