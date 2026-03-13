"""Kokoro-82M TTS + mlx-whisper ASR Integration Service

Provides TTS, ASR, and speech-to-speech capabilities:
- TTS: Kokoro-82M (82M params, ~350 MB, Apache 2.0)
- ASR: mlx-whisper (Apple Silicon accelerated Whisper)
- S2S: Decomposed ASR → Ollama LLM → Kokoro TTS pipeline

Replaces LFM2.5-Audio-1.5B (1.5B params, ~3.4 GB, restrictive license).
"""

import asyncio
import os
import re
import wave
from pathlib import Path
from typing import Optional, Dict, List
import numpy as np
import uuid

from config import settings


# ─── Kokoro Voice Catalog ─────────────────────────────────────────────────────
# Naming: {lang_code}{gender}_{name}
#   lang_code: a=American, b=British, e=Spanish, f=French, h=Hindi,
#              i=Italian, j=Japanese, p=Portuguese, z=Chinese
#   gender: f=female, m=male

KOKORO_VOICES: Dict[str, Dict] = {
    # ── American English ──────────────────────────────────────────────────
    "af_heart":   {"lang": "a", "gender": "female", "accent": "us", "name": "Heart",   "default": True},
    "af_bella":   {"lang": "a", "gender": "female", "accent": "us", "name": "Bella"},
    "af_nicole":  {"lang": "a", "gender": "female", "accent": "us", "name": "Nicole"},
    "af_sarah":   {"lang": "a", "gender": "female", "accent": "us", "name": "Sarah"},
    "af_sky":     {"lang": "a", "gender": "female", "accent": "us", "name": "Sky"},
    "af_nova":    {"lang": "a", "gender": "female", "accent": "us", "name": "Nova"},
    "am_adam":    {"lang": "a", "gender": "male",   "accent": "us", "name": "Adam",    "default": True},
    "am_michael": {"lang": "a", "gender": "male",   "accent": "us", "name": "Michael"},
    "am_fenrir":  {"lang": "a", "gender": "male",   "accent": "us", "name": "Fenrir"},
    # ── British English ───────────────────────────────────────────────────
    "bf_emma":      {"lang": "b", "gender": "female", "accent": "uk", "name": "Emma",   "default": True},
    "bf_isabella":  {"lang": "b", "gender": "female", "accent": "uk", "name": "Isabella"},
    "bm_george":    {"lang": "b", "gender": "male",   "accent": "uk", "name": "George", "default": True},
    "bm_lewis":     {"lang": "b", "gender": "male",   "accent": "uk", "name": "Lewis"},
    "bm_daniel":    {"lang": "b", "gender": "male",   "accent": "uk", "name": "Daniel"},
    # ── Spanish ───────────────────────────────────────────────────────────
    "ef_dora":      {"lang": "e", "gender": "female", "accent": "es", "name": "Dora",   "default": True},
    "em_alex":      {"lang": "e", "gender": "male",   "accent": "es", "name": "Alex",   "default": True},
    "em_santa":     {"lang": "e", "gender": "male",   "accent": "es", "name": "Santa"},
    # ── French ────────────────────────────────────────────────────────────
    "ff_siwis":     {"lang": "f", "gender": "female", "accent": "fr", "name": "Siwis",  "default": True},
    # ── Hindi ─────────────────────────────────────────────────────────────
    "hf_alpha":     {"lang": "h", "gender": "female", "accent": "hi", "name": "Alpha",  "default": True},
    "hm_omega":     {"lang": "h", "gender": "male",   "accent": "hi", "name": "Omega",  "default": True},
    "hm_psi":       {"lang": "h", "gender": "male",   "accent": "hi", "name": "Psi"},
    "hf_beta":      {"lang": "h", "gender": "female", "accent": "hi", "name": "Beta"},
    # ── Italian ───────────────────────────────────────────────────────────
    "if_sara":      {"lang": "i", "gender": "female", "accent": "it", "name": "Sara",   "default": True},
    "im_nicola":    {"lang": "i", "gender": "male",   "accent": "it", "name": "Nicola", "default": True},
    # ── Japanese ──────────────────────────────────────────────────────────
    "jf_alpha":     {"lang": "j", "gender": "female", "accent": "ja", "name": "Alpha",  "default": True},
    "jm_beta":      {"lang": "j", "gender": "male",   "accent": "ja", "name": "Beta"},
    "jf_gongitsune":{"lang": "j", "gender": "female", "accent": "ja", "name": "Gongitsune"},
    # ── Brazilian Portuguese ──────────────────────────────────────────────
    "pf_dora":      {"lang": "p", "gender": "female", "accent": "pt", "name": "Dora",   "default": True},
    "pm_alex":      {"lang": "p", "gender": "male",   "accent": "pt", "name": "Alex",   "default": True},
    "pm_santa":     {"lang": "p", "gender": "male",   "accent": "pt", "name": "Santa"},
    # ── Mandarin Chinese ──────────────────────────────────────────────────
    "zf_xiaobei":   {"lang": "z", "gender": "female", "accent": "zh", "name": "Xiaobei","default": True},
    "zf_xiaoni":    {"lang": "z", "gender": "female", "accent": "zh", "name": "Xiaoni"},
    "zf_xiaoxiao":  {"lang": "z", "gender": "female", "accent": "zh", "name": "Xiaoxiao"},
    "zm_yunjian":   {"lang": "z", "gender": "male",   "accent": "zh", "name": "Yunjian","default": True},
    "zm_yunxi":     {"lang": "z", "gender": "male",   "accent": "zh", "name": "Yunxi"},
}

# Backward-compatible aliases — map old LFM2.5 voice names to Kokoro defaults
VOICE_ALIASES: Dict[str, str] = {
    "us_male":   "am_adam",
    "us_female": "af_heart",
    "uk_male":   "bm_george",
    "uk_female": "bf_emma",
}

# Language code → display info
SUPPORTED_LANGUAGES: Dict[str, Dict] = {
    "a": {"name": "American English", "code": "en-us"},
    "b": {"name": "British English",  "code": "en-gb"},
    "e": {"name": "Spanish",          "code": "es"},
    "f": {"name": "French",           "code": "fr"},
    "h": {"name": "Hindi",            "code": "hi"},
    "i": {"name": "Italian",          "code": "it"},
    "j": {"name": "Japanese",         "code": "ja"},
    "p": {"name": "Portuguese (BR)",   "code": "pt-br"},
    "z": {"name": "Mandarin Chinese", "code": "zh"},
}

DEFAULT_VOICE = "af_heart"
SAMPLE_RATE = 24_000


def resolve_voice(voice: str) -> str:
    """Resolve a voice name to a Kokoro voice ID.
    
    Accepts Kokoro IDs (e.g. 'af_heart') or legacy aliases ('us_male').
    """
    if voice in KOKORO_VOICES:
        return voice
    if voice in VOICE_ALIASES:
        return VOICE_ALIASES[voice]
    return DEFAULT_VOICE


def get_voices_for_language(lang_code: str) -> Dict[str, Dict]:
    """Return available voices for a language code."""
    return {k: v for k, v in KOKORO_VOICES.items() if v["lang"] == lang_code}


class AudioLLMService:
    """TTS + ASR service backed by Kokoro-82M and mlx-whisper."""
    
    def __init__(self):
        self._pipeline = None       # Kokoro KPipeline
        self._pipeline_lang = None  # Current pipeline lang_code
        self._initialized = False
        self._initializing = False
        self._init_error = None
        
    async def initialize(self, lang_code: str = "a"):
        """Lazy initialization of Kokoro TTS pipeline."""
        if self._initialized and self._pipeline_lang == lang_code:
            return
        if self._initializing:
            return
            
        self._initializing = True
        
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_model, lang_code)
            self._initialized = True
            print(f"[AudioLLM] ✓ Kokoro-82M loaded (lang={lang_code})")
        except ImportError as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[AudioLLM] ⚠ kokoro not installed: {e}")
            self._init_error = tb
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[AudioLLM] ⚠ Failed to load Kokoro: {e}")
            self._init_error = tb
        finally:
            self._initializing = False
    
    @staticmethod
    def _find_kokoro_cache() -> Optional[Path]:
        """Find the Kokoro-82M model in HuggingFace cache.
        
        Returns the snapshot directory path, or None if not cached.
        """
        hf_cache = Path(os.path.expanduser("~/.cache/huggingface/hub"))
        model_dir = hf_cache / "models--hexgrad--Kokoro-82M"
        if not model_dir.exists():
            return None
        refs_main = model_dir / "refs" / "main"
        if refs_main.exists():
            commit = refs_main.read_text().strip()
            snap = model_dir / "snapshots" / commit
            if snap.exists():
                return snap
        # Fallback: pick first snapshot
        snaps = model_dir / "snapshots"
        if snaps.exists():
            for d in sorted(snaps.iterdir()):
                if d.is_dir():
                    return d
        return None
    
    def _load_model(self, lang_code: str = "a"):
        """Load Kokoro-82M pipeline (runs in thread).
        
        Downloads ~350 MB from HuggingFace on first use.
        Once cached (~/.cache/huggingface/hub/), loads locally.
        
        On macOS Python 3.13, SSL cert verification often fails for
        HuggingFace downloads. We bypass Kokoro's internal hf_hub_download
        by loading config + weights from the local cache directly.
        """
        # Ensure macOS system certs are available for HuggingFace downloads
        if not os.environ.get("SSL_CERT_FILE") and os.path.exists("/etc/ssl/cert.pem"):
            os.environ["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
        
        print(f"[AudioLLM] Loading Kokoro-82M (lang={lang_code})...")
        from kokoro import KPipeline
        from kokoro.model import KModel
        import torch
        
        # Try loading from local cache first (bypasses SSL issues on macOS Python 3.13)
        cache_dir = self._find_kokoro_cache()
        if cache_dir and (cache_dir / "config.json").exists() and (cache_dir / "kokoro-v1_0.pth").exists():
            print(f"[AudioLLM]   Loading from cache: {cache_dir}")
            config_path = str(cache_dir / "config.json")
            model_path = str(cache_dir / "kokoro-v1_0.pth")
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"
            print(f"[AudioLLM]   Device: {device}")
            kmodel = KModel(config=config_path, model=model_path).to(device).eval()
            self._pipeline = KPipeline(lang_code=lang_code, model=kmodel)
            # Pre-load cached voice .pt files so KPipeline.load_single_voice
            # doesn't call hf_hub_download (which fails with SSL errors)
            voices_dir = cache_dir / "voices"
            if voices_dir.exists():
                loaded = 0
                for vf in voices_dir.glob("*.pt"):
                    voice_name = vf.stem  # e.g. "af_heart"
                    try:
                        pack = torch.load(str(vf), weights_only=True)
                        self._pipeline.voices[voice_name] = pack
                        loaded += 1
                    except Exception:
                        # PyTorch 2.6+ defaults weights_only=True which fails
                        # on some voice packs. Fall back to weights_only=False
                        # (safe — these are official HuggingFace model files).
                        try:
                            pack = torch.load(str(vf), weights_only=False)
                            self._pipeline.voices[voice_name] = pack
                            loaded += 1
                        except Exception as e2:
                            print(f"[AudioLLM]   Warning: failed to load voice {voice_name}: {e2}")
                print(f"[AudioLLM]   Pre-loaded {loaded} voice(s) from cache")
            # Monkey-patch load_single_voice to use local cache with fallback.
            # Without this, any voice not pre-loaded triggers hf_hub_download
            # which crashes on macOS Python 3.13 due to SSL cert issues.
            _orig_load = self._pipeline.load_single_voice
            _voices_dir = voices_dir
            _pipe = self._pipeline
            def _safe_load_single_voice(voice: str):
                # Already loaded
                if voice in _pipe.voices:
                    return _pipe.voices[voice]
                # Try local cache file first
                if voice.endswith('.pt'):
                    local_path = Path(voice)
                else:
                    local_path = _voices_dir / f"{voice}.pt" if _voices_dir else None
                if local_path and local_path.exists():
                    try:
                        pack = torch.load(str(local_path), weights_only=True)
                    except Exception:
                        pack = torch.load(str(local_path), weights_only=False)
                    _pipe.voices[voice] = pack
                    print(f"[AudioLLM]   Loaded voice from cache: {voice}")
                    return pack
                # Try original method (works if SSL is functional)
                try:
                    return _orig_load(voice)
                except Exception as e:
                    # Fallback: use first available cached voice
                    if _pipe.voices:
                        fallback = next(iter(_pipe.voices))
                        print(f"[AudioLLM]   ⚠ Voice '{voice}' unavailable ({e}), falling back to '{fallback}'")
                        return _pipe.voices[fallback]
                    raise
            self._pipeline.load_single_voice = _safe_load_single_voice
        else:
            # Fallback: let Kokoro download via hf_hub_download
            print(f"[AudioLLM]   No local cache, downloading from HuggingFace...")
            self._pipeline = KPipeline(lang_code=lang_code)
        
        self._pipeline_lang = lang_code
        self._cache_dir = cache_dir
        print(f"[AudioLLM] ✓ Kokoro pipeline ready")
    
    def _ensure_lang(self, voice: str):
        """Switch pipeline language if voice requires it."""
        voice_id = resolve_voice(voice)
        voice_info = KOKORO_VOICES.get(voice_id)
        if voice_info and voice_info["lang"] != self._pipeline_lang:
            new_lang = voice_info["lang"]
            print(f"[AudioLLM] Switching pipeline: {self._pipeline_lang} → {new_lang}")
            self._load_model(new_lang)
    
    @staticmethod
    def _save_wav(path: str, audio_data, sample_rate: int = SAMPLE_RATE):
        """Save audio array as WAV. Accepts numpy arrays or PyTorch tensors."""
        # Convert torch tensor to numpy if needed
        if hasattr(audio_data, 'numpy'):
            audio_data = audio_data.squeeze().cpu().float().numpy()
        # Convert float [-1, 1] to 16-bit PCM
        if audio_data.dtype != np.int16:
            pcm = (audio_data * 32767).clip(-32768, 32767).astype(np.int16)
        else:
            pcm = audio_data
        
        with wave.open(str(path), 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
    
    @property
    def is_available(self) -> bool:
        """Check if the TTS model is available."""
        return self._initialized and self._pipeline is not None
    
    async def transcribe(self, audio_path: str) -> str:
        """Transcribe audio to text using mlx-whisper (ASR).
        
        Args:
            audio_path: Path to audio file
            
        Returns:
            Transcribed text
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_path)
    
    def _transcribe_sync(self, audio_path: str) -> str:
        """Synchronous transcription via mlx-whisper."""
        import mlx_whisper
        result = mlx_whisper.transcribe(
            audio_path, 
            path_or_hf_repo="mlx-community/whisper-base-mlx"
        )
        return result.get("text", "").strip()
    
    async def text_to_speech(
        self, 
        text: str, 
        voice: str = DEFAULT_VOICE,
        output_path: Optional[str] = None,
        speed: float = 1.0,
    ) -> str:
        """Convert text to speech using Kokoro-82M.
        
        Args:
            text: Text to convert to speech
            voice: Kokoro voice ID or legacy alias (us_male, etc.)
            output_path: Optional path to save audio file
            speed: Speech speed multiplier (default 1.0)
            
        Returns:
            Path to generated audio file
        """
        if not self.is_available:
            await self.initialize()
            if not self.is_available:
                raise RuntimeError("Kokoro TTS not available")
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            self._tts_sync, 
            text, 
            voice, 
            output_path,
            speed,
        )
    
    def _tts_sync(
        self, 
        text: str, 
        voice: str, 
        output_path: Optional[str],
        speed: float = 1.0,
    ) -> str:
        """Synchronous TTS generation via Kokoro pipeline.
        
        Kokoro's KPipeline handles phonemization, chunking, and synthesis.
        It yields (graphemes, phonemes, audio_numpy) per sentence.
        We concatenate with small pauses and crossfade for seamless output.
        """
        voice_id = resolve_voice(voice)
        
        # Switch language pipeline if needed
        self._ensure_lang(voice_id)
        
        # Chunk text for better prosody on long inputs
        chunks = self._chunk_text_for_tts(text)
        print(f"[AudioLLM] TTS: {len(chunks)} chunks, voice={voice_id}")
        
        all_segments: List[np.ndarray] = []
        
        for i, chunk in enumerate(chunks):
            chunk_audio_parts = []
            try:
                generator = self._pipeline(chunk, voice=voice_id, speed=speed)
                for gs, ps, audio in generator:
                    if audio is not None and len(audio) > 0:
                        # Kokoro returns torch tensors — convert to numpy
                        if hasattr(audio, 'numpy'):
                            audio = audio.squeeze().cpu().float().numpy()
                        chunk_audio_parts.append(audio)
            except Exception as e:
                print(f"[AudioLLM] Warning: chunk {i+1}/{len(chunks)} failed: {e}")
                continue
            
            if chunk_audio_parts:
                # Kokoro yields per-sentence — join with tiny pause (60ms)
                pause = np.zeros(int(SAMPLE_RATE * 0.06), dtype=np.float32)
                joined = chunk_audio_parts[0]
                for part in chunk_audio_parts[1:]:
                    joined = np.concatenate([joined, pause, part])
                all_segments.append(joined)
                dur = len(joined) / SAMPLE_RATE
                print(f"[AudioLLM]   chunk {i+1}: {dur:.1f}s")
        
        if not all_segments:
            raise RuntimeError("No audio generated from any text chunk")
        
        # Crossfade segments for seamless output
        final_audio = self._crossfade_segments(all_segments)
        total_dur = len(final_audio) / SAMPLE_RATE
        print(f"[AudioLLM] TTS complete: {total_dur:.1f}s total")
        
        # Save to file
        if output_path is None:
            output_dir = Path(settings.data_dir) / "audio_output"
            output_dir.mkdir(exist_ok=True)
            output_path = str(output_dir / f"tts_{uuid.uuid4().hex[:8]}.wav")
        
        self._save_wav(output_path, final_audio, SAMPLE_RATE)
        return output_path
    
    @staticmethod
    def _crossfade_segments(segments: List[np.ndarray], pause_ms: int = 80, crossfade_ms: int = 30) -> np.ndarray:
        """Crossfade audio segments with natural pauses."""
        if len(segments) == 1:
            return segments[0]
        
        crossfade_samples = int(SAMPLE_RATE * crossfade_ms / 1000)
        pause_samples = int(SAMPLE_RATE * pause_ms / 1000)
        pause = np.zeros(pause_samples, dtype=np.float32)
        
        merged = segments[0]
        for nxt in segments[1:]:
            merged = np.concatenate([merged, pause])
            # Apply crossfade if both segments are long enough
            if len(merged) > crossfade_samples and len(nxt) > crossfade_samples:
                tail = merged[-crossfade_samples:]
                head = nxt[:crossfade_samples]
                fade_out = np.linspace(1.0, 0.0, crossfade_samples, dtype=np.float32)
                fade_in = np.linspace(0.0, 1.0, crossfade_samples, dtype=np.float32)
                blended = tail * fade_out + head * fade_in
                merged = np.concatenate([merged[:-crossfade_samples], blended, nxt[crossfade_samples:]])
            else:
                merged = np.concatenate([merged, nxt])
        
        return merged
    
    def _chunk_text_for_tts(self, text: str, max_chunk_chars: int = 500) -> list:
        """Split text into sentence-level chunks for TTS generation.
        
        Kokoro handles per-sentence splitting internally, but feeding very
        long text in one shot can degrade quality. We chunk at ~500 chars
        (Kokoro is more robust than LFM2.5 at longer inputs).
        """
        # First try paragraph boundaries (double newline)
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
        
        chunks = []
        current = ""
        
        for para in paragraphs:
            if len(para) <= max_chunk_chars:
                if current and len(current) + len(para) + 2 > max_chunk_chars:
                    chunks.append(current.strip())
                    current = para
                else:
                    current = f"{current}\n\n{para}".strip() if current else para
            else:
                if current:
                    chunks.append(current.strip())
                    current = ""
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sentence in sentences:
                    sentence = sentence.strip()
                    if not sentence:
                        continue
                    if current and len(current) + len(sentence) + 1 > max_chunk_chars:
                        chunks.append(current.strip())
                        current = sentence
                    else:
                        current = f"{current} {sentence}".strip() if current else sentence
        
        if current.strip():
            chunks.append(current.strip())
        
        if not chunks and text.strip():
            chunks = [text.strip()[:max_chunk_chars]]
        
        # Safety net: force-split any chunk still over 2x limit
        # This prevents mega-chunks that crash Kokoro or consume excessive memory
        hard_limit = max_chunk_chars * 2
        final = []
        for chunk in chunks:
            if len(chunk) <= hard_limit:
                final.append(chunk)
            else:
                # Split at clause boundaries, then word boundaries as last resort
                parts = re.split(r'(?:\n|;\s*|,\s+)', chunk)
                buf = ""
                for p in parts:
                    p = p.strip()
                    if not p:
                        continue
                    if buf and len(buf) + len(p) + 1 > max_chunk_chars:
                        final.append(buf)
                        buf = p
                    else:
                        buf = f"{buf} {p}".strip() if buf else p
                if buf:
                    # Word-boundary split if still too large
                    if len(buf) > hard_limit:
                        words = buf.split()
                        wbuf = ""
                        for w in words:
                            if wbuf and len(wbuf) + len(w) + 1 > max_chunk_chars:
                                final.append(wbuf)
                                wbuf = w
                            else:
                                wbuf = f"{wbuf} {w}".strip() if wbuf else w
                        if wbuf:
                            final.append(wbuf)
                    else:
                        final.append(buf)
        
        return final
    
    async def speech_to_speech(
        self,
        audio_path: str,
        system_prompt: str = "You are a helpful assistant.",
        voice: str = DEFAULT_VOICE,
        output_path: Optional[str] = None
    ) -> tuple[str, str]:
        """Speech-to-speech via decomposed pipeline: ASR → Ollama → TTS.
        
        Args:
            audio_path: Path to input audio file
            system_prompt: System prompt for the LLM
            voice: Voice for TTS output
            output_path: Optional path to save output audio
            
        Returns:
            Tuple of (response_text, output_audio_path)
        """
        if not self.is_available:
            await self.initialize()
            if not self.is_available:
                raise RuntimeError("Kokoro TTS not available")
        
        # Step 1: ASR — transcribe input audio
        user_text = await self.transcribe(audio_path)
        if not user_text.strip():
            raise RuntimeError("Could not transcribe any speech from audio")
        print(f"[AudioLLM] S2S ASR: '{user_text[:80]}...'")
        
        # Step 2: LLM — generate response via Ollama
        from services.rag_engine import rag_engine
        response_text = await rag_engine._call_ollama(
            system_prompt,
            user_text,
            num_predict=512,
            temperature=0.7,
        )
        if not response_text.strip():
            response_text = "I'm sorry, I couldn't generate a response."
        print(f"[AudioLLM] S2S LLM: '{response_text[:80]}...'")
        
        # Step 3: TTS — synthesize response audio
        audio_path_out = await self.text_to_speech(
            text=response_text,
            voice=voice,
            output_path=output_path,
        )
        
        return response_text, audio_path_out
    
    async def generate_podcast_audio(
        self,
        script: str,
        voice: str = "bm_george",
        output_path: Optional[str] = None
    ) -> str:
        """Generate audio for a podcast script.
        
        Args:
            script: Full podcast script text
            voice: Kokoro voice ID or legacy alias
            output_path: Optional path for output
            
        Returns:
            Path to generated audio file
        """
        return await self.text_to_speech(script, voice, output_path)


# Singleton instance
audio_llm = AudioLLMService()


async def check_audio_llm_available() -> dict:
    """Check if Kokoro TTS + mlx-whisper ASR are available."""
    diag = {}
    
    # Check kokoro
    try:
        import kokoro
        diag["kokoro"] = f"ok (v{kokoro.__version__})"
    except Exception as e:
        diag["kokoro"] = f"FAIL: {e}"
    
    # Check misaki (G2P)
    try:
        import misaki
        diag["misaki"] = "ok"
    except Exception as e:
        diag["misaki"] = f"FAIL: {e}"
    
    # Check espeak-ng (optional — misaki handles G2P for English without it)
    import shutil
    espeak_path = shutil.which("espeak-ng")
    diag["espeak_ng"] = f"ok ({espeak_path})" if espeak_path else "not installed (optional, English works via misaki)"
    
    # Check mlx-whisper (ASR)
    try:
        import mlx_whisper
        diag["mlx_whisper"] = "ok"
    except Exception as e:
        diag["mlx_whisper"] = f"FAIL: {e}"
    
    # Check soundfile
    try:
        import soundfile
        diag["soundfile"] = "ok"
    except Exception as e:
        diag["soundfile"] = f"FAIL: {e}"
    
    # Check HuggingFace model cache
    import os
    hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
    kokoro_cache = os.path.join(hf_cache, "models--hexgrad--Kokoro-82M")
    diag["model_cached"] = "yes" if os.path.exists(kokoro_cache) else "no (will download ~350 MB on first use)"
    
    all_ok = all("FAIL" not in str(v) for v in diag.values() if v != diag.get("model_cached"))
    
    return {
        "available": all_ok,
        "initialized": audio_llm.is_available,
        "message": "Kokoro-82M TTS ready" if all_ok else "Some audio dependencies missing",
        "init_error": audio_llm._init_error,
        "diagnostics": diag
    }
