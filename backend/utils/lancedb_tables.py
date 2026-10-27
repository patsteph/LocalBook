"""List and look up LanceDB tables without the default page limit.

`db.table_names()` returns only the FIRST 10 tables unless told otherwise. A LocalBook data folder has one
table per notebook, so with more than ten notebooks every lookup of the form `name in db.table_names()` was
wrong for the rest: delete_source reported "nothing to delete" and the sync indexer saw every source of a later
notebook as unindexed, re-embedding it (and duplicating its chunks) at every sync (2026-10-22).

Use these two helpers instead of `table_names()` so the number of notebooks never matters.
"""
from __future__ import annotations

import warnings
from typing import Any, List

_PAGE = 1000


def list_tables(db: Any) -> List[str]:
    """Every table name, however many there are."""
    names: List[str] = []
    token = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        while True:
            try:
                page = list(db.table_names(page_token=token, limit=_PAGE))
            except TypeError:                       # an older LanceDB without paging lists everything at once
                return list(db.table_names())
            names.extend(page)
            if len(page) < _PAGE:
                return names
            token = page[-1]


def has_table(db: Any, name: str) -> bool:
    """Whether a table exists. Opens it, so it does not depend on any listing."""
    try:
        db.open_table(name)
        return True
    except Exception:
        return False
