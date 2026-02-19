---
description: NLP command reference for @collector and @curator chat agents
---

# Agent NLP Command Reference

Everything a user can do in a settings panel, wizard, or menu should be doable
via natural language in chat using `@collector` or `@curator`. This file is the
living reference that maps NLP intents → backend actions.

---

## @collector — Per-Notebook Content Agent

### Source Management
| Intent ID | Example Phrases | Backend Action | Status |
|---|---|---|---|
| `add_url` | "add https://…", "monitor this page: https://…" | `source_store.create` + `rag_engine.ingest_document` + `config.sources.web_pages` | ✅ |
| `add_rss` | "follow this RSS feed: https://…/rss", "subscribe to feed https://…" | `config.sources.rss_feeds.append()` | ✅ |
| `add_keyword` | "watch for news about 'quantum computing'", "add keyword: CRISPR" | `config.sources.news_keywords.append()` | ✅ |
| `remove_source` | "stop monitoring https://…", "remove that RSS feed", "unsubscribe from …" | Remove from `web_pages`/`rss_feeds`/`news_keywords` or add to `disabled_sources` | ✅ |
| `toggle_source` | "pause https://…", "re-enable the MIT feed" | `config.disabled_sources` toggle | ✅ |

### Profile / Config
| Intent ID | Example Phrases | Backend Action | Status |
|---|---|---|---|
| `set_intent` | "this notebook tracks AI policy developments" | `config.intent = …` | ✅ |
| `set_subject` | "the subject is Costco", "we're researching Tesla" | `config.subject = …` | ✅ |
| `set_focus` | "focus on earnings, M&A, regulation" | `config.focus_areas = […]` | ✅ |
| `add_focus` | "also focus on supply chain" | `config.focus_areas.append(…)` | ✅ |
| `set_excluded` | "ignore anything about crypto", "exclude meme stocks" | `config.excluded_topics = […]` | ✅ |
| `add_excluded` | "also ignore NFTs" | `config.excluded_topics.append(…)` | ✅ |
| `set_name` | "rename yourself to Scout", "your name is Radar" | `config.name = …` | ✅ |
| `set_mode` | "run in automatic mode", "switch to manual", "go hybrid" | `config.collection_mode` | ✅ |
| `set_approval` | "auto-approve everything", "show me first", "trust me mode" | `config.approval_mode` | ✅ |
| `set_schedule` | "check daily", "once a week", "every hour" | `config.schedule.frequency` | ✅ |
| `set_max_items` | "collect up to 20 items per run" | `config.schedule.max_items_per_run` | 🔲 |
| `set_filters` | "only articles less than 7 days old", "min relevance 0.7" | `config.filters` | ✅ |

### Actions
| Intent ID | Example Phrases | Backend Action | Status |
|---|---|---|---|
| `collect_now` | "go find new sources", "collect now", "run a sweep" | `POST /collector/{id}/collect-now` | ✅ |
| `show_status` | "what's my collection status?", "how many sources?" | Source count, schedule, web_pages | ✅ |
| `show_pending` | "what's waiting for approval?", "show pending items" | `collector.get_pending_approvals()` | ✅ |
| `approve_all` | "approve all pending items", "accept everything" | `collector.approve_batch(all_ids)` | ✅ |
| `reject_all` | "reject all pending", "clear the queue" | `collector.reject_item()` loop | 🔲 |
| `source_health` | "how are my sources doing?", "any sources failing?" | `collector.get_source_health_report()` | ✅ |
| `show_profile` | "show me your full profile", "what's your config?" | `/collector/{id}/profile` | ✅ |
| `show_history` | "show collection history", "when did you last run?" | `/collector/{id}/history` | ✅ |

---

## @curator — Cross-Notebook Intelligence Agent

### Cross-Notebook Operations
| Intent ID | Example Phrases | Backend Action | Status |
|---|---|---|---|
| `search_cross_nb` | "what do all my notebooks say about AI?", any general question | `cross_notebook_search.search()` → LLM synthesis | ✅ |
| `synthesize` | "synthesize insights across notebooks", "compare all notebooks on X" | `curator.synthesize_across_notebooks()` | ✅ (via cross-NB fallback) |
| `discover_patterns` | "find patterns across my notebooks", "what connections exist?" | `curator.discover_cross_notebook_patterns()` | ✅ |
| `devils_advocate` | "challenge my thesis on X", "play devil's advocate", "find counterarguments" | `curator.find_counterarguments()` | ✅ |
| `morning_brief` | "give me a morning brief", "what did I miss?", "catch me up" | `curator.generate_morning_brief()` | ✅ |

### Profile / Config
| Intent ID | Example Phrases | Backend Action | Status |
|---|---|---|---|
| `set_name` | "call yourself Athena", "your name is Oracle" | `curator.update_config({name})` | ✅ |
| `set_personality` | "be more casual and witty", "be formal and thorough" | `curator.update_config({personality})` | ✅ |
| `toggle_overwatch` | "stop watching my chats", "enable overwatch", "be quiet" | `oversight.overwatch_enabled` | ✅ |
| `exclude_notebook` | "don't include Personal in cross-notebook", "exclude notebook X" | `oversight.excluded_notebook_ids` | ✅ |
| `include_notebook` | "start including Personal again" | Remove from `excluded_notebook_ids` | ✅ |
| `set_insight_freq` | "give me insights weekly instead of daily" | `synthesis.insight_frequency` | 🔲 |

### Conversational
| Intent ID | Example Phrases | Backend Action | Status |
|---|---|---|---|
| `chat` | any general message that doesn't match above intents | `curator.conversational_reply()` / cross-NB RAG fallback | ✅ |

---

## Implementation Notes

### NLP Intent Detection Strategy
Use a two-tier approach for each agent:
1. **Fast regex pass** — catch obvious patterns (URLs, keywords like "focus on",
   "ignore", "rename", "approve", "schedule", "collect now", etc.)
2. **LLM classifier fallback** — for ambiguous messages, use a fast LLM call with
   the intent list above to classify the user's intent before routing.

### Agent Response Contract
Every agent response stream MUST include in its `done` event:
```json
{
  "type": "done",
  "agent_name": "<display name>",
  "agent_type": "collector" | "curator",
  "follow_up_questions": [...]
}
```

### Color Coding
- **@collector**: teal (bg-teal-600 user, bg-teal-50 assistant, border-teal-500)
- **@curator**: purple (bg-purple-600 user, bg-purple-50 assistant, border-purple-500)
- **default RAG**: blue user, gray assistant
