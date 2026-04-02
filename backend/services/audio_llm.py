"""Kokoro-82M TTS (MLX) + mlx-whisper ASR Integration Service

Provides TTS, ASR, and speech-to-speech capabilities:
- TTS: Kokoro-82M via kokoro-mlx (Apple Silicon Metal, no PyTorch)
- ASR: mlx-whisper (Apple Silicon accelerated Whisper)
- S2S: Decomposed ASR → Ollama LLM → Kokoro TTS pipeline

Uses kokoro-mlx for native Apple Silicon acceleration.
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
        self._model = None          # kokoro-mlx KokoroTTS instance
        self._initialized = False
        self._init_lock = asyncio.Lock()
        self._init_error = None
        
    async def initialize(self):
        """Lazy initialization of Kokoro TTS pipeline.
        
        Uses asyncio.Lock so concurrent callers wait for init to complete
        rather than silently returning with the model unavailable.
        """
        if self._initialized:
            return
        
        async with self._init_lock:
            # Double-check after acquiring lock (another caller may have finished init)
            if self._initialized:
                return
            
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._load_model)
                self._initialized = True
                print(f"[AudioLLM] ✓ Kokoro-82M (MLX) loaded")
            except ImportError as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[AudioLLM] ⚠ kokoro-mlx not installed: {e}")
                self._init_error = tb
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"[AudioLLM] ⚠ Failed to load Kokoro: {e}")
                self._init_error = tb
    
    # HuggingFace model ID for MLX Kokoro
    MLX_KOKORO_REPO = "mlx-community/Kokoro-82M-bf16"
    
    def _load_model(self):
        """Load Kokoro-82M via kokoro-mlx (runs in thread).
        
        Downloads ~330 MB from HuggingFace on first use.
        Once cached (~/.cache/huggingface/hub/), loads locally.
        MLX auto-uses Metal on Apple Silicon — no manual device selection.
        """
        # ── 1. Prevent spacy.cli.download() from crashing in PyInstaller ──
        # misaki's G2P.__init__ calls spacy.cli.download('en_core_web_sm')
        # if the model isn't found as an installed package. In a frozen binary,
        # this runs `sys.executable -m pip install ...` which crashes because
        # sys.executable is the PyInstaller binary, not Python.
        # Fix: patch spacy.cli.download to no-op BEFORE importing kokoro_mlx.
        # The model data is bundled via --collect-all=en_core_web_sm.
        print(f"[AudioLLM] Step 1/5: Patching spacy download...")
        self._patch_spacy_download()
        
        # ── 2. SSL fixes for any HuggingFace downloads needed ──
        print(f"[AudioLLM] Step 2/5: Configuring SSL...")
        self._fix_ssl_for_hf_download()
        
        if not self._ssl_probe():
            print(f"[AudioLLM] SSL cert verification broken — disabling for HuggingFace downloads")
            self._disable_hf_ssl_verify()
        else:
            print(f"[AudioLLM] SSL verification OK")
        
        # ── 3. Import kokoro_mlx ──
        print(f"[AudioLLM] Step 3/5: Importing kokoro_mlx...")
        from kokoro_mlx import KokoroTTS
        print(f"[AudioLLM] Step 3/5: kokoro_mlx imported OK")
        
        # ── 4. Download model files ──
        print(f"[AudioLLM] Step 4/5: Downloading model files...")
        local_dir = self._download_model_files()
        print(f"[AudioLLM] Step 4/5: Model files at {local_dir}")
        
        # ── 5. Load model (with self-healing for corrupt files) ──
        print(f"[AudioLLM] Step 5/5: Loading model into memory...")
        try:
            self._model = KokoroTTS.from_pretrained(local_dir)
        except Exception as e:
            err_msg = str(e).lower()
            if any(k in err_msg for k in ("incomplete", "deserializ", "safetensor", "truncat", "corrupt")):
                print(f"[AudioLLM] Model files corrupt ({e}), forcing re-download...")
                local_dir = self._download_model_files(force_redownload=True)
                self._model = KokoroTTS.from_pretrained(local_dir)
            else:
                raise
        
        print(f"[AudioLLM] ✓ Kokoro-82M (MLX) pipeline ready")
    
    @staticmethod
    def _patch_spacy_download():
        """Prevent spacy.cli.download() from running pip in frozen binaries.
        
        In PyInstaller bundles, sys.executable points to the frozen binary.
        spacy.cli.download() calls subprocess with sys.executable -m pip,
        which crashes fatally. We replace it with a no-op that logs a warning.
        The en_core_web_sm model data is bundled with the app.
        """
        import sys
        try:
            import spacy.cli
            original_download = spacy.cli.download
            
            def _safe_download(model, *args, **kwargs):
                if getattr(sys, 'frozen', False):
                    print(f"[AudioLLM] Skipping spacy.cli.download('{model}') in frozen binary — model should be bundled")
                    return
                return original_download(model, *args, **kwargs)
            
            spacy.cli.download = _safe_download
        except ImportError:
            pass
    
    @staticmethod
    def _validate_safetensors_file(path: str) -> bool:
        """Validate a safetensors file is complete (not truncated).
        
        Reads the binary header to determine expected file size, then verifies
        the actual file size matches. Catches truncated downloads without
        loading any tensor data. Fast (~1ms per file).
        """
        import struct
        import json as _json
        try:
            file_size = os.path.getsize(path)
            if file_size < 8:
                return False
            with open(path, 'rb') as f:
                header_size = struct.unpack('<Q', f.read(8))[0]
                if file_size < 8 + header_size:
                    return False
                header_json = f.read(header_size)
                metadata = _json.loads(header_json)
                max_end = 0
                for key, info in metadata.items():
                    if key == "__metadata__":
                        continue
                    offsets = info.get("data_offsets", [0, 0])
                    if len(offsets) == 2:
                        max_end = max(max_end, offsets[1])
                expected_size = 8 + header_size + max_end
                return file_size >= expected_size
        except Exception:
            return False
    
    @classmethod
    def _validate_model_dir(cls, model_dir: str) -> tuple:
        """Validate all model files in a directory are complete.
        
        Returns (is_valid: bool, error_message: str).
        """
        import glob
        
        config_path = os.path.join(model_dir, "config.json")
        if not os.path.isfile(config_path):
            return False, "config.json missing"
        
        try:
            import json
            with open(config_path) as f:
                json.load(f)
        except Exception:
            return False, "config.json corrupted"
        
        # Validate model safetensors
        st_files = glob.glob(os.path.join(model_dir, "*.safetensors"))
        if not st_files:
            return False, "No .safetensors model files found"
        
        for st_file in st_files:
            if not cls._validate_safetensors_file(st_file):
                return False, f"Corrupt/truncated: {os.path.basename(st_file)}"
        
        # Validate voice files (if voices/ exists)
        voices_dir = os.path.join(model_dir, "voices")
        if os.path.isdir(voices_dir):
            voice_files = glob.glob(os.path.join(voices_dir, "*.safetensors"))
            for vf in voice_files:
                if not cls._validate_safetensors_file(vf):
                    return False, f"Corrupt/truncated voice: {os.path.basename(vf)}"
        
        return True, "ok"
    
    @classmethod
    def _find_cached_model_dir(cls) -> Optional[str]:
        """Find the cached model directory (local or HF cache).
        
        Returns the path if found, None otherwise.
        Does NOT validate file integrity — use _validate_model_dir for that.
        """
        # Check local cache first
        local_cache = os.path.expanduser("~/.cache/kokoro-mlx-model")
        if (os.path.isdir(local_cache)
                and os.path.isfile(os.path.join(local_cache, "config.json"))):
            return local_cache
        
        # Check HF cache
        import glob
        hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
        kokoro_cache = os.path.join(hf_cache, "models--mlx-community--Kokoro-82M-bf16")
        if os.path.isdir(kokoro_cache):
            snapshots = sorted(glob.glob(os.path.join(kokoro_cache, "snapshots", "*")))
            for snap in snapshots:
                if os.path.isfile(os.path.join(snap, "config.json")):
                    return snap
        
        return None
    
    @classmethod
    def _download_model_files(cls, force_redownload: bool = False) -> str:
        """Locate or download only the model files needed for inference.
        
        Check order:
        1. Local curl-based cache (~/.cache/kokoro-mlx-model)
        2. HuggingFace Hub cache (via snapshot_download)
        
        Validates file integrity before returning cached paths.
        If files are corrupt/truncated, clears cache and re-downloads.
        
        The HF repo contains ~2GB of WAV samples we don't need.
        allow_patterns limits to config.json and *.safetensors only.
        """
        import shutil
        
        local_cache = os.path.expanduser("~/.cache/kokoro-mlx-model")
        hf_cache = os.path.expanduser("~/.cache/huggingface/hub")
        kokoro_hf_cache = os.path.join(hf_cache, "models--mlx-community--Kokoro-82M-bf16")
        
        if force_redownload:
            # Clear all caches to force a fresh download
            if os.path.isdir(local_cache):
                print(f"[AudioLLM] Clearing local cache for re-download...")
                shutil.rmtree(local_cache, ignore_errors=True)
            if os.path.isdir(kokoro_hf_cache):
                print(f"[AudioLLM] Clearing HF cache for re-download...")
                shutil.rmtree(kokoro_hf_cache, ignore_errors=True)
        else:
            # Check local cache first — validate before trusting
            if (os.path.isdir(local_cache)
                    and os.path.isfile(os.path.join(local_cache, "config.json"))
                    and os.path.isdir(os.path.join(local_cache, "voices"))):
                valid, err = cls._validate_model_dir(local_cache)
                if valid:
                    print(f"[AudioLLM] Using local model cache: {local_cache}")
                    return local_cache
                else:
                    print(f"[AudioLLM] Local cache corrupt ({err}), removing...")
                    shutil.rmtree(local_cache, ignore_errors=True)
            
            # Check HF cache — validate before trusting
            cached = cls._find_cached_model_dir()
            if cached and cached != local_cache:
                valid, err = cls._validate_model_dir(cached)
                if valid:
                    print(f"[AudioLLM] Using HF model cache: {cached}")
                    return cached
                else:
                    print(f"[AudioLLM] HF cache corrupt ({err}), clearing...")
                    shutil.rmtree(kokoro_hf_cache, ignore_errors=True)
        
        # Download via HuggingFace Hub — configure robust HTTP backend first
        # Matches install.sh: retries + timeouts + SSL handling for PyInstaller
        print(f"[AudioLLM] Downloading Kokoro-82M model (~330 MB)...")
        import sys
        import glob as _glob
        import requests as _requests
        from requests.adapters import HTTPAdapter
        from huggingface_hub import configure_http_backend
        try:
            from huggingface_hub.utils._http import reset_sessions
        except ImportError:
            reset_sessions = lambda: None

        _frozen = getattr(sys, 'frozen', False)

        # ── Clean up stale lock files that cause snapshot_download to hang ──
        locks_dir = os.path.join(hf_cache, ".locks")
        if os.path.isdir(locks_dir):
            stale_locks = _glob.glob(os.path.join(locks_dir, "**/*.lock"), recursive=True)
            if stale_locks:
                print(f"[AudioLLM] Clearing {len(stale_locks)} stale HF lock files...")
                for lf in stale_locks:
                    try:
                        os.remove(lf)
                    except OSError:
                        pass
        # Also clear .incomplete download markers
        for inc in _glob.glob(os.path.join(hf_cache, "**/*.incomplete"), recursive=True):
            try:
                os.remove(inc)
            except OSError:
                pass

        # ── Disable tqdm progress bars (hang in PyInstaller without terminal) ──
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        # Set download timeout env var as a backstop
        os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "120"

        class _TimeoutAdapter(HTTPAdapter):
            def send(self, *a, **kw):
                kw.setdefault('timeout', (30, 120))
                return super().send(*a, **kw)

        def _robust_factory() -> _requests.Session:
            s = _requests.Session()
            s.mount('http://', _TimeoutAdapter(max_retries=3))
            s.mount('https://', _TimeoutAdapter(max_retries=3))
            if _frozen or os.environ.get('LOCALBOOK_SSL_NOVERIFY') == '1':
                s.verify = False
            return s

        configure_http_backend(backend_factory=_robust_factory)
        reset_sessions()
        if _frozen:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            print(f"[AudioLLM] Using robust HTTP backend (retries=3, SSL verify=off for frozen build)")

        # ── Download directly to local_dir — bypasses HF blob/symlink/lock system ──
        # snapshot_download with local_dir puts files directly in our folder
        # instead of going through the blob cache that requires filelock.
        os.makedirs(local_cache, exist_ok=True)
        print(f"[AudioLLM] Downloading to {local_cache} (direct mode, no HF blob cache)...")
        
        try:
            from huggingface_hub import snapshot_download
            result = snapshot_download(
                repo_id=cls.MLX_KOKORO_REPO,
                allow_patterns=["config.json", "*.safetensors", "voices/*.safetensors"],
                local_dir=local_cache,
                local_dir_use_symlinks=False,
                force_download=force_redownload,
                max_workers=1,
            )
            print(f"[AudioLLM] snapshot_download complete: {result}")
        except Exception as e:
            print(f"[AudioLLM] snapshot_download failed: {e}, trying individual file downloads...")
            # Fallback: download files individually with hf_hub_download
            from huggingface_hub import hf_hub_download, list_repo_tree
            try:
                files = list(list_repo_tree(cls.MLX_KOKORO_REPO, recursive=True))
                target_files = [
                    f.rfilename for f in files
                    if f.rfilename == "config.json"
                    or (f.rfilename.endswith(".safetensors") and "/" not in f.rfilename)
                    or (f.rfilename.startswith("voices/") and f.rfilename.endswith(".safetensors"))
                ]
            except Exception:
                # Hard-coded fallback if API is also down
                target_files = ["config.json", "model.safetensors"]
            
            for fname in target_files:
                print(f"[AudioLLM] Downloading {fname}...")
                hf_hub_download(
                    repo_id=cls.MLX_KOKORO_REPO,
                    filename=fname,
                    local_dir=local_cache,
                    local_dir_use_symlinks=False,
                    force_download=True,
                )
                print(f"[AudioLLM] ✓ {fname} downloaded")
            result = local_cache
        
        # Validate the fresh download
        valid, err = cls._validate_model_dir(result)
        if not valid:
            raise RuntimeError(
                f"Downloaded model files are corrupt: {err}. "
                f"This may indicate a network issue. Try running Repair again."
            )
        
        print(f"[AudioLLM] ✓ All model files validated OK")
        return result
    
    @staticmethod
    def _fix_ssl_for_hf_download():
        """Apply SSL certificate fixes for HuggingFace Hub downloads.
        
        macOS Python 3.13 from python.org often has broken SSL certs.
        We try certifi's CA bundle first, then /etc/ssl/cert.pem.
        """
        try:
            import certifi
            ca_bundle = certifi.where()
            os.environ.setdefault("SSL_CERT_FILE", ca_bundle)
            os.environ.setdefault("REQUESTS_CA_BUNDLE", ca_bundle)
            os.environ.setdefault("CURL_CA_BUNDLE", ca_bundle)
        except ImportError:
            if os.path.exists("/etc/ssl/cert.pem"):
                os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    
    @staticmethod
    def _ssl_probe() -> bool:
        """Quick probe: can we reach huggingface.co with SSL verification?
        
        Returns True if SSL works, False if verification fails.
        """
        import urllib.request
        import ssl
        try:
            ctx = ssl.create_default_context()
            urllib.request.urlopen("https://huggingface.co", timeout=5, context=ctx)
            return True
        except Exception:
            return False
    
    @staticmethod
    def _disable_hf_ssl_verify():
        """Disable SSL verification for HuggingFace Hub downloads.
        
        Uses configure_http_backend to create requests Sessions with
        verify=False AND retries + timeouts. Without retries and timeouts,
        downloads hang forever in PyInstaller bundles.
        
        The model weights have their own checksums verified by HuggingFace Hub,
        so disabling SSL verification for the download is acceptable.
        """
        import requests
        from requests.adapters import HTTPAdapter
        from huggingface_hub import configure_http_backend
        try:
            from huggingface_hub.utils._http import reset_sessions
        except ImportError:
            reset_sessions = lambda: None
        
        class _TimeoutAdapter(HTTPAdapter):
            def send(self, *a, **kw):
                kw.setdefault('timeout', (30, 120))
                return super().send(*a, **kw)
        
        def robust_no_ssl_factory() -> requests.Session:
            session = requests.Session()
            session.verify = False
            session.mount('http://', _TimeoutAdapter(max_retries=3))
            session.mount('https://', _TimeoutAdapter(max_retries=3))
            return session
        
        # Clear cert env vars that override session.verify in requests
        for key in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE"):
            os.environ.pop(key, None)
        
        configure_http_backend(backend_factory=robust_no_ssl_factory)
        reset_sessions()
        
        # Suppress urllib3 InsecureRequestWarning
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    @staticmethod
    def _save_wav(path: str, audio_data, sample_rate: int = SAMPLE_RATE):
        """Save audio array as WAV. Accepts numpy arrays."""
        # Convert mlx array to numpy if needed
        if not isinstance(audio_data, np.ndarray):
            audio_data = np.array(audio_data, dtype=np.float32).flatten()
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
        return self._initialized and self._model is not None
    
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
    
    @staticmethod
    def _preprocess_text_for_tts(text: str) -> str:
        """Clean text for TTS — remove non-spoken artifacts that degrade pronunciation.
        
        Strips markdown formatting, citation markers, URLs, and other artifacts
        that Kokoro would try to pronounce literally (e.g., "hashtag hashtag" for ##,
        "bracket one bracket" for [1]).
        """
        # Strip citation markers: [1], [2], [1,2], [1][2]
        text = re.sub(r'\[\d+(?:,\s*\d+)*\]', '', text)
        
        # Strip markdown headings (## Header → Header)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        
        # Strip bold/italic markers
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        
        # Strip inline code backticks
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        # Strip fenced code blocks entirely (not speakable)
        text = re.sub(r'```[\s\S]*?```', '', text)
        
        # Strip URLs (replace with "link" or just remove)
        text = re.sub(r'https?://\S+', '', text)
        
        # Strip markdown links: [text](url) → text
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # Strip bullet/list markers
        text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
        
        # Strip horizontal rules
        text = re.sub(r'^[\s]*[-=_]{3,}[\s]*$', '', text, flags=re.MULTILINE)
        
        # Strip remaining markdown artifacts
        text = re.sub(r'\*+', '', text)
        text = re.sub(r'\[\s*\]', '', text)
        
        # Expand common abbreviations for better pronunciation
        text = re.sub(r'\be\.g\.\s*', 'for example, ', text, flags=re.IGNORECASE)
        text = re.sub(r'\bi\.e\.\s*', 'that is, ', text, flags=re.IGNORECASE)
        text = re.sub(r'\betc\.', 'etcetera', text, flags=re.IGNORECASE)
        text = re.sub(r'\bvs\.\s*', 'versus ', text, flags=re.IGNORECASE)
        text = re.sub(r'\bDr\.\s', 'Doctor ', text)
        text = re.sub(r'\bMr\.\s', 'Mister ', text)
        text = re.sub(r'\bMrs\.\s', 'Missus ', text)
        text = re.sub(r'\bMs\.\s', 'Ms ', text)
        
        # Clean up whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'  +', ' ', text)
        text = text.strip()
        
        return text

    def _tts_sync(
        self, 
        text: str, 
        voice: str, 
        output_path: Optional[str],
        speed: float = 1.0,
    ) -> str:
        """Synchronous TTS generation via kokoro-mlx.
        
        For short text: model.generate() returns TTSResult with audio as np.ndarray.
        For long text: chunks processed individually and crossfaded.
        kokoro-mlx handles language detection internally from voice prefix.
        """
        voice_id = resolve_voice(voice)
        
        # Preprocess text: clean markdown, citations, URLs, abbreviations
        text = self._preprocess_text_for_tts(text)
        
        # Chunk text for better prosody on long inputs
        chunks = self._chunk_text_for_tts(text)
        print(f"[AudioLLM] TTS: {len(chunks)} chunks, voice={voice_id}")
        
        all_segments: List[np.ndarray] = []
        failed_chunks = 0
        
        for i, chunk in enumerate(chunks):
            success = False
            for attempt in range(2):  # Try each chunk up to 2 times
                try:
                    result = self._model.generate(
                        chunk,
                        voice=voice_id,
                        speed=speed,
                    )
                    if result.audio is not None and len(result.audio) > 0:
                        audio_np = result.audio.flatten().astype(np.float32)
                        all_segments.append(audio_np)
                        dur = len(audio_np) / SAMPLE_RATE
                        retry_note = " (retry)" if attempt > 0 else ""
                        print(f"[AudioLLM]   chunk {i+1}/{len(chunks)}: {dur:.1f}s{retry_note}")
                        success = True
                        break
                    else:
                        print(f"[AudioLLM] Warning: chunk {i+1}/{len(chunks)} returned empty audio (attempt {attempt+1})")
                except Exception as e:
                    if attempt == 0:
                        print(f"[AudioLLM] Warning: chunk {i+1}/{len(chunks)} failed (attempt 1): {e} — retrying")
                    else:
                        print(f"[AudioLLM] ERROR: chunk {i+1}/{len(chunks)} failed after retry: {e}")
                        print(f"[AudioLLM]   chunk text ({len(chunk)} chars): {chunk[:80]}...")
            if not success:
                failed_chunks += 1
        
        if failed_chunks > 0:
            print(f"[AudioLLM] ⚠ TTS summary: {len(all_segments)}/{len(chunks)} chunks succeeded, {failed_chunks} failed")
        
        if not all_segments:
            raise RuntimeError(f"No audio generated from any text chunk ({len(chunks)} chunks all failed)")
        
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
    
    def _chunk_text_for_tts(self, text: str, max_chunk_chars: int = 350) -> list:
        """Split text into sentence-level chunks for TTS generation.
        
        Kokoro produces better prosody with shorter chunks (~350 chars).
        Longer chunks (500+) can cause pronunciation degradation and
        monotone output, especially mid-chunk.
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
    
    # Check kokoro-mlx (Kokoro TTS)
    try:
        import kokoro_mlx
        diag["kokoro_mlx"] = "ok"
    except Exception as e:
        diag["kokoro_mlx"] = f"FAIL: {e}"
    
    # Check mlx (Apple Silicon framework)
    try:
        import mlx.core as mx
        diag["mlx"] = "ok"
    except Exception as e:
        diag["mlx"] = f"FAIL: {e}"
    
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
    
    # Check HuggingFace model cache (MLX version) — validate file integrity
    import os
    cached_dir = AudioLLMService._find_cached_model_dir()
    if cached_dir:
        valid, err = AudioLLMService._validate_model_dir(cached_dir)
        if valid:
            diag["model_cached"] = "yes (validated)"
        else:
            diag["model_cached"] = f"CORRUPT: {err} — click Repair to re-download"
    else:
        diag["model_cached"] = "no (will download ~330 MB on first use)"
    
    all_ok = all("FAIL" not in str(v) for v in diag.values() if v != diag.get("model_cached"))
    
    return {
        "available": all_ok,
        "initialized": audio_llm.is_available,
        "message": "Kokoro-82M TTS (MLX) ready" if all_ok else "Some audio dependencies missing",
        "init_error": audio_llm._init_error,
        "diagnostics": diag
    }
