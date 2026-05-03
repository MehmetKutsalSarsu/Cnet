"""Net DRC: pin uniqueness, self-loop deduplication, orphan components, and ref/pin validity."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Set

from src.interfaces.schemas.scientist import SystemBlueprint, NetDef, NetConnection



MECHANICAL_CATEGORIES: Set[str] = {"mechanical", "heatsink", "mounting_hole", "fiducial"}



class DRCStatus(str, Enum):
    PASS = "PASS"
    FIXED = "FIXED"
    REJECT = "REJECT"


@dataclass
class DRCViolation:
    check: str
    severity: str
    description: str
    affected_refs: List[str] = field(default_factory=list)
    auto_fixed: bool = False


@dataclass
class DRCReport:
    status: DRCStatus = DRCStatus.PASS
    violations: List[DRCViolation] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return self.status == DRCStatus.REJECT

    @property
    def was_fixed(self) -> bool:
        return self.status == DRCStatus.FIXED

    @property
    def reject_violations(self) -> List[DRCViolation]:
        return [v for v in self.violations if not v.auto_fixed]

    def summary(self) -> str:
        lines = [
            "=== Stage 1: Net DRC Report ===",
            f"Status     : {self.status.value}",
            f"Violations : {len(self.violations)}",
            f"  Auto-fixed : {sum(1 for v in self.violations if v.auto_fixed)}",
            f"  Rejected   : {len(self.reject_violations)}",
        ]
        for v in self.violations:
            tag = "AUTO-FIXED" if v.auto_fixed else "REJECT"
            lines.append(f"  [{tag}][{v.severity}] {v.check}: {v.description}")
        return "\n".join(lines)




def _check_self_loop(blueprint: SystemBlueprint) -> List[DRCViolation]:
    violations: List[DRCViolation] = []

    for net in blueprint.nets:
        seen: Set[str] = set()
        unique_conns: List[NetConnection] = []
        duplicates: List[str] = []

        for conn in net.connections:
            key = f"{conn.component_ref}::{conn.pin_name}"
            if key in seen:
                duplicates.append(key)
            else:
                seen.add(key)
                unique_conns.append(conn)

        if duplicates:
            net.connections = unique_conns
            violations.append(DRCViolation(
                check="self_loop",
                severity="LOW",
                description=(
                    f"Net '{net.name}' had duplicate pin(s): {duplicates}. "
                    f"Duplicates removed."
                ),
                affected_refs=[d.split("::")[0] for d in duplicates],
                auto_fixed=True,
            ))

    return violations


def _check_pin_uniqueness(blueprint: SystemBlueprint) -> List[DRCViolation]:

    pin_to_nets: Dict[str, List[str]] = {}
    for net in blueprint.nets:
        for conn in net.connections:
            key = f"{conn.component_ref}::{conn.pin_name}"
            pin_to_nets.setdefault(key, []).append(net.name)

    violations: List[DRCViolation] = []
    for pin_key, net_names in pin_to_nets.items():
        if len(net_names) < 2:
            continue
        component_ref, pin_name = pin_key.split("::", 1)
        unique_nets = sorted(set(net_names))
        violations.append(DRCViolation(
            check="pin_uniqueness",
            severity="CRITICAL",
            description=(
                f"Component '{component_ref}', pin '{pin_name}' is assigned to "
                f"multiple nets: {unique_nets}. "
                f"Remove the duplicate so the pin belongs to exactly one net."
            ),
            affected_refs=[component_ref],
            auto_fixed=False,
        ))

    return violations


def _check_orphan_components(blueprint: SystemBlueprint) -> List[DRCViolation]:
    connected_refs: Set[str] = set()
    for net in blueprint.nets:
        for conn in net.connections:
            connected_refs.add(conn.component_ref)

    violations: List[DRCViolation] = []
    for comp in blueprint.components:
        if comp.category.lower() in MECHANICAL_CATEGORIES:
            continue
        if comp.ref not in connected_refs:
            violations.append(DRCViolation(
                check="orphan_component",
                severity="CRITICAL",
                description=(
                    f"Component '{comp.ref}' ({comp.exact_part_name}) is not connected to any net."
                ),
                affected_refs=[comp.ref],
                auto_fixed=False,
            ))

    return violations


def _check_ref_validity(blueprint: SystemBlueprint) -> List[DRCViolation]:
    valid_refs: Set[str] = {comp.ref for comp in blueprint.components}

    violations: List[DRCViolation] = []
    seen_bad: Set[str] = set()

    for net in blueprint.nets:
        for conn in net.connections:
            if conn.component_ref not in valid_refs and conn.component_ref not in seen_bad:
                seen_bad.add(conn.component_ref)
                violations.append(DRCViolation(
                    check="ref_validity",
                    severity="CRITICAL",
                    description=(
                        f"Net '{net.name}' references component '{conn.component_ref}' "
                        f"which does not exist in the components list."
                    ),
                    affected_refs=[conn.component_ref],
                    auto_fixed=False,
                ))

    return violations


def _check_pin_name_validity(blueprint: SystemBlueprint) -> List[DRCViolation]:
    pin_map: Dict[str, Set[str]] = {}
    for comp in blueprint.components:
        if comp.pins:
            pin_map[comp.ref] = {p.name for p in comp.pins}

    violations: List[DRCViolation] = []
    seen_bad: Set[str] = set()

    for net in blueprint.nets:
        for conn in net.connections:
            if conn.component_ref not in pin_map:
                continue
            key = f"{conn.component_ref}::{conn.pin_name}"
            if conn.pin_name not in pin_map[conn.component_ref] and key not in seen_bad:
                seen_bad.add(key)
                valid_pins = sorted(pin_map[conn.component_ref])
                violations.append(DRCViolation(
                    check="pin_name_validity",
                    severity="CRITICAL",
                    description=(
                        f"Net '{net.name}' uses pin '{conn.pin_name}' on component "
                        f"'{conn.component_ref}', but valid pins are: {valid_pins}."
                    ),
                    affected_refs=[conn.component_ref],
                    auto_fixed=False,
                ))

    return violations




def run_net_drc(blueprint: SystemBlueprint, in_place: bool = False) -> tuple[SystemBlueprint, DRCReport]:

    bp = blueprint if in_place else blueprint.model_copy(deep=True)
    report = DRCReport()

    report.violations.extend(_check_self_loop(bp))
    report.violations.extend(_check_pin_uniqueness(bp))
    report.violations.extend(_check_orphan_components(bp))
    report.violations.extend(_check_ref_validity(bp))
    report.violations.extend(_check_pin_name_validity(bp))

    has_reject = any(not v.auto_fixed for v in report.violations)
    has_fix = any(v.auto_fixed for v in report.violations)

    if has_reject:
        report.status = DRCStatus.REJECT
    elif has_fix:
        report.status = DRCStatus.FIXED
    else:
        report.status = DRCStatus.PASS

    return bp, report
