"""Profile Indexer — converts PersonProfile data into RAG-indexable text.

After social collection, this service:
1. Converts a PersonProfile into a structured text document
2. Deletes any previously-indexed source for this person
3. Ingests the new text into the notebook's LanceDB via rag_engine
4. Entity extraction + relationship mapping happen automatically (in rag_engine)

Source type: "people_profile" — allows custom chunking in the future.
"""
import logging
from typing import Optional, Dict, List
from datetime import datetime

from models.person_profile import PersonProfile, CoachingNote, CoachingGoal
from storage.source_store import source_store
from services.rag_engine import rag_engine

logger = logging.getLogger(__name__)


class ProfileIndexer:
    """Converts PersonProfile data into text and ingests into the RAG system."""

    # =========================================================================
    # Public API
    # =========================================================================

    async def index_person(
        self,
        notebook_id: str,
        person: PersonProfile,
    ) -> Optional[Dict]:
        """Index (or re-index) a person's profile into the notebook RAG store.

        Returns the ingest result dict on success, None on failure.
        """
        text = self._profile_to_text(person)
        if not text or len(text.strip()) < 20:
            logger.info(f"[ProfileIndexer] Skipping {person.name} — insufficient data")
            return None

        source_id = self._source_id_for(person)
        filename = f"People Profile — {person.name}"

        # Delete previous version (if any) so we don't accumulate stale data
        await self._delete_previous(notebook_id, source_id)

        # Create source record
        source = await source_store.create(
            notebook_id=notebook_id,
            filename=filename,
            metadata={
                "id": source_id,
                "type": "people_profile",
                "format": "people_profile",
                "status": "processing",
                "person_id": person.id,
                "person_name": person.name,
            },
        )

        # Ingest into RAG
        try:
            result = await rag_engine.ingest_document(
                notebook_id=notebook_id,
                source_id=source["id"],
                text=text,
                filename=filename,
                source_type="people_profile",
            )

            await source_store.update(notebook_id, source["id"], {
                "chunks": result.get("chunks", 0),
                "characters": result.get("characters", len(text)),
                "status": "completed",
                "content": text,
            })

            logger.info(
                f"[ProfileIndexer] Indexed {person.name}: "
                f"{result.get('chunks', 0)} chunks, {len(text)} chars"
            )

            # Wire into knowledge graph — register person + key entities
            try:
                await self._register_in_knowledge_graph(notebook_id, person, source["id"])
            except Exception as kg_err:
                logger.debug(f"[ProfileIndexer] KG registration non-fatal: {kg_err}")

            return result

        except Exception as e:
            logger.error(f"[ProfileIndexer] Failed to index {person.name}: {e}")
            await source_store.delete(notebook_id, source["id"])
            return None

    async def index_coaching_notes(
        self,
        notebook_id: str,
        person: PersonProfile,
    ) -> Optional[Dict]:
        """Index coaching notes for a person into the RAG system.

        Kept as a separate source so notes can be re-indexed independently.
        """
        if not person.coaching_notes:
            return None

        text = self._notes_to_text(person)
        if not text or len(text.strip()) < 20:
            return None

        source_id = self._notes_source_id_for(person)
        filename = f"Coaching Notes — {person.name}"

        await self._delete_previous(notebook_id, source_id)

        source = await source_store.create(
            notebook_id=notebook_id,
            filename=filename,
            metadata={
                "id": source_id,
                "type": "coaching_notes",
                "format": "coaching_notes",
                "status": "processing",
                "person_id": person.id,
                "person_name": person.name,
            },
        )

        try:
            result = await rag_engine.ingest_document(
                notebook_id=notebook_id,
                source_id=source["id"],
                text=text,
                filename=filename,
                source_type="coaching_notes",
            )

            await source_store.update(notebook_id, source["id"], {
                "chunks": result.get("chunks", 0),
                "characters": result.get("characters", len(text)),
                "status": "completed",
                "content": text,
            })

            logger.info(
                f"[ProfileIndexer] Indexed coaching notes for {person.name}: "
                f"{result.get('chunks', 0)} chunks"
            )
            return result

        except Exception as e:
            logger.error(f"[ProfileIndexer] Failed to index notes for {person.name}: {e}")
            await source_store.delete(notebook_id, source["id"])
            return None

    # =========================================================================
    # Text generation
    # =========================================================================

    def _profile_to_text(self, person: PersonProfile) -> str:
        """Convert a PersonProfile into a structured text document for RAG."""
        sections: List[str] = []

        # Header
        header = f"# Profile: {person.name}\n"
        if person.headline:
            header += f"Headline: {person.headline}\n"
        if person.current_role:
            header += f"Current Role: {person.current_role}"
            if person.current_company:
                header += f" at {person.current_company}"
            header += "\n"
        if person.location:
            header += f"Location: {person.location}\n"
        if person.tags:
            header += f"Tags: {', '.join(person.tags)}\n"
        sections.append(header.strip())

        # Bio
        if person.bio:
            sections.append(f"## About\n{person.bio}")

        # Experience
        if person.experience:
            exp_lines = ["## Work Experience"]
            for exp in person.experience[:10]:
                line = f"- {exp.title}"
                if exp.company:
                    line += f" at {exp.company}"
                if exp.dates:
                    line += f" ({exp.dates})"
                if exp.description:
                    line += f"\n  {exp.description}"
                exp_lines.append(line)
            sections.append("\n".join(exp_lines))

        # Education
        if person.education:
            edu_lines = ["## Education"]
            for edu in person.education:
                line = f"- {edu.degree}"
                if edu.school:
                    line += f", {edu.school}"
                if edu.dates:
                    line += f" ({edu.dates})"
                edu_lines.append(line)
            sections.append("\n".join(edu_lines))

        # Skills
        if person.skills:
            sections.append(f"## Skills\n{', '.join(person.skills)}")

        # LinkedIn posts
        if person.linkedin_posts:
            post_lines = ["## Recent LinkedIn Activity"]
            for post in person.linkedin_posts[:10]:
                text = post.text[:300] if post.text else ""
                date_str = f" ({post.date})" if post.date else ""
                post_lines.append(f"- {text}{date_str}")
            sections.append("\n".join(post_lines))

        # Tweets
        if person.tweets:
            tweet_lines = ["## Recent Tweets"]
            for tweet in person.tweets[:10]:
                text = tweet.text[:280] if tweet.text else ""
                date_str = f" ({tweet.date})" if tweet.date else ""
                tweet_lines.append(f"- {text}{date_str}")
            sections.append("\n".join(tweet_lines))

        # GitHub
        if person.github_activity:
            gh_lines = ["## GitHub Activity"]
            repos = person.github_activity.get("pinned_repos", [])
            for repo in repos[:6]:
                name = repo.get("name", "")
                desc = repo.get("description", "")
                lang = repo.get("language", "")
                gh_lines.append(f"- {name}: {desc} [{lang}]")
            contrib = person.github_activity.get("contributions_text", "")
            if contrib:
                gh_lines.append(f"Contributions: {contrib}")
            sections.append("\n".join(gh_lines))

        # Blog posts
        if person.blog_posts:
            blog_lines = ["## Blog Posts"]
            for post in person.blog_posts[:10]:
                text = post.text[:300] if post.text else ""
                date_str = f" ({post.date})" if post.date else ""
                blog_lines.append(f"- {text}{date_str}")
            sections.append("\n".join(blog_lines))

        # Coaching insights (if already generated)
        if person.coaching_insights:
            if isinstance(person.coaching_insights, str):
                sections.append(f"## Coaching Insights\n{person.coaching_insights}")
            elif isinstance(person.coaching_insights, dict):
                insight_text = "\n".join(
                    f"- {k}: {v}" for k, v in person.coaching_insights.items()
                )
                sections.append(f"## Coaching Insights\n{insight_text}")

        # Goals
        if person.goals:
            goal_lines = ["## Goals"]
            for goal in person.goals:
                status = f" [{goal.status}]" if goal.status else ""
                goal_lines.append(f"- {goal.goal}{status}")
            sections.append("\n".join(goal_lines))

        return "\n\n".join(sections)

    def _notes_to_text(self, person: PersonProfile) -> str:
        """Convert coaching notes into a text document for RAG."""
        if not person.coaching_notes:
            return ""

        sections = [f"# Coaching Notes: {person.name}\n"]

        for note in person.coaching_notes:
            cat = note.category.replace("_", " ").title() if note.category else "General"
            date = note.created_at or ""
            sections.append(f"## [{cat}] — {date}\n{note.text}")

        return "\n\n".join(sections)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _source_id_for(self, person: PersonProfile) -> str:
        """Deterministic source ID for a person's profile source."""
        return f"people-profile-{person.id}"

    def _notes_source_id_for(self, person: PersonProfile) -> str:
        """Deterministic source ID for a person's coaching notes source."""
        return f"people-notes-{person.id}"

    async def _register_in_knowledge_graph(
        self, notebook_id: str, person: PersonProfile, source_id: str
    ):
        """Register a person and their key entities/relationships in the knowledge graph.

        Creates:
        - Person entity
        - Company entities (current + past)
        - Skill entities
        - WORKS_AT / WORKED_AT / HAS_SKILL relationships
        """
        from services.knowledge_graph import KnowledgeGraphService
        kg = KnowledgeGraphService()

        # Register the person as an entity
        person_entity_id = await kg.upsert_entity(
            name=person.name,
            entity_type="PERSON",
            notebook_id=notebook_id,
            aliases=[person.headline] if person.headline else None,
            source_id=source_id,
            metadata={
                "role": person.current_role or "",
                "company": person.current_company or "",
            },
        )
        if not person_entity_id:
            return

        # Register current company + relationship
        if person.current_company:
            company_id = await kg.upsert_entity(
                name=person.current_company,
                entity_type="ORGANIZATION",
                notebook_id=notebook_id,
                source_id=source_id,
            )
            if company_id:
                await kg.add_entity_relationship(
                    source_entity_id=person_entity_id,
                    target_entity_id=company_id,
                    relationship_type="WORKS_AT",
                    notebook_id=notebook_id,
                    value=person.current_role or "",
                    source_id=source_id,
                )

        # Register past companies
        for exp in (person.experience or [])[:5]:
            if exp.company and exp.company != (person.current_company or ""):
                co_id = await kg.upsert_entity(
                    name=exp.company,
                    entity_type="ORGANIZATION",
                    notebook_id=notebook_id,
                    source_id=source_id,
                )
                if co_id:
                    await kg.add_entity_relationship(
                        source_entity_id=person_entity_id,
                        target_entity_id=co_id,
                        relationship_type="WORKED_AT",
                        notebook_id=notebook_id,
                        value=exp.title or "",
                        source_id=source_id,
                        confidence=0.9,
                    )

        # Register top skills as entities + relationships
        for skill in (person.skills or [])[:10]:
            skill_id = await kg.upsert_entity(
                name=skill,
                entity_type="SKILL",
                notebook_id=notebook_id,
                source_id=source_id,
            )
            if skill_id:
                await kg.add_entity_relationship(
                    source_entity_id=person_entity_id,
                    target_entity_id=skill_id,
                    relationship_type="HAS_SKILL",
                    notebook_id=notebook_id,
                    source_id=source_id,
                    confidence=0.85,
                )

        logger.info(
            f"[ProfileIndexer] Registered {person.name} in knowledge graph "
            f"(company={person.current_company}, skills={len(person.skills or [])})"
        )

    async def _delete_previous(self, notebook_id: str, source_id: str):
        """Delete a previously indexed source (by deterministic ID)."""
        try:
            existing = await source_store.get(source_id)
            if existing and existing.get("notebook_id") == notebook_id:
                # Delete from RAG vector store
                try:
                    await rag_engine.delete_source(notebook_id, source_id)
                except Exception:
                    pass  # May not exist in vector store yet
                # Delete source record
                await source_store.delete(notebook_id, source_id)
                logger.debug(f"[ProfileIndexer] Deleted previous source {source_id}")
        except Exception:
            pass  # No previous source


# Singleton
profile_indexer = ProfileIndexer()
