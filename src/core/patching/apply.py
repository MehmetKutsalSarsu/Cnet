

from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any, List, Tuple

import jsonpatch
from pydantic import ValidationError

from src.interfaces.schemas.fixer_plan import (
    AppliedGroup,
    FixerPlan,
    PatchGroup,
    PatchOp,
    RejectedGroup,
)
from src.interfaces.schemas.scientist import SystemBlueprint

logger = logging.getLogger(__name__)


def _op_to_dict(op: PatchOp) -> dict:
    payload: dict[str, Any] = {"op": op.op, "path": op.path}
    if op.op in {"add", "replace", "test"}:
        payload["value"] = op.value
    if op.op in {"move", "copy"} and op.from_:
        payload["from"] = op.from_
    return payload


def _apply_group(document: dict, group: PatchGroup) -> Tuple[dict, PatchOp | None, str | None]:

    ops = [_op_to_dict(op) for op in group.preconditions + group.changes]
    try:
        patched = jsonpatch.apply_patch(document, ops, in_place=False)
    except jsonpatch.JsonPatchTestFailed as exc:
        return document, _first_op_like(group, "test"), f"precondition test failed: {exc}"
    except jsonpatch.JsonPatchException as exc:
        return document, _first_op_like(group), f"patch error: {exc}"
    except Exception as exc:  # defensive
        return document, _first_op_like(group), f"unexpected error: {exc}"

    try:
        SystemBlueprint(**patched)
    except ValidationError as exc:
        return document, _first_op_like(group), f"schema violation: {exc.errors()[:2]}"

    return patched, None, None


def _first_op_like(group: PatchGroup, op_name: str | None = None) -> PatchOp | None:
    if op_name:
        for op in group.preconditions:
            if op.op == op_name:
                return op
    return group.changes[0] if group.changes else None


def apply_groups(
    blueprint: SystemBlueprint, plan: FixerPlan
) -> Tuple[SystemBlueprint, List[AppliedGroup], List[RejectedGroup]]:
    #fix it after the exam
    document: dict = json.loads(blueprint.model_dump_json())
    applied: List[AppliedGroup] = []
    rejected: List[RejectedGroup] = []

    for group in plan.groups:
        candidate = deepcopy(document)
        new_doc, offending, reason = _apply_group(candidate, group)
        if reason is None:
            document = new_doc
            applied.append(
                AppliedGroup(
                    issue_index=group.issue_index,
                    rationale=group.rationale,
                    op_count=len(group.preconditions) + len(group.changes),
                )
            )
            logger.info(
                "Fixer group %d applied (%d ops): %s",
                group.issue_index,
                len(group.preconditions) + len(group.changes),
                group.rationale,
            )
        else:
            rejected.append(
                RejectedGroup(
                    issue_index=group.issue_index,
                    rationale=group.rationale,
                    reason=reason,
                    offending_op=offending,
                )
            )
            logger.warning(
                "Fixer group %d rejected: %s", group.issue_index, reason
            )

    return SystemBlueprint(**document), applied, rejected
