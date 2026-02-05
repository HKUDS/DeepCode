"""
DeepCode New UI - FastAPI Backend Entry Point
"""

import sys
from pathlib import Path

# ============================================================
# Path Setup - Critical for avoiding module naming conflicts
# ============================================================
# Directory layout:
#   PROJECT_ROOT/              <- DeepCode root (config/, utils/, workflows/, prompts/, tools/)
#   PROJECT_ROOT/new_ui/
#   PROJECT_ROOT/new_ui/backend/  <- This file's directory (api/, models/, services/, settings.py)
#
# IMPORTANT: Backend modules (settings, models, services, api) must NOT shadow
# DeepCode modules (config, utils, workflows, prompts, tools).
# We renamed: config.py -> settings.py, utils/ -> app_utils/
# ============================================================

BACKEND_DIR = Path(__file__).resolve().parent
NEW_UI_DIR = BACKEND_DIR.parent
PROJECT_ROOT = NEW_UI_DIR.parent

# PROJECT_ROOT must be first so DeepCode modules (config, utils, etc.) are found correctly
# BACKEND_DIR must also be present so local modules (settings, api, models, services) are found
# Since there are no naming conflicts after renaming, order is safe
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(1, str(BACKEND_DIR))

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from settings import settings
from api.routes import workflows, requirements, config as config_routes, files
from api.websockets import workflow_ws, code_stream_ws, logs_ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management"""
    # Startup
    print("Starting DeepCode New UI Backend...")
    print(f"  Project root: {PROJECT_ROOT}")
    print(f"  Backend dir:  {BACKEND_DIR}")

    # Ensure upload directory exists
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    yield

    # Shutdown
    print("Shutting down DeepCode New UI Backend...")


app = FastAPI(
    title="DeepCode New UI API",
    description="Modern API backend for DeepCode - AI-powered code generation platform",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include REST API routes
app.include_router(workflows.router, prefix="/api/v1/workflows", tags=["Workflows"])
app.include_router(
    requirements.router, prefix="/api/v1/requirements", tags=["Requirements"]
)
app.include_router(
    config_routes.router, prefix="/api/v1/config", tags=["Configuration"]
)
app.include_router(files.router, prefix="/api/v1/files", tags=["Files"])

# Include WebSocket routes
app.include_router(workflow_ws.router, prefix="/ws", tags=["WebSocket"])
app.include_router(code_stream_ws.router, prefix="/ws", tags=["WebSocket"])
app.include_router(logs_ws.router, prefix="/ws", tags=["WebSocket"])


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": "DeepCode New UI API",
        "version": "1.0.0",
        "status": "running",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
