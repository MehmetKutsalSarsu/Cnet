"""this is the last stage of critic and prepares report for fixer if needed"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_ISSUE_LIST_KEYS: List[str] = [
    "stage_3_issues",
    "stage_4_issues",
]

_CRITICAL = "CRITICAL"
_HIGH = "HIGH"
_MEDIUM = "MEDIUM"
_LOW = "LOW"

_ROUTE_FIX_NEEDED = {"status": "FIX_NEEDED", "next_step": "Fixer Agent"}
_ROUTE_PASS = {"status": "PASS", "next_step": "Coder Agent"}


def _collect_all_issues(reports: dict) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    for key in _ISSUE_LIST_KEYS:
        stage_issues = reports.get(key)
        if isinstance(stage_issues, list):
            issues.extend(stage_issues)
    return issues


def _is_unfixed_critical(issue: dict) -> bool:
    severity = str(issue.get("severity", "")).upper()
    auto_fixed = issue.get("auto_fixed", False)
    return severity in (_CRITICAL, _HIGH) and not auto_fixed


def _is_warning(issue: dict) -> bool:
    severity = str(issue.get("severity", "")).upper()
    return severity in (_MEDIUM, _LOW, "UNKNOWN")


def evaluate_pipeline(reports: dict) -> dict:

    all_issues = _collect_all_issues(reports)
    unfixed_criticals = [i for i in all_issues if _is_unfixed_critical(i)]
    if unfixed_criticals:
        logger.warning(
            "%d unfixed CRITICAL issue(s) — routing to Fixer Agent",
            len(unfixed_criticals),
        )
        return {**_ROUTE_FIX_NEEDED, "warnings": []}

    warnings = [i for i in all_issues if _is_warning(i)]
    if warnings:
        logger.info(
            "PASS with %d warning(s) — routing to Coder Agent",
            len(warnings),
        )
    else:
        logger.info("Clean PASS — routing to Coder Agent")

    return {**_ROUTE_PASS, "warnings": warnings}
