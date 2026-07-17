#!/bin/bash

# Build the LocalBook backend as a standalone bundle using PyInstaller
# This creates a folder that Tauri bundles as a resource with the app

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Building LocalBook backend...${NC}"

# Ensure virtual environment exists
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv .venv
fi

# Activate virtual environment
source .venv/bin/activate

# Install dependencies if needed
if ! python -c "import pyinstaller" 2>/dev/null; then
    echo -e "${YELLOW}Installing dependencies...${NC}"
    pip install -q -r requirements.txt
fi

# kokoro-mlx: Kokoro-82M TTS on Apple Silicon via MLX.
# Install --no-deps because it declares misaki>=0.9.4 but PyPI only has 0.7.4
# (works fine at runtime). Also avoids pulling unnecessary transitive deps.
if ! python -c "import kokoro_mlx" 2>/dev/null; then
    echo -e "${YELLOW}Installing kokoro-mlx (--no-deps)...${NC}"
    pip install -q --no-deps kokoro-mlx 2>/dev/null || echo -e "${YELLOW}  kokoro-mlx install warning (non-fatal)${NC}"
fi

# Verify critical TTS imports — warn loudly if missing
if ! python -c "import kokoro_mlx; import misaki; import soundfile" 2>/dev/null; then
    echo -e "${RED}⚠ WARNING: kokoro-mlx TTS packages not importable after install.${NC}"
    echo -e "${RED}  Audio generation (podcasts, video narration) will not work.${NC}"
    echo -e "${RED}  Try: pip install --no-deps kokoro-mlx && pip install misaki soundfile${NC}"
fi

# mlx-lm / mlx-vlm: Wave 9 in-process MLX LLM engine (opt-in, dual-engine).
# Install --no-deps because their declared trees conflict with LocalBook's (mlx-vlm pins
# starlette>=1.0.1 vs fastapi <0.51.0; both pin transformers 5.x) — but they run fine on the
# existing transformers 5.12.1. Real runtime needs (mlx, torchvision, sentencepiece, protobuf)
# are in requirements.in. Lazy-imported: only loaded when a role's engine == "mlx".
#
# PIN EXACT VERSIONS. Unpinned `>=` let a fresh machine pull *latest* — mlx-vlm 0.6.5 (2026-07)
# requires transformers>=5.14, but our validated lock is 5.12.1 (0.6.4 caps at <5.13). That drift
# is the source of the "rebuild flagging errors" reports. The version-aware guard reinstalls the
# pinned build even when a WRONG version is already present (downgrades 0.6.5 → 0.6.4).
MLXLM_VER="0.31.3"; MLXVLM_VER="0.6.4"
if ! python -c "import importlib.metadata as m; assert m.version('mlx-lm')=='$MLXLM_VER' and m.version('mlx-vlm')=='$MLXVLM_VER'" 2>/dev/null; then
    echo -e "${YELLOW}Installing mlx-lm==$MLXLM_VER + mlx-vlm==$MLXVLM_VER (--no-deps, Wave 9 MLX engine)...${NC}"
    pip install -q --no-deps "mlx-lm==$MLXLM_VER" "mlx-vlm==$MLXVLM_VER" 2>/dev/null || echo -e "${YELLOW}  mlx-lm/mlx-vlm install warning (non-fatal — MLX opt-in only)${NC}"
fi

# mflux: FLUX.2 Klein image generation on MLX (Wave 9.3b, opt-in image_engine=mlx).
# --no-deps to avoid re-pinning our stack; its runtime deps (mlx, numpy, Pillow, huggingface_hub,
# tqdm) are already present. Lazy-imported. PINNED for the same reproducibility reason: mflux 0.18.0
# validated against our mlx 0.32 (its declared mlx<0.32 cap is advisory — it imports + runs fine).
MFLUX_VER="0.18.0"
if ! python -c "import importlib.metadata as m; assert m.version('mflux')=='$MFLUX_VER'" 2>/dev/null; then
    echo -e "${YELLOW}Installing mflux==$MFLUX_VER (--no-deps, Wave 9.3b Klein/FLUX image engine)...${NC}"
    pip install -q --no-deps "mflux==$MFLUX_VER" 2>/dev/null || echo -e "${YELLOW}  mflux install warning (non-fatal — MLX image opt-in only)${NC}"
fi

# Ensure Playwright Chromium browser is installed — required by People Profiler social auth.
# Without this, playwright.chromium.launch() fails with "Executable doesn't exist" error.
if ! python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); p.chromium.executable_path; p.stop()" 2>/dev/null; then
    echo -e "${YELLOW}Installing Playwright Chromium browser...${NC}"
    python -m playwright install chromium 2>/dev/null || echo -e "${YELLOW}  Playwright browser install warning (non-fatal)${NC}"
fi

# Ensure spacy en_core_web_sm model is installed — required by misaki (kokoro-mlx phonemizer).
# Without this, misaki's G2P.__init__ calls spacy.cli.download() at runtime which crashes
# in a PyInstaller bundle (sys.executable is the frozen binary, not Python).
if ! python -c "import en_core_web_sm" 2>/dev/null; then
    echo -e "${YELLOW}Installing spacy en_core_web_sm model (required by kokoro-mlx)...${NC}"
    python -m spacy download en_core_web_sm 2>/dev/null || echo -e "${YELLOW}  spacy model install warning (non-fatal)${NC}"
fi

OUTPUT_DIR="../src-tauri/resources/backend"

echo -e "${YELLOW}Output: ${OUTPUT_DIR}${NC}"

# Wave 9 — strip dev-only spike artifacts before bundling. scripts/ is --add-data'd whole,
# but scripts/local/ is throwaway spikes; the sidecar-spike Swift .build alone was ~1.6 GB and
# doubled the .app. Production only uses scripts/ root (e.g. start_bonsai_sidecar.sh).
echo -e "${YELLOW}Stripping dev-only spike build artifacts from scripts/local...${NC}"
rm -rf "$SCRIPT_DIR"/scripts/local/*/.build "$SCRIPT_DIR"/scripts/local/*/.swiftpm \
       "$SCRIPT_DIR"/scripts/local/*_venv "$SCRIPT_DIR"/scripts/local/*/results 2>/dev/null || true

# Clean previous build
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# Run PyInstaller in onedir mode (more reliable for complex apps)
echo -e "${YELLOW}Running PyInstaller (onedir mode)...${NC}"
# Suppress noisy third-party warnings:
# PYTHONWARNINGS: catches FutureWarning (torch ddp_comm_hooks), SyntaxWarning (hdbscan/docopt/misaki)
# python -W: catches DeprecationWarning from PyInstaller's __import__ hooks (torch.distributed)
# --log-level ERROR: hides PyInstaller's own WARNINGs (pandas.plotting, tensorboard, scipy, ctypes libs)
# 2>&1 | grep -v: catches the 3 stubborn torch.distributed DeprecationWarnings that bypass all filters
export PYTHONWARNINGS="ignore::FutureWarning,ignore::DeprecationWarning,ignore::SyntaxWarning"
python -W ignore -m PyInstaller \
    --onedir \
    --name "localbook-backend" \
    --distpath "$OUTPUT_DIR" \
    --workpath "./build" \
    --specpath "./build" \
    --clean \
    --noconfirm \
    --log-level ERROR \
    --paths="$SCRIPT_DIR" \
    --add-data="$SCRIPT_DIR/api:api" \
    --add-data="$SCRIPT_DIR/services:services" \
    --add-data="$SCRIPT_DIR/storage:storage" \
    --add-data="$SCRIPT_DIR/models:models" \
    --add-data="$SCRIPT_DIR/utils:utils" \
    --add-data="$SCRIPT_DIR/agents:agents" \
    --add-data="$SCRIPT_DIR/scripts:scripts" \
    --add-data="$SCRIPT_DIR/static:static" \
    --add-data="$SCRIPT_DIR/templates:templates" \
    --add-data="$SCRIPT_DIR/evaluator/test_content:evaluator/test_content" \
    --add-data="$SCRIPT_DIR/evaluator/test_fixtures:evaluator/test_fixtures" \
    --add-data="$SCRIPT_DIR/evaluator/registry_data:evaluator/registry_data" \
    --add-data="$SCRIPT_DIR/config.py:." \
    --hidden-import=api \
    --hidden-import=api.agent_browser \
    --hidden-import=api.audio \
    --hidden-import=api.audio_llm \
    --hidden-import=api.browser \
    --hidden-import=api.chat \
    --hidden-import=api.collector \
    --hidden-import=api.constellation_ws \
    --hidden-import=api.content \
    --hidden-import=api.contradictions \
    --hidden-import=api.credentials \
    --hidden-import=api.curator \
    --hidden-import=api.embeddings \
    --hidden-import=api.evaluator \
    --hidden-import=api.exploration \
    --hidden-import=api.export \
    --hidden-import=api.findings \
    --hidden-import=api.graph \
    --hidden-import=api.health_portal \
    --hidden-import=api.jobs \
    --hidden-import=api.memory \
    --hidden-import=api.notebooks \
    --hidden-import=api.quiz \
    --hidden-import=api.rag_health \
    --hidden-import=api.reindex \
    --hidden-import=api.rlm \
    --hidden-import=api.settings \
    --hidden-import=api.site_search \
    --hidden-import=api.skills \
    --hidden-import=api.source_discovery \
    --hidden-import=api.source_viewer \
    --hidden-import=api.sources \
    --hidden-import=api.timeline \
    --hidden-import=api.updates \
    --hidden-import=api.visual \
    --hidden-import=api.voice \
    --hidden-import=api.web \
    --hidden-import=api.video \
    --hidden-import=api.writing \
    --hidden-import=services \
    --hidden-import=services.agent_browser \
    --hidden-import=services.audio_generator \
    --hidden-import=services.audio_llm \
    --hidden-import=services.activity_ledger \
    --hidden-import=services.citation_verifier \
    --hidden-import=services.collection_scheduler \
    --hidden-import=services.community_detection \
    --hidden-import=services.company_profiler \
    --hidden-import=services.context_builder \
    --hidden-import=services.content_fetcher \
    --hidden-import=services.contradiction_detector \
    --hidden-import=services.credential_locker \
    --hidden-import=services.document_processor \
    --hidden-import=services.entity_extractor \
    --hidden-import=services.entity_graph \
    --hidden-import=services.event_logger \
    --hidden-import=storage.findings_store \
    --hidden-import=services.hierarchical_chunker \
    --hidden-import=services.job_queue \
    --hidden-import=services.knowledge_graph \
    --hidden-import=services.memory_agent \
    --hidden-import=services.memory_manager \
    --hidden-import=services.migration_manager \
    --hidden-import=services.model_warmup \
    --hidden-import=services.multimodal_extractor \
    --hidden-import=services.output_templates \
    --hidden-import=services.query_decomposer \
    --hidden-import=services.query_orchestrator \
    --hidden-import=services.rag_cache \
    --hidden-import=services.rag_chunking \
    --hidden-import=services.rag_engine \
    --hidden-import=services.rag_generation \
    --hidden-import=services.rag_metrics \
    --hidden-import=services.rag_query_analyzer \
    --hidden-import=services.rag_context \
    --hidden-import=services.rag_embeddings \
    --hidden-import=services.rag_llm \
    --hidden-import=services.rag_search \
    --hidden-import=services.rag_storage \
    --hidden-import=services.rag_verification \
    --hidden-import=services.rlm_executor \
    --hidden-import=services.site_search \
    --hidden-import=services.source_discovery \
    --hidden-import=services.source_ingestion \
    --hidden-import=services.source_router \
    --hidden-import=services.startup_checks \
    --hidden-import=services.structured_llm \
    --hidden-import=services.mlx_engine \
    --hidden-import=services.mlx_download \
    --hidden-import=services.stuck_source_recovery \
    --hidden-import=services.keychain_manager \
    --hidden-import=services.shallow_scrape_remediation \
    --hidden-import=services.svg_templates \
    --hidden-import=services.template_scorer \
    --hidden-import=services.theme_extractor \
    --hidden-import=services.topic_modeling \
    --hidden-import=services.visual_analyzer \
    --hidden-import=services.visual_cache \
    --hidden-import=services.visual_generator \
    --hidden-import=services.visual_router \
    --hidden-import=services.web_fallback \
    --hidden-import=services.web_scraper \
    --hidden-import=services.social_auth \
    --hidden-import=services.social_collector \
    --hidden-import=services.profile_indexer \
    --hidden-import=services.coaching_insights \
    --hidden-import=services.change_detector \
    --hidden-import=services.playwright_utils \
    --hidden-import=services.mermaid_renderer \
    --hidden-import=services.video_storyboard \
    --hidden-import=services.video_slide_renderer \
    --hidden-import=services.video_compositor \
    --hidden-import=services.video_generator \
    --hidden-import=services.activity_analyzer \
    --hidden-import=services.auto_tagger \
    --hidden-import=api.people \
    --hidden-import=models.person_profile \
    --hidden-import=playwright \
    --hidden-import=playwright.async_api \
    --hidden-import=playwright._impl \
    --hidden-import=playwright._impl._api_structures \
    --hidden-import=playwright._impl._connection \
    --hidden-import=playwright._impl._driver \
    --hidden-import=greenlet \
    --hidden-import=pyee \
    --hidden-import=pyee.asyncio \
    --hidden-import=storage \
    --hidden-import=storage.database \
    --hidden-import=storage.migrate_json_to_sqlite \
    --hidden-import=storage.notebook_store \
    --hidden-import=storage.source_store \
    --hidden-import=storage.audio_store \
    --hidden-import=storage.video_store \
    --hidden-import=storage.content_store \
    --hidden-import=storage.exploration_store \
    --hidden-import=storage.highlights_store \
    --hidden-import=storage.skills_store \
    --hidden-import=storage.memory_store \
    --hidden-import=models \
    --hidden-import=models.memory \
    --hidden-import=models.knowledge_graph \
    --hidden-import=config \
    --hidden-import=utils \
    --hidden-import=utils.tasks \
    --hidden-import=utils.diagnostics \
    --hidden-import=utils.model_display \
    --hidden-import=agents \
    --hidden-import=agents.collector \
    --hidden-import=agents.curator \
    --collect-submodules=agents.curator \
    --collect-submodules=agents.collector \
    --collect-submodules=services.curator_brain \
    --collect-submodules=api.chat \
    --hidden-import=agents.tools \
    --hidden-import=agents.state \
    --collect-all=sentence_transformers \
    --collect-all=evaluator \
    --collect-all=kokoro_mlx \
    --collect-all=trafilatura \
    --collect-all=justext \
    --collect-all=mlx \
    --collect-all=mlx_whisper \
    --collect-all=mlx_lm \
    --collect-all=mlx_vlm \
    --collect-all=mflux \
    --collect-all=torchvision \
    --collect-all=misaki \
    --collect-all=spacy \
    --collect-all=en_core_web_sm \
    --collect-all=phonemizer \
    --collect-all=num2words \
    --collect-data=tld \
    --hidden-import=soundfile \
    --collect-all=loguru \
    --collect-all=dlinfo \
    --collect-all=segments \
    --collect-all=csvw \
    --collect-all=rdflib \
    --collect-all=isodate \
    --collect-all=language_tags \
    --collect-all=rfc3986 \
    --collect-all=uritemplate \
    --collect-all=termcolor \
    --collect-all=thinc \
    --collect-all=blis \
    --collect-all=cymem \
    --collect-all=murmurhash \
    --collect-all=preshed \
    --collect-all=srsly \
    --collect-all=catalogue \
    --collect-all=wasabi \
    --collect-all=weasel \
    --collect-all=confection \
    --collect-all=cloudpathlib \
    --collect-all=smart_open \
    --collect-all=spacy_legacy \
    --collect-all=spacy_loggers \
    --hidden-import=docopt \
    --collect-all=zstandard \
    --collect-all=bertopic \
    --collect-all=joblib \
    --collect-submodules=pandas.core \
    --collect-submodules=pandas.io \
    --collect-submodules=pandas.tslibs \
    --collect-submodules=pandas.compat \
    --collect-submodules=pandas.api \
    --collect-submodules=pandas.plotting \
    --collect-submodules=pandas.errors \
    --collect-submodules=pandas._libs \
    --collect-data=lancedb \
    --collect-data=tiktoken \
    --hidden-import=sklearn.cluster \
    --hidden-import=trafilatura \
    --hidden-import=httpx \
    --hidden-import=youtube_transcript_api \
    --hidden-import=keyring \
    --hidden-import=dateparser \
    --hidden-import=fitz \
    --hidden-import=docx \
    --hidden-import=pptx \
    --collect-all=pptx \
    --collect-all=playwright \
    --hidden-import=lxml \
    --collect-all=lxml \
    --hidden-import=xlsxwriter \
    --hidden-import=openpyxl \
    --hidden-import=xlrd \
    --hidden-import=pandas._config \
    --hidden-import=anthropic \
    --hidden-import=openai \
    --hidden-import=multiprocessing \
    --hidden-import=rank_bm25 \
    --hidden-import=ebooklib \
    --hidden-import=nbformat \
    --hidden-import=odf \
    --hidden-import=pytesseract \
    --hidden-import=PIL \
    --hidden-import=feedparser \
    --hidden-import=sgmllib \
    --hidden-import=aiohttp \
    --collect-all=langchain_core \
    --collect-all=langgraph \
    --hidden-import=pymupdf4llm \
    --hidden-import=pillow_heif \
    --hidden-import=olefile \
    --hidden-import=psutil \
    --hidden-import=yaml \
    --hidden-import=cryptography \
    --hidden-import=zoneinfo \
    --collect-all=objc \
    --collect-all=Quartz \
    --collect-all=Vision \
    --collect-all=CoreML \
    --collect-all=Cocoa \
    --collect-all=LocalAuthentication \
    --collect-all=Security \
    --hidden-import=Foundation \
    --hidden-import=CoreFoundation \
    --exclude-module=boto3 \
    --exclude-module=botocore \
    --exclude-module=s3transfer \
    --exclude-module=pandas.tests \
    --exclude-module=plotly \
    --exclude-module=torch.distributed \
    --exclude-module=torch.testing \
    --exclude-module=torch._inductor \
    --exclude-module=tensorboard \
    --exclude-module=matplotlib \
    main.py

# Make the main executable... executable
chmod +x "$OUTPUT_DIR/localbook-backend/localbook-backend"

# Fix MLX metallib not found in PyInstaller bundle
# PyInstaller moves libmlx.dylib to _internal/ but mlx expects mlx.metallib colocated with it
MLX_METALLIB=$(find "$OUTPUT_DIR/localbook-backend/_internal" -name "mlx.metallib" -type f 2>/dev/null | head -1)
if [ -n "$MLX_METALLIB" ]; then
    echo -e "${YELLOW}Fixing MLX metallib path for PyInstaller bundle...${NC}"
    cp "$MLX_METALLIB" "$OUTPUT_DIR/localbook-backend/_internal/mlx.metallib" 2>/dev/null || true
fi

# Fix pandas._config not being bundled by PyInstaller
# This is a known PyInstaller issue with pandas - manually copy the _config module
PANDAS_CONFIG=$(find .venv/lib -path "*/pandas/_config" -type d 2>/dev/null | head -1)
if [ -n "$PANDAS_CONFIG" ]; then
    echo -e "${YELLOW}Fixing pandas._config bundling issue...${NC}"
    cp -r "$PANDAS_CONFIG" "$OUTPUT_DIR/localbook-backend/_internal/pandas/" 2>/dev/null || true
fi

# Bring in ALL of torch's Python source. PyInstaller's collect_submodules("torch") silently
# drops several submodules (distributed, testing, _inductor.test_operators, …) that transformers
# 5.x pulls in when mlx-lm/mlx-vlm resolve AutoTokenizer/AutoProcessor — without them MLX fails to
# import and silently falls back to Ollama. Rather than chase each missing submodule, copy the whole
# torch .py tree from the venv (only ~+48 MB — the bulk of torch is its binaries, already bundled).
# EXCLUDE: lib/bin (the compiled .dylibs — PyInstaller already bundles them; re-copying duplicates
# ~200 MB and risks a double-load) and _C/csrc/include/share (C-extension type stubs + headers — the
# _C stub DIR shadows the real _C .so extension and breaks torch's init: "torch has no attribute
# float16" circular import). --ignore-existing preserves PyInstaller's processed files.
VENV_TORCH=$(find .venv/lib -maxdepth 4 -path "*/site-packages/torch" -type d 2>/dev/null | head -1)
BUNDLE_TORCH="$OUTPUT_DIR/localbook-backend/_internal/torch"
if [ -n "$VENV_TORCH" ] && [ -d "$BUNDLE_TORCH" ]; then
    echo -e "${YELLOW}Completing torch Python source in bundle (required for MLX via transformers 5.x)...${NC}"
    rsync -a --ignore-existing \
        --exclude='lib/' --exclude='bin/' --exclude='_C/' --exclude='csrc/' \
        --exclude='include/' --exclude='share/' \
        "$VENV_TORCH/" "$BUNDLE_TORCH/" 2>/dev/null || true
fi

# Bring in ALL of transformers' Python source — SAME reason as torch above. PyInstaller's
# collect_submodules("transformers") silently drops lazy model submodules that raise during its
# import-based discovery (e.g. transformers.models.diffusion_gemma, which mlx-vlm's prompt_utils
# imports on EVERY gemma generation via apply_chat_template). Missing it → "No module named
# 'transformers.models.diffusion_gemma'" → gemma MLX dies and falls back to Ollama. transformers
# is pure Python (no compiled libs to shadow), so a plain --ignore-existing rsync of the whole
# tree is safe and ends the lazy-submodule whack-a-mole permanently (~+60 MB).
VENV_TF=$(find .venv/lib -maxdepth 4 -path "*/site-packages/transformers" -type d 2>/dev/null | head -1)
BUNDLE_TF="$OUTPUT_DIR/localbook-backend/_internal/transformers"
if [ -n "$VENV_TF" ] && [ -d "$BUNDLE_TF" ]; then
    echo -e "${YELLOW}Completing transformers Python source in bundle (lazy model submodules e.g. diffusion_gemma)...${NC}"
    rsync -a --ignore-existing "$VENV_TF/" "$BUNDLE_TF/" 2>/dev/null || true
fi

echo -e "${GREEN}✓ Backend built: $OUTPUT_DIR/localbook-backend/${NC}"

# Verify kokoro-mlx TTS bundled correctly — fail fast if any dep is missing
echo -e "${YELLOW}Verifying kokoro-mlx TTS bundle integrity...${NC}"
TTS_EXIT=0
PYTHONPATH="$OUTPUT_DIR/localbook-backend/_internal" python -c "
import sys, importlib
mods = ['kokoro_mlx','mlx','misaki','phonemizer','segments','csvw','language_tags',
        'rdflib','soundfile','loguru','num2words','dlinfo','spacy','en_core_web_sm',
        'thinc','blis','cymem','murmurhash','preshed','srsly','catalogue','isodate']
failed = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        failed.append(f'{m}: {e}')
if failed:
    print('TTS BUNDLE VERIFICATION FAILED:')
    for f in failed:
        print(f'  ✗ {f}')
    sys.exit(1)
else:
    print('All TTS imports verified OK')
" 2>/dev/null || TTS_EXIT=$?
if [ $TTS_EXIT -eq 0 ]; then
    echo -e "${GREEN}✓ kokoro-mlx TTS bundle verified${NC}"
else
    echo -e "${RED}✗ kokoro-mlx TTS bundle verification FAILED — check missing deps above${NC}"
    echo -e "${YELLOW}  The build will continue but TTS may not work at runtime${NC}"
fi

# Verify MLX LLM engine (Wave 9) bundled. mlx-lm/mlx-vlm are lazy-imported (only when
# a role's engine == "mlx"), so a bundling miss would stay hidden until a user opts into
# MLX in Labs — assert at build time instead. Non-fatal (Ollama path unaffected).
echo -e "${YELLOW}Verifying MLX LLM engine (mlx-lm / mlx-vlm) bundle integrity...${NC}"
MLXLLM_EXIT=0
MLX_OUT=$(PYTHONPATH="$OUTPUT_DIR/localbook-backend/_internal" python -c "
import sys, importlib
failed = []
# Import the EXACT module chain the gemma runtime touches — cheap, no model load. mlx_vlm's
# load()/apply_chat_template() pull in transformers.models.diffusion_gemma at load time (via the
# transformers auto-registry); PyInstaller's collect_submodules drops that lazy submodule, which
# is why gemma fell back with 'No module named transformers.models.diffusion_gemma'. Importing it
# (+ torchvision + Gemma4Processor) directly asserts the bundle is complete. The bring-in-ALL-
# transformers rsync above is the actual fix; this is the guard. (A full real-load smoke test is
# NOT shipped in the build — it was a one-off used to prove the fix; too heavy for every build.)
mods = ['mlx_lm','mlx_lm.sample_utils','mlx_vlm','mlx_vlm.models.gemma4',
        'mlx_vlm.prompt_utils','mlx_vlm.utils','torchvision',
        'transformers.models.diffusion_gemma']
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        failed.append(f'{m}: {type(e).__name__}: {e}')
try:
    from transformers import Gemma4Processor  # noqa: F401  (the processor→torchvision chain)
except Exception as e:
    failed.append(f'transformers.Gemma4Processor: {type(e).__name__}: {e}')
if failed:
    print('MLX LLM ENGINE BUNDLE VERIFICATION FAILED:')
    for f in failed:
        print(f'  ✗ {f}')
    sys.exit(1)
else:
    print('MLX LLM engine + gemma-4 import chain verified OK')
" 2>&1); MLXLLM_EXIT=$?
# Show the verdict line(s) without any HF noise.
echo "$MLX_OUT" | grep -vE 'Fetching|Warning: You are sending|UserWarning|warnings.warn|mel filter|rope_parameters|zero values|it/s\]' | tail -5
if [ $MLXLLM_EXIT -eq 0 ]; then
    echo -e "${GREEN}✓ MLX LLM engine bundle verified${NC}"
else
    echo -e "${YELLOW}⚠ MLX LLM engine bundle verification failed — MLX opt-in won't work until fixed (Ollama path unaffected)${NC}"
fi

# Verify PyObjC frameworks (Apple Vision OCR + Touch ID keychain). These fall
# back silently if Vision/CoreML/LocalAuthentication aren't collected, so assert
# them at build time rather than discover the loss at runtime in the .app.
echo -e "${YELLOW}Verifying PyObjC (Vision OCR + Touch ID) bundle integrity...${NC}"
PYOBJC_EXIT=0
PYTHONPATH="$OUTPUT_DIR/localbook-backend/_internal" python -c "
import sys, importlib
mods = ['objc','Foundation','Quartz','Vision','CoreML','LocalAuthentication','Security']
failed = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        failed.append(f'{m}: {e}')
if failed:
    print('PYOBJC BUNDLE VERIFICATION FAILED:')
    for f in failed:
        print(f'  ✗ {f}')
    sys.exit(1)
else:
    print('All PyObjC framework imports verified OK')
" 2>/dev/null || PYOBJC_EXIT=$?
if [ $PYOBJC_EXIT -eq 0 ]; then
    echo -e "${GREEN}✓ PyObjC frameworks bundled (Apple Vision OCR + Touch ID active)${NC}"
else
    echo -e "${RED}✗ PyObjC bundle verification FAILED — Apple Vision OCR / Touch ID would fall back${NC}"
    echo -e "${YELLOW}  Check --collect-all for objc/Vision/CoreML/Cocoa/LocalAuthentication/Security${NC}"
fi

# Trim unnecessary bulk from bundle
echo -e "${YELLOW}Trimming unnecessary files from bundle...${NC}"
# plotly: excluded at build time via --exclude-module=plotly
# BERTopic's find_spec("plotly") returns None → uses MockPlotlyModule (no plotting needed)
# babel locale data (32MB) - only dateparser uses it, keep just en
BABEL_LOCALE="$OUTPUT_DIR/localbook-backend/_internal/babel/locale-data"
if [ -d "$BABEL_LOCALE" ]; then
    find "$BABEL_LOCALE" -name "*.dat" ! -name "en*" ! -name "root*" -delete 2>/dev/null
fi
TRIMMED_SIZE=$(du -sh "$OUTPUT_DIR/localbook-backend" | cut -f1)
echo -e "${GREEN}✓ Bundle trimmed to: $TRIMMED_SIZE${NC}"

# Sign all binaries for macOS compatibility.
#
# Dual-mode signing:
#   * If APPLE_SIGNING_IDENTITY is set (release builds): sign with Developer
#     ID + hardened runtime + timestamp + entitlements. This produces
#     notarization-eligible binaries.
#   * Otherwise (local dev / end-user install.sh): adhoc sign ("-"). Satisfies
#     macOS Sequoia's signing requirement for .so/.dylib loading but is NOT
#     notarization-eligible. Same behavior as every release prior to Sprint 7.
#
# The entitlements.plist is the APP-level one (grants JIT, library-validation
# disable, etc.) — the Python backend is a child of LocalBook.app and inherits
# its TCC grants, but when signed as a standalone child executable under
# Hardened Runtime it needs these low-level entitlements explicitly.
BACKEND_INTERNAL="$OUTPUT_DIR/localbook-backend/_internal"
BACKEND_EXE="$OUTPUT_DIR/localbook-backend/localbook-backend"
ROOT_ENTITLEMENTS="../src-tauri/entitlements.plist"

# ─── Strip unsignable test fixtures before signing ──────────────────────────
# PyInstaller's data-collection pulls joblib's test/ directory (compressed-
# pickle test fixtures with magic-byte sequences that Apple's notary flags
# as "binary not signed with valid Developer ID"). These have no business
# in a production bundle. Apply to both Developer ID and adhoc paths since
# the adhoc bundle is still inspected by `codesign --verify --deep --strict`.
if [ -d "$BACKEND_INTERNAL/joblib/test" ]; then
    rm -rf "$BACKEND_INTERNAL/joblib/test"
    echo -e "${YELLOW}  Stripped joblib/test/ fixtures (not for production)${NC}"
fi

# Identify every Mach-O binary inside the PyInstaller bundle. Notarization
# fails on the FIRST unsigned Mach-O it finds — `.so` + `.dylib` are not
# enough: PyInstaller also embeds the Python interpreter (no extension),
# the full Python.framework, plus per-package CLI binaries (torch's
# protoc / torch_shm_manager, playwright's bundled node, etc.). We use
# `file -b` to find them all by content type rather than enumerating names.
#
# Skip the main backend executable here — it gets signed LAST with the app
# entitlements attached.
find_macho_files() {
    # `-type f` excludes symlinks (they're signed via their target).
    # The `printf` + read loop tolerates filenames with spaces.
    find "$BACKEND_INTERNAL" -type f -print0 | while IFS= read -r -d '' f; do
        if file -b "$f" 2>/dev/null | grep -q "Mach-O"; then
            printf '%s\0' "$f"
        fi
    done
}

if [ -n "${APPLE_SIGNING_IDENTITY:-}" ]; then
    # Developer ID mode: Adhoc-sign everything here, then let build.sh
    # do the REAL Developer ID + entitlements + timestamp + runtime signing
    # AFTER Tauri's bundler runs.
    #
    # Why: Tauri's `tauri build` bundles `src-tauri/resources/backend/...`
    # into `LocalBook.app/Contents/Resources/resources/backend/...`. PyInstaller
    # creates `_internal/Python` as a SYMLINK to `Python.framework/Versions/3.13/Python`.
    # Tauri's copy dereferences the symlink into a standalone file, breaking
    # any signature that was applied to the symlink in place. So per-file
    # Developer ID signing here is wasted work — Tauri throws it away.
    #
    # Solution: produce an adhoc-signed, runnable bundle here. Tauri can
    # bundle it (Tauri's --deep signing on the .app preserves inner adhoc
    # sigs). Then build.sh re-signs the backend inside the .app with the
    # real Developer ID + entitlements + runtime + timestamp, using the
    # post-deref file structure. See build.sh after `npm run tauri build`.
    echo -e "${YELLOW}Signing binaries with adhoc identity (real signing happens post-Tauri)...${NC}"
    find_macho_files | while IFS= read -r -d '' f; do
        codesign --force --sign - "$f" >/dev/null 2>&1 || true
    done
    if [ -d "$BACKEND_INTERNAL/Python.framework" ]; then
        codesign --force --sign - "$BACKEND_INTERNAL/Python.framework" 2>/dev/null || true
    fi
    codesign --force --sign - "$BACKEND_EXE" 2>/dev/null
    echo -e "${GREEN}✓ Adhoc signing complete (release.sh will Developer-ID-sign post-Tauri)${NC}"
else
    echo -e "${YELLOW}Signing binaries with adhoc identity (local/dev mode)...${NC}"
    # Adhoc signing — also covers all Mach-O, not just .so/.dylib, so the
    # adhoc bundle has the same structural validity as the signed one.
    find_macho_files | while IFS= read -r -d '' f; do
        codesign --force --sign - "$f" >/dev/null 2>&1 || true
    done
    if [ -d "$BACKEND_INTERNAL/Python.framework" ]; then
        codesign --force --sign - "$BACKEND_INTERNAL/Python.framework" 2>/dev/null || true
    fi
    codesign --force --sign - "$BACKEND_EXE" 2>/dev/null
    echo -e "${GREEN}✓ Adhoc signing complete${NC}"
fi

# Show size
SIZE=$(du -sh "$OUTPUT_DIR/localbook-backend" | cut -f1)
echo -e "${GREEN}✓ Bundle size: $SIZE${NC}"

# Cleanup build artifacts
rm -rf ./build
rm -f ./*.spec

echo -e "${GREEN}✓ Build complete!${NC}"
