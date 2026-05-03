

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from src.interfaces.schemas.scientist import NetDef, SystemBlueprint

CACHE_SCHEMA_VERSION = 1

NET_BEGIN_RE = re.compile(r"^\s*#\s*---\s*NET_BEGIN:\s*(?P<name>\S+)\s*---\s*$")
NET_END_RE = re.compile(r"^\s*#\s*---\s*NET_END:\s*(?P<name>\S+)\s*---\s*$")


def _normalize_component(comp) -> dict:
    return {
        "ref": comp.ref,
        "library": comp.library or "",
        "exact_part_name": comp.exact_part_name or "",
        "value": comp.value or "",
        "footprint": comp.footprint or "",
        "mpn": comp.mpn or "",
        "pins": sorted(
            (
                {
                    "number": p.number or "",
                    "name": p.name or "",
                    "electrical_type": (p.electrical_type or "").lower(),
                }
                for p in comp.pins
            ),
            key=lambda p: (p["number"], p["name"]),
        ),
    }


def _normalize_components(bp: SystemBlueprint) -> list[dict]:
    return sorted(
        (_normalize_component(c) for c in bp.components),
        key=lambda c: c["ref"],
    )


def _normalize_net(net: NetDef) -> dict:
    return {
        "name": net.name,
        "connections": sorted(
            (
                {"component_ref": c.component_ref, "pin_name": c.pin_name}
                for c in net.connections
            ),
            key=lambda c: (c["component_ref"], c["pin_name"]),
        ),
    }


def _normalize_nets(bp: SystemBlueprint) -> list[dict]:
    return sorted((_normalize_net(n) for n in bp.nets), key=lambda n: n["name"])


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_hashes(bp: SystemBlueprint) -> Tuple[str, str, Dict[str, str]]:
    prefix = f"v{CACHE_SCHEMA_VERSION}:"
    components_hash = _sha256(prefix + _canonical_json(_normalize_components(bp)))
    normalized_nets = _normalize_nets(bp)
    nets_hash = _sha256(prefix + _canonical_json(normalized_nets))
    net_hashes = {
        n["name"]: _sha256(prefix + _canonical_json(n)) for n in normalized_nets
    }
    return components_hash, nets_hash, net_hashes


@dataclass(frozen=True)
class NetDelta:

    added: Dict[str, NetDef]
    removed: Dict[str, NetDef]
    changed: Dict[str, Tuple[NetDef, NetDef]]

    @property
    def size(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)

    @property
    def touched_names(self) -> set[str]:
        return set(self.added) | set(self.removed) | set(self.changed)


def compute_net_delta(prev: SystemBlueprint, cur: SystemBlueprint) -> NetDelta:
    _, _, prev_hashes = compute_hashes(prev)
    _, _, cur_hashes = compute_hashes(cur)
    prev_by_name = {n.name: n for n in prev.nets}
    cur_by_name = {n.name: n for n in cur.nets}

    added = {
        name: cur_by_name[name] for name in cur_by_name if name not in prev_by_name
    }
    removed = {
        name: prev_by_name[name] for name in prev_by_name if name not in cur_by_name
    }
    changed: Dict[str, Tuple[NetDef, NetDef]] = {}
    for name in cur_by_name.keys() & prev_by_name.keys():
        if cur_hashes.get(name) != prev_hashes.get(name):
            changed[name] = (prev_by_name[name], cur_by_name[name])
    return NetDelta(added=added, removed=removed, changed=changed)


class SpliceError(ValueError):
    pass



def split_fill_zone(fill_zone: str) -> Dict[str, str]:

    blocks: Dict[str, list[str]] = {"": []}
    current: Optional[str] = None
    for line in fill_zone.splitlines():
        begin = NET_BEGIN_RE.match(line)
        end = NET_END_RE.match(line)
        if begin:
            name = begin.group("name")
            if current is not None:
                raise SpliceError(
                    f"Nested NET_BEGIN for '{name}' while inside '{current}'"
                )
            if name in blocks and name != "":
                raise SpliceError(f"Duplicate NET_BEGIN for '{name}'")
            blocks[name] = []
            current = name
            continue
        if end:
            name = end.group("name")
            if current != name:
                raise SpliceError(
                    f"Unmatched NET_END for '{name}' (current='{current}')"
                )
            current = None
            continue
        if current is None:
            blocks[""].append(line)
        else:
            blocks[current].append(line)
    if current is not None:
        raise SpliceError(f"Unclosed NET_BEGIN for '{current}'")
    return {k: "\n".join(v) for k, v in blocks.items()}


def _render_block(name: str, body: str) -> str:
    body = body.rstrip("\n")
    if body:
        return f"# --- NET_BEGIN: {name} ---\n{body}\n# --- NET_END: {name} ---"
    return f"# --- NET_BEGIN: {name} ---\n# --- NET_END: {name} ---"


def splice_fill_zone(
    previous_blocks: Dict[str, str],
    updated_blocks: Dict[str, str],
    delta: NetDelta,
    ordered_net_names: list[str],
) -> str:
   
    expected = set(delta.added) | set(delta.changed)
    extras = set(updated_blocks) - expected
    if extras:
        raise SpliceError(
            f"recived blocks for unexpected nets: {sorted(extras)}"
        )
    missing = expected - set(updated_blocks)
    if missing:
        raise SpliceError(
            f" missing net blocks: {sorted(missing)}"
        )

    merged: Dict[str, str] = {
        name: body for name, body in previous_blocks.items() if name != ""
    }
    for name in delta.removed:
        merged.pop(name, None)
    for name, body in updated_blocks.items():
        merged[name] = body

    for name in ordered_net_names:
        if name not in merged:
            raise SpliceError(f"Net '{name}' missing from spliced fill zone")
    unexpected = set(merged) - set(ordered_net_names)
    if unexpected:
        raise SpliceError(f"Spliced fill zone has stale nets: {sorted(unexpected)}")

    preamble = previous_blocks.get("", "").strip("\n")
    parts: list[str] = []
    if preamble:
        parts.append(preamble)
    for name in ordered_net_names:
        parts.append(_render_block(name, merged[name]))
    return "\n".join(parts) + "\n"
