"""
DeepCode New UI - FastAPI Backend Entry Point

Supports two modes:
  - Development: Frontend runs on Vite dev server (port 5173), proxied to backend
  - Production/Docker: FastAPI serves the frontend static build directly
"""

import os
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
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from settings import settings
from api.routes import workflows, requirements, config as config_routes, files
from api.websockets import workflow_ws, code_stream_ws, logs_ws

# Check if running in Docker/production mode
IS_DOCKER = os.environ.get("DEEPCODE_ENV") == "docker"
FRONTEND_DIST = NEW_UI_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management"""
    # Startup
    print("Starting DeepCode New UI Backend...")
    print(f"  Project root: {PROJECT_ROOT}")
    print(f"  Backend dir:  {BACKEND_DIR}")
    print(f"  Mode:         {'Docker/Production' if IS_DOCKER else 'Development'}")

    if IS_DOCKER and FRONTEND_DIST.exists():
        print(f"  Frontend:     Serving static files from {FRONTEND_DIST}")
    elif IS_DOCKER:
        print(f"  ⚠️  Frontend dist not found at {FRONTEND_DIST}")

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


# ============================================================
# Static file serving for Docker/production mode
# In development, Vite dev server handles this via proxy
# ============================================================
if IS_DOCKER and FRONTEND_DIST.exists():
    # Serve static assets (JS, CSS, images, etc.)
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="static-assets",
    )

    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy"}

    # Catch-all: serve index.html for SPA client-side routing
    # This must be registered AFTER all API/WS routes
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve frontend SPA - fallback to index.html for client-side routing"""
        # Check if a static file exists at the requested path
        file_path = FRONTEND_DIST / full_path
        if full_path and file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        # Otherwise return index.html (SPA routing)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    # Development mode endpoints
    @app.get("/")
    async def root():
        """Root endpoint (dev mode)"""
        return {
            "name": "DeepCode New UI API",
            "version": "1.0.0",
            "status": "running",
            "mode": "development",
        }

    @app.get("/health")
    async def health_check_dev():
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
