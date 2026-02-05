"""Request models for API endpoints"""

from typing import Dict, Any
from pydantic import BaseModel, Field


class PaperToCodeRequest(BaseModel):
    """Request model for paper-to-code workflow"""

    input_source: str = Field(..., description="Path to paper file or URL")
    input_type: str = Field(..., description="Type of input: file, url")
    enable_indexing: bool = Field(default=False, description="Enable code indexing")


class ChatPlanningRequest(BaseModel):
    """Request model for chat-based planning workflow"""

    requirements: str = Field(..., description="User requirements text")
    enable_indexing: bool = Field(default=False, description="Enable code indexing")


class GenerateQuestionsRequest(BaseModel):
    """Request model for generating guiding questions"""

    initial_requirement: str = Field(..., description="Initial requirement text")


class SummarizeRequirementsRequest(BaseModel):
    """Request model for summarizing requirements"""

    initial_requirement: str = Field(..., description="Initial requirement text")
    user_answers: Dict[str, str] = Field(
        default_factory=dict, description="User answers to guiding questions"
    )


class ModifyRequirementsRequest(BaseModel):
    """Request model for modifying requirements"""

    current_requirements: str = Field(..., description="Current requirements document")
    modification_feedback: str = Field(..., description="User's modification feedback")


class LLMProviderUpdateRequest(BaseModel):
    """Request model for updating LLM provider"""

    provider: str = Field(
        ..., description="LLM provider name: google, anthropic, openai"
    )


class FileUploadResponse(BaseModel):
    """Response model for file upload"""

    file_id: str
    filename: str
    path: str
    size: int


class InteractionResponseRequest(BaseModel):
    """Request model for responding to user-in-loop interactions"""

    action: str = Field(
        ..., description="User action: submit, confirm, modify, skip, cancel"
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Response data (e.g., answers to questions, modification feedback)",
    )
    skipped: bool = Field(default=False, description="Whether user chose to skip")
