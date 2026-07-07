"""CuratorConfigMixin — extracted from the former agents/curator.py (Wave 3 split)."""
from ._models import *  # noqa: F401,F403


class CuratorConfigMixin:
    DEFAULT_CONFIG = {
        "name": "Curator",
        "personality": "thorough, slightly formal, always cites sources",
        "oversight": {
            "auto_approve_threshold": 0.85,
            "require_approval_for": ["new_source_types", "large_batches", "low_confidence"]
        },
        "synthesis": {
            "proactive_insights": True,
            "insight_frequency": "daily"
        },
        # Curator Phase 6a (2026-05-13): narrative voice for written
        # output (morning brief, weekly wrap, drafts). One of the
        # named voices in VOICE_PROMPTS below. Separate from
        # `personality` which is the free-text persona descriptor.
        "narrative_voice": "conversational_analyst",
        "voice": {
            "style": "professional",
            "verbosity": "concise"
        }
    }

    def __init__(self):
        self.config = self._load_config()
        self.name = self.config.get("name", "Curator")
        self.personality = self.config.get("personality", "helpful and thorough")
        # Curator Phase 6a: narrative voice for written output. Falls back
        # to default when missing or invalid.
        _vc = self.config.get("narrative_voice")
        self.narrative_voice = _vc if _vc in VALID_VOICES else DEFAULT_VOICE
        # Insights moved to curator_brain.db as of 2026-05-12 (Phase 1).
        # Brain handles legacy curator_insights.json migration on first init.
        # Master scheduler lock — ensures only ONE notebook collection runs at a time.
        # Prevents Ollama contention when multiple notebooks are due simultaneously.
        self._collection_lock = asyncio.Lock()
        self._active_collection: Optional[str] = None  # notebook_id currently collecting

        # Weekly-wrap single-flight guards. Prevents two concurrent generations
        # (e.g. chat-triggered + scheduler-triggered) from racing and the second
        # empty/shorter wrap overwriting the first one the user was reading.
        self._weekly_wrap_lock = asyncio.Lock()
        self._weekly_wrap_cache: Optional[WeeklyWrapUp] = None
        self._weekly_wrap_cached_at: Optional[datetime] = None
        # Same treatment for morning briefs — same failure mode exists there.
        self._morning_brief_lock = asyncio.Lock()
        self._morning_brief_cache: Optional[MorningBrief] = None
        self._morning_brief_cached_at: Optional[datetime] = None

    def _get_config_path(self) -> Path:
        """Get path to curator config file"""
        return Path(settings.data_dir) / "curator_config.yaml"

    def _load_config(self) -> Dict[str, Any]:
        """Load curator configuration from YAML file"""
        config_path = self._get_config_path()
        
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
                    if config:
                        return {**self.DEFAULT_CONFIG, **config}
            except Exception as e:
                logger.error(f"Error loading curator config: {e}")
        
        # Save default config if none exists
        self._save_config(self.DEFAULT_CONFIG)
        return self.DEFAULT_CONFIG

    def _save_config(self, config: Dict[str, Any]) -> None:
        """Save curator configuration to YAML file"""
        config_path = self._get_config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update curator configuration"""
        # Curator Phase 6a: validate narrative_voice before persisting.
        # Silently coerce invalid values to default so a bad set_voice
        # call doesn't break the curator.
        if "narrative_voice" in updates:
            requested = updates["narrative_voice"]
            if requested not in VALID_VOICES:
                logger.warning(
                    f"[curator] update_config: invalid narrative_voice "
                    f"{requested!r}; coercing to default ({DEFAULT_VOICE})"
                )
                updates["narrative_voice"] = DEFAULT_VOICE

        self.config.update(updates)
        self._save_config(self.config)

        # Update instance attributes
        if "name" in updates:
            self.name = updates["name"]
        if "personality" in updates:
            self.personality = updates["personality"]
        if "narrative_voice" in updates:
            self.narrative_voice = updates["narrative_voice"]

        return self.config

    def get_config(self) -> Dict[str, Any]:
        """Get current curator configuration"""
        return self.config

    def _get_user_timezone(self) -> str:
        """Return the user's configured timezone, defaulting to America/Chicago.

        Set via @curator set timezone <tz> or directly in curator_config.yaml.
        Stored as config key 'timezone'.
        """
        return self.config.get("timezone", "America/Chicago")

    async def generate_setup_followup(self, notebook_id: str) -> Optional[str]:
        """
        After initial template setup, generate a contextual follow-up suggestion.
        Called once after config is saved. Returns a short message or None.
        """
        from agents.collector import get_collector
        
        try:
            collector = get_collector(notebook_id)
            config = collector.get_config()
            
            if not config.intent:
                return None
            
            prompt = f"""You are {self.name}, helping set up a research notebook.
The user just configured:
- Subject: {config.subject}
- Intent: {config.intent}
- Focus areas: {', '.join(config.focus_areas or [])}

Generate ONE helpful follow-up question or suggestion. Examples:
- "Any specific competitors to watch alongside [subject]?"
- "Want me to focus on recent news or go back further?"
- "I noticed you're tracking financials — should I watch SEC filings too?"

Keep it conversational and short (1-2 sentences). Return just the message, no JSON."""

            response = await ollama_service.generate(
                prompt=prompt,
                system=f"You are {self.name}. Personality: {self.personality}",
                model=settings.ollama_fast_model,
                temperature=0.5
            )
            
            from utils.json_repair import sanitize_prose_output
            text = sanitize_prose_output(response.get("response", ""))
            if text and len(text) < 300:
                return text
        except Exception as e:
            logger.debug(f"Setup follow-up generation failed (non-fatal): {e}")
        
        return None

    async def infer_config_from_content(
        self,
        notebook_id: str,
        filenames: List[str],
        sample_content: str
    ) -> Dict[str, Any]:
        """
        Analyze dropped files and suggest Collector configuration.
        Called after files are ingested into a new notebook.
        
        Returns:
            Dict with suggested_subject, suggested_intent, suggested_focus_areas,
            suggested_template, and a curator_message for the user.
        """
        filenames_str = ", ".join(filenames[:10])
        prompt = f"""Analyze these research files and suggest how to configure a research assistant.

Filenames: {filenames_str}
Sample content (first 2000 chars): {sample_content[:2000]}

Respond with JSON only:
{{
    "suggested_subject": "main subject or entity these files are about",
    "suggested_intent": "one paragraph describing what this research covers",
    "suggested_focus_areas": ["area1", "area2", "area3"],
    "suggested_template": "company_intel or industry_watch or topic_research or project_archive",
    "curator_message": "Friendly 2-3 sentence message to the user summarizing what you found and suggesting next steps"
}}"""

        try:
            response = await ollama_service.generate(
                prompt=prompt,
                system=f"You are {self.name}. Analyze research content and suggest configuration. Respond with valid JSON only.",
                model=settings.ollama_fast_model,
                temperature=0.3
            )
            
            text = response.get("response", "")
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(text[json_start:json_end])
                return {
                    "suggested_subject": result.get("suggested_subject", ""),
                    "suggested_intent": result.get("suggested_intent", ""),
                    "suggested_focus_areas": result.get("suggested_focus_areas", []),
                    "suggested_template": result.get("suggested_template", "custom"),
                    "curator_message": result.get("curator_message", ""),
                    "notebook_id": notebook_id
                }
        except Exception as e:
            logger.error(f"Config inference from content failed: {e}")
        
        # Fallback — use filenames to guess
        subject_guess = filenames[0].rsplit(".", 1)[0] if filenames else "Research"
        return {
            "suggested_subject": subject_guess,
            "suggested_intent": f"Research related to {subject_guess}",
            "suggested_focus_areas": [],
            "suggested_template": "custom",
            "curator_message": f"I see you've added some files. Want me to set up a Collector to find related content?",
            "notebook_id": notebook_id
        }
