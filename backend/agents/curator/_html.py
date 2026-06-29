"""CuratorHtmlMixin — extracted from the former agents/curator.py (Wave 3 split)."""
from ._models import *  # noqa: F401,F403


class CuratorHtmlMixin:
    def _compose_brief_html(
        self,
        *,
        duration_str: str,
        summaries: List["NotebookSummary"],
        narrative: str,
        cross_insight: Optional[str],
        clusters: List[Any],
        deep_reads: List[Dict[str, Any]],
        total_recent_ingests: int,
    ) -> Optional[str]:
        """Compose the HTML dashboard variant of the morning brief.

        Server-side composition (not LLM-generated HTML) — for a layout
        this structured, deterministic assembly is more reliable than
        prompting gemma4 to produce dashboard HTML reliably. The LLM-
        authored prose (`narrative`) sits inside the dashboard wrapper.

        Strict mode: output uses the Tailwind subset that
        HtmlArtifactRenderer's Shadow DOM injects. No <script>, no inline
        styles requiring url(), no <img>.
        """
        import html as _html

        # Skip-digest path: quiet morning (P10.D). The check happens here
        # so the HTML output stays in lockstep with the skip-digest
        # decision in the markdown narrative.
        meaningful = any(
            nb.items_added > 0 or nb.pending_approval > 0
            or nb.collection_items_approved > 0 or nb.highlights_since > 0
            or nb.notes_created > 0 or nb.interactions_since > 0
            or nb.emerging_topics or nb.recent_stories
            for nb in summaries
        )
        if not clusters and total_recent_ingests < 5 and not meaningful:
            return (
                '<div class="lb-html-artifact p-6 max-w-2xl mx-auto">'
                '<h3 class="text-lg font-semibold text-gray-800 mb-2">Quiet morning</h3>'
                f'<p class="text-sm text-gray-600">'
                f'{total_recent_ingests} items came in across your notebooks. '
                'Nothing converging yet — your notebooks are where you left them.'
                '</p>'
                '</div>'
            )

        parts: List[str] = []
        parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto">')

        # Cornerstone / header
        parts.append('<div class="mb-6">')
        parts.append(
            f'<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">'
            f'You\'ve been away for {_html.escape(duration_str)}'
            '</p>'
        )
        if cross_insight:
            parts.append(
                '<p class="text-sm text-gray-700 italic">'
                + _html.escape(cross_insight)
                + '</p>'
            )
        parts.append('</div>')

        # Narrative prose intentionally omitted from the HTML dashboard
        # (K3, 2026-06-09). The LLM-generated narrative often contains
        # markdown (### headings, **bold**) which renders as raw text
        # when shoved through html.escape + <p> wrapping. CuratorPanel
        # now renders the narrative as a separate Markdown artifact
        # below the dashboard so heading styles + emphasis work properly.

        # Consensus clusters
        if clusters:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">What\'s converging</h3>'
            )
            parts.append('<div class="grid grid-cols-2 gap-3 mb-6">')
            for cl in clusters[:6]:
                top_senders = sorted(
                    (cl.sender_counts or {}).items(), key=lambda x: x[1], reverse=True
                )[:4]
                notebooks_count = len(cl.notebook_counts or {})

                # Phase 14 (2026-06-08) — per-cluster sender share bars.
                # CSS-only (no scripts) so it renders inside the strict
                # HtmlArtifactRenderer Shadow DOM. Bars are normalized to
                # the strongest sender in the cluster so visual weight
                # tracks agenda concentration. Empty senders → fall back
                # to "various sources" prose.
                # Bucket continuous pct → twelfths because the strict
                # HtmlArtifactRenderer Shadow DOM strips `style` attributes
                # via DOMPurify. Tailwind subset (export_assets +
                # htmlArtifactTailwindSubset) defines w-1/12 .. w-11/12 so
                # bars render proportionally with class names only. The CSS
                # selectors escape the slash; the class attribute emits a
                # literal `/`.
                def _bucket_class(p: int) -> str:
                    twelfths = max(1, min(12, round(p / 8.333)))
                    return "w-full" if twelfths >= 12 else f"w-{twelfths}/12"
                bars_html = ""
                max_n = max((n for _, n in top_senders if n), default=0)
                if max_n > 0:
                    rows = []
                    for s, n in top_senders:
                        if not s:
                            continue
                        pct = max(8, int((n / max_n) * 100))
                        width_cls = _bucket_class(pct)
                        rows.append(
                            '<div class="flex items-center gap-2 mb-1">'
                            f'<div class="text-xs text-gray-700 w-24 truncate" title="{_html.escape(s)}">{_html.escape(s)}</div>'
                            '<div class="flex-1 bg-blue-100 rounded h-2 overflow-hidden">'
                            f'<div class="bg-blue-500 h-2 rounded {width_cls}"></div>'
                            '</div>'
                            f'<div class="text-xs text-gray-600 w-6 text-right">{n}</div>'
                            '</div>'
                        )
                    bars_html = "".join(rows)
                else:
                    bars_html = '<p class="text-xs text-gray-500 italic">various sources</p>'

                parts.append(
                    '<div class="rounded-lg border border-blue-200 bg-blue-50 p-3">'
                    f'<p class="text-xs uppercase tracking-wide text-blue-700 mb-1">'
                    f'{cl.size} sources across {notebooks_count} notebook'
                    f'{"s" if notebooks_count != 1 else ""}</p>'
                    f'<p class="text-sm font-medium text-gray-800 mb-2">'
                    f'{_html.escape(cl.topic_label or "(unlabeled)")}</p>'
                    f'<div class="mt-2">{bars_html}</div>'
                    '</div>'
                )
            parts.append('</div>')

        # Deep reads triggered
        if deep_reads:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">Deep reads triggered</h3>'
            )
            parts.append('<ul class="mb-6">')
            for dr in deep_reads:
                parts.append(
                    f'<li class="text-sm text-gray-700">'
                    f'Researching <strong>{_html.escape(dr.get("topic_label", "topic"))}</strong> '
                    '— will surface results in the notebook shortly.'
                    '</li>'
                )
            parts.append('</ul>')

        # Per-notebook activity
        active = [nb for nb in summaries if (
            nb.items_added or nb.pending_approval or nb.notes_created
            or nb.highlights_since or nb.interactions_since
            or nb.collection_items_approved or nb.emerging_topics or nb.recent_stories
        )]
        if active:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">Today across your notebooks</h3>'
            )
            parts.append('<div class="flex flex-col gap-2">')
            for nb in active[:8]:
                bits: List[str] = []
                if nb.items_added:
                    bits.append(f"{nb.items_added} new")
                if nb.pending_approval:
                    bits.append(f"{nb.pending_approval} pending")
                if nb.notes_created:
                    bits.append(f"{nb.notes_created} note{'s' if nb.notes_created != 1 else ''}")
                if nb.highlights_since:
                    bits.append(f"{nb.highlights_since} highlight{'s' if nb.highlights_since != 1 else ''}")
                summary_bits = " · ".join(bits) if bits else "activity"
                parts.append(
                    '<div class="rounded-md border border-gray-200 bg-white p-3">'
                    f'<p class="text-sm font-medium text-gray-800">{_html.escape(nb.name)}</p>'
                    f'<p class="text-xs text-gray-500">{summary_bits}</p>'
                    '</div>'
                )
            parts.append('</div>')

        parts.append('</div>')
        return "".join(parts)

    def _compose_weekly_wrap_html(
        self,
        *,
        week_start: str,
        week_end: str,
        summaries: List["NotebookSummary"],
        narrative: str,
        cross_insight: Optional[str],
        total_sources: int,
        total_collector: int,
        total_user: int,
        total_convos: int,
        total_audio: int,
        total_docs: int,
    ) -> Optional[str]:
        """Server-composed HTML dashboard for the weekly wrap-up.

        Same Tailwind subset / strict-HTML constraints as Phase 10's
        morning brief composer — no scripts, no inline styles, no <img>.
        Renders via the Phase 14 ```html fence handler in chat replies.
        """
        import html as _html

        parts: List[str] = []
        parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto">')

        # Header
        parts.append(
            '<div class="mb-6">'
            '<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">Weekly wrap-up</p>'
            f'<p class="text-lg font-semibold text-gray-900 mb-1">{_html.escape(week_start)} → {_html.escape(week_end)}</p>'
            '</div>'
        )

        if cross_insight:
            parts.append(
                '<p class="text-sm text-gray-700 italic mb-4">'
                + _html.escape(cross_insight)
                + '</p>'
            )

        # Aggregate stats grid — 6 tiles, 3 columns
        stats = [
            ("Sources added", total_sources),
            ("Collected", total_collector),
            ("Added by you", total_user),
            ("Conversations", total_convos),
            ("Audio created", total_audio),
            ("Docs created", total_docs),
        ]
        parts.append(
            '<h3 class="text-base font-semibold text-gray-800 mb-2">This week at a glance</h3>'
            '<div class="grid grid-cols-3 gap-2 mb-6">'
        )
        for label, value in stats:
            parts.append(
                '<div class="rounded-lg border border-gray-200 bg-gray-50 p-3 text-center">'
                f'<p class="text-xl font-semibold text-gray-900">{value}</p>'
                f'<p class="text-xs text-gray-500 mt-1">{_html.escape(label)}</p>'
                '</div>'
            )
        parts.append('</div>')

        # Narrative (sanitized prose paragraphs)
        if narrative:
            paras = [p.strip() for p in narrative.split("\n\n") if p.strip()]
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">The story</h3>'
                '<div class="mb-6">'
            )
            for p in paras[:10]:
                parts.append(
                    '<p class="text-sm text-gray-800 mb-3">' + _html.escape(p) + '</p>'
                )
            parts.append('</div>')

        # Per-notebook activity
        active = [nb for nb in summaries if (
            nb.items_added or nb.notes_created or nb.highlights_since
            or nb.interactions_since or nb.collection_items_approved
            or nb.emerging_topics or nb.recent_stories
        )]
        if active:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">Across your notebooks</h3>'
                '<div class="flex flex-col gap-2">'
            )
            for nb in active[:8]:
                bits: List[str] = []
                if nb.items_added:
                    bits.append(f"{nb.items_added} sources")
                if nb.notes_created:
                    bits.append(f"{nb.notes_created} note{'s' if nb.notes_created != 1 else ''}")
                if nb.highlights_since:
                    bits.append(f"{nb.highlights_since} highlight{'s' if nb.highlights_since != 1 else ''}")
                if nb.interactions_since:
                    bits.append(f"{nb.interactions_since} chat{'s' if nb.interactions_since != 1 else ''}")
                summary_bits = " · ".join(bits) if bits else "activity"
                parts.append(
                    '<div class="rounded-md border border-gray-200 bg-white p-3">'
                    f'<p class="text-sm font-medium text-gray-800">{_html.escape(nb.name)}</p>'
                    f'<p class="text-xs text-gray-500">{summary_bits}</p>'
                    '</div>'
                )
            parts.append('</div>')

        parts.append('</div>')
        return "".join(parts)

    async def compose_notebook_dashboard_html(self, notebook_id: str) -> str:
        """Server-composed HTML overview for a single notebook.

        Reuses the same digest + activity pipeline that powers Phase 10's
        morning brief but scoped to one notebook. Includes consensus
        clusters from the last 7 days filtered to this notebook.
        """
        import html as _html
        from datetime import timedelta as _td

        def esc(s: Any) -> str:
            return _html.escape(str(s or ""), quote=True)

        # 1. Digest
        try:
            from services.curator_brain import curator_brain
            digest = curator_brain.get_digest(notebook_id) or {}
        except Exception:
            digest = {}

        # 2. Notebook record (for name)
        notebook = await notebook_store.get(notebook_id) or {}
        nb_name = notebook.get("title", "Notebook")

        # 3. Recent activity (last 7 days)
        try:
            from storage.source_store import source_store as _src_store
            all_sources_by_nb = await _src_store.list_all()
            sources_for_nb = all_sources_by_nb.get(notebook_id, [])
        except Exception:
            sources_for_nb = []
        since = datetime.utcnow() - _td(days=7)
        try:
            activity = await self._get_activity_since(notebook_id, since, sources_for_nb)
        except Exception as e:
            logger.debug(f"[curator.dashboard] activity fetch failed: {e}")
            activity = {}

        # 4. Filtered consensus
        consensus: List[Any] = []
        try:
            from services.consensus_detector import detect_consensus
            all_clusters = await detect_consensus(since_days=7, min_cluster_size=2)
            consensus = [c for c in all_clusters if c.primary_notebook_id == notebook_id]
        except Exception as e:
            logger.debug(f"[curator.dashboard] consensus fetch failed: {e}")

        # Decode key_themes / key_entities (stored as JSON strings)
        def _decode_json_list(s: Any) -> List[str]:
            if not s:
                return []
            if isinstance(s, list):
                return [str(x) for x in s]
            try:
                v = json.loads(s) if isinstance(s, str) else []
                return [str(x) for x in v] if isinstance(v, list) else []
            except Exception:
                return []

        themes = _decode_json_list(digest.get("key_themes"))[:8]
        entities = _decode_json_list(digest.get("key_entities"))[:10]
        summary = digest.get("current_summary") or "Notebook still warming up — generate some content to see synthesis."

        parts: List[str] = []
        parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto">')
        # Header
        parts.append(
            '<div class="mb-6">'
            '<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">Notebook dashboard</p>'
            f'<p class="text-lg font-semibold text-gray-900 mb-1">{esc(nb_name)}</p>'
            f'<p class="text-sm text-gray-700">{esc(summary)}</p>'
            '</div>'
        )

        # Themes + entities chips
        if themes or entities:
            parts.append('<div class="mb-6 grid grid-cols-2 gap-3">')
            if themes:
                chips = "".join(
                    f'<span class="text-xs rounded-full px-2 py-0.5 bg-blue-50 text-blue-700 mr-1 mb-1 inline-block">{esc(t)}</span>'
                    for t in themes
                )
                parts.append(
                    '<div class="rounded-lg border border-gray-200 bg-white p-3">'
                    '<p class="text-xs uppercase tracking-wide text-gray-500 mb-2">Key themes</p>'
                    f'<div>{chips}</div></div>'
                )
            if entities:
                chips = "".join(
                    f'<span class="text-xs rounded-full px-2 py-0.5 bg-purple-50 text-purple-700 mr-1 mb-1 inline-block">{esc(e)}</span>'
                    for e in entities
                )
                parts.append(
                    '<div class="rounded-lg border border-gray-200 bg-white p-3">'
                    '<p class="text-xs uppercase tracking-wide text-gray-500 mb-2">Key entities</p>'
                    f'<div>{chips}</div></div>'
                )
            parts.append('</div>')

        # Activity grid (last 7 days)
        items_added = activity.get("items_added", 0)
        pending = activity.get("pending_approval", 0)
        notes_created = activity.get("notes_created", 0)
        highlights = activity.get("highlights_since", 0)
        if any([items_added, pending, notes_created, highlights]):
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">This week\'s activity</h3>'
                '<div class="grid grid-cols-4 gap-2 mb-6">'
            )
            for label, value in (
                ("New sources", items_added),
                ("Pending", pending),
                ("Notes", notes_created),
                ("Highlights", highlights),
            ):
                parts.append(
                    '<div class="rounded-lg border border-gray-200 bg-gray-50 p-3 text-center">'
                    f'<p class="text-xl font-semibold text-gray-900">{value}</p>'
                    f'<p class="text-xs text-gray-500 mt-1">{esc(label)}</p>'
                    '</div>'
                )
            parts.append('</div>')

        # Consensus
        if consensus:
            parts.append(
                '<h3 class="text-base font-semibold text-gray-800 mb-2">What\'s converging in this notebook</h3>'
                '<div class="grid grid-cols-2 gap-3 mb-6">'
            )
            for cl in consensus[:6]:
                parts.append(
                    '<div class="rounded-lg border border-blue-200 bg-blue-50 p-3">'
                    f'<p class="text-xs uppercase tracking-wide text-blue-700 mb-1">{cl.size} sources</p>'
                    f'<p class="text-sm font-medium text-gray-800">{esc(cl.topic_label or "(unlabeled)")}</p>'
                    '</div>'
                )
            parts.append('</div>')

        parts.append('</div>')
        return "".join(parts)
