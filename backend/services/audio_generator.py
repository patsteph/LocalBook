"""Audio generation service for podcasts

Uses LFM2.5-Audio (Liquid AI) for high-quality text-to-speech generation.
This provides phenomenal audio quality with native speech synthesis.
"""
import asyncio
import subprocess
from pathlib import Path
from typing import Dict, List, Optional
from config import settings
from storage.audio_store import audio_store
from storage.source_store import source_store
from storage.skills_store import skills_store
from services.rag_engine import rag_engine

class AudioGenerator:
    """Generate podcast audio from notebooks using LFM2.5-Audio"""

    def __init__(self):
        self.audio_dir = settings.data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._background_tasks = set()
        
        # Voice options for LFM2.5-Audio - rotate through for variety
        # Available voices: us_male, us_female, uk_male, uk_female
        self.voices = {
            "us": {
                "male": "us_male",
                "female": "us_female"
            },
            "uk": {
                "male": "uk_male", 
                "female": "uk_female"
            }
        }
        
        self._voice_index = 0  # Track voice rotation for variety

    async def generate(
        self,
        notebook_id: str,
        topic: Optional[str] = None,
        duration_minutes: int = 10,
        skill_id: Optional[str] = None,
        host1_gender: str = "male",
        host2_gender: str = "female",
        accent: str = "us"
    ) -> Dict:
        """Generate podcast audio"""

        # Generate script first
        script = await self._generate_script(
            notebook_id=notebook_id,
            topic=topic,
            duration_minutes=duration_minutes,
            skill_id=skill_id
        )

        # Create audio record
        generation = await audio_store.create(
            notebook_id=notebook_id,
            script=script,
            topic=topic or "the research content",
            duration_minutes=duration_minutes,
            host1_gender=host1_gender,
            host2_gender=host2_gender,
            accent=accent,
            skill_id=skill_id
        )

        # Update status to processing
        await audio_store.update(generation["audio_id"], {"status": "processing"})

        # Determine if two-host or single narrator
        is_two_host = skill_id in ["podcast_script", "debate"]

        # Start audio generation in background
        print(f"🎬 Starting background audio task for {generation['audio_id']}")
        task = asyncio.create_task(
            self._generate_audio_async(
                audio_id=generation["audio_id"],
                script=script,
                host1_gender=host1_gender,
                host2_gender=host2_gender,
                accent=accent,
                is_two_host=is_two_host
            )
        )
        # Keep reference to prevent garbage collection
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return generation

    async def _generate_script(
        self,
        notebook_id: str,
        topic: Optional[str],
        duration_minutes: int,
        skill_id: Optional[str]
    ) -> str:
        """Generate content from notebook based on skill type"""
        
        # Get sources content
        sources = await source_store.list(notebook_id)
        content_parts = []
        
        for source in sources[:5]:  # Limit to first 5 sources
            source_content = await source_store.get_content(notebook_id, source["id"])
            if source_content and source_content.get("content"):
                content_parts.append(f"Source: {source.get('filename', 'Unknown')}\n{source_content['content'][:3000]}")
        
        context = "\n\n---\n\n".join(content_parts)
        
        # Get skill info
        skill = await skills_store.get(skill_id) if skill_id else None
        skill_name = skill.get("name", "Content") if skill else "Podcast"
        skill_prompt = skill.get("system_prompt", "") if skill else ""
        
        # Determine if this is a two-host format or single narrator
        two_host_skills = ["podcast_script", "debate"]
        is_two_host = skill_id in two_host_skills
        
        if is_two_host:
            # Podcast/debate format with two speakers
            if not skill_prompt:
                skill_prompt = """Create an engaging conversational podcast script between two hosts (Host A and Host B) 
discussing the research content. Include natural dialogue, questions, insights, and occasional humor."""
            
            system_prompt = f"""{skill_prompt}

Format with clear speaker labels:
Host A: [dialogue]
Host B: [dialogue]

Target length: approximately {duration_minutes} minutes when read aloud.
Focus on: {topic or 'the main topics and insights from the research'}"""

            prompt = f"""Based on the following research content, create a podcast script:

{context[:8000]}

Generate an engaging {duration_minutes}-minute podcast script:"""
        else:
            # Single narrator audio format (summary, study guide, FAQ, etc.)
            system_prompt = f"""{skill_prompt}

IMPORTANT: Format this as a spoken audio script for a single narrator.
- Write in a conversational, engaging tone suitable for listening
- Use natural transitions between sections
- Avoid bullet points or visual formatting - write in flowing paragraphs
- Target length: approximately {duration_minutes} minutes when read aloud
Focus on: {topic or 'the main topics and insights'}"""

            prompt = f"""Based on the following research content, create an audio script:

{context[:8000]}

Generate a {skill_name} formatted as a {duration_minutes}-minute audio narration:"""

        script = await rag_engine._call_ollama(system_prompt, prompt)
        return script
    
    def _is_audio_skill(self, skill_id: Optional[str]) -> bool:
        """Check if skill produces audio output - all skills produce audio"""
        return True

    async def _generate_audio_async(
        self,
        audio_id: str,
        script: str,
        host1_gender: str,
        host2_gender: str,
        accent: str,
        is_two_host: bool = True
    ):
        """Background task to generate audio using LFM2.5-Audio"""
        import traceback
        from services.audio_llm import audio_llm
        
        print(f"🎤 Starting LFM2.5-Audio generation for {audio_id}")
        
        try:
            # Initialize audio model if needed
            await audio_llm.initialize()
            
            if not audio_llm.is_available:
                raise RuntimeError("LFM2.5-Audio model not available. Please install: pip install liquid-audio torchaudio")
            
            # Limit script length for reasonable generation time
            max_script_length = 5000  # ~3-4 minutes of audio
            if len(script) > max_script_length:
                script = script[:max_script_length] + "... [Content truncated]"
                print(f"   Script truncated to {max_script_length} chars")
            
            # Get voice based on gender and accent
            voice = self.voices.get(accent, self.voices["us"]).get(host1_gender, "us_male")
            print(f"   Using voice: {voice}")
            
            # Generate audio
            output_path = self.audio_dir / f"{audio_id}.wav"
            
            audio_path = await audio_llm.text_to_speech(
                text=script,
                voice=voice,
                output_path=str(output_path)
            )
            
            if audio_path and Path(audio_path).exists():
                duration_seconds = self._get_audio_duration(Path(audio_path))
                
                await audio_store.update(audio_id, {
                    "status": "completed",
                    "audio_file_path": audio_path,
                    "duration_seconds": duration_seconds
                })
                print(f"✅ Audio generated: {audio_id} -> {audio_path}")
            else:
                raise RuntimeError("Audio file was not created")
            
        except Exception as e:
            print(f"❌ Audio generation failed: {e}")
            traceback.print_exc()
            await audio_store.update(audio_id, {
                "status": "failed",
                "error_message": str(e)
            })

    def _parse_script(self, script: str) -> List[tuple]:
        """Parse script into speaker segments"""
        segments = []
        current_speaker = "A"
        current_text = []
        
        for line in script.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            # Check for speaker labels
            if line.lower().startswith('host a:') or line.lower().startswith('speaker a:'):
                if current_text:
                    segments.append((current_speaker, ' '.join(current_text)))
                current_speaker = "A"
                current_text = [line.split(':', 1)[1].strip()] if ':' in line else []
            elif line.lower().startswith('host b:') or line.lower().startswith('speaker b:'):
                if current_text:
                    segments.append((current_speaker, ' '.join(current_text)))
                current_speaker = "B"
                current_text = [line.split(':', 1)[1].strip()] if ':' in line else []
            else:
                current_text.append(line)
        
        # Add final segment
        if current_text:
            segments.append((current_speaker, ' '.join(current_text)))
        
        # If no segments parsed, treat whole script as one segment
        if not segments:
            segments = [("A", script)]
        
        return segments

    def _split_script_into_chunks(self, script: str, max_chars: int = 2000) -> List[str]:
        """Split script into chunks for TTS processing"""
        chunks = []
        paragraphs = script.split('\n\n')
        current_chunk = ""
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            # If adding this paragraph exceeds max, save current and start new
            if len(current_chunk) + len(para) > max_chars and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = para
            else:
                current_chunk += "\n\n" + para if current_chunk else para
        
        # Add final chunk
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        # If no chunks, return whole script as one chunk
        if not chunks:
            chunks = [script]
        
        return chunks

    def _get_audio_duration(self, audio_file: Path) -> int:
        """Get audio duration in seconds"""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_file)],
                capture_output=True,
                text=True
            )
            return int(float(result.stdout.strip()))
        except:
            return 0

    async def list(self, notebook_id: str) -> List[Dict]:
        """List audio files for a notebook"""
        return await audio_store.list(notebook_id)

    async def get(self, notebook_id: str, audio_id: str) -> Optional[Dict]:
        """Get audio file info"""
        return await audio_store.get_by_notebook(notebook_id, audio_id)
    
    async def get_by_id(self, audio_id: str) -> Optional[Dict]:
        """Get audio file info by ID only"""
        return await audio_store.get(audio_id)
    
    async def delete(self, audio_id: str) -> bool:
        """Delete audio generation record"""
        return await audio_store.delete(audio_id)


audio_service = AudioGenerator()
