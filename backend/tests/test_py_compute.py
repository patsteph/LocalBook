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
    # The intended shape: compute in Python, then emit a chart-style payload.
    code = (
        "vals = [i * i for i in range(5)]\n"
        "print('sum', sum(vals))\n"
        "emit({'id': 'c', 'type': 'json:chart', 'payload': {'series': vals}})"
    )
    res = pc.run(code)
    assert res["ok"] is True
    assert res["artifact"]["payload"]["series"] == [0, 1, 4, 9, 16]
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
