"""OpenAlex as a searchable source.

OpenAlex is an open catalogue of ~250M scholarly works — broader than arXiv
(every discipline, not just preprints) and openly licensed, unlike Semantic
Scholar's rate-limited free tier.

Two details make or break it, and both were got wrong first:

  * **Abstracts are an inverted index**, {word: [positions]}, not a string — a
    copyright workaround on their side. Without reconstructing it every result
    reads "No abstract available", which is a bare title rather than a usable
    result.

  * **Never sort by citations.** It looks right for a research catalogue and
    ranks by citations across everything matching ANY term: a search for
    "retrieval augmented generation" returned SciPy and QUANTUM ESPRESSO.
    Their default relevance score already weighs citations.
"""
import pytest

from services.site_search import OpenAlexSearchHandler, SiteSearchService


def test_openalex_is_registered_as_a_searchable_site():
    sites = {s["name"] for s in SiteSearchService.get_supported_sites()}
    assert "OpenAlex" in sites
    assert "openalex.org" in SiteSearchService.HANDLERS


def test_it_advertises_that_no_api_key_is_needed():
    """OpenAlex is free and unauthenticated. Claiming otherwise sends people
    hunting for a key that does not exist."""
    assert OpenAlexSearchHandler.requires_api_key is False


def test_the_abstract_index_is_rebuilt_in_order():
    index = {"Large": [0], "language": [1], "models": [2], "are": [3], "useful": [4]}
    assert OpenAlexSearchHandler._deinvert_abstract(index) == \
        "Large language models are useful"


def test_a_word_appearing_twice_lands_in_both_places():
    index = {"the": [0, 3], "cat": [1], "sat": [2], "mat": [4]}
    assert OpenAlexSearchHandler._deinvert_abstract(index) == "the cat sat the mat"


@pytest.mark.parametrize("index", [None, {}, "not a dict", {"x": None}])
def test_a_missing_or_malformed_abstract_never_raises(index):
    assert OpenAlexSearchHandler._deinvert_abstract(index) == ""


def test_the_abstract_is_truncated_for_a_snippet():
    index = {f"word{i}": [i] for i in range(500)}
    assert len(OpenAlexSearchHandler._deinvert_abstract(index, max_chars=120)) <= 120


def test_results_are_never_sorted_by_citation_count():
    """Measured 2026-09-21: `sort=cited_by_count:desc` on "retrieval augmented
    generation" returned SciPy (39,742 cites) and QUANTUM ESPRESSO — the most
    cited papers that happen to contain any of those words."""
    import ast
    import inspect
    # Parse it. The comment above the params dict NAMES the wrong sort in order
    # to explain why it is absent — a textual search would fail on the very
    # explanation that keeps the mistake from returning.
    tree = ast.parse(inspect.getsource(OpenAlexSearchHandler.search).lstrip())
    keys = [k.value for node in ast.walk(tree) if isinstance(node, ast.Dict)
            for k in node.keys if isinstance(k, ast.Constant)]
    assert "sort" not in keys, "the request sets an explicit sort; relevance must decide"


def test_the_url_prefers_something_that_reaches_the_text():
    """An OpenAlex id resolves to a metadata page. The open-access PDF is what
    the user — and our own ingest — actually wants."""
    import inspect
    src = inspect.getsource(OpenAlexSearchHandler.search)
    assert src.index('oa.get("oa_url")') < src.index('work.get("id")')
