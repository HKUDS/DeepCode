#!/bin/bash
# DeepCode New UI - Production Build Script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "🏗️  Building DeepCode New UI for Production..."
echo ""

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Build Frontend
echo -e "${BLUE}📦 Building React Frontend...${NC}"
cd "$PROJECT_ROOT/frontend"

# Install dependencies if needed
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install
fi

# Build
npm run build

echo -e "${GREEN}✓ Frontend built successfully!${NC}"
echo "  Output: $PROJECT_ROOT/frontend/dist"
echo ""

# Backend doesn't need building (Python)
echo -e "${BLUE}📦 Backend is ready (Python - no build required)${NC}"
echo ""

echo "=========================================="
echo -e "${GREEN}🎉 Build complete!${NC}"
echo ""
echo "To run in production:"
echo ""
echo "  Backend:"
echo "    cd $PROJECT_ROOT/backend"
echo "    uvicorn main:app --host 0.0.0.0 --port 8000"
echo ""
echo "  Frontend (serve static files):"
echo "    npx serve $PROJECT_ROOT/frontend/dist"
echo ""
echo "=========================================="
