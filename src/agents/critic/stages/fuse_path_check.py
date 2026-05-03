"""
Fuse-in-series verification.

Every electrical path from a connector input pin to any IC power_in pin must
pass through a fuse component. Uses a pin/net bipartite graph with BFS; series
passives (diodes, resistors, etc.) are modelled as transparent conductors.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Set, Tuple


Node = Tuple[str, str, str]


FUSE_CATEGORY = "fuse"
CONNECTOR_CATEGORY = "connector"
POWER_IN = "power_in"


@dataclass
class FusePathViolation:
    connector_ref: str
    connector_pin: str
    ic_ref: str
    ic_pin: str
    reason: str

@dataclass
class FusePathReport:
    passed: bool
    violations: List[FusePathViolation] = field(default_factory=list)
    traced_paths: int = 0
    fuse_refs_seen: Set[str] = field(default_factory=set)

    def summary(self) -> str:
        lines = [
            "=== Fuse-in-Series Check ===",
            f"Status        : {'PASS' if self.passed else 'REJECT'}",
            f"Paths traced  : {self.traced_paths}",
            f"Fuses present : {sorted(self.fuse_refs_seen) or 'NONE'}",
            f"Violations    : {len(self.violations)}",
        ]
        for v in self.violations:
            lines.append(
                f"  [{v.reason}] {v.connector_ref}.{v.connector_pin} → "
                f"{v.ic_ref}.{v.ic_pin}"
            )
        return "\n".join(lines)



def _build_graph(
    components: List[Dict[str, Any]],
    nets: List[Dict[str, Any]],
) -> Dict[Node, Set[Node]]:
    adj: Dict[Node, Set[Node]] = {}

    def link(a: Node, b: Node) -> None:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)

    for net in nets:
        net_name = net.get("name")
        if not net_name:
            continue
        net_node: Node = ("net", net_name, "")
        adj.setdefault(net_node, set())
        for conn in net.get("connections", []):
            ref = conn.get("component_ref")
            pin = conn.get("pin_name")
            if ref is None or pin is None:
                continue
            pin_node: Node = ("pin", ref, pin)
            link(pin_node, net_node)

    for comp in components:
        ref = comp.get("ref")
        pins = comp.get("pins", [])
        if ref is None or len(pins) < 2:
            continue
        pin_nodes = [("pin", ref, p["name"]) for p in pins if p.get("name") is not None]
        for i in range(len(pin_nodes)):
            for j in range(i + 1, len(pin_nodes)):
                link(pin_nodes[i], pin_nodes[j])

    return adj



def _component_category(comp: Dict[str, Any]) -> str:
    return (comp.get("category") or "").strip().lower()


def _connector_pins(components: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for comp in components:
        if _component_category(comp) != CONNECTOR_CATEGORY:
            continue
        ref = comp.get("ref")
        if ref is None:
            continue
        for p in comp.get("pins", []):
            name = p.get("name")
            if name is not None:
                out.append((ref, name))
    return out


def _ic_power_in_pins(components: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for comp in components:
        if _component_category(comp) == CONNECTOR_CATEGORY:
            continue
        ref = comp.get("ref")
        if ref is None:
            continue
        for p in comp.get("pins", []):
            etype = (p.get("electrical_type") or "").strip().lower()
            name = p.get("name")
            if etype == POWER_IN and name is not None:
                out.append((ref, name))
    return out


def _fuse_refs(components: List[Dict[str, Any]]) -> Set[str]:
    return {c["ref"] for c in components if _component_category(c) == FUSE_CATEGORY and c.get("ref")}


def _bfs_paths_through_fuse(
    adj: Dict[Node, Set[Node]],
    start: Node,
    goal: Node,
    fuse_refs: Set[str],
) -> Tuple[bool, bool]:
    if start == goal:
        return True, False  # degerenate
     if start not in adj or goal not in adj:
        return False, False

    visited_plain: Set[Node] = {start}
    visited_fused: Set[Node] = set()
    queue: deque[Tuple[Node, bool]] = deque([(start, False)])

    reachable = False
    fuse_path = False

    while queue:
        node, has_fuse = queue.popleft()
        if node == goal:
            reachable = True
            if has_fuse:
                fuse_path = True
                return True, True
            continue

        for neighbor in adj.get(node, ()):
            neighbor_has_fuse = has_fuse or (
                neighbor[0] == "pin" and neighbor[1] in fuse_refs
            )
            target_visited = visited_fused if neighbor_has_fuse else visited_plain
            if neighbor in target_visited:
                continue
            target_visited.add(neighbor)
            queue.append((neighbor, neighbor_has_fuse))

    return reachable, fuse_path


def check_fuse_in_series(
    components: List[Dict[str, Any]],
    nets: List[Dict[str, Any]],
) -> FusePathReport:
    adj = _build_graph(components, nets)
    fuse_refs = _fuse_refs(components)
    connectors = _connector_pins(components)
    ic_power_ins = _ic_power_in_pins(components)

    report = FusePathReport(passed=True, fuse_refs_seen=set(fuse_refs))

    if not connectors or not ic_power_ins:
        return report

    for c_ref, c_pin in connectors:
        start: Node = ("pin", c_ref, c_pin)
        for ic_ref, ic_pin in ic_power_ins:
            goal: Node = ("pin", ic_ref, ic_pin)
            report.traced_paths += 1

            reachable, fused = _bfs_paths_through_fuse(adj, start, goal, fuse_refs)
            if not reachable:
                continue
            if not fused:
                report.passed = False
                report.violations.append(FusePathViolation(
                    connector_ref=c_ref,
                    connector_pin=c_pin,
                    ic_ref=ic_ref,
                    ic_pin=ic_pin,
                    reason="no_fuse_in_series",
                ))

    return report
