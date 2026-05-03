
import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Set, Tuple

from src.core.paths import ASSETS_DIR
from src.interfaces.schemas.scientist import SystemBlueprint

_INDEX_PATH = ASSETS_DIR / "json_library_index.json"



@dataclass
class MergeIssue:
    net_a: str
    net_b: str
    shared_pins: List[str]  # "REF::PIN" format


@dataclass
class OrphanComponent:
    ref: str
    missing_pins: List[str]


@dataclass
class LibraryMiss:
    ref: str
    exact_part_name: str
    library: str


@dataclass
class ValidationReport:
    merge_issues: List[MergeIssue] = field(default_factory=list)
    orphan_components: List[OrphanComponent] = field(default_factory=list)
    library_misses: List[LibraryMiss] = field(default_factory=list)

    def passed(self) -> bool:
        return not (self.merge_issues or self.orphan_components or self.library_misses)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        lines = ["=== Validation Report ==="]
        lines.append(f"Auto-merge issues : {len(self.merge_issues)}")
        lines.append(f"Orphan components : {len(self.orphan_components)}")
        lines.append(f"Library misses    : {len(self.library_misses)}")
        lines.append(f"Overall           : {'PASS' if self.passed() else 'FAIL'}")
        return "\n".join(lines)


# checks

def _check_auto_merge(blueprint: SystemBlueprint) -> List[MergeIssue]:
    pin_to_nets: Dict[str, List[str]] = {}
    for net in blueprint.nets:
        for conn in net.connections:
            key = f"{conn.component_ref}::{conn.pin_name}"
            pin_to_nets.setdefault(key, []).append(net.name)

    seen_pairs: Set[Tuple[str, str]] = set()
    issues: List[MergeIssue] = []

    for pin, nets in pin_to_nets.items():
        if len(nets) <= 1:
            continue
        for i in range(len(nets)):
            for j in range(i + 1, len(nets)):
                pair = tuple(sorted((nets[i], nets[j])))
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    # collect pins
                    shared = [
                        p for p, n in pin_to_nets.items()
                        if pair[0] in n and pair[1] in n
                    ]
                    issues.append(MergeIssue(net_a=pair[0], net_b=pair[1], shared_pins=shared))

    return issues


def _check_orphans(blueprint: SystemBlueprint) -> List[OrphanComponent]:
    connected_pins: Dict[str, Set[str]] = {}
    for net in blueprint.nets:
        for conn in net.connections:
            connected_pins.setdefault(conn.component_ref, set()).add(conn.pin_name)

    orphans: List[OrphanComponent] = []
    for comp in blueprint.components:
        if not comp.pins:

            continue
        comp_connected = connected_pins.get(comp.ref, set())
        if not comp_connected:
            orphans.append(OrphanComponent(
                ref=comp.ref,
                missing_pins=[p.name for p in comp.pins],
            ))

    return orphans


def _check_library(blueprint: SystemBlueprint) -> List[LibraryMiss]:
    if not _INDEX_PATH.exists():
        return [LibraryMiss(ref="*", exact_part_name="*", library="INDEX_NOT_FOUND")]

    try:
        with open(str(_INDEX_PATH), "r", encoding="utf-8") as f:
            index = json.load(f)
    except json.JSONDecodeError:
        return [LibraryMiss(ref="*", exact_part_name="*", library="INDEX_CORRUPT")]

    known_names: Set[str] = {sym["exact_name"] for sym in index}

    misses: List[LibraryMiss] = []
    for comp in blueprint.components:
        if comp.exact_part_name not in known_names:
            misses.append(LibraryMiss(
                ref=comp.ref,
                exact_part_name=comp.exact_part_name,
                library=comp.library,
            ))

    return misses



def validate(blueprint: SystemBlueprint) -> ValidationReport:
    return ValidationReport(
        merge_issues=_check_auto_merge(blueprint),
        orphan_components=_check_orphans(blueprint),
        library_misses=_check_library(blueprint),
    )
