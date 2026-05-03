"""mini orchestator for coder pipeline
"""

from __future__ import annotations

import logging
from pathlib import Path

from datetime import datetime, timezone

from src.agents.coder.assembler import AssemblyResult, assemble
from src.agents.coder.llm_coder import (
    PinReferenceError,
    generate_fill_zone,
    generate_fill_zone_delta,
)
from src.agents.coder.netlist_diff import (
    CACHE_SCHEMA_VERSION,
    NetDelta,
    SpliceError,
    compute_hashes,
    compute_net_delta,
    split_fill_zone,
    splice_fill_zone,
)
from src.agents.coder.skeleton_cache import (
    CachedFillZone,
    CachedSkeleton,
    FillZoneCache,
    SkeletonCache,
)
from src.agents.coder.static_verifier import verify
from src.agents.coder.template_composer import TemplateResult, compose_template
from src.core.config import get as cfg
from src.core.paths import PROJECT_ROOT
from src.interfaces.schemas.scientist import (
    ComponentDef,
    NetConnection,
    NetDef,
    PinDef,
    SystemBlueprint,
)

logger = logging.getLogger(__name__)


def _normalize_blueprint_pins(blueprint: SystemBlueprint) -> SystemBlueprint:

    comp_by_ref = {comp.ref: comp for comp in blueprint.components}
    name_to_num: dict[str, dict[str, str]] = {}
    valid_numbers: dict[str, set[str]] = {}
    output_nums: dict[str, list[str]] = {}

    for ref, comp in comp_by_ref.items():
        num_map: dict[str, str] = {}
        nums: set[str] = set()
        outs: list[str] = []
        name_counts: dict[str, int] = {}

        for pin in comp.pins:
            if pin.number:
                nums.add(pin.number)
            if pin.name:
                name_counts[pin.name] = name_counts.get(pin.name, 0) + 1
            if (pin.electrical_type or "").lower() == "output" and pin.number:
                outs.append(pin.number)

        for pin in comp.pins:
            # only unique name
            if pin.name and pin.number and name_counts.get(pin.name, 0) == 1:
                num_map[pin.name] = pin.number

        name_to_num[ref] = num_map
        valid_numbers[ref] = nums
        output_nums[ref] = outs

    new_nets: list[NetDef] = []
    for net in blueprint.nets:
        new_conns: list[NetConnection] = []
        for conn in net.connections:
            ref = conn.component_ref
            pin = conn.pin_name

            # no pin
            if ref not in comp_by_ref or not comp_by_ref[ref].pins:
                new_conns.append(conn)
                continue

            # already vlaid
            if pin in valid_numbers.get(ref, set()):
                new_conns.append(conn)
                continue

            # name to numbe
            if pin and pin in name_to_num.get(ref, {}):
                new_conns.append(
                    NetConnection(
                        component_ref=ref,
                        pin_name=name_to_num[ref][pin],
                    )
                )
                continue


            if not pin:
                outs = output_nums.get(ref, [])
                if len(outs) == 1:
                    logger.warning(
                        "normalize_blueprint_pins: auto-resolved empty pin on %s "
                        "to output pin %s in net '%s'",
                        ref, outs[0], net.name,
                    )
                    new_conns.append(
                        NetConnection(component_ref=ref, pin_name=outs[0])
                    )
                    continue
                raise ValueError(
                    f"normalize_blueprint_pins: empty pin_name on '{ref}' in net "
                    f"'{net.name}' — {len(outs)} output pin(s) found {outs}. "
                    f"Cannot auto-resolve; use explicit pin numbers."
                )

            raise ValueError(
                f"normalize_blueprint_pins: pin '{pin}' on '{ref}' in net "
                f"'{net.name}' is ambiguous or unknown. "
                f"name_to_number: {name_to_num.get(ref, {})} "
                f"(duplicate names excluded). "
                f"Valid pin numbers: {sorted(valid_numbers.get(ref, set()))}."
            )

        new_nets.append(NetDef(name=net.name, connections=new_conns))

    return blueprint.model_copy(update={"nets": new_nets})


class CoderAgentError(Exception):
    #error part

    def __init__(self, message: str, diagnostics: dict | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class CoderAgent:


    def __init__(self):
        self.model: str = cfg("agents", "coder", "model", default="gpt-4o-mini")
        self.escalation_model: str = cfg(
            "agents", "coder", "escalation_model", default="gpt-4o"
        )
        self.temperature: float = cfg("agents", "coder", "temperature", default=0.0)
        _MAX_CODER_RETRIES = 5
        self.max_retries: int = min(
            cfg("agents", "coder", "max_retries", default=2),
            _MAX_CODER_RETRIES,
        )
        raw_output = cfg("agents", "coder", "output_dir", default="output")
        self.output_dir: str = str((PROJECT_ROOT / raw_output).resolve())

        self._cache_enabled: bool = bool(
            cfg("agents", "coder", "cache", "enabled", default=False)
        )
        cache_dir_raw = cfg(
            "agents", "coder", "cache", "dir", default="output/.cache/coder"
        )
        cache_root = Path(cache_dir_raw)
        if not cache_root.is_absolute():
            cache_root = PROJECT_ROOT / cache_root
        self._cache_root = cache_root
        self._max_delta_nets: int = int(
            cfg("agents", "coder", "cache", "max_delta_nets", default=5)
        )
        self._skeleton_cache = SkeletonCache(
            self._cache_root,
            max_entries=int(
                cfg("agents", "coder", "cache", "max_skeleton_entries", default=64)
            ),
        )
        self._fillzone_cache = FillZoneCache(
            self._cache_root,
            max_entries=int(
                cfg("agents", "coder", "cache", "max_fillzone_entries", default=256)
            ),
        )

    def _compose_template_cached(
        self, blueprint: SystemBlueprint, components_hash: str
    ) -> TemplateResult:
        if self._cache_enabled:
            hit = self._skeleton_cache.get(components_hash)
            if hit:
                logger.info(
                    "Skeleton cache hit for components_hash=%s", components_hash[:12]
                )
                return hit.to_template_result()
        template_result = compose_template(blueprint)
        if self._cache_enabled:
            self._skeleton_cache.put(
                CachedSkeleton.from_template_result(template_result, components_hash)
            )
        return template_result

    def _reconstruct_prev_blueprint(
        self, blueprint: SystemBlueprint, cached: CachedFillZone
    ) -> SystemBlueprint:
        nets = [
            NetDef(
                name=n["name"],
                connections=[
                    NetConnection(
                        component_ref=c["component_ref"], pin_name=c["pin_name"]
                    )
                    for c in n["connections"]
                ],
            )
            for n in cached.normalized_nets
        ]
        return blueprint.model_copy(update={"nets": nets})

    def _resolve_fill_zone(
        self,
        template_result: TemplateResult,
        blueprint: SystemBlueprint,
        components_hash: str,
        nets_hash: str,
    ) -> tuple[str | None, str | None]:

        if not self._cache_enabled:
            return None, None

        full_hit = self._fillzone_cache.get(components_hash, nets_hash)
        if full_hit:
            logger.info(
                "Fill-zone cache HIT (components=%s, nets=%s)",
                components_hash[:12],
                nets_hash[:12],
            )
            return full_hit.fill_zone, "full-hit"

        sibling = self._fillzone_cache.find_any_for_components(components_hash)
        if not sibling:
            return None, None

        prev_bp = self._reconstruct_prev_blueprint(blueprint, sibling)
        delta = compute_net_delta(prev_bp, blueprint)
        if delta.size == 0:

            return None, None
        if delta.size > self._max_delta_nets:
            logger.info(
                "Delta size %d exceeds cap %d — falling back to full generation",
                delta.size,
                self._max_delta_nets,
            )
            return None, None

        try:
            prev_blocks = split_fill_zone(sibling.fill_zone)
        except SpliceError as exc:
            logger.warning("cached fill zone lacks usable markers (%s) ", exc)
            return None, None

        logger.info(
            "Fill-zone DELTA path (added=%d changed=%d removed=%d)",
            len(delta.added),
            len(delta.changed),
            len(delta.removed),
        )
        delta_text = generate_fill_zone_delta(
            template_result, blueprint, delta, self.model, self.temperature
        )
        try:
            llm_blocks = split_fill_zone(delta_text)
            llm_blocks.pop("", None)
            ordered = [n.name for n in blueprint.nets]
            spliced = splice_fill_zone(prev_blocks, llm_blocks, delta, ordered)
        except SpliceError as exc:
            logger.warning("Delta splice failed (%s) — full regeneration", exc)
            return None, None

        return spliced, "delta"

    def _persist_fill_zone(
        self,
        blueprint: SystemBlueprint,
        fill_zone: str,
        components_hash: str,
        nets_hash: str,
    ) -> None:
        if not self._cache_enabled:
            return
        normalized_nets = [
            {
                "name": n.name,
                "connections": [
                    {"component_ref": c.component_ref, "pin_name": c.pin_name}
                    for c in n.connections
                ],
            }
            for n in blueprint.nets
        ]
        self._fillzone_cache.put(
            CachedFillZone(
                components_hash=components_hash,
                nets_hash=nets_hash,
                schema_version=CACHE_SCHEMA_VERSION,
                fill_zone=fill_zone,
                model=self.model,
                created_at=datetime.now(timezone.utc).isoformat(),
                net_names=sorted(n.name for n in blueprint.nets),
                normalized_nets=normalized_nets,
            )
        )

    def run(self, blueprint: SystemBlueprint) -> AssemblyResult:
       #main run for coder
        print(f"\n[CODER AGENT] Starting pipeline for: {blueprint.title}")
        print(f"[CODER AGENT] Components: {len(blueprint.components)} | Nets: {len(blueprint.nets)}")

        try:
            blueprint = _normalize_blueprint_pins(blueprint)
        except ValueError as exc:
            raise CoderAgentError(
                f"Blueprint pin-reference normalization failed: {exc}",
                diagnostics={"stage_failed": "pin_normalization", "error_message": str(exc)},
            ) from exc

        components_hash, nets_hash, _ = compute_hashes(blueprint)
        print("[CODER AGENT] Stage 1: Composing template...")
        template_result = self._compose_template_cached(blueprint, components_hash)
        logger.info(
            "Template composed: %d parts, %d nets",
            len(template_result.declared_parts),
            len(template_result.net_names),
        )

        fill_zone: str | None = None
        error_context: str | None = None
        verified = False
        last_result = None

        cache_fill_zone, cache_mode = self._resolve_fill_zone(
            template_result, blueprint, components_hash, nets_hash
        )
        if cache_fill_zone is not None:
            print(f"[CODER AGENT] Cache {cache_mode}  verifying...")
            cache_verify = verify(
                cache_fill_zone,
                template_result.declared_parts,
                template_result.net_names,
                template_result.skeleton,
            )
            if cache_verify.passed:
                fill_zone = cache_fill_zone
                verified = True
                last_result = cache_verify
                if cache_mode == "full-hit":
                    print("[CODER AGENT] skipping LLM — using cached fill zone")
                self._persist_fill_zone(
                    blueprint, fill_zone, components_hash, nets_hash
                )
            else:
                logger.warning(
                    "cache path (%s) failed verification (%s) — full regeneration",
                    cache_mode,
                    cache_verify.error_message,
                )

        total_attempts = self.max_retries
        attempts_run = 0
        while not verified and attempts_run < total_attempts:
            attempts_run += 1
            attempt = attempts_run
            print(f"[CODER AGENT] Stage 2: generating wiring code (attempt {attempt}/{total_attempts}, model={self.model})...")
            fill_zone = generate_fill_zone(
                template_result, blueprint, self.model, self.temperature, error_context
            )

            print(f"[CODER AGENT] Stage 3: Verifying...")
            last_result = verify(
                fill_zone,
                template_result.declared_parts,
                template_result.net_names,
                template_result.skeleton,
            )

            if last_result.passed:
                verified = True
                if last_result.warnings:
                    print(f"[CODER AGENT] verfication passed with {len(last_result.warnings)} warning(s)")
                else:
                    print("[CODER AGENT] verification passed")
                self._persist_fill_zone(
                    blueprint, fill_zone, components_hash, nets_hash
                )
                break

            logger.warning(
                "verification failed (attempt %d/%d, stage=%s): %s",
                attempt, total_attempts, last_result.stage_failed, last_result.error_message,
            )
            print(f"[CODER AGENT] verification failed ({last_result.stage_failed}): {last_result.error_message}")
            error_context = (
                f"Attempt {attempt} failed.\n"
                f"Stage: {last_result.stage_failed}\n"
                f"Error: {last_result.error_message}"
            )


        if not verified:
            print(f"[CODER AGENT] escalating to model: {self.escalation_model}")
            logger.info("Escalating to model: %s", self.escalation_model)

            fill_zone = generate_fill_zone(
                template_result, blueprint, self.escalation_model, self.temperature, error_context
            )

            last_result = verify(
                fill_zone,
                template_result.declared_parts,
                template_result.net_names,
                template_result.skeleton,
            )

            if last_result.passed:
                verified = True
                print("[CODER AGENT] Escalation succeeded")
                self._persist_fill_zone(
                    blueprint, fill_zone, components_hash, nets_hash
                )
            else:
                print(f"[CODER AGENT] Escalation failed ({last_result.stage_failed}): {last_result.error_message}")
                raise CoderAgentError(
                    f"Coder Agent failed after {total_attempts} attempt(s) + escalation",
                    diagnostics={
                        "stage_failed": last_result.stage_failed,
                        "error_message": last_result.error_message,
                        "warnings": last_result.warnings,
                        "last_fill_zone": fill_zone,
                    },
                )

        import re
        safe_title = re.sub(r"[^A-Za-z0-9._-]+", "_", blueprint.design_id).strip("_") or "design"
        output_path = str(Path(self.output_dir) / f"{safe_title}.net")

        print(f"[CODER AGENT] Stage 4: Assembling netlist + schematic → {output_path}")
        result = assemble(template_result.skeleton, fill_zone, output_path)

        print(f"[CODER AGENT] Netlist generated: {result.netlist_path}")
        if result.svg_path:
            print(f"[CODER AGENT] SVG schematic generated: {result.svg_path}")
        else:
            print("[CODER AGENT] SVG schematic was not generated (netlistsvg unavailable)")
        return result
