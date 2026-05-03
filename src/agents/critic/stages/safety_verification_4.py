

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from src.interfaces.schemas.scientist import SystemBlueprint

logger = logging.getLogger(__name__)




_CONNECTOR_TAGS: frozenset[str] = frozenset({
    "connector", "conn", "jack", "plug", "terminal", "header", "barrel_jack",
})
_REGULATOR_TAGS: frozenset[str] = frozenset({
    "regulator", "linear_regulator", "switching_regulator",
    "ldo", "vreg", "voltage_regulator", "dc_dc", "converter", "pmic",
})
_FUSE_TAGS: frozenset[str] = frozenset({
    "fuse", "polyfuse", "ptc", "resettable_fuse",
})
_DIODE_TAGS: frozenset[str] = frozenset({
    "diode", "zener", "schottky", "tvs",
})
_CAPACITOR_TAGS: frozenset[str] = frozenset({
    "capacitor", "cap",
})
_INDUCTIVE_LOAD_TAGS: frozenset[str] = frozenset({
    "motor", "relay", "solenoid",
})
_IC_TAGS: frozenset[str] = frozenset({
    "ic", "mcu", "microcontroller", "op_amp", "comparator", "gate", "logic",
    "driver", "amplifier", "timer", "adc", "dac", "sensor", "transceiver",
})


_IC_POWER_PINS: frozenset[str] = frozenset({
    "vcc", "vdd", "v+", "vs", "vin", "vcc1", "vcc2", "avcc", "dvcc",
})


_GND_NET_NAMES: frozenset[str] = frozenset({
    "gnd", "0", "vss", "ground", "net_gnd", "dgnd", "agnd", "pgnd",
})


_REG_IN_PINS: frozenset[str] = frozenset({"in", "vin", "input", "vi"})
_REG_OUT_PINS: frozenset[str] = frozenset({"out", "vout", "output", "vo"})



def _matches_tags(category: str, part_name: str, tags: frozenset[str]) -> bool:

    cat = category.lower()
    pn = part_name.lower()
    return any(tag in cat or tag in pn for tag in tags)


def _is_gnd_net(name: str) -> bool:
    lower = name.strip().lower()
    if lower in _GND_NET_NAMES:
        return True
    import re as _re
    parts = _re.split(r"[_\-\s]+", lower)
    return any(p in _GND_NET_NAMES for p in parts)


# results

class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_APPLICABLE = "N/A"


@dataclass
class PathDetail:
    path: List[str]
    protection_found: bool
    protection_refs: List[str]
    detail: str


@dataclass
class RegulatorCapDetail:
    regulator: str
    in_net: Optional[str]
    out_net: Optional[str]
    in_caps: List[str]
    out_caps: List[str]
    in_caps_grounded: List[str]
    out_caps_grounded: List[str]
    passed: bool


@dataclass
class LoadDiodeDetail:
    load: str
    terminal_nets: List[str]
    flyback_diodes: List[str]
    passed: bool


@dataclass
class SafetyCheckResult:
    check: str
    status: CheckStatus
    description: str
    details: List[Any] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "status": self.status.value,
            "description": self.description,
            "details": [
                asdict(d) if hasattr(d, "__dataclass_fields__") else d
                for d in self.details
            ],
        }


@dataclass
class SafetyReport:
    design_id: str
    checks: List[SafetyCheckResult] = field(default_factory=list)

    @property
    def overall_passed(self) -> bool:
        return all(c.status != CheckStatus.FAIL for c in self.checks)

    @property
    def failed_checks(self) -> List[SafetyCheckResult]:
        return [c for c in self.checks if c.status == CheckStatus.FAIL]

    def summary(self) -> str:
        passed = sum(1 for c in self.checks if c.status == CheckStatus.PASS)
        na = sum(1 for c in self.checks if c.status == CheckStatus.NOT_APPLICABLE)
        total = len(self.checks)
        lines = [
            "=== Safety Component Verification Report ===",
            f"Design ID : {self.design_id}",
            f"Result    : {passed}/{total} passed, {na} not applicable",
        ]
        for c in self.checks:
            lines.append(f"  [{c.status.value:4}] {c.check}: {c.description}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "stage": "safety_verification",
            "design_id": self.design_id,
            "overall_passed": self.overall_passed,
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary(),
        }


@dataclass
class _BlueprintGraph:
    comp_by_ref: Dict[str, Any]                     # ref -> ComponentDef
    net_map: Dict[str, List[Tuple[str, str]]]        # net_name -> [(ref, pin_name), ...]
    pin_net: Dict[Tuple[str, str], str]              # (ref, pin_name) -> net_name
    ref_nets: Dict[str, Dict[str, str]]              # ref -> {pin_name: net_name}
    adj: Dict[str, Set[str]]                         # ref -> {adjacent refs}


def _build_graph(blueprint: SystemBlueprint) -> _BlueprintGraph:
    comp_by_ref = {comp.ref: comp for comp in blueprint.components}

    net_map: Dict[str, List[Tuple[str, str]]] = {}
    pin_net: Dict[Tuple[str, str], str] = {}
    ref_nets: Dict[str, Dict[str, str]] = {ref: {} for ref in comp_by_ref}

    for net_def in blueprint.nets:
        members: List[Tuple[str, str]] = []
        for conn in net_def.connections:
            pair = (conn.component_ref, conn.pin_name)
            members.append(pair)
            pin_net[pair] = net_def.name
            if conn.component_ref in ref_nets:
                ref_nets[conn.component_ref][conn.pin_name] = net_def.name
        net_map[net_def.name] = members

    adj: Dict[str, Set[str]] = {ref: set() for ref in comp_by_ref}
    for net_name, members in net_map.items():
        if _is_gnd_net(net_name):
            continue
        refs_on_net = {ref for ref, _ in members if ref in comp_by_ref}
        for ref in refs_on_net:
            adj[ref].update(refs_on_net - {ref})

    return _BlueprintGraph(
        comp_by_ref=comp_by_ref,
        net_map=net_map,
        pin_net=pin_net,
        ref_nets=ref_nets,
        adj=adj,
    )


def _find_power_sink_refs(g: _BlueprintGraph) -> Tuple[Set[str], str]:

    regulator_refs = {
        ref for ref, comp in g.comp_by_ref.items()
        if _matches_tags(comp.category, comp.exact_part_name, _REGULATOR_TAGS)
    }
    if regulator_refs:
        return regulator_refs, "voltage regulator"

    ic_power_refs: Set[str] = set()
    for ref, comp in g.comp_by_ref.items():
        if not _matches_tags(comp.category, comp.exact_part_name, _IC_TAGS):
            continue
        pin_nets = g.ref_nets.get(ref, {})
        for pname, net_name in pin_nets.items():
            if _is_gnd_net(net_name):
                continue
            if pname.lower() in _IC_POWER_PINS:
                ic_power_refs.add(ref)
                break
            for pin_def in (comp.pins if hasattr(comp, "pins") else []):
                if pin_def.name == pname and (pin_def.electrical_type or "").lower() == "power_in":
                    ic_power_refs.add(ref)
                    break
            else:
                continue
            break

    if ic_power_refs:
        return ic_power_refs, "IC power input"

    return set(), ""


def find_paths_between(
    start_refs: List[str],
    target_refs: Set[str],
    adj: Dict[str, Set[str]],
    *,
    max_depth: int = 20,
) -> List[List[str]]:
    paths: List[List[str]] = []

    for start in start_refs:
        queue: deque[List[str]] = deque([[start]])
        visited: Set[str] = {start}

        while queue:
            path = queue.popleft()
            if len(path) > max_depth:
                continue
            current = path[-1]

            if current in target_refs and current != start:
                paths.append(path)
                continue

            for nbr in adj.get(current, set()):
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append(path + [nbr])

    return paths


def find_components_on_net(
    net_name: str,
    tags: frozenset[str],
    g: _BlueprintGraph,
) -> List[str]:
    hits: List[str] = []
    seen: Set[str] = set()
    for ref, _pin in g.net_map.get(net_name, []):
        if ref in seen:
            continue
        seen.add(ref)
        comp = g.comp_by_ref.get(ref)
        if comp and _matches_tags(comp.category, comp.exact_part_name, tags):
            hits.append(ref)
    return hits


def _check_input_fuse(
    paths: List[List[str]],
    g: _BlueprintGraph,
    target_label: str = "voltage regulator",
) -> SafetyCheckResult:
    if not paths:
        return SafetyCheckResult(
            check="Input Protection — Fuse",
            status=CheckStatus.NOT_APPLICABLE,
            description=f"No electrical path found from input connector to {target_label}; check not applicable.",
        )

    details: List[PathDetail] = []
    any_passed = False

    for path in paths:
        fuses = [
            r for r in path
            if _matches_tags(
                g.comp_by_ref[r].category,
                g.comp_by_ref[r].exact_part_name,
                _FUSE_TAGS,
            )
        ]
        found = len(fuses) > 0
        if found:
            any_passed = True
        details.append(PathDetail(
            path=path,
            protection_found=found,
            protection_refs=fuses,
            detail=f"Fuse(s) {fuses} in path" if found else "No fuse in path",
        ))

    return SafetyCheckResult(
        check="Input Protection — Fuse",
        status=CheckStatus.PASS if any_passed else CheckStatus.FAIL,
        description=(
            f"Fuse detected on input-to-{target_label} path."
            if any_passed
            else f"No fuse found on any input-to-{target_label} path."
        ),
        details=details,
    )


def _check_reverse_polarity(
    paths: List[List[str]],
    g: _BlueprintGraph,
    target_label: str = "voltage regulator",
) -> SafetyCheckResult:
    if not paths:
        return SafetyCheckResult(
            check="Input Protection — Reverse Polarity Diode",
            status=CheckStatus.NOT_APPLICABLE,
            description=f"No electrical path found from input connector to {target_label}; check not applicable.",
        )

    details: List[PathDetail] = []
    any_passed = False

    for path in paths:
        path_set = set(path)

        series = [
            r for r in path
            if _matches_tags(
                g.comp_by_ref[r].category,
                g.comp_by_ref[r].exact_part_name,
                _DIODE_TAGS,
            )
        ]

        shunt: List[str] = []
        checked_nets: Set[str] = set()

        for ref in path:
            for _pin, net_name in g.ref_nets.get(ref, {}).items():
                if _is_gnd_net(net_name) or net_name in checked_nets:
                    continue
                checked_nets.add(net_name)

                for d_ref, _d_pin in g.net_map.get(net_name, []):
                    if d_ref in path_set:
                        continue
                    d_comp = g.comp_by_ref.get(d_ref)
                    if not d_comp:
                        continue
                    if not _matches_tags(d_comp.category, d_comp.exact_part_name, _DIODE_TAGS):
                        continue
                    # Diode must also touch a GND net
                    if any(_is_gnd_net(n) for n in g.ref_nets.get(d_ref, {}).values()):
                        if d_ref not in shunt:
                            shunt.append(d_ref)

        all_protection = series + shunt
        found = bool(all_protection)
        if found:
            any_passed = True

        parts: List[str] = []
        if series:
            parts.append(f"series {series}")
        if shunt:
            parts.append(f"shunt {shunt}")

        details.append(PathDetail(
            path=path,
            protection_found=found,
            protection_refs=all_protection,
            detail=" + ".join(parts) if parts else "No protection diode found",
        ))

    return SafetyCheckResult(
        check="Input Protection — Reverse Polarity Diode",
        status=CheckStatus.PASS if any_passed else CheckStatus.FAIL,
        description=(
            f"Reverse-polarity protection diode detected on input-to-{target_label} path."
            if any_passed
            else f"No reverse-polarity protection diode found on any input-to-{target_label} path."
        ),
        details=details,
    )


def _check_bypass_capacitors(g: _BlueprintGraph) -> SafetyCheckResult:
    regulators = {
        ref: comp for ref, comp in g.comp_by_ref.items()
        if _matches_tags(comp.category, comp.exact_part_name, _REGULATOR_TAGS)
    }

    if not regulators:
        return SafetyCheckResult(
            check="Regulator Protection — Bypass Capacitors",
            status=CheckStatus.NOT_APPLICABLE,
            description="No voltage regulators in design; check not applicable.",
        )

    details: List[RegulatorCapDetail] = []
    all_passed = True

    for ref in regulators:
        pin_nets = g.ref_nets.get(ref, {})

        in_net: Optional[str] = None
        out_net: Optional[str] = None
        for pname, net in pin_nets.items():
            low = pname.lower()
            if low in _REG_IN_PINS:
                in_net = net
            elif low in _REG_OUT_PINS:
                out_net = net

        if in_net is None:
            in_net = pin_nets.get("1")
        if out_net is None:
            out_net = pin_nets.get("3")

        in_caps = find_components_on_net(in_net, _CAPACITOR_TAGS, g) if in_net else []
        out_caps = find_components_on_net(out_net, _CAPACITOR_TAGS, g) if out_net else []

        def _grounded(caps: List[str]) -> List[str]:
            return [
                c for c in caps
                if any(_is_gnd_net(n) for n in g.ref_nets.get(c, {}).values())
            ]

        passed = bool(in_caps) and bool(out_caps)
        if not passed:
            all_passed = False

        details.append(RegulatorCapDetail(
            regulator=ref,
            in_net=in_net,
            out_net=out_net,
            in_caps=in_caps,
            out_caps=out_caps,
            in_caps_grounded=_grounded(in_caps),
            out_caps_grounded=_grounded(out_caps),
            passed=passed,
        ))

    failed = [d for d in details if not d.passed]
    return SafetyCheckResult(
        check="Regulator Protection — Bypass Capacitors",
        status=CheckStatus.PASS if all_passed else CheckStatus.FAIL,
        description=(
            "All regulators have input and output bypass capacitors."
            if all_passed
            else (
                f"{len(failed)} regulator(s) missing bypass cap(s): "
                + ", ".join(d.regulator for d in failed) + "."
            )
        ),
        details=details,
    )


def _check_flyback_diodes(g: _BlueprintGraph) -> SafetyCheckResult:
    loads = {
        ref: comp for ref, comp in g.comp_by_ref.items()
        if _matches_tags(comp.category, comp.exact_part_name, _INDUCTIVE_LOAD_TAGS)
    }

    if not loads:
        return SafetyCheckResult(
            check="Inductive Load — Flyback Diode",
            status=CheckStatus.NOT_APPLICABLE,
            description="No inductive loads in design; check not applicable.",
        )

    details: List[LoadDiodeDetail] = []
    all_passed = True

    for ref in loads:
        load_nets = list(g.ref_nets.get(ref, {}).values())

        if len(load_nets) < 2:
            details.append(LoadDiodeDetail(
                load=ref,
                terminal_nets=load_nets,
                flyback_diodes=[],
                passed=False,
            ))
            all_passed = False
            continue

        net_a, net_b = load_nets[0], load_nets[1]
        bridging: List[str] = []

        for d_ref, d_comp in g.comp_by_ref.items():
            if not _matches_tags(d_comp.category, d_comp.exact_part_name, _DIODE_TAGS):
                continue
            d_nets = set(g.ref_nets.get(d_ref, {}).values())
            if net_a in d_nets and net_b in d_nets:
                bridging.append(d_ref)

        passed = len(bridging) > 0
        if not passed:
            all_passed = False

        details.append(LoadDiodeDetail(
            load=ref,
            terminal_nets=[net_a, net_b],
            flyback_diodes=bridging,
            passed=passed,
        ))

    failed = [d for d in details if not d.passed]
    return SafetyCheckResult(
        check="Inductive Load — Flyback Diode",
        status=CheckStatus.PASS if all_passed else CheckStatus.FAIL,
        description=(
            "All inductive loads have flyback diodes."
            if all_passed
            else (
                f"{len(failed)} load(s) missing flyback diode(s): "
                + ", ".join(d.load for d in failed) + "."
            )
        ),
        details=details,
    )


def run_safety_verification(blueprint: SystemBlueprint) -> SafetyReport:
    g = _build_graph(blueprint)

    connector_refs = [
        ref for ref, comp in g.comp_by_ref.items()
        if _matches_tags(comp.category, comp.exact_part_name, _CONNECTOR_TAGS)
    ]
    power_sink_refs, target_label = _find_power_sink_refs(g)

    if connector_refs and power_sink_refs:
        input_paths = find_paths_between(connector_refs, power_sink_refs, g.adj)
    else:
        input_paths = []

    logger.info(
        "Power-sink discovery: %d connector(s), %d target(s) (%s), %d path(s)",
        len(connector_refs), len(power_sink_refs), target_label or "none", len(input_paths),
    )

    report = SafetyReport(
        design_id=blueprint.design_id,
        checks=[
            _check_input_fuse(input_paths, g, target_label),
            _check_reverse_polarity(input_paths, g, target_label),
            _check_bypass_capacitors(g),
            _check_flyback_diodes(g),
        ],
    )

    logger.info("Safety verification complete: %s", "PASS" if report.overall_passed else "FAIL")
    return report


def verify_safety_topology(blueprint: SystemBlueprint) -> dict:
    return run_safety_verification(blueprint).to_dict()
