#!/bin/bash

# LocalBook Launcher
# Starts all required services with a single command

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}                    LocalBook Launcher                       ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Cleanup function
cleanup() {
    echo -e "\n${YELLOW}Shutting down LocalBook...${NC}"
    if [ ! -z "$OLLAMA_PID" ]; then
        kill $OLLAMA_PID 2>/dev/null || true
    fi
    if [ ! -z "$BACKEND_PID" ]; then
        kill $BACKEND_PID 2>/dev/null || true
    fi
    if [ ! -z "$FRONTEND_PID" ]; then
        kill $FRONTEND_PID 2>/dev/null || true
    fi
    echo -e "${GREEN}Goodbye!${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Check prerequisites
echo -e "\n${YELLOW}Checking prerequisites...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 not found. Install with: brew install python${NC}"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo -e "${RED}Error: Node.js not found. Install with: brew install node${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All prerequisites found${NC}"

# No engine daemon to start and no models to pull: MLX runs in-process and its models come
# from the HuggingFace cache. This used to `ollama serve` and pull three models, and would
# `exit 1` if Ollama was missing — a hard failure for a machine that no longer needs it.

# Set up Python virtual environment if needed
if [ ! -d "backend/.venv" ]; then
    echo -e "${YELLOW}Setting up Python environment (first run only)...${NC}"
    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -q -r requirements.txt
    # kokoro needs --no-deps (upstream misaki version mismatch in metadata)
    pip install -q --no-deps kokoro 2>/dev/null || true
    cd ..
    echo -e "${GREEN}✓ Python environment ready${NC}"
fi

# Start backend
echo -e "${YELLOW}Starting backend server...${NC}"
BACKEND_LOG="$SCRIPT_DIR/backend.log"
cd backend
source .venv/bin/activate
python main.py > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
cd ..
echo -e "${BLUE}Backend logs: $BACKEND_LOG${NC}"

# Fast poll for backend readiness — no fixed sleep
for i in {1..60}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Backend ready${NC}"
        break
    fi
    if [ $i -eq 60 ]; then
        echo -e "${RED}Error: Backend failed to start. Check $BACKEND_LOG${NC}"
        cleanup
        exit 1
    fi
    sleep 0.5
done

# Install npm dependencies if needed
if [ ! -d "node_modules" ]; then
    echo -e "${YELLOW}Installing frontend dependencies (first run only)...${NC}"
    npm install --silent
    echo -e "${GREEN}✓ Frontend dependencies ready${NC}"
fi

# Start Tauri dev
echo -e "${YELLOW}Starting LocalBook app...${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  LocalBook is starting! The app window will open shortly.  ${NC}"
echo -e "${GREEN}  Press Ctrl+C to stop all services.                        ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

npm run tauri dev &
FRONTEND_PID=$!

# Wait for frontend process
wait $FRONTEND_PID

cleanup
