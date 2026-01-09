"""LFM2.5-Audio Integration Service

Provides speech-to-speech, TTS, and ASR capabilities using Liquid AI's LFM2.5-Audio model.
This enables Jarvis mode, podcast audio generation, and voice Q&A.
"""

import asyncio
from pathlib import Path
from typing import Optional, Literal, AsyncGenerator
from datetime import datetime
import uuid
import io

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
        self._device = None
        self._initialized = False
        self._initializing = False
        
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
            print(f"[AudioLLM] ⚠ liquid-audio not installed: {e}")
            print("[AudioLLM] Run: pip install liquid-audio")
        except Exception as e:
            print(f"[AudioLLM] ⚠ Failed to load model: {e}")
        finally:
            self._initializing = False
    
    def _load_model(self):
        """Load the LFM2.5-Audio model (runs in thread)."""
        _ensure_audio_deps()
        from liquid_audio import LFM2AudioModel, LFM2AudioProcessor
        
        HF_REPO = "LiquidAI/LFM2.5-Audio-1.5B"
        
        self._processor = LFM2AudioProcessor.from_pretrained(HF_REPO).eval()
        self._model = LFM2AudioModel.from_pretrained(HF_REPO).eval()
        
        # Use MPS on Apple Silicon if available
        if torch.backends.mps.is_available():
            self._device = torch.device("mps")
            self._model = self._model.to(self._device)
        elif torch.cuda.is_available():
            self._device = torch.device("cuda")
            self._model = self._model.to(self._device)
        else:
            self._device = torch.device("cpu")
    
    @property
    def is_available(self) -> bool:
        """Check if the audio model is available."""
        return self._initialized and self._model is not None
    
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
        """Synchronous TTS generation."""
        _ensure_audio_deps()
        from liquid_audio import ChatState
        
        # Get voice prompt
        voice_prompt = VOICE_PROMPTS.get(voice, VOICE_PROMPTS[DEFAULT_VOICE])
        
        # Set up chat for TTS
        chat = ChatState(self._processor)
        chat.new_turn("system")
        chat.add_text(voice_prompt)
        chat.end_turn()
        
        chat.new_turn("user")
        chat.add_text(text)
        chat.end_turn()
        
        chat.new_turn("assistant")
        
        # Generate audio
        audio_out = []
        for t in self._model.generate_sequential(
            **chat, 
            max_new_tokens=512,
            audio_temperature=0.8,
            audio_top_k=64
        ):
            if t.numel() > 1:
                audio_out.append(t)
        
        # Detokenize audio
        if not audio_out:
            raise RuntimeError("No audio generated")
            
        audio_codes = torch.stack(audio_out[:-1], 1).unsqueeze(0)
        waveform = self._processor.decode(audio_codes)
        
        # Save to file
        if output_path is None:
            output_dir = Path(settings.data_dir) / "audio_output"
            output_dir.mkdir(exist_ok=True)
            output_path = str(output_dir / f"tts_{uuid.uuid4().hex[:8]}.wav")
        
        torchaudio.save(output_path, waveform.cpu(), 24_000)
        
        return output_path
    
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
            waveform = self._processor.decode(audio_codes)
            torchaudio.save(output_path, waveform.cpu(), 24_000)
        
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
    try:
        # Try importing liquid_audio
        import liquid_audio
        
        return {
            "available": True,
            "initialized": audio_llm.is_available,
            "message": "LFM2.5-Audio is available"
        }
    except ImportError:
        return {
            "available": False,
            "initialized": False,
            "message": "liquid-audio not installed. Run: pip install liquid-audio"
        }
