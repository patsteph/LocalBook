"""CI-pure tests for the cursor SQL validator's display-name-in-key guard.

The classic text-to-SQL failure is the model stuffing a person's DISPLAY NAME into an id/key
column (`owner_id = 'Jordan Lee'`) instead of resolving it. `_validate_sql` catches that and
returns a directed repair. These tests use only generic identifiers.
"""
from services import cursor_sql as cs

_LINKED = [{
    "table_name": "records",
    "columns": [{"sanitized": "owner_id"}, {"sanitized": "record_key"},
                {"sanitized": "region"}, {"sanitized": "count"}],
}]
_ALL = {"records", "people"}


def _v(sql):
    return cs._validate_sql(sql, _LINKED, None, _ALL)


def test_rejects_display_name_in_id_column():
    err = _v("SELECT COUNT(*) FROM records WHERE owner_id = 'Jordan Lee'")
    assert err and "owner_id" in err and "id/key" in err.lower()


def test_rejects_display_name_in_key_column():
    err = _v("SELECT COUNT(*) FROM records WHERE record_key = 'Acme West'")
    assert err and "record_key" in err


def test_allows_name_resolved_via_subquery():
    # The CORRECT shape — resolve the name to a key with a subquery. Must NOT be rejected.
    sql = ("SELECT COUNT(*) FROM records WHERE owner_id = "
           "(SELECT person_id FROM people WHERE full_name LIKE '%Jordan Lee%')")
    assert _v(sql) is None


def test_allows_email_id_value():
    # An email IS a valid id value (has no internal whitespace, contains '@').
    assert _v("SELECT COUNT(*) FROM records WHERE owner_id = 'jlee@example.com'") is None


def test_ignores_non_key_columns():
    # A spaced value in a NON-key column (a region/label) is legitimate → not rejected.
    assert _v("SELECT COUNT(*) FROM records WHERE region = 'North West'") is None


def test_allows_single_token_id():
    # A bare id token (no whitespace) is fine even in an id column.
    assert _v("SELECT COUNT(*) FROM records WHERE owner_id = 'jlee'") is None


def test_lookup_value_token_and(tmp_path):
    import sqlite3
    p = tmp_path / "e.db"
    c = sqlite3.connect(str(p))
    c.executescript(
        "CREATE TABLE people(full_name TEXT, person_id TEXT);"
        "INSERT INTO people VALUES ('Lee, Jordan','jlee'),('Smith, Sam','ssmith');"
        "CREATE TABLE recs(op TEXT); INSERT INTO recs VALUES ('STR_BIG RIVER OP'),('STR_BLUE SKY OP');")
    c.commit()
    c.close()
    # reordered name resolves to the stored form, then to its key
    v = cs._lookup_value(str(p), "people", "full_name", "Jordan Lee")
    assert v == "Lee, Jordan"
    assert cs._lookup_stable_key(str(p), "people", "full_name", v, "person_id") == "jlee"
    # a value embedded in a longer label
    assert cs._lookup_value(str(p), "recs", "op", "Big River") == "STR_BIG RIVER OP"
    # ambiguous single token → None (never guess between candidates)
    assert cs._lookup_value(str(p), "recs", "op", "STR") is None


def test_tier0_complexity_gate():
    # simple counts stay in the deterministic fast-path
    for q in ["how many orders does Jordan Lee have", "orders by region",
              "how many orders in the west", "how many accounts are there this year"]:
        assert cs._tier0_too_complex(q) is False, q
    # analytical questions must decline to the LLM
    for q in ["compare orders to revenue for the last 4 years",
              "break down pipeline by product by quarter",
              "what is the total bookings goal for the west",
              "trend of forecast attainment over time"]:
        assert cs._tier0_too_complex(q) is True, q


def test_canonical_rules_block_from_catalog():
    cat = {"defaults": [{"col": "order_year", "op": "=", "value": "2025"},
                        {"col": "status", "op": "=", "value": "open"}],
           "person_convention": {"name_table": "customers", "name_col": "full_name",
                                 "key_col": "customer_id"}}
    block = cs._canonical_rules_block(cat)
    assert "order_year = 2025" in block and "status = 'open'" in block      # numeric bare, text quoted
    assert "SELECT customer_id FROM customers WHERE full_name LIKE" in block  # person→key pattern
    assert cs._canonical_rules_block(None) == "" and cs._canonical_rules_block({}) == ""


def test_display_name_helper():
    assert cs._looks_like_display_name("Jordan Lee") is True
    assert cs._looks_like_display_name("jlee") is False
    assert cs._looks_like_display_name("jlee@x.com") is False
    assert cs._looks_like_display_name("12 34") is False   # numeric-ish → not a name
