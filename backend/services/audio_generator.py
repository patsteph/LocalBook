"""Audio generation service for podcasts"""
import asyncio
import subprocess
import os
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from config import settings
from storage.audio_store import audio_store
from storage.source_store import source_store
from storage.skills_store import skills_store
from services.rag_engine import rag_service

# Dedicated thread pool for audio generation (max 2 concurrent)
_audio_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio_gen")


class AudioGenerator:
    """Generate podcast audio from notebooks"""

    def __init__(self):
        self.audio_dir = settings.data_dir / "audio"
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._background_tasks = set()
        
        # Voice mappings for macOS say command - using premium/enhanced voices
        # Multiple voices per gender for variety
        self.voices = {
            "us": {
                "male": ["Evan (Enhanced)", "Tom (Enhanced)", "Alex"],
                "female": ["Ava (Premium)", "Zoe (Premium)", "Samantha"]
            },
            "uk": {
                "male": ["Daniel (Enhanced)"],
                "female": ["Jamie (Premium)"]
            }
        }
        self._voice_index = 0  # Track which voice to use next

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

        script = await rag_service._call_ollama(system_prompt, prompt)
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
        """Background task to generate audio using macOS TTS"""
        import asyncio
        import traceback
        
        print(f"🎤 _generate_audio_async started for {audio_id}")
        
        # Run in dedicated executor to not block the event loop
        loop = asyncio.get_event_loop()
        try:
            print(f"   Running TTS in dedicated executor...")
            final_audio_path = await loop.run_in_executor(
                _audio_executor, 
                lambda: self._generate_audio_sync(
                    audio_id, script, host1_gender, host2_gender, accent, is_two_host
                )
            )
            print(f"   Executor returned: {final_audio_path}")
            
            # Check for any audio file that was created
            # Handle both relative and absolute paths
            final_audio = None
            if final_audio_path:
                audio_path = Path(final_audio_path)
                if not audio_path.is_absolute():
                    # Convert relative path to absolute using audio_dir parent
                    audio_path = settings.data_dir.parent / final_audio_path
                if audio_path.exists():
                    final_audio = audio_path
                    print(f"   Found audio at: {final_audio}")
            
            # Fallback: look for any audio file with this ID
            if not final_audio:
                print(f"   Looking for fallback audio files...")
                for ext in ['.aiff', '.mp3', '.m4a']:
                    candidate = self.audio_dir / f"{audio_id}{ext}"
                    if candidate.exists():
                        final_audio = candidate
                        print(f"   Found fallback: {final_audio}")
                        break
            
            duration_seconds = self._get_audio_duration(final_audio) if final_audio and final_audio.exists() else 0
            
            await audio_store.update(audio_id, {
                "status": "completed",
                "audio_file_path": str(final_audio) if final_audio and final_audio.exists() else None,
                "duration_seconds": duration_seconds
            })
            print(f"✅ Audio generated: {audio_id} -> {final_audio}")
            
        except Exception as e:
            print(f"❌ Audio generation failed: {e}")
            traceback.print_exc()
            await audio_store.update(audio_id, {
                "status": "failed",
                "error_message": str(e)
            })

    def _generate_audio_sync(
        self,
        audio_id: str,
        script: str,
        host1_gender: str,
        host2_gender: str,
        accent: str,
        is_two_host: bool = True
    ) -> str:
        """Synchronous audio generation - runs in thread pool. Returns path to final audio file."""
        import tempfile
        
        # Use absolute path for audio directory
        audio_dir = self.audio_dir.resolve()
        print(f"🎙️ Starting audio generation for {audio_id}")
        print(f"   Audio dir: {audio_dir}")
        print(f"   Script length: {len(script)} chars")
        
        try:
            # Limit script length for faster generation
            max_script_length = 5000  # ~3-4 minutes of audio
            if len(script) > max_script_length:
                script = script[:max_script_length] + "... [Content truncated]"
                print(f"   Script truncated to {max_script_length} chars")
            
            # Get voice name - rotate through available voices
            voice_list = self.voices.get(accent, self.voices["us"]).get(host1_gender, ["Alex"])
            if isinstance(voice_list, str):
                voice_list = [voice_list]
            host1_voice = voice_list[self._voice_index % len(voice_list)]
            self._voice_index += 1  # Rotate for next generation
            print(f"   Using voice: {host1_voice}")
            
            # Generate single audio file directly (simpler and faster)
            output_file = audio_dir / f"{audio_id}.aiff"
            print(f"   Output file: {output_file}")
            
            # Write script to temp file to avoid shell escaping issues
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                f.write(script)
                script_file = f.name
            
            try:
                # Use macOS say command with file input
                result = subprocess.run(
                    ["say", "-v", host1_voice, "-o", str(output_file), "-f", script_file],
                    check=True,
                    timeout=120,  # 2 min timeout for longer scripts
                    capture_output=True
                )
                
                # Clean up temp file
                os.unlink(script_file)
                
                if output_file.exists():
                    size = output_file.stat().st_size
                    print(f"   ✓ AIFF created: {output_file} ({size} bytes)")
                    
                    # Convert to M4A using macOS built-in afconvert (more reliable than ffmpeg)
                    m4a_file = audio_dir / f"{audio_id}.m4a"
                    try:
                        subprocess.run(
                            ["afconvert", "-f", "mp4f", "-d", "aac ",
                             str(output_file), str(m4a_file)],
                            check=True,
                            timeout=60,
                            capture_output=True
                        )
                        # Remove the AIFF file
                        output_file.unlink()
                        print(f"   ✓ Converted to M4A: {m4a_file}")
                        return str(m4a_file)
                    except Exception as e:
                        print(f"   ✗ Conversion failed: {e}")
                        # Return AIFF as fallback
                        return str(output_file)
                else:
                    print(f"   ✗ Audio file not created")
                    return None
                    
            except subprocess.TimeoutExpired:
                print(f"   ✗ TTS timeout after 120s")
                os.unlink(script_file)
                return None
            except subprocess.CalledProcessError as e:
                print(f"   ✗ TTS error: {e.stderr.decode() if e.stderr else e}")
                os.unlink(script_file)
                return None

        except Exception as e:
            print(f"   ✗ Error in audio generation: {e}")
            import traceback
            traceback.print_exc()
            return None

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
