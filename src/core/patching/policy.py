

from __future__ import annotations

from typing import Iterable, List

from src.core.config import get as cfg
from src.interfaces.schemas.fixer_plan import FixerPlan, PatchOp

_DEFAULT_FORBIDDEN = (
    "/design_id",
    "/schema_version",
    "/metadata",
)
_DEFAULT_MAX_OPS = 40


class PolicyError(ValueError):
    pass


def _forbidden_paths() -> List[str]:
    configured = cfg("agents", "fixer", "forbidden_paths", default=None)
    if not configured:
        return list(_DEFAULT_FORBIDDEN)
    return list(configured)


def _max_ops() -> int:
    return int(cfg("agents", "fixer", "max_ops", default=_DEFAULT_MAX_OPS))


def _touches_forbidden(path: str, forbidden: Iterable[str]) -> bool:
    for prefix in forbidden:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _validate_op(op: PatchOp, *, is_precondition: bool, forbidden: List[str]) -> None:
    if is_precondition and op.op != "test":
        raise PolicyError(
            f"Precondition ops must use op='test', got '{op.op}'"
        )
    if op.op in {"add", "replace", "test"} and op.value is None:
        raise PolicyError(f"Op '{op.op}' at '{op.path}' requires a 'value'")
    if op.op in {"move", "copy"} and not op.from_:
        raise PolicyError(f"Op '{op.op}' at '{op.path}' requires a 'from'")
    if _touches_forbidden(op.path, forbidden):
        raise PolicyError(f"Op path '{op.path}' is forbidden")
    if op.from_ and _touches_forbidden(op.from_, forbidden):
        raise PolicyError(f"Op from-path '{op.from_}' is forbidden")


def validate_plan(plan: FixerPlan) -> None:

    forbidden = _forbidden_paths()
    total_ops = sum(len(g.preconditions) + len(g.changes) for g in plan.groups)
    cap = _max_ops()
    if total_ops > cap:
        raise PolicyError(
            f"Plan has {total_ops} ops, exceeds cap of {cap}"
        )

    seen_indices: set[int] = set()
    for group in plan.groups:
        if group.issue_index in seen_indices:
            raise PolicyError(
                f"Duplicate issue_index {group.issue_index} in plan"
            )
        seen_indices.add(group.issue_index)
        if not group.changes:
            raise PolicyError(
                f"Group for issue {group.issue_index} has no 'changes' ops"
            )
        for op in group.preconditions:
            _validate_op(op, is_precondition=True, forbidden=forbidden)
        for op in group.changes:
            _validate_op(op, is_precondition=False, forbidden=forbidden)
