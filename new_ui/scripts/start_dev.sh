#!/bin/bash
# DeepCode New UI - Development Startup Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "🚀 Starting DeepCode New UI Development Environment..."
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Check if we're in the right directory
if [ ! -f "$PROJECT_ROOT/backend/main.py" ]; then
    echo "❌ Error: Please run this script from the new_ui directory"
    exit 1
fi

# Function to cleanup on exit
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    pkill -P $$ 2>/dev/null || true
}
trap cleanup EXIT

# Start Backend
echo -e "${BLUE}📦 Starting FastAPI Backend...${NC}"
cd "$PROJECT_ROOT/backend"

# Check if pydantic-settings is installed
if ! python -c "import pydantic_settings" 2>/dev/null; then
    echo "Installing pydantic-settings..."
    pip install pydantic-settings
fi

# Start uvicorn in background
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
echo -e "${GREEN}✓ Backend started on http://localhost:8000${NC}"
echo ""

# Start Frontend
echo -e "${BLUE}📦 Starting React Frontend...${NC}"
cd "$PROJECT_ROOT/frontend"

# Check if node_modules exists
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install
fi

# Start vite in background
npm run dev &
FRONTEND_PID=$!
echo -e "${GREEN}✓ Frontend started on http://localhost:5173${NC}"
echo ""

echo "=========================================="
echo -e "${GREEN}🎉 DeepCode New UI is running!${NC}"
echo ""
echo "  Frontend: http://localhost:5173"
echo "  Backend:  http://localhost:8000"
echo "  API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services"
echo "=========================================="

# Wait for both processes
wait
