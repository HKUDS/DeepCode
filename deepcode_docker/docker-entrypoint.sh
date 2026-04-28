#!/bin/bash
set -e

echo "============================================"
echo "  DeepCode - AI Research Engine (Docker)"
echo "============================================"

# ------ Validate configuration ------
if [ ! -f "deepcode_config.json" ]; then
    echo ""
    echo "❌ ERROR: deepcode_config.json not found!"
    echo ""
    echo "Mount your configuration file, e.g.:"
    echo "  docker run -v ./deepcode_config.json:/app/deepcode_config.json:ro ..."
    echo ""
    echo "Or use docker-compose with the provided template."
    echo ""
    exit 1
fi

# ------ Ensure directories exist ------
mkdir -p deepcode_lab uploads logs

# ------ CLI mode: launch interactive CLI ------
if [ "$1" = "cli" ]; then
    shift
    echo ""
    echo "🖥️  Starting DeepCode CLI..."
    echo "============================================"
    echo ""
    exec python cli/main_cli.py "$@"
fi

# ------ Web mode (default): start backend + frontend ------
echo ""
echo "🚀 Starting DeepCode..."
echo "   API:  http://localhost:${DEEPCODE_PORT:-8000}"
echo "   Docs: http://localhost:${DEEPCODE_PORT:-8000}/docs"
echo "============================================"
echo ""

exec python -m uvicorn new_ui.backend.main:app \
    --host "${DEEPCODE_HOST:-0.0.0.0}" \
    --port "${DEEPCODE_PORT:-8000}" \
    --workers 1 \
    --log-level info
