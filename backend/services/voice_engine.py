import logging
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

from storage.database import get_db
from services.ollama_service import ollama_service

logger = logging.getLogger(__name__)

class VoiceEngine:
    def add_observation(self, text_sample: str, source_type: str, voice_weight: float, notebook_id: Optional[str] = None, source_note_id: Optional[str] = None):
        """Add a text sample to the voice observations table."""
        if not text_sample or len(text_sample.split()) < 5:
            # Ignore very short samples
            return

        db = get_db()
        conn = db.get_connection()
        cursor = conn.cursor()
        
        word_count = len(text_sample.split())
        created_at = datetime.utcnow().isoformat() + "Z"

        if source_note_id:
            cursor.execute("SELECT id FROM voice_observations WHERE source_note_id = ?", (source_note_id,))
            row = cursor.fetchone()
            if row:
                cursor.execute("""
                    UPDATE voice_observations 
                    SET text_sample = ?, source_type = ?, voice_weight = ?, word_count = ?, created_at = ?
                    WHERE id = ?
                """, (text_sample, source_type, voice_weight, word_count, created_at, row['id']))
            else:
                cursor.execute("""
                    INSERT INTO voice_observations 
                    (text_sample, source_type, voice_weight, word_count, notebook_id, source_note_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (text_sample, source_type, voice_weight, word_count, notebook_id, source_note_id, created_at))
        else:
            cursor.execute("""
                INSERT INTO voice_observations 
                (text_sample, source_type, voice_weight, word_count, notebook_id, source_note_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (text_sample, source_type, voice_weight, word_count, notebook_id, source_note_id, created_at))
        
        conn.commit()
        logger.info(f"Added voice observation ({word_count} words, weight {voice_weight}) from {source_type}")

    async def maybe_rebuild_profile(self) -> bool:
        """Analyze recent observations to rebuild the voice profile if enough new data exists."""
        db = get_db()
        conn = db.get_connection()
        cursor = conn.cursor()

        # Check current profile state
        cursor.execute("SELECT sample_count, rebuild_version FROM voice_profile WHERE id = 1")
        row = cursor.fetchone()
        
        current_sample_count = row['sample_count'] if row else 0
        rebuild_version = row['rebuild_version'] if row else 0

        # Count total valid observations (e.g. recent or highest weight)
        cursor.execute("SELECT COUNT(*) as count FROM voice_observations")
        total_obs = cursor.fetchone()['count']

        # Only rebuild if we have at least 5 new observations since last rebuild
        if total_obs < current_sample_count + 5 and current_sample_count > 0:
            return False

        logger.info(f"Rebuilding voice profile (Total obs: {total_obs}, Prev: {current_sample_count})")

        # Fetch top observations weighted by voice_weight and recency
        cursor.execute("""
            SELECT text_sample, source_type, voice_weight 
            FROM voice_observations 
            ORDER BY voice_weight DESC, created_at DESC 
            LIMIT 20
        """)
        observations = cursor.fetchall()
        
        if not observations:
            return False

        samples_text = ""
        for i, obs in enumerate(observations):
            samples_text += f"\n--- Sample {i+1} (Type: {obs['source_type']}, Weight: {obs['voice_weight']}) ---\n"
            samples_text += obs['text_sample'][:500] + "...\n"

        prompt = f"""You are an expert linguist and communication analyst.
Analyze the following text samples written by a user and build a comprehensive JSON profile of their "Voice".
Extract the following:
1. "vocabulary": A brief description of their word choice (e.g., academic, conversational, technical).
2. "style_markers": Specific phrasing habits, sentence lengths, or punctuation tendencies.
3. "thinking_framework": How they structure their thoughts (e.g., analytical, narrative, list-heavy).
4. "formality": The level of formality in their writing.
5. "interests": Any recurring topics or themes in these samples.

Output MUST be a valid JSON object matching this schema:
{{
  "vocabulary": "string",
  "style_markers": "string",
  "thinking_framework": "string",
  "formality": "string",
  "interests": ["string", "string"]
}}

SAMPLES:
{samples_text}
"""

        try:
            result = await ollama_service.generate(
                prompt=prompt,
                model="phi4-mini:latest",
                system="You output strictly valid JSON with no markdown formatting or explanation.",
                temperature=0.1
            )
            
            response_text = result.get("response", "").strip()
            # Clean up potential markdown formatting
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            
            profile_json = response_text.strip()
            
            # Validate JSON
            json.loads(profile_json)
            
            # Save to DB
            now = datetime.utcnow().isoformat() + "Z"
            cursor.execute("""
                INSERT INTO voice_profile (id, profile_json, sample_count, last_rebuilt, rebuild_version)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET 
                    profile_json = excluded.profile_json,
                    sample_count = excluded.sample_count,
                    last_rebuilt = excluded.last_rebuilt,
                    rebuild_version = excluded.rebuild_version
            """, (profile_json, total_obs, now, rebuild_version + 1))
            
            conn.commit()
            logger.info("Successfully rebuilt and saved voice profile.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to rebuild voice profile: {e}")
            return False

    def get_profile(self) -> Optional[Dict[str, Any]]:
        """Retrieve the current voice profile."""
        db = get_db()
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT profile_json FROM voice_profile WHERE id = 1")
        row = cursor.fetchone()
        
        if row and row['profile_json']:
            try:
                return json.loads(row['profile_json'])
            except:
                pass
        return None

voice_engine = VoiceEngine()
