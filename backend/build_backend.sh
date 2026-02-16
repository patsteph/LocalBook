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

OUTPUT_DIR="../src-tauri/resources/backend"

echo -e "${YELLOW}Output: ${OUTPUT_DIR}${NC}"

# Clean previous build
rm -rf "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"

# Run PyInstaller in onedir mode (more reliable for complex apps)
echo -e "${YELLOW}Running PyInstaller (onedir mode)...${NC}"
pyinstaller \
    --onedir \
    --name "localbook-backend" \
    --distpath "$OUTPUT_DIR" \
    --workpath "./build" \
    --specpath "./build" \
    --clean \
    --noconfirm \
    --log-level WARN \
    --paths="$SCRIPT_DIR" \
    --add-data="$SCRIPT_DIR/api:api" \
    --add-data="$SCRIPT_DIR/services:services" \
    --add-data="$SCRIPT_DIR/storage:storage" \
    --add-data="$SCRIPT_DIR/models:models" \
    --add-data="$SCRIPT_DIR/utils:utils" \
    --add-data="$SCRIPT_DIR/agents:agents" \
    --add-data="$SCRIPT_DIR/static:static" \
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
    --hidden-import=api.writing \
    --hidden-import=services \
    --hidden-import=services.agent_browser \
    --hidden-import=services.audio_generator \
    --hidden-import=services.audio_llm \
    --hidden-import=services.citation_verifier \
    --hidden-import=services.collection_scheduler \
    --hidden-import=services.community_detection \
    --hidden-import=services.company_profiler \
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
    --hidden-import=services.activity_analyzer \
    --hidden-import=services.auto_tagger \
    --hidden-import=api.people \
    --hidden-import=models.person_profile \
    --hidden-import=playwright \
    --hidden-import=playwright.async_api \
    --hidden-import=playwright._impl \
    --hidden-import=playwright._impl._api_types \
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
    --hidden-import=agents \
    --hidden-import=agents.collector \
    --hidden-import=agents.curator \
    --hidden-import=agents.tools \
    --hidden-import=agents.state \
    --hidden-import=agents.supervisor \
    --collect-all=sentence_transformers \
    --collect-all=torch \
    --collect-all=transformers \
    --collect-all=trafilatura \
    --collect-all=justext \
    --collect-all=mlx \
    --collect-all=mlx_metal \
    --collect-all=mlx_whisper \
    --collect-all=liquid_audio \
    --collect-all=torchaudio \
    --copy-metadata=torchcodec \
    --collect-all=zstandard \
    --collect-all=bertopic \
    --collect-all=umap \
    --collect-all=hdbscan \
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
    --hidden-import=pdfplumber \
    --hidden-import=docx \
    --hidden-import=pptx \
    --collect-all=pptx \
    --collect-all=playwright \
    --hidden-import=lxml \
    --collect-all=lxml \
    --hidden-import=XlsxWriter \
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
    --exclude-module=torch.distributed \
    --exclude-module=torch.testing \
    --exclude-module=torch.utils.tensorboard \
    --exclude-module=torch._inductor \
    --exclude-module=torch._dynamo \
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

# Trim unnecessary bulk from bundle
echo -e "${YELLOW}Trimming unnecessary files from bundle...${NC}"
# torch C++ headers (59MB) - never needed at runtime
rm -rf "$OUTPUT_DIR/localbook-backend/_internal/torch/include" 2>/dev/null
# plotly (13MB) - pulled in by BERTopic but never rendered in our app
rm -rf "$OUTPUT_DIR/localbook-backend/_internal/plotly" 2>/dev/null
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
