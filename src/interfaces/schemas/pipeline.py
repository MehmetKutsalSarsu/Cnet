
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from src.interfaces.schemas.scientist import SystemBlueprint
from src.interfaces.schemas.critic import CriticResult


@dataclass
class PipelineIssue:
    severity: str                          # CRITICAL, HIGH, MEDIUM, LOW, UNKNOWN
    auto_fixed: bool = False
    description: str = ""
    check: str = ""                        # e.g. "pin_uniqueness", "self_loop"
    component_ref: Optional[str] = None
    pin_name: Optional[str] = None
    affected_refs: List[str] = field(default_factory=list)


@dataclass
class CriticPipelineResult:

    blueprint: SystemBlueprint
    decision: dict                         # {"status": str, "next_step": str, "warnings": list}
    critic_result: CriticResult
