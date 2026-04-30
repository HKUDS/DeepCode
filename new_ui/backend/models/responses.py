"""Response models for API endpoints"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field


class TaskResponse(BaseModel):
    """Response model for task creation"""

    task_id: str
    session_id: Optional[str] = None
    task_short_id: Optional[str] = None
    status: str = "created"
    message: str = "Task created successfully"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class WorkflowStatusResponse(BaseModel):
    """Response model for workflow status"""

    task_id: str
    status: str
    progress: int = 0
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class QuestionsResponse(BaseModel):
    """Response model for generated questions"""

    questions: List[Dict[str, Any]]
    status: str = "success"


class RequirementsSummaryResponse(BaseModel):
    """Response model for requirements summary"""

    summary: str
    status: str = "success"


class ConfigResponse(BaseModel):
    """Response model for configuration"""

    llm_provider: str
    available_providers: List[str]
    models: Dict[str, str]
    indexing_enabled: bool


class SettingsResponse(BaseModel):
    """Response model for settings"""

    llm_provider: str
    models: Dict[str, str]
    indexing_enabled: bool
    document_segmentation: Dict[str, Any]


class OpenRouterModelInfo(BaseModel):
    """One OpenRouter model option shown in the settings UI."""

    id: str
    name: str
    context_length: Optional[int] = None
    top_provider: Dict[str, Any] = Field(default_factory=dict)
    supported_parameters: List[str] = Field(default_factory=list)
    pricing: Dict[str, Any] = Field(default_factory=dict)
    expiration_date: Optional[str] = None
    source: str = "openrouter"


class OpenRouterModelsResponse(BaseModel):
    """Response model for OpenRouter model catalog."""

    models: List[OpenRouterModelInfo]
    source: str
    cached_at: Optional[int] = None
    stale: bool = False


class ErrorResponse(BaseModel):
    """Response model for errors"""

    error: str
    detail: Optional[str] = None
    code: Optional[str] = None
