# Test fixtures

Phase 1 copies the fixtures the engine's Swift Testing cases need from `backend/tests` (BM25 corpora, the
recipe conformance vectors, `VisualSpec` samples) into this folder. Each file is named after the Python test it
came from so the two suites can be compared. Nothing here is loaded by the apps.
