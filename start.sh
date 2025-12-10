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

if ! command -v ollama &> /dev/null; then
    echo -e "${RED}Error: Ollama not found. Install with: brew install ollama${NC}"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 not found. Install with: brew install python${NC}"
    exit 1
fi

if ! command -v node &> /dev/null; then
    echo -e "${RED}Error: Node.js not found. Install with: brew install node${NC}"
    exit 1
fi

echo -e "${GREEN}✓ All prerequisites found${NC}"

# Check if Ollama is already running
if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Ollama already running${NC}"
else
    echo -e "${YELLOW}Starting Ollama...${NC}"
    ollama serve > /dev/null 2>&1 &
    OLLAMA_PID=$!
    sleep 2
    
    if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Ollama started${NC}"
    else
        echo -e "${RED}Error: Failed to start Ollama${NC}"
        exit 1
    fi
fi

# Check for required models
echo -e "${YELLOW}Checking AI models...${NC}"
MODELS=$(ollama list 2>/dev/null || echo "")

if ! echo "$MODELS" | grep -q "mistral-nemo"; then
    echo -e "${YELLOW}Downloading mistral-nemo model (this may take a few minutes)...${NC}"
    ollama pull mistral-nemo:12b-instruct-2407-q4_K_M
fi

if ! echo "$MODELS" | grep -q "phi4-mini"; then
    echo -e "${YELLOW}Downloading phi4-mini model...${NC}"
    ollama pull phi4-mini
fi

echo -e "${GREEN}✓ AI models ready${NC}"

# Set up Python virtual environment if needed
if [ ! -d "backend/.venv" ]; then
    echo -e "${YELLOW}Setting up Python environment (first run only)...${NC}"
    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -q -r requirements.txt
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
sleep 3
echo -e "${BLUE}Backend logs: $BACKEND_LOG${NC}"

# Wait for backend to be ready
for i in {1..30}; do
    if curl -s http://localhost:8000/health > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Backend ready${NC}"
        break
    fi
    if [ $i -eq 30 ]; then
        echo -e "${RED}Error: Backend failed to start${NC}"
        cleanup
        exit 1
    fi
    sleep 1
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
