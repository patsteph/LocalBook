"""What makes the Mac's answers personal, for a paired phone: the user's profile and core memory, and the
notebook's entities and relationships (which the Mac uses to boost passages and to add context).

Query-dependent memory (archival search, recent chat turns) is not sent: it changes with every question.
Notebooks the user keeps off phones send nothing."""
import hashlib
import json
from typing import Any, Dict, List, Optional

from services.sync import phone_library

# The numbers the Mac's own code uses (entity_extractor, entity_graph, rag_engine); published in the recipe so
# the phone follows them, and checked against the Mac's functions by the conformance vectors.
RULES: Dict[str, Any] = {
    "boost_factor": 0.15, "boost_cap": 0.3, "mention_divisor": 10.0, "source_weight_new": 0.5,
    "source_weight_add": 0.3, "first_name_min_chars": 2, "context_entities": 3, "snippets": 2,
    "relationships_per_entity": 3, "relationship_lines": 5, "entity_background_chars": 500,
    "related_entities_chars": 500, "relationship_limit": 5000,
}


def _entities(notebook_id: str) -> List[Dict[str, Any]]:
    from services.entity_extractor import entity_extractor
    return [{"name": e.name, "type": e.type, "mentions": int(e.mentions), "source_ids": list(e.source_ids or []),
             "snippets": list(e.context_snippets or [])[:RULES["snippets"]]}
            for e in entity_extractor._entities.get(notebook_id, {}).values()]


def _relationships(notebook_id: str) -> List[Dict[str, Any]]:
    from services.entity_graph import entity_graph
    rels = list(entity_graph._relationships.get(notebook_id, {}).values())
    rels.sort(key=lambda r: -r.strength)
    return [{"source": r.source_entity, "relationship": r.relationship, "target": r.target_entity,
             "strength": float(r.strength)} for r in rels[:RULES["relationship_limit"]]]


def personal(notebook_id: str) -> Optional[Dict[str, Any]]:
    if not phone_library.is_visible(notebook_id):
        return None
    from api.settings import build_user_context, get_user_profile_sync
    from storage.memory_store import memory_store
    try:
        core = memory_store.load_core_memory().to_prompt_block()
    except Exception:
        core = ""
    body = {"notebook_id": notebook_id, "user_context": build_user_context(get_user_profile_sync()),
            "core_memory": core, "entities": _entities(notebook_id), "relationships": _relationships(notebook_id)}
    body["version"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]
    return body


# ── what the phone is told, and the proof it follows it ────────────────────────────────────────────────────

def routing() -> Dict[str, Any]:
    """services/source_router.SourceRouter as data: intent patterns and the boost each intent gives each kind
    of source. The boost table is repeated from SourceRouter.route; the vectors below prove they agree."""
    from services.source_router import SourceRouter
    r = SourceRouter()
    return {
        "patterns": {"numeric": r.numeric_patterns, "comparison": r.comparison_patterns, "lookup": r.lookup_patterns,
                     "explanation": r.explanation_patterns, "summary": r.summary_patterns, "list": r.list_patterns},
        "boosts": {"numeric": [0.25, -0.1], "comparison": [0.2, 0.0], "lookup": [0.1, 0.05],
                   "explanation": [-0.1, 0.2], "summary": [0.0, 0.15], "list": [0.1, 0.05], "mixed": [0.0, 0.0]},
        "tabular_types": ["xlsx", "csv", "tabular", "spreadsheet"],
        "text_types": ["pdf", "docx", "txt", "web", "document", "text", "youtube"],
        "tabular_extensions": [".xlsx", ".csv", ".xls"],
        "text_extensions": [".pdf", ".docx", ".txt", ".md"],
    }


def routing_vectors(questions: List[str]) -> List[Dict[str, Any]]:
    from services.source_router import SourceRouter
    r = SourceRouter()
    out = []
    for q in questions:
        d = r.route(q)
        out.append({"question": q, "tabular": d.boost_tabular, "text": d.boost_text})
    return out


def _synthetic():
    from services.entity_extractor import Entity, EntityExtractor
    from services.entity_graph import EntityGraph, EntityRelationship
    ex = object.__new__(EntityExtractor)
    ex._entities = {"nb": {
        "pat": Entity("Pat Stephens", "person", 12, ["s1", "s2"], ["Pat leads the pilot", "Pat owns the budget", "third"]),
        "luma": Entity("Luma AI", "company", 4, ["s2", "s3"], []),
        "netflix": Entity("Netflix", "company", 25, ["s3"], ["Netflix onboarding is a bootcamp"]),
        "q3": Entity("Q3", "date", 2, ["s1"], []),
    }}
    gr = object.__new__(EntityGraph)
    mk = lambda a, rel, b, s: EntityRelationship(a, "person", rel, b, "company", s)
    gr._relationships = {"nb": {
        "1": mk("Pat Stephens", "works_with", "Luma AI", 3.0), "2": mk("Pat Stephens", "reported", "Netflix", 3.0),
        "3": mk("Luma AI", "mentioned_with", "Netflix", 1.0), "4": mk("Pat Stephens", "owns", "Q3", 2.0),
        "5": mk("Pat Stephens", "met", "Netflix", 0.5),
    }}
    wire_entities = [{"name": e.name, "type": e.type, "mentions": e.mentions, "source_ids": e.source_ids,
                      "snippets": e.context_snippets[:RULES["snippets"]]} for e in ex._entities["nb"].values()]
    rels = sorted(gr._relationships["nb"].values(), key=lambda r: -r.strength)
    wire_rels = [{"source": r.source_entity, "relationship": r.relationship, "target": r.target_entity,
                  "strength": r.strength} for r in rels]
    return ex, gr, wire_entities, wire_rels


def personal_vectors() -> Dict[str, Any]:
    """Synthetic entities and passages run through the Mac's own boost and context functions; the phone must
    reproduce the order, the scores and the extra prompt text exactly."""
    import copy
    from services.source_router import source_router
    ex, gr, wire_entities, wire_rels = _synthetic()
    results = [
        {"source_id": "s1", "text": "Pat Stephens will review the pilot budget.", "rerank_score": 0.50, "source_type": "pdf", "filename": "a.pdf"},
        {"source_id": "s2", "text": "Quarterly numbers by region.", "rerank_score": 0.62, "source_type": "xlsx", "filename": "b.xlsx"},
        {"source_id": "s3", "text": "Onboarding at netflix runs four to six weeks.", "rerank_score": 0.48, "source_type": "web", "filename": "c"},
        {"source_id": "s4", "text": "Nothing relevant here.", "rerank_score": 0.55, "source_type": "", "filename": "d.csv"},
        {"source_id": "s5", "text": "Plain notes.", "rerank_score": 0.40, "source_type": "md", "filename": "e.md"},
    ]
    cases = []
    for q in ["How long is onboarding at Netflix", "What did pat say about the Q3 pilot?", "How many demos did Luma AI do?",
              "Explain how hybrid search works", "no entities in this one"]:
        res = copy.deepcopy(results)
        res = ex.boost_results_by_entity("nb", q, res)
        res = source_router.apply_routing_boost(res, source_router.route(q))
        found = ex.find_entities_in_query("nb", q)
        entity_context = ex.get_entity_context_for_query("nb", q)
        supplementary = []
        if entity_context:
            supplementary.append(f"Entity background: {entity_context[:RULES['entity_background_chars']]}")
        if found:
            graph_context = gr.get_context_for_query("nb", [e.name for e in found])
            if graph_context:
                supplementary.append(f"Related entities: {graph_context[:RULES['related_entities_chars']]}")
        suffix = ("\n\n---\nAdditional context:\n" + "\n".join(supplementary)) if supplementary else ""
        cases.append({"question": q, "order": [r["source_id"] for r in res],
                      "scores": [round(r.get("boosted_score", r["rerank_score"]), 6) for r in res], "suffix": suffix})
    return {"entities": wire_entities, "relationships": wire_rels, "results": results, "cases": cases}
