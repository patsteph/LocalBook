"""Skills storage"""
import json
import uuid
from typing import List, Optional, Dict
from config import settings
from utils.json_io import atomic_write_json

class SkillsStore:
    def __init__(self):
        self.storage_path = settings.data_dir / "skills.json"
        self._ensure_storage()

    def _ensure_storage(self):
        """Ensure storage file exists with default skills, and add any missing builtins"""
        if self.storage_path.exists():
            # Migrate: add any missing built-in skills to existing file
            self._add_missing_builtins()
            return
        
        if not self.storage_path.exists():
            default_skills = {
                "skills": {
                    "podcast_script": {
                        "skill_id": "podcast_script",
                        "name": "Podcast Script",
                        "system_prompt": "Create an engaging conversational podcast script between two hosts discussing the research content. Include natural dialogue, questions, and insights.",
                        "description": "Generate a two-host podcast conversation",
                        "is_builtin": True
                    },
                    "summary": {
                        "skill_id": "summary",
                        "name": "Summary",
                        "system_prompt": "Provide a concise summary of the key points from the documents.",
                        "description": "Summarize the main ideas",
                        "is_builtin": True
                    },
                    "deep_dive": {
                        "skill_id": "deep_dive",
                        "name": "Deep Dive",
                        "system_prompt": "Create an in-depth analysis and exploration of the topic, covering nuances, implications, and connections between ideas.",
                        "description": "In-depth topic exploration",
                        "is_builtin": True
                    },
                    "explain": {
                        "skill_id": "explain",
                        "name": "Explain Like I'm 5",
                        "system_prompt": "Explain this concept in simple terms that anyone can understand, using analogies and everyday examples.",
                        "description": "Simple explanations for complex topics",
                        "is_builtin": True
                    },
                    "debate": {
                        "skill_id": "debate",
                        "name": "Debate",
                        "system_prompt": "Create a balanced debate script where two hosts take opposing viewpoints on the topic, presenting arguments and counterarguments.",
                        "description": "Explore multiple perspectives through debate",
                        "is_builtin": True
                    },
                    "study_guide": {
                        "skill_id": "study_guide",
                        "name": "Study Guide",
                        "system_prompt": "Create a comprehensive study guide with key concepts, definitions, important facts, and review questions. Organize by topic with clear headings.",
                        "description": "Generate study materials and review questions",
                        "is_builtin": True
                    },
                    "faq": {
                        "skill_id": "faq",
                        "name": "FAQ",
                        "system_prompt": "Generate a list of frequently asked questions and detailed answers based on the content. Include both basic and advanced questions.",
                        "description": "Create Q&A from the content",
                        "is_builtin": True
                    },
                    "briefing": {
                        "skill_id": "briefing",
                        "name": "Executive Briefing",
                        "system_prompt": "Create a concise executive briefing document with key findings, implications, and recommended actions. Use bullet points and clear sections.",
                        "description": "Professional briefing document",
                        "is_builtin": True
                    },
                    "feynman_curriculum": {
                        "skill_id": "feynman_curriculum",
                        "name": "Feynman Learning Curriculum",
                        "system_prompt": "Create a multi-part learning curriculum using the Feynman Technique. Progress from novice to near-expert in 4 parts: Foundation (explain to a 12-year-old), Building Understanding (connections and examples), First Principles (why things work), and Mastery Synthesis (teach it back). Include self-assessments at each level.",
                        "description": "Novice-to-expert learning path using the Feynman method",
                        "is_builtin": True
                    }
                }
            }
            self._save_data(default_skills)

    # All built-in skill IDs — used for migration
    BUILTIN_SKILLS = {
        "podcast_script": ("Podcast Script", "Create an engaging conversational podcast script between two hosts discussing the research content. Include natural dialogue, questions, and insights.", "Generate a two-host podcast conversation"),
        "summary": ("Summary", "Provide a concise summary of the key points from the documents.", "Summarize the main ideas"),
        "deep_dive": ("Deep Dive", "Create an in-depth analysis and exploration of the topic, covering nuances, implications, and connections between ideas.", "In-depth topic exploration"),
        "explain": ("Explain Like I'm 5", "Explain this concept in simple terms that anyone can understand, using analogies and everyday examples.", "Simple explanations for complex topics"),
        "debate": ("Debate", "Create a balanced debate script where two hosts take opposing viewpoints on the topic, presenting arguments and counterarguments.", "Explore multiple perspectives through debate"),
        "study_guide": ("Study Guide", "Create a comprehensive study guide with key concepts, definitions, important facts, and review questions. Organize by topic with clear headings.", "Generate study materials and review questions"),
        "faq": ("FAQ", "Generate a list of frequently asked questions and detailed answers based on the content. Include both basic and advanced questions.", "Create Q&A from the content"),
        "briefing": ("Executive Briefing", "Create a concise executive briefing document with key findings, implications, and recommended actions. Use bullet points and clear sections.", "Professional briefing document"),
        "feynman_curriculum": ("Feynman Learning Curriculum", "Create a multi-part learning curriculum using the Feynman Technique. Progress from novice to near-expert in 4 parts: Foundation (explain to a 12-year-old), Building Understanding (connections and examples), First Principles (why things work), and Mastery Synthesis (teach it back). Include self-assessments at each level.", "Novice-to-expert learning path using the Feynman method"),
    }

    def _add_missing_builtins(self):
        """Add any missing built-in skills to existing storage"""
        try:
            data = self._load_data()
            existing = data.get("skills", {})
            added = []
            for skill_id, (name, prompt, desc) in self.BUILTIN_SKILLS.items():
                if skill_id not in existing:
                    existing[skill_id] = {
                        "skill_id": skill_id,
                        "name": name,
                        "system_prompt": prompt,
                        "description": desc,
                        "is_builtin": True
                    }
                    added.append(skill_id)
            if added:
                data["skills"] = existing
                self._save_data(data)
                print(f"[Skills] Added missing built-in skills: {', '.join(added)}")
        except Exception as e:
            print(f"[Skills] Migration check failed: {e}")

    def _load_data(self) -> dict:
        """Load skills from storage"""
        if not self.storage_path.exists():
            self._ensure_storage()
        with open(self.storage_path, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        """Save skills to storage"""
        atomic_write_json(self.storage_path, data)

    async def list(self) -> List[Dict]:
        """List all skills"""
        data = self._load_data()
        return list(data["skills"].values())

    async def create(self, name: str, system_prompt: str, description: Optional[str] = None) -> Dict:
        """Create a new skill"""
        skill_id = str(uuid.uuid4())

        skill = {
            "skill_id": skill_id,
            "name": name,
            "system_prompt": system_prompt,
            "description": description,
            "is_builtin": False
        }

        data = self._load_data()
        data["skills"][skill_id] = skill
        self._save_data(data)

        return skill

    async def get(self, skill_id: str) -> Optional[Dict]:
        """Get a skill by ID"""
        data = self._load_data()
        return data["skills"].get(skill_id)

skills_store = SkillsStore()
