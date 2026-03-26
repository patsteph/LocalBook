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
    --add-data="$SCRIPT_DIR/static:static" \
    --add-data="$SCRIPT_DIR/templates:templates" \
    --add-data="$SCRIPT_DIR/config.py:." \
    --hidden-import=api \
    --hidden-import=api.agent \
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
    --hidden-import=services.ollama_client \
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
    --hidden-import=services.stuck_source_recovery \
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
    --hidden-import=agents \
    --hidden-import=agents.collector \
    --hidden-import=agents.curator \
    --hidden-import=agents.tools \
    --hidden-import=agents.state \
    --hidden-import=agents.supervisor \
    --collect-all=sentence_transformers \
    --collect-all=kokoro_mlx \
    --collect-all=trafilatura \
    --collect-all=justext \
    --collect-all=mlx \
    --collect-all=mlx_whisper \
    --collect-all=misaki \
    --collect-all=spacy \
    --collect-all=en_core_web_sm \
    --collect-all=phonemizer \
    --collect-all=num2words \
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

# Sign all binaries for macOS Sequoia compatibility
# macOS Sequoia requires proper code signing for all .so/.dylib files
echo -e "${YELLOW}Signing binaries for macOS compatibility...${NC}"
find "$OUTPUT_DIR/localbook-backend/_internal" -name "*.so" -exec codesign --force --sign - {} \; 2>/dev/null
find "$OUTPUT_DIR/localbook-backend/_internal" -name "*.dylib" -exec codesign --force --sign - {} \; 2>/dev/null
codesign --force --sign - "$OUTPUT_DIR/localbook-backend/localbook-backend" 2>/dev/null
echo -e "${GREEN}✓ Code signing complete${NC}"

# Show size
SIZE=$(du -sh "$OUTPUT_DIR/localbook-backend" | cut -f1)
echo -e "${GREEN}✓ Bundle size: $SIZE${NC}"

# Cleanup build artifacts
rm -rf ./build
rm -f ./*.spec

echo -e "${GREEN}✓ Build complete!${NC}"
