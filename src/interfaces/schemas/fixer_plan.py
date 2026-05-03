

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# RFC 6901 JSON Pointer or ref-path selector (e.g. ``/components[ref=R1]/value``).
JsonPointer = str


class PatchOp(BaseModel):

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["add", "remove", "replace", "move", "copy", "test"] = Field(
        description="RFC 6902 operation name"
    )
    path: JsonPointer = Field(description="Target pointer (may use ref-path syntax)")
    value: Optional[Any] = Field(
        default=None,
        description="Value payload (required for add/replace/test; ignored otherwise)",
    )
    from_: Optional[JsonPointer] = Field(
        default=None,
        alias="from",
        description="Source pointer for move/copy operations",
    )


class PatchGroup(BaseModel):

    issue_index: int = Field(
        description="1-based index matching the Fixer prompt issue list"
    )
    rationale: str = Field(
        description="One-line explanation logged alongside applied/rejected groups"
    )
    preconditions: List[PatchOp] = Field(
        default_factory=list,
        description="All entries MUST have op='test' and run before changes",
    )
    changes: List[PatchOp] = Field(
        description="RFC 6902 ops to mutate the blueprint"
    )


class FixerPlan(BaseModel):

    schema_version: Literal["1.0"] = Field(
        default="1.0", description="Fixer plan schema version"
    )
    groups: List[PatchGroup] = Field(
        description="One PatchGroup per addressed CriticIssue"
    )


class AppliedGroup(BaseModel):

    issue_index: int
    rationale: str
    op_count: int


class RejectedGroup(BaseModel):

    issue_index: int
    rationale: str
    reason: str
    offending_op: Optional[PatchOp] = None


class FixOutcome(BaseModel):
    """Result of one Fixer pass in patch mode."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    blueprint: Any  # SystemBlueprint — typed as Any to avoid a circular import
    applied_groups: List[AppliedGroup] = Field(default_factory=list)
    rejected_groups: List[RejectedGroup] = Field(default_factory=list)
