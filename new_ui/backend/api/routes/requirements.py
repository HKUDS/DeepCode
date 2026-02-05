"""
Requirements API Routes
Handles requirement analysis operations
"""

from fastapi import APIRouter, HTTPException

from services.requirement_service import requirement_service
from models.requests import (
    GenerateQuestionsRequest,
    SummarizeRequirementsRequest,
    ModifyRequirementsRequest,
)
from models.responses import QuestionsResponse, RequirementsSummaryResponse


router = APIRouter()


@router.post("/questions", response_model=QuestionsResponse)
async def generate_questions(request: GenerateQuestionsRequest):
    """Generate guiding questions based on initial requirements"""
    result = await requirement_service.generate_questions(request.initial_requirement)

    if result["status"] != "success":
        raise HTTPException(
            status_code=500,
            detail=result.get("error", "Failed to generate questions"),
        )

    return QuestionsResponse(
        questions=result["questions"],
        status="success",
    )


@router.post("/summarize", response_model=RequirementsSummaryResponse)
async def summarize_requirements(request: SummarizeRequirementsRequest):
    """Summarize requirements based on initial input and user answers"""
    result = await requirement_service.summarize_requirements(
        request.initial_requirement,
        request.user_answers,
    )

    if result["status"] != "success":
        raise HTTPException(
            status_code=500,
            detail=result.get("error", "Failed to summarize requirements"),
        )

    return RequirementsSummaryResponse(
        summary=result["summary"],
        status="success",
    )


@router.put("/modify", response_model=RequirementsSummaryResponse)
async def modify_requirements(request: ModifyRequirementsRequest):
    """Modify requirements based on user feedback"""
    result = await requirement_service.modify_requirements(
        request.current_requirements,
        request.modification_feedback,
    )

    if result["status"] != "success":
        raise HTTPException(
            status_code=500,
            detail=result.get("error", "Failed to modify requirements"),
        )

    return RequirementsSummaryResponse(
        summary=result["summary"],
        status="success",
    )
