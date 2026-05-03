"""refrain the paths for components"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, List, Tuple

from src.interfaces.schemas.fixer_plan import FixerPlan, PatchGroup, PatchOp


_SEG_RE = re.compile(r"^(?P<name>[^\[\]/]+)\[(?P<selector>[^\[\]]+)\]$")


class RefPathError(ValueError):
    pass


class AmbiguousSelectorError(RefPathError):
    def __init__(self, filters: List[Tuple[str, str]], candidates: List[int]):
        super().__init__(
            f"Selector {filters} is ambiguous — matches indices {candidates}"
        )
        self.filters = filters
        self.candidates = candidates


def _parse_selector(selector: str) -> List[Tuple[str, str]]:

    pairs: List[Tuple[str, str]] = []
    for chunk in selector.split(","):
        if "=" not in chunk:
            raise RefPathError(f"Malformed selector fragment '{chunk}'")
        key, _, val = chunk.partition("=")
        pairs.append((key.strip(), val.strip()))
    return pairs


def _find_index(items: List[dict], filters: List[Tuple[str, str]]) -> int:

    hits: List[int] = []
    for idx, entry in enumerate(items):
        if not isinstance(entry, dict):
            continue
        if all(str(entry.get(k)) == v for k, v in filters):
            hits.append(idx)
    if not hits:
        raise RefPathError(f"No item matches selector {filters}")
    if len(hits) > 1:
        raise AmbiguousSelectorError(filters, hits)
    return hits[0]


def rewrite_path(path: str, document: Any) -> str:

    if not path.startswith("/"):
        raise RefPathError(f"Path must start with '/': {path}")

    rewritten: List[str] = []
    cursor: Any = document
    for raw_seg in path.split("/")[1:]:
        match = _SEG_RE.match(raw_seg)
        if match:
            name = match.group("name")
            filters = _parse_selector(match.group("selector"))
            if not isinstance(cursor, dict) or name not in cursor:
                raise RefPathError(
                    f"Cannot resolve segment '{raw_seg}' — "
                    f"'{name}' missing at current cursor"
                )
            target = cursor[name]
            if not isinstance(target, list):
                raise RefPathError(
                    f"Segment '{raw_seg}' expects a list at '{name}'"
                )
            idx = _find_index(target, filters)
            rewritten.append(name)
            rewritten.append(str(idx))
            cursor = target[idx]
        else:
            rewritten.append(raw_seg)
            if raw_seg == "-":
                # Append marker; stop descending.
                cursor = None
            elif isinstance(cursor, dict) and raw_seg in cursor:
                cursor = cursor[raw_seg]
            elif isinstance(cursor, list) and raw_seg.isdigit():
                i = int(raw_seg)
                cursor = cursor[i] if 0 <= i < len(cursor) else None
            else:
                cursor = None

    return "/" + "/".join(rewritten)


def _rewrite_op(op: PatchOp, document: Any) -> PatchOp:
    new_path = rewrite_path(op.path, document)
    new_from = rewrite_path(op.from_, document) if op.from_ else None
    return PatchOp(
        op=op.op,
        path=new_path,
        value=deepcopy(op.value) if op.value is not None else None,
        **({"from": new_from} if new_from is not None else {}),
    )


def rewrite_group(group: PatchGroup, document: Any) -> PatchGroup:
    return PatchGroup(
        issue_index=group.issue_index,
        rationale=group.rationale,
        preconditions=[_rewrite_op(op, document) for op in group.preconditions],
        changes=[_rewrite_op(op, document) for op in group.changes],
    )


def rewrite_plan(plan: FixerPlan, document: Any) -> FixerPlan:
    return FixerPlan(
        schema_version=plan.schema_version,
        groups=[rewrite_group(g, document) for g in plan.groups],
    )
