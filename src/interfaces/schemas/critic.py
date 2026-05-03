from pydantic import BaseModel, Field
from typing import List, Dict, Optional


class CriticIssue(BaseModel):
    severity: str = Field(description="CRITICAL, HIGH, MEDIUM, or LOW")
    category: str = Field(description="One of: goal_alignment, electrical_correctness, safety_components, net_integrity, data_completeness, library_verification, metadata_consistency")
    component_ref: Optional[str] = Field(default=None, description="Reference designator of the affected component, if applicable")
    description: str = Field(description="Clear description of the issue")
    fix_action: str = Field(description="Concrete action the Scientist must take to fix this issue")


class CriticScore(BaseModel):
    goal_alignment: str = Field(description="PASS, PARTIAL, or FAIL")
    electrical_correctness: str = Field(description="PASS, PARTIAL, or FAIL")
    safety_components: str = Field(description="PASS, PARTIAL, or FAIL")
    net_integrity: str = Field(description="PASS, PARTIAL, or FAIL")
    data_completeness: str = Field(description="PASS, PARTIAL, or FAIL")
    library_verification: str = Field(description="PASS, PARTIAL, or FAIL")
    metadata_consistency: str = Field(description="PASS, PARTIAL, or FAIL")


class CriticResult(BaseModel):
    is_sufficient: bool = Field(description="True if the blueprint is ready for code generation, False otherwise.")
    summary: str = Field(description="One-line assessment of the blueprint quality.")
    score: CriticScore = Field(description="Per-category pass/partial/fail scores.")
    issues: List[CriticIssue] = Field(default_factory=list, description="All issues found, each with severity, category, description, and fix_action.")
