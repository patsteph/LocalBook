"""CI-pure tests for the deterministic recipe-template engine (cursor_recipes)."""
from services import cursor_recipes as cr

_SQL = """
-- recipe: accounts_owned_by_person
-- when: how many accounts does {person} own; accounts owned by {person}; {person} account count
SELECT COUNT(DISTINCT record_key) AS n FROM v_records
WHERE owner_id = (SELECT email_id FROM employees WHERE full_name LIKE '%' || :person || '%')
  AND fiscal_year = 2027 AND record_status = 'active' AND in_scope = 1;

-- recipe: accounts_in_area
-- when: how many accounts in {area}; account count for {area}
SELECT COUNT(*) FROM v_records WHERE area = :area AND fiscal_year = 2027;

-- recipe: roster_by_area
-- when: accounts by area; account count by area
SELECT area, COUNT(*) FROM v_records WHERE fiscal_year = 2027 GROUP BY area;
"""


def test_parse_and_match_person_param():
    tpls = cr.parse_recipe_templates(_SQL)
    assert {t["name"] for t in tpls} == {"accounts_owned_by_person", "accounts_in_area", "roster_by_area"}
    hit = cr.match("how many accounts does Sam Rivera own", tpls)
    assert hit and hit[2] == "accounts_owned_by_person" and hit[1]["person"] == "Sam Rivera"
    # lowercase + trailing filler still works (LIKE handles case; filler trimmed)
    hit2 = cr.match("how many accounts does Sam Rivera own this year", tpls)
    assert hit2[1]["person"] == "Sam Rivera"


def test_trailing_param_captured_fully():
    tpls = cr.parse_recipe_templates(_SQL)
    hit = cr.match("how many accounts in Region WEST", tpls)
    assert hit and hit[2] == "accounts_in_area" and hit[1]["area"] == "Region WEST"


def test_no_param_recipe_and_fallback():
    tpls = cr.parse_recipe_templates(_SQL)
    assert cr.match("accounts by area", tpls)[2] == "roster_by_area"
    assert cr.match("what is the weather today", tpls) is None   # → LLM fallback


def test_rejects_non_select_templates():
    bad = "-- recipe: evil\n-- when: delete stuff\nDELETE FROM v_records;"
    assert cr.parse_recipe_templates(bad) == []
    ok = "-- recipe: ok\n-- when: count\nSELECT COUNT(*) FROM v_records;"
    assert len(cr.parse_recipe_templates(ok)) == 1
