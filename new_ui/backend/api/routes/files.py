"""
Files API Routes
Handles file upload and download operations
"""

import uuid
import shutil
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import FileResponse

from settings import settings


router = APIRouter()

# In-memory file registry (in production, use a database)
_file_registry: dict = {}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file (PDF, markdown, etc.)"""
    # Validate file type
    allowed_types = {".pdf", ".md", ".txt", ".markdown"}
    file_ext = Path(file.filename).suffix.lower()

    if file_ext not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{file_ext}' not allowed. Allowed: {', '.join(allowed_types)}",
        )

    # Generate unique file ID
    file_id = str(uuid.uuid4())
    safe_filename = f"{file_id}{file_ext}"
    file_path = Path(settings.upload_dir) / safe_filename

    try:
        # Ensure upload directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Save file
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Get file size
        file_size = file_path.stat().st_size

        # Check size limit
        if file_size > settings.max_upload_size:
            file_path.unlink()  # Delete oversized file
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds limit of {settings.max_upload_size // (1024*1024)}MB",
            )

        # Register file
        _file_registry[file_id] = {
            "id": file_id,
            "original_name": file.filename,
            "path": str(file_path),
            "size": file_size,
            "type": file_ext,
        }

        return {
            "file_id": file_id,
            "filename": file.filename,
            "path": str(file_path),
            "size": file_size,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload file: {str(e)}",
        )


@router.get("/download/{file_id}")
async def download_file(file_id: str):
    """Download a file by ID"""
    file_info = _file_registry.get(file_id)

    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(file_info["path"])

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File no longer exists")

    return FileResponse(
        path=str(file_path),
        filename=file_info["original_name"],
        media_type="application/octet-stream",
    )


@router.delete("/delete/{file_id}")
async def delete_file(file_id: str):
    """Delete an uploaded file"""
    file_info = _file_registry.get(file_id)

    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")

    file_path = Path(file_info["path"])

    try:
        if file_path.exists():
            file_path.unlink()

        del _file_registry[file_id]

        return {"status": "deleted", "file_id": file_id}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete file: {str(e)}",
        )


@router.get("/info/{file_id}")
async def get_file_info(file_id: str):
    """Get information about an uploaded file"""
    file_info = _file_registry.get(file_id)

    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")

    return file_info
