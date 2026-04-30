"""Request models for API endpoints"""

from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class PaperToCodeRequest(BaseModel):
    """Request model for paper-to-code workflow"""

    input_source: str = Field(..., description="Path to paper file or URL")
    input_type: str = Field(..., description="Type of input: file, url")
    enable_indexing: bool = Field(default=False, description="Enable code indexing")
    enable_user_interaction: bool = Field(
        default=True, description="Enable user review and approval steps"
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Attach this run to an existing session (created if absent).",
    )


class ChatPlanningRequest(BaseModel):
    """Request model for chat-based planning workflow"""

    requirements: str = Field(..., description="User requirements text")
    enable_indexing: bool = Field(default=False, description="Enable code indexing")
    enable_user_interaction: bool = Field(
        default=True, description="Enable user review and approval steps"
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Attach this run to an existing session (created if absent).",
    )


class SessionCreateRequest(BaseModel):
    """Request model for explicitly creating a session."""

    title: str = Field(default="", description="Optional human-readable title")


class SessionMessageRequest(BaseModel):
    """Request model for appending a free-form message to a session."""

    role: str = Field(
        default="user",
        description="Message role: user | assistant | system",
    )
    content: str = Field(..., description="Message body")


class SessionBranchRequest(BaseModel):
    """Request model for forking a session at a given message index."""

    from_message_index: int = Field(
        ..., description="Number of messages from the source to keep"
    )
    title: Optional[str] = Field(
        default=None, description="Optional title for the new branch"
    )


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


class LLMModelsUpdateRequest(BaseModel):
    """Request model for updating phase-specific LLM models."""

    provider: str = Field(default="openrouter", description="LLM provider name")
    default_model: str = Field(..., description="Default phase model id")
    planning_model: str = Field(..., description="Planning phase model id")
    implementation_model: str = Field(..., description="Implementation phase model id")


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
