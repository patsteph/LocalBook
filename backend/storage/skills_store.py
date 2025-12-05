"""Skills storage"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
from config import settings

class SkillsStore:
    def __init__(self):
        self.storage_path = settings.data_dir / "skills.json"
        self._ensure_storage()

    def _ensure_storage(self):
        """Ensure storage file exists with default skills"""
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
                    }
                }
            }
            self._save_data(default_skills)

    def _load_data(self) -> dict:
        """Load skills from storage"""
        if not self.storage_path.exists():
            self._ensure_storage()
        with open(self.storage_path, 'r') as f:
            return json.load(f)

    def _save_data(self, data: dict):
        """Save skills to storage"""
        with open(self.storage_path, 'w') as f:
            json.dump(data, f, indent=2)

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
