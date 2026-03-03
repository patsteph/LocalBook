"""LFM2.5-Audio Integration Service

Provides speech-to-speech, TTS, and ASR capabilities using Liquid AI's LFM2.5-Audio model.
This enables Jarvis mode, podcast audio generation, and voice Q&A.
"""

import asyncio
from pathlib import Path
from typing import Optional
import uuid

from config import settings

# Lazy imports for optional audio dependencies
torch = None
torchaudio = None

def _ensure_audio_deps():
    """Lazily import audio dependencies."""
    global torch, torchaudio
    if torch is None:
        import torch as _torch
        import torchaudio as _torchaudio
        torch = _torch
        torchaudio = _torchaudio


# Voice options for TTS
VOICE_PROMPTS = {
    "us_male": "Perform TTS. Use the US male voice.",
    "us_female": "Perform TTS. Use the US female voice.",
    "uk_male": "Perform TTS. Use the UK male voice.",
    "uk_female": "Perform TTS. Use the UK female voice."
}

DEFAULT_VOICE = "us_male"


class AudioLLMService:
    """Service for LFM2.5-Audio model operations."""
    
    def __init__(self):
        self._model = None
        self._processor = None
        self._detokenizer = None  # Our own detokenizer — bypasses processor.decode()
        self._device = None
        self._initialized = False
        self._initializing = False
        self._init_error = None
        
    async def initialize(self):
        """Lazy initialization of the audio model."""
        if self._initialized or self._initializing:
            return
            
        self._initializing = True
        
        try:
            # Run model loading in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._load_model)
            self._initialized = True
            print("[AudioLLM] ✓ LFM2.5-Audio model loaded")
        except ImportError as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[AudioLLM] ⚠ liquid-audio not installed: {e}")
            print(f"[AudioLLM] Full traceback:\n{tb}")
            self._init_error = tb
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[AudioLLM] ⚠ Failed to load model: {e}")
            print(f"[AudioLLM] Full traceback:\n{tb}")
            self._init_error = tb
        finally:
            self._initializing = False
    
    def _load_model(self):
        """Load the LFM2.5-Audio model (runs in thread).
        
        Downloads ~3 GB from HuggingFace on first use via snapshot_download.
        Once cached (~/.cache/huggingface/hub/), loads locally with no network.
        """
        print("[AudioLLM] Step 1/5: importing audio deps...")
        _ensure_audio_deps()
        
        print("[AudioLLM] Step 2/5: importing liquid_audio...")
        from liquid_audio import LFM2AudioModel, LFM2AudioProcessor
        
        HF_REPO = "LiquidAI/LFM2.5-Audio-1.5B"
        
        # Detect device FIRST — liquid_audio defaults to 'cuda' which crashes on macOS
        if torch.backends.mps.is_available():
            self._device = torch.device("mps")
        elif torch.cuda.is_available():
            self._device = torch.device("cuda")
        else:
            self._device = torch.device("cpu")
        
        # snapshot_download (inside liquid_audio) handles caching automatically:
        # - If model is cached: returns cached path instantly (no network needed)
        # - If not cached: downloads ~3 GB from HuggingFace (needs internet)
        # NOTE: liquid_audio's from_pretrained does NOT support local_files_only —
        #       passing it causes TypeError. The device param IS supported and required
        #       (defaults to "cuda" which crashes on macOS).
        try:
            print(f"[AudioLLM] Step 3/5: loading processor on {self._device}...")
            processor = LFM2AudioProcessor.from_pretrained(
                HF_REPO, device=self._device
            ).eval()
            print(f"[AudioLLM] Step 4/5: loading model on {self._device}...")
            model = LFM2AudioModel.from_pretrained(
                HF_REPO, device=self._device
            ).eval()
        except Exception as e:
            raise RuntimeError(
                f"LFM2.5-Audio model failed to load. The model (~3 GB) downloads "
                f"from HuggingFace on first use and requires internet. If this is "
                f"a fresh machine, ensure you have internet access and ~4 GB free "
                f"disk space, then try again via Health Portal → Repair. "
                f"Error: {e}"
            ) from e
        
        self._processor = processor
        self._model = model
        
        print("[AudioLLM] Step 5/5: loading detokenizer (our own, bypasses processor.decode())...")
        self._load_detokenizer()
    
    def _load_detokenizer(self):
        """Load the audio detokenizer directly, bypassing processor.decode().
        
        The liquid_audio processor.decode() method accesses a lazy property that
        calls Lfm2Config.from_pretrained(), which triggers transformers' internal
        import of torchcodec (a video library needing FFmpeg dylibs). This fails
        in PyInstaller bundles because FFmpeg dylibs aren't bundled.
        
        Solution: load the detokenizer ourselves using json.load + direct
        constructor, store it on our class, and call it directly in _tts_sync()
        and _s2s_sync(). processor.decode() is never called.
        """
        import json
        from pathlib import Path
        from liquid_audio.detokenizer import LFM2AudioDetokenizer
        from transformers import Lfm2Config
        from safetensors.torch import load_file
        
        detok_path = self._processor.detokenizer_path
        if detok_path is None:
            raise RuntimeError("[AudioLLM] No detokenizer path found — model repo may be incomplete")
        
        # Load config via json.load — NOT from_pretrained — to avoid torchcodec
        detok_config_path = Path(detok_path) / "config.json"
        with open(detok_config_path) as f:
            config_dict = json.load(f)
        detok_config = Lfm2Config(**config_dict)
        
        # Same layer renaming the library does internally
        if isinstance(detok_config.layer_types, list):
            def rename_layer(layer):
                if layer in ("conv", "full_attention"):
                    return layer
                elif layer == "sliding_attention":
                    return "full_attention"
                return layer
            detok_config.layer_types = [rename_layer(l) for l in detok_config.layer_types]
        
        # Create on correct device (MPS/CPU) instead of hardcoded .cuda()
        detok = LFM2AudioDetokenizer(detok_config).eval().to(self._device)
        
        detok_weights_path = Path(detok_path) / "model.safetensors"
        detok_weights = load_file(str(detok_weights_path), device=str(self._device))
        detok.load_state_dict(detok_weights)
        detok.eval()
        
        # Validate: test-decode a dummy tensor to prove it works
        dummy = torch.randint(0, 2048, (1, 8, 10), device=self._device)
        test_out = detok(dummy)
        assert test_out.shape[0] == 1, f"Detokenizer test failed: unexpected shape {test_out.shape}"
        
        self._detokenizer = detok
        print(f"[AudioLLM] ✓ Detokenizer loaded on {self._device} (test decode OK)")
    
    def _decode_audio(self, audio_codes):
        """Decode audio codes to waveform using our own detokenizer.
        
        Replaces processor.decode() which triggers torchcodec import chain.
        """
        if self._detokenizer is None:
            raise RuntimeError("Detokenizer not loaded — initialize() must be called first")
        if torch.any(audio_codes >= 2048) or torch.any(audio_codes < 0):
            raise RuntimeError("expected audio codes in range [0, 2048)")
        with torch.no_grad():
            return self._detokenizer(audio_codes)
    
    @staticmethod
    def _save_wav(path: str, waveform, sample_rate: int = 24_000):
        """Save waveform tensor as WAV using stdlib wave module.
        
        Replaces torchaudio.save() which triggers torchcodec import.
        """
        import wave
        import struct
        
        # Handle tensor shapes: (1, N) or (N,)
        if hasattr(waveform, 'numpy'):
            audio = waveform.squeeze().cpu().float().numpy()
        else:
            audio = waveform
        
        # Convert float [-1, 1] to 16-bit PCM
        pcm = (audio * 32767).clip(-32768, 32767).astype('int16')
        
        with wave.open(str(path), 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
    
    @property
    def is_available(self) -> bool:
        """Check if the audio model is available."""
        return self._initialized and self._model is not None and self._detokenizer is not None
    
    async def transcribe(self, audio_path: str) -> str:
        """Transcribe audio to text (ASR).
        
        Args:
            audio_path: Path to audio file
            
        Returns:
            Transcribed text
        """
        if not self.is_available:
            await self.initialize()
            if not self.is_available:
                raise RuntimeError("Audio model not available")
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_path)
    
    def _transcribe_sync(self, audio_path: str) -> str:
        """Synchronous transcription."""
        _ensure_audio_deps()
        from liquid_audio import ChatState
        
        # Load audio
        wav, sampling_rate = torchaudio.load(audio_path)
        
        # Set up chat for ASR
        chat = ChatState(self._processor)
        chat.new_turn("system")
        chat.add_text("Perform ASR.")
        chat.end_turn()
        
        chat.new_turn("user")
        chat.add_audio(wav, sampling_rate)
        chat.end_turn()
        
        chat.new_turn("assistant")
        
        # Generate text
        text_parts = []
        for t in self._model.generate_sequential(**chat, max_new_tokens=512):
            if t.numel() == 1:
                text_parts.append(self._processor.text.decode(t))
        
        return "".join(text_parts)
    
    async def text_to_speech(
        self, 
        text: str, 
        voice: str = DEFAULT_VOICE,
        output_path: Optional[str] = None
    ) -> str:
        """Convert text to speech (TTS).
        
        Args:
            text: Text to convert to speech
            voice: Voice to use (us_male, us_female, uk_male, uk_female)
            output_path: Optional path to save audio file
            
        Returns:
            Path to generated audio file
        """
        if not self.is_available:
            await self.initialize()
            if not self.is_available:
                raise RuntimeError("Audio model not available")
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            self._tts_sync, 
            text, 
            voice, 
            output_path
        )
    
    def _tts_sync(
        self, 
        text: str, 
        voice: str, 
        output_path: Optional[str]
    ) -> str:
        """Synchronous TTS generation with sentence chunking."""
        _ensure_audio_deps()
        from liquid_audio import ChatState
        
        # Get voice prompt
        voice_prompt = VOICE_PROMPTS.get(voice, VOICE_PROMPTS[DEFAULT_VOICE])
        
        # Chunk text into sentences for reliable generation
        chunks = self._chunk_text_for_tts(text)
        print(f"[AudioLLM] TTS: {len(chunks)} chunks from {len(text)} chars")
        
        all_waveforms = []
        
        for i, chunk in enumerate(chunks):
            # Fresh chat state per chunk
            chat = ChatState(self._processor)
            chat.new_turn("system")
            chat.add_text(voice_prompt)
            chat.end_turn()
            
            chat.new_turn("user")
            chat.add_text(chunk)
            chat.end_turn()
            
            chat.new_turn("assistant")
            
            # Generate audio for this chunk
            # Scale max_new_tokens: ~2.5 tokens per char gives headroom for shorter chunks
            import time
            chunk_max_tokens = min(1500, max(400, int(len(chunk) * 2.5)))
            audio_out = []
            gen_start = time.time()
            token_count = 0
            for t in self._model.generate_sequential(
                **chat, 
                max_new_tokens=chunk_max_tokens,
                audio_temperature=0.8,
                audio_top_k=64
            ):
                token_count += 1
                if t.numel() > 1:
                    audio_out.append(t)
                if token_count % 50 == 0:
                    elapsed = time.time() - gen_start
                    print(f"[AudioLLM]   chunk {i+1}: {token_count} tokens, {len(audio_out)} audio frames, {elapsed:.1f}s")
            
            if not audio_out:
                print(f"[AudioLLM] Warning: chunk {i+1}/{len(chunks)} produced no audio, skipping")
                continue
            
            # Filter out end-of-audio markers before stacking
            valid_frames = [f for f in audio_out if not (f == 2048).any()]
            if not valid_frames:
                print(f"[AudioLLM] Warning: chunk {i+1} had only end markers")
                continue
                
            audio_codes = torch.stack(valid_frames, 1).unsqueeze(0)
            waveform = self._decode_audio(audio_codes)
            all_waveforms.append(waveform.cpu())
            print(f"[AudioLLM] Chunk {i+1}/{len(chunks)}: {len(valid_frames)} frames, {waveform.shape[-1]/24000:.1f}s")
        
        if not all_waveforms:
            raise RuntimeError("No audio generated from any text chunk")
        
        # Trim leading/trailing near-silence from each waveform to reduce dead air
        trimmed = []
        for w in all_waveforms:
            trimmed.append(self._trim_silence(w))
        all_waveforms = trimmed
        
        # Crossfade between adjacent waveforms for seamless joins
        if len(all_waveforms) > 1:
            crossfade_samples = int(24000 * 0.03)  # 30ms crossfade at 24kHz
            merged = all_waveforms[0]
            for j in range(1, len(all_waveforms)):
                nxt = all_waveforms[j]
                # Add a small natural pause (80ms silence) then crossfade
                pause = torch.zeros(1, int(24000 * 0.08))
                merged = torch.cat([merged, pause], dim=-1)
                # Apply crossfade if both segments are long enough
                if merged.shape[-1] > crossfade_samples and nxt.shape[-1] > crossfade_samples:
                    tail = merged[..., -crossfade_samples:]
                    head = nxt[..., :crossfade_samples]
                    fade_out = torch.linspace(1.0, 0.0, crossfade_samples)
                    fade_in = torch.linspace(0.0, 1.0, crossfade_samples)
                    blended = tail * fade_out + head * fade_in
                    merged = torch.cat([merged[..., :-crossfade_samples], blended, nxt[..., crossfade_samples:]], dim=-1)
                else:
                    merged = torch.cat([merged, nxt], dim=-1)
            final_waveform = merged
        else:
            final_waveform = all_waveforms[0]
        print(f"[AudioLLM] TTS complete: {final_waveform.shape[-1]/24000:.1f}s total")
        
        # Save to file
        if output_path is None:
            output_dir = Path(settings.data_dir) / "audio_output"
            output_dir.mkdir(exist_ok=True)
            output_path = str(output_dir / f"tts_{uuid.uuid4().hex[:8]}.wav")
        
        self._save_wav(output_path, final_waveform, 24_000)
        
        return output_path
    
    def _trim_silence(self, waveform, threshold: float = 0.01, min_samples: int = 480) -> 'torch.Tensor':
        """Trim leading/trailing near-silence from a waveform tensor.
        
        Preserves at least min_samples (20ms at 24kHz) at each end to avoid
        cutting into actual speech. Only trims truly silent padding.
        """
        audio = waveform.squeeze()
        abs_audio = audio.abs()
        # Find first and last sample above threshold
        above = (abs_audio > threshold).nonzero(as_tuple=True)[0]
        if len(above) == 0:
            return waveform  # All silence, return as-is
        start = max(0, above[0].item() - min_samples)
        end = min(len(audio), above[-1].item() + min_samples + 1)
        return audio[start:end].unsqueeze(0)
    
    def _chunk_text_for_tts(self, text: str, max_chunk_chars: int = 350) -> list:
        """Split text into sentence-level chunks for TTS generation.
        
        Shorter chunks (2-3 sentences) produce dramatically better prosody
        and natural speech from the LFM2.5-Audio model. The official Liquid
        examples use single sentences. We use ~350 chars (~50 words, ~15s)
        as the sweet spot between quality and efficiency.
        """
        import re
        
        # First try paragraph boundaries (double newline)
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
        
        chunks = []
        current = ""
        
        for para in paragraphs:
            # If a single paragraph fits, add it
            if len(para) <= max_chunk_chars:
                if current and len(current) + len(para) + 2 > max_chunk_chars:
                    chunks.append(current.strip())
                    current = para
                else:
                    current = f"{current}\n\n{para}".strip() if current else para
            else:
                # Paragraph too long — split on sentences
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
        
        # Fallback: if no boundaries found, take the whole text
        if not chunks and text.strip():
            chunks = [text.strip()[:max_chunk_chars]]
        
        return chunks
    
    async def speech_to_speech(
        self,
        audio_path: str,
        system_prompt: str = "You are a helpful assistant.",
        voice: str = DEFAULT_VOICE,
        output_path: Optional[str] = None
    ) -> tuple[str, str]:
        """Full speech-to-speech conversation.
        
        Args:
            audio_path: Path to input audio file
            system_prompt: System prompt for the conversation
            voice: Voice for output
            output_path: Optional path to save output audio
            
        Returns:
            Tuple of (response_text, output_audio_path)
        """
        if not self.is_available:
            await self.initialize()
            if not self.is_available:
                raise RuntimeError("Audio model not available")
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._s2s_sync,
            audio_path,
            system_prompt,
            voice,
            output_path
        )
    
    def _s2s_sync(
        self,
        audio_path: str,
        system_prompt: str,
        voice: str,
        output_path: Optional[str]
    ) -> tuple[str, str]:
        """Synchronous speech-to-speech."""
        _ensure_audio_deps()
        from liquid_audio import ChatState
        
        # Load input audio
        wav, sampling_rate = torchaudio.load(audio_path)
        
        # Set up chat for interleaved generation
        chat = ChatState(self._processor)
        chat.new_turn("system")
        chat.add_text(system_prompt)
        chat.end_turn()
        
        chat.new_turn("user")
        chat.add_audio(wav, sampling_rate)
        chat.end_turn()
        
        chat.new_turn("assistant")
        
        # Generate interleaved text and audio
        text_parts = []
        audio_out = []
        
        for t in self._model.generate_interleaved(
            **chat,
            max_new_tokens=1024,
            audio_temperature=0.8,
            audio_top_k=64
        ):
            if t.numel() == 1:
                text_parts.append(self._processor.text.decode(t))
            else:
                audio_out.append(t)
        
        response_text = "".join(text_parts)
        
        # Detokenize audio
        if output_path is None:
            output_dir = Path(settings.data_dir) / "audio_output"
            output_dir.mkdir(exist_ok=True)
            output_path = str(output_dir / f"s2s_{uuid.uuid4().hex[:8]}.wav")
        
        if audio_out:
            audio_codes = torch.stack(audio_out[:-1], 1).unsqueeze(0)
            waveform = self._decode_audio(audio_codes)
            self._save_wav(output_path, waveform.cpu(), 24_000)
        
        return response_text, output_path
    
    async def generate_podcast_audio(
        self,
        script: str,
        voice: str = "uk_male",
        output_path: Optional[str] = None
    ) -> str:
        """Generate audio for a podcast script.
        
        For longer scripts, this splits into segments and generates each.
        
        Args:
            script: Full podcast script text
            voice: Voice to use
            output_path: Optional path for output
            
        Returns:
            Path to generated audio file
        """
        # For now, use TTS for the full script
        # Future: Split into speaker segments and use different voices
        return await self.text_to_speech(script, voice, output_path)


# Singleton instance
audio_llm = AudioLLMService()


async def check_audio_llm_available() -> dict:
    """Check if audio LLM is available and return status."""
    # Step-by-step import diagnostics
    diag = {}
    try:
        import torch
        diag["torch"] = f"ok (v{torch.__version__})"
    except Exception as e:
        diag["torch"] = f"FAIL: {e}"
    
    try:
        import torchaudio
        diag["torchaudio"] = f"ok (v{torchaudio.__version__})"
    except Exception as e:
        diag["torchaudio"] = f"FAIL: {e}"
    
    try:
        import transformers
        diag["transformers"] = f"ok (v{transformers.__version__})"
    except Exception as e:
        diag["transformers"] = f"FAIL: {e}"
    
    try:
        from liquid_audio import LFM2AudioModel, LFM2AudioProcessor
        diag["liquid_audio"] = "ok"
        
        return {
            "available": True,
            "initialized": audio_llm.is_available,
            "message": "LFM2.5-Audio is available",
            "init_error": audio_llm._init_error,
            "diagnostics": diag
        }
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[AudioLLM] Status check import failed:\n{tb}")
        diag["liquid_audio"] = f"FAIL: {e}"
        return {
            "available": False,
            "initialized": False,
            "message": f"liquid-audio import failed: {e}",
            "traceback": tb,
            "init_error": audio_llm._init_error,
            "diagnostics": diag
        }
