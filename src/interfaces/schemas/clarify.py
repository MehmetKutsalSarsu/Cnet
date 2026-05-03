
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ProcessedInput(BaseModel):
    is_clear: bool = Field(
        description="True if the user's intent is clear and actionable (SPEC_COMPLETE), False if more questions are needed."
    )
    refined_intent: Optional[str] = Field(
        default=None,
        description="The final [SPEC_COMPLETE] output. Empty if is_clear is False.",
    )
    extracted_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="CUMULATIVE dictionary of ALL electrical parameters extracted from the conversation so far.",
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description="The next focused question to ask the user.",
    )


class ClarificationQuestions(BaseModel):
    questions: List[str] = Field(
        default_factory=list,
        description="A list of short, specific questions to ask the user for missing details.",
    )
