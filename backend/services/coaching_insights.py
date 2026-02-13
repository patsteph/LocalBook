"""Coaching Insights Generator — LLM-powered analysis of PersonProfile data.

After collection, this service analyzes the full profile + activity + goals
and produces actionable coaching insights:
- Strengths observed from social activity
- Growth areas / development suggestions
- Conversation starters for 1:1 prep
- Topic trends (what they're posting/engaging with)
- Goal progress observations

The result is stored in person.coaching_insights as a dict.
"""
import asyncio
import logging
import json
from typing import Optional, Dict, Any
from datetime import datetime

from models.person_profile import PersonProfile
from config import settings

logger = logging.getLogger(__name__)


class CoachingInsightGenerator:
    """Generates LLM-powered coaching insights from collected profile data."""

    async def generate_insights(
        self,
        person: PersonProfile,
        notebook_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Analyze a person's profile and generate coaching insights.

        Enriches the prompt with:
        - Notebook sources/documents (team priorities, org context)
        - User profile preferences (favorite authors, coaching style)

        Returns a dict with structured insight categories, or None on failure.
        """
        profile_summary = self._build_profile_summary(person)
        if not profile_summary or len(profile_summary) < 50:
            logger.info(f"[CoachingInsights] Insufficient data for {person.name}")
            return None

        # Enrich with notebook context (team priorities, org documents)
        notebook_context = ""
        if notebook_id:
            notebook_context = await self._get_notebook_context(notebook_id, person.name)

        # Enrich with topic modeling data (BERTopic clusters for this notebook)
        topic_context = ""
        if notebook_id:
            topic_context = await self._get_topic_context(notebook_id)

        # Enrich with findings (bookmarked answers, highlights, saved notes)
        findings_context = ""
        if notebook_id:
            findings_context = await self._get_findings_context(notebook_id, person.name)

        # Enrich with exploration history (what the manager has been researching)
        exploration_context = ""
        if notebook_id:
            exploration_context = await self._get_exploration_context(notebook_id)

        # Enrich with user preferences (favorite authors, coaching style)
        user_coaching_context = self._get_user_coaching_context()

        prompt = self._build_prompt(
            person, profile_summary, notebook_context,
            topic_context=topic_context,
            findings_context=findings_context,
            exploration_context=exploration_context,
        )

        # Build system prompt with user coaching style
        system_base = (
            "You are a professional executive coach and talent analyst. "
            "Analyze the provided profile data and generate actionable "
            "coaching insights. Be specific and evidence-based — cite "
            "observable behaviors from the data. Respond with JSON only."
        )
        if user_coaching_context:
            system_base = f"{system_base}\n\n{user_coaching_context}"

        try:
            from services.ollama_client import ollama_client

            response = await ollama_client.generate(
                prompt=prompt,
                system=system_base,
                model=settings.ollama_model,
                temperature=0.4,
                timeout=60.0,
            )

            text = response.get("response", "")
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                insights = json.loads(text[start:end])
                insights["generated_at"] = datetime.utcnow().isoformat()
                logger.info(
                    f"[CoachingInsights] Generated insights for {person.name}: "
                    f"{len(insights)} categories"
                )
                return insights

            logger.warning(f"[CoachingInsights] No valid JSON in LLM response for {person.name}")
            return None

        except Exception as e:
            logger.error(f"[CoachingInsights] Failed for {person.name}: {e}")
            return None

    # =========================================================================
    # Prompt construction
    # =========================================================================

    async def _get_notebook_context(self, notebook_id: str, person_name: str) -> str:
        """Query the notebook's RAG sources for context relevant to coaching.

        This pulls in team priorities, org documents, performance frameworks,
        etc. that should influence coaching recommendations.
        Uses lightweight vector search — no LLM call.
        
        Results are sorted chronologically by source creation date so the
        LLM can see progression of information over time.
        """
        try:
            from services.rag_engine import rag_engine
            from storage.source_store import source_store

            table = rag_engine._get_table(notebook_id)
            if table is None or table.count_rows() == 0:
                return ""

            # Build a source_id → date lookup for temporal ordering
            # Prefer content_date (when content is FROM) over created_at (ingestion)
            source_dates = {}
            try:
                sources = await source_store.list(notebook_id)
                for s in sources:
                    sid = s.get("id", "")
                    if sid:
                        source_dates[sid] = s.get("content_date") or s.get("created_at", "")
            except Exception:
                pass

            queries = [
                f"team priorities goals objectives for {person_name}",
                "performance expectations development framework grade level accountability",
                f"coaching notes observations feedback for {person_name}",
            ]

            # Collect results with temporal metadata
            raw_results = []  # (date_str, text)
            seen = set()

            for q in queries:
                try:
                    embedding = rag_engine.encode([q])[0]
                    results = table.search(embedding).limit(3).to_list()
                    for r in results:
                        text = r.get("text", "")[:300]
                        if text and text[:80] not in seen:
                            seen.add(text[:80])
                            source_id = r.get("source_id", "")
                            date_str = source_dates.get(source_id, "")
                            raw_results.append((date_str, r.get("filename", ""), text))
                except Exception:
                    continue

            if not raw_results:
                return ""

            # Sort chronologically (oldest first) for progression visibility
            raw_results.sort(key=lambda x: x[0])

            context_parts = []
            for date_str, filename, text in raw_results[:6]:
                date_label = date_str[:10] if date_str else "unknown date"
                src_label = filename if filename else "source"
                context_parts.append(f"[{date_label} — {src_label}] {text}")

            combined = "\n---\n".join(context_parts)
            return combined[:2500]
        except Exception as e:
            logger.warning(f"[CoachingInsights] Failed to get notebook context: {e}")
            return ""

    def _get_user_coaching_context(self) -> str:
        """Load user profile preferences that should influence coaching style.

        If the user lists favorite authors (e.g. Simon Sinek, Adam Grant),
        the coaching insights should reflect those philosophies.
        """
        try:
            from api.settings import get_user_profile_sync
            profile = get_user_profile_sync()
            if not profile:
                return ""

            parts = []
            authors = profile.get("favorite_authors", [])
            if authors:
                authors_str = ", ".join(authors)
                parts.append(
                    f"The manager/user admires these thought leaders: {authors_str}. "
                    f"Subtly reflect their coaching philosophies and frameworks "
                    f"in your insights where appropriate."
                )

            interests = profile.get("interests", [])
            if interests:
                parts.append(
                    f"The manager's areas of interest: {', '.join(interests[:5])}."
                )

            goals = profile.get("goals")
            if goals:
                parts.append(f"The manager's stated goals: {goals}")

            custom = profile.get("custom_instructions")
            if custom:
                parts.append(f"Additional coaching guidance: {custom}")

            return " ".join(parts)
        except Exception as e:
            logger.warning(f"[CoachingInsights] Failed to load user profile: {e}")
            return ""

    async def _get_topic_context(self, notebook_id: str) -> str:
        """Get BERTopic topic clusters for this notebook.

        Shows what topic themes the notebook's content clusters into,
        giving the LLM awareness of content distribution.
        """
        try:
            from services.topic_modeling import topic_modeling_service
            topics = await topic_modeling_service.get_topics(notebook_id)
            if not topics:
                return ""
            lines = []
            for t in topics[:8]:
                kw = ", ".join(w for w, _ in t.keywords[:5]) if t.keywords else ""
                lines.append(f"- {t.display_name} ({t.document_count} docs): {kw}")
            return "Topic clusters in notebook:\n" + "\n".join(lines)
        except Exception as e:
            logger.debug(f"[CoachingInsights] Topic context failed: {e}")
            return ""

    async def _get_findings_context(self, notebook_id: str, person_name: str) -> str:
        """Get bookmarked findings (highlights, saved answers, notes) from this notebook.

        These represent the manager's key insights and decisions — high-signal data.
        """
        try:
            from services.findings_store import get_findings_store
            store = get_findings_store()
            findings = await store.get_findings(notebook_id, limit=10)
            if not findings:
                return ""
            lines = []
            for f in findings:
                f_dict = f.to_dict() if hasattr(f, 'to_dict') else f
                title = f_dict.get("title", "")
                f_type = f_dict.get("type", "")
                lines.append(f"- [{f_type}] {title}")
            return "Manager's bookmarked findings:\n" + "\n".join(lines[:8])
        except Exception as e:
            logger.debug(f"[CoachingInsights] Findings context failed: {e}")
            return ""

    async def _get_exploration_context(self, notebook_id: str) -> str:
        """Get recent queries the manager has been asking about this notebook.

        This reveals implicit coaching priorities without explicit note-taking.
        """
        try:
            from storage.exploration_store import exploration_store
            journey = await exploration_store.get_journey(notebook_id, limit=15)
            if not journey:
                return ""
            query_list = journey.get("queries", [])
            if not query_list:
                return ""
            queries = []
            for entry in query_list:
                q = entry.get("query", "")
                if q and len(q) > 10:
                    queries.append(f"- {q}")
            if not queries:
                return ""
            return "Recent research questions from the manager:\n" + "\n".join(queries[:10])
        except Exception as e:
            logger.debug(f"[CoachingInsights] Exploration context failed: {e}")
            return ""

    def _build_prompt(
        self,
        person: PersonProfile,
        profile_summary: str,
        notebook_context: str = "",
        topic_context: str = "",
        findings_context: str = "",
        exploration_context: str = "",
    ) -> str:
        """Build the coaching insights prompt."""
        goals_section = ""
        if person.goals:
            goals_lines = []
            for g in person.goals:
                status = f" [{g.status}]" if g.status else ""
                goals_lines.append(f"- {g.goal}{status}")
            goals_section = f"\n\nCurrent Goals:\n" + "\n".join(goals_lines)

        notes_section = ""
        if person.coaching_notes:
            # Sort chronologically (oldest first) so LLM sees progression
            sorted_notes = sorted(
                person.coaching_notes,
                key=lambda n: n.created_at or '',
            )
            notes_lines = []
            for n in sorted_notes:
                cat = n.category.replace("_", " ").title() if n.category else "General"
                date_str = n.created_at[:10] if n.created_at else ""
                notes_lines.append(f"- [{cat}] ({date_str}) {n.text}")
            notes_section = f"\n\nCoaching Notes (chronological):\n" + "\n".join(notes_lines)

        notebook_section = ""
        if notebook_context:
            notebook_section = (
                f"\n\nNotebook Documents (chronological — team priorities, performance "
                f"frameworks, uploaded materials, discovered items):\n{notebook_context}"
            )

        topic_section = ""
        if topic_context:
            topic_section = f"\n\n{topic_context}"

        findings_section = ""
        if findings_context:
            findings_section = f"\n\n{findings_context}"

        exploration_section = ""
        if exploration_context:
            exploration_section = f"\n\n{exploration_context}"

        return f"""Analyze this person's profile and generate coaching insights.

IMPORTANT: The manager's coaching notes and goals below are PRIMARY context.
These are direct observations from the person's manager and should be treated
as the most authoritative data source. If a coaching note states a grade or
level (e.g. "grade 12 SE"), that is the person's CURRENT level — anchor ALL
evaluation there. Do not compare them to lower grades. Everything below their
stated level is assumed mastery. Only surface insights that are relevant to
their actual grade and the evidence in the notebook.

{profile_summary}
{goals_section}
{notes_section}
{notebook_section}{topic_section}{findings_section}{exploration_section}

Generate structured coaching insights as JSON:
{{
    "strengths": [
        "Specific strength observed — cite evidence from activity AND/OR coaching notes"
    ],
    "growth_areas": [
        "Specific area for development — factor in role expectations from coaching notes"
    ],
    "conversation_starters": [
        "Specific 1:1 topic grounded in recent activity AND coaching context"
    ],
    "topic_trends": [
        "Topic or theme they've been engaging with recently"
    ],
    "goal_observations": [
        "Observation about progress toward stated goals (if any)"
    ],
    "overall_summary": "2-3 sentence coaching summary that integrates coaching notes context"
}}

Rules:
- Be specific, not generic. Reference actual data points.
- Each item should be 1-2 sentences max.
- If data is limited for a category, return fewer items or an empty list.
- Strengths and growth_areas: 2-4 items each.
- Conversation starters: 2-3 relevant topics for a coaching conversation.
- Topic trends: top 2-3 themes from their recent activity.
- Goal observations: only if goals are set, otherwise empty list.

CRITICAL — Grade/Level Calibration:
- Coaching notes specify the person's current grade or level (e.g. "grade 12 SE").
  This is the ONLY level that matters for evaluation.
- A person at grade N is ASSUMED to already meet ALL expectations for grades
  below N. Do NOT praise them for meeting lower-grade expectations — that is
  table stakes, not a strength. Do NOT suggest they "move beyond" a lower
  grade — they already have.
- Strengths should highlight where the person meets or EXCEEDS their current
  grade expectations, or where they show distinctive capability relative to
  peers at the same level.
- Growth areas should ONLY be surfaced when notebook evidence (uploaded docs,
  collected activity, coaching notes) shows a specific pattern of NOT meeting
  their current-grade expectations. If there is no evidence of a gap, do NOT
  invent one. Prefer an empty list over speculative growth areas.
- When referencing framework documents (e.g. expectations docs), ONLY
  reference the expectations at the person's stated grade level or above.
  Lower-grade expectations are irrelevant context.

JSON:"""

    def _build_profile_summary(self, person: PersonProfile) -> str:
        """Build a concise profile summary for the LLM prompt."""
        parts = []

        parts.append(f"Name: {person.name}")
        if person.current_role:
            role = person.current_role
            if person.current_company:
                role += f" at {person.current_company}"
            parts.append(f"Role: {role}")
        if person.headline:
            parts.append(f"Headline: {person.headline}")
        if person.location:
            parts.append(f"Location: {person.location}")

        if person.bio:
            parts.append(f"\nBio: {person.bio[:500]}")

        if person.skills:
            parts.append(f"\nSkills: {', '.join(person.skills[:20])}")

        # Experience
        if person.experience:
            exp_lines = ["\nExperience:"]
            for exp in person.experience[:5]:
                line = f"- {exp.title}"
                if exp.company:
                    line += f" at {exp.company}"
                if exp.dates:
                    line += f" ({exp.dates})"
                exp_lines.append(line)
            parts.append("\n".join(exp_lines))

        # Recent LinkedIn (sorted oldest→newest for progression visibility)
        if person.linkedin_posts:
            sorted_posts = sorted(
                person.linkedin_posts,
                key=lambda p: p.date or '',
            )
            post_lines = ["\nRecent LinkedIn Posts (chronological):"]
            for post in sorted_posts[-5:]:
                date_prefix = f"[{post.date}] " if post.date else ""
                post_lines.append(f"- {date_prefix}{post.text[:200]}")
            parts.append("\n".join(post_lines))

        # Recent Tweets (sorted oldest→newest)
        if person.tweets:
            sorted_tweets = sorted(
                person.tweets,
                key=lambda t: t.date or '',
            )
            tweet_lines = ["\nRecent Tweets (chronological):"]
            for tweet in sorted_tweets[-5:]:
                date_prefix = f"[{tweet.date}] " if tweet.date else ""
                tweet_lines.append(f"- {date_prefix}{tweet.text[:200]}")
            parts.append("\n".join(tweet_lines))

        # GitHub
        if person.github_activity:
            repos = person.github_activity.get("pinned_repos", [])
            if repos:
                gh_lines = ["\nGitHub Pinned Repos:"]
                for repo in repos[:4]:
                    gh_lines.append(
                        f"- {repo.get('name', '')}: {repo.get('description', '')} "
                        f"[{repo.get('language', '')}]"
                    )
                parts.append("\n".join(gh_lines))

        # Tags
        if person.tags:
            parts.append(f"\nTags: {', '.join(person.tags)}")

        return "\n".join(parts)


# Singleton
coaching_insight_generator = CoachingInsightGenerator()


# =============================================================================
# Notebook-triggered insight refresh
# =============================================================================

async def refresh_notebook_insights(notebook_id: str):
    """Regenerate coaching insights for all members in a people notebook.

    Called automatically after new sources are ingested into a notebook
    that has people profiles. Uses the current notebook RAG sources
    (reviews, work samples, team requirements, etc.) to enrich insights.

    Skips members with insufficient profile data.
    """
    try:
        from api.people import _load_config, _save_config, _get_config_path

        config_path = _get_config_path(notebook_id)
        if not config_path.exists():
            return  # Not a people notebook — nothing to do

        config = _load_config(notebook_id)
        if not config.members:
            return

        updated = 0
        for member in config.members:
            try:
                insights = await coaching_insight_generator.generate_insights(
                    member, notebook_id=notebook_id
                )
                if insights:
                    member.coaching_insights = insights
                    updated += 1
                    logger.info(
                        f"[CoachingInsights] Refreshed insights for {member.name} "
                        f"(notebook {notebook_id})"
                    )
            except Exception as e:
                logger.warning(
                    f"[CoachingInsights] Failed to refresh {member.name}: {e}"
                )

        if updated:
            _save_config(notebook_id, config)
            logger.info(
                f"[CoachingInsights] Refreshed {updated}/{len(config.members)} "
                f"members for notebook {notebook_id}"
            )

    except Exception as e:
        logger.error(f"[CoachingInsights] Notebook refresh failed: {e}")


# =============================================================================
# Debounced trigger — prevents redundant refreshes when multiple sources
# are uploaded in rapid succession
# =============================================================================

async def check_stale_insights_on_startup():
    """Check all people notebooks for stale coaching insights at app launch.

    Compares the newest source timestamp in each notebook against the
    coaching insights generated_at timestamp. If sources are newer,
    the insights are stale and need regeneration.

    This catches:
    - App crash after upload but before insight refresh completed
    - Documents uploaded before this auto-refresh feature existed
    - Any other scenario where sources and insights are out of sync
    """
    try:
        from config import settings
        from api.people import _get_config_path, _load_config
        from storage.source_store import source_store

        notebooks_dir = settings.data_dir / "notebooks"
        if not notebooks_dir.exists():
            return

        refreshed = 0
        # Pre-load all sources once for the loop below
        all_sources_by_nb = await source_store.list_all()
        
        for notebook_dir in notebooks_dir.iterdir():
            if not notebook_dir.is_dir():
                continue

            notebook_id = notebook_dir.name
            config_path = _get_config_path(notebook_id)
            if not config_path.exists():
                continue  # Not a people notebook

            config = _load_config(notebook_id)
            if not config.members:
                continue

            # Find the newest non-people source in the notebook
            sources = all_sources_by_nb.get(notebook_id, [])
            non_people_sources = [
                s for s in sources
                if s.get("format") not in ("people_profile", "coaching_notes")
                and s.get("status") == "completed"
            ]
            if not non_people_sources:
                continue

            newest_source_time = max(
                s.get("created_at", "") for s in non_people_sources
            )
            if not newest_source_time:
                continue

            # Check if any member has stale or missing insights
            needs_refresh = False
            for member in config.members:
                insights = member.coaching_insights
                if not insights:
                    needs_refresh = True
                    break
                generated_at = insights.get("generated_at", "")
                if not generated_at or generated_at < newest_source_time:
                    needs_refresh = True
                    break

            if needs_refresh:
                logger.info(
                    f"[CoachingInsights] Stale insights detected in notebook "
                    f"{notebook_id} — scheduling refresh"
                )
                await refresh_notebook_insights(notebook_id)
                refreshed += 1

        if refreshed:
            logger.info(
                f"[CoachingInsights] Startup refresh completed: "
                f"{refreshed} notebook(s) updated"
            )

    except Exception as e:
        logger.error(f"[CoachingInsights] Startup stale check failed: {e}")


# =============================================================================
# Debounced trigger — prevents redundant refreshes when multiple sources
# are uploaded in rapid succession
# =============================================================================

_pending_refreshes: Dict[str, asyncio.Task] = {}
_DEBOUNCE_SECONDS = 30  # Wait 30s after last ingestion before refreshing


def schedule_insight_refresh(notebook_id: str):
    """Schedule a debounced insight refresh for a people notebook.

    If called multiple times for the same notebook within DEBOUNCE_SECONDS,
    only the last call triggers the actual refresh. This prevents redundant
    LLM calls when uploading multiple documents in sequence.
    """
    # Cancel any pending refresh for this notebook
    existing = _pending_refreshes.get(notebook_id)
    if existing and not existing.done():
        existing.cancel()

    async def _delayed_refresh():
        try:
            await asyncio.sleep(_DEBOUNCE_SECONDS)
            await refresh_notebook_insights(notebook_id)
        except asyncio.CancelledError:
            pass  # Debounced — a newer trigger replaced us
        except Exception as e:
            logger.error(f"[CoachingInsights] Scheduled refresh failed: {e}")
        finally:
            _pending_refreshes.pop(notebook_id, None)

    _pending_refreshes[notebook_id] = asyncio.create_task(_delayed_refresh())
