"""Generates SKiDL wiring code (Net declarations and += lines) for the fill zone."""

from __future__ import annotations

import logging

from openai import OpenAI

from src.agents.coder.template_composer import PartPins, TemplateResult
from src.core.paths import PROMPTS_DIR
from src.interfaces.schemas.scientist import SystemBlueprint


class PinReferenceError(ValueError):

    pass

logger = logging.getLogger(__name__)

_PROMPT_DIR = PROMPTS_DIR / "coder"


def _load_system_prompt() -> str:
    path = _PROMPT_DIR / "coder_system_prompt.txt"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_delta_prompt() -> str:
    path = _PROMPT_DIR / "coder_delta_prompt.txt"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _strip_markdown(code: str) -> str:

    code = code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    return code


def _resolve_pin_to_number(
    component_ref: str,
    pin_name: str,
    declared_parts: dict[str, PartPins],
) -> str:

    if component_ref not in declared_parts:
        return pin_name

    part_pins = declared_parts[component_ref]

    if pin_name in part_pins.by_number:
        return pin_name

    if pin_name and pin_name in part_pins.name_to_number:
        return part_pins.name_to_number[pin_name]

    if not pin_name:
        output_pins = [
            num for num, etype in part_pins.number_to_etype.items()
            if etype == "output"
        ]
        if len(output_pins) == 1:
            logger.warning(
                "Auto-resolved empty pin_name on %s to output pin %s",
                component_ref, output_pins[0],
            )
            return output_pins[0]
        raise PinReferenceError(
            f"pin name of Component '{component_ref}' is empty "
            f"output pins found: {output_pins}). "
            f"available pin numbers: {part_pins.by_number}."
        )

    raise PinReferenceError(
        f"Pin '{pin_name}' on component '{component_ref}' is unknown. "
        f"name_to_number map: {part_pins.name_to_number}"
        f"Valid pin numbers: {part_pins.by_number}."
    )


def _build_user_message(
    template_result: TemplateResult,
    blueprint: SystemBlueprint,
    error_context: str | None,
) -> str:

    comp_symbol = {comp.ref: comp.exact_part_name for comp in blueprint.components}

    parts_lines = ["## DECLARED PARTS"]
    for ref in sorted(template_result.declared_parts):
        pins = template_result.declared_parts[ref]
        symbol = comp_symbol.get(ref, "")
        header = f"- {ref} ({symbol}):" if symbol else f"- {ref}:"
        pin_annotations = []
        for num in pins.by_number:
            name = pins.number_to_name.get(num, "")
            if name:
                pin_annotations.append(f"pin {num} ({name})")
            else:
                pin_annotations.append(f"pin {num}")
        parts_lines.append(f"{header} {', '.join(pin_annotations)}")

    conn_lines = ["\n## CONNECTIONS TO WIRE"]
    resolution_errors: list[str] = []
    for net in blueprint.nets:
        parts: list[str] = []
        for c in net.connections:
            try:
                pin_num = _resolve_pin_to_number(
                    c.component_ref, c.pin_name, template_result.declared_parts
                )
                parts.append(f"{c.component_ref}[{pin_num}]")
            except PinReferenceError as exc:
                resolution_errors.append(str(exc))
                parts.append(f"{c.component_ref}[?{c.pin_name!r}]")
        conn_lines.append(f"- Net '{net.name}': {', '.join(parts)}")

    message = "\n".join(parts_lines + conn_lines)
    message += "\n\nwrite the wiring code and outputs is only python code lines."

    if resolution_errors:
        message += (
            "\n\n## PIN REFERENCE ERRORS — blueprint defects detected\n"
            + "\n".join(f"- {e}" for e in resolution_errors)
            + "\n\nDo NOT wire connections marked with '?'. "
            "Report the above errors as a Python comment instead of writing broken code."
        )

    if error_context:
        message += (
            f"\n\n## PREVIOUS ATTEMPT FAILED\n{error_context}"
            "\n\nFix the error and output corrected wiring code. "

        )

    return message


def generate_fill_zone(
    template_result: TemplateResult,
    blueprint: SystemBlueprint,
    model: str,
    temperature: float,
    error_context: str | None = None,
) -> str:
    system_prompt = _load_system_prompt()
    user_message = _build_user_message(template_result, blueprint, error_context)

    logger.info("Calling LLM coder (model=%s, retry=%s)", model, error_context is not None)

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    raw = response.choices[0].message.content or ""
    fill_zone = _strip_markdown(raw)

    logger.debug("LLM fill zone output:\n%s", fill_zone)
    return fill_zone


def _build_delta_user_message(
    template_result: TemplateResult,
    blueprint: SystemBlueprint,
    delta,
    error_context: str | None,
) -> str:
    comp_symbol = {comp.ref: comp.exact_part_name for comp in blueprint.components}

    relevant_refs: set[str] = set()
    for net in list(delta.added.values()) + [cur for _, cur in delta.changed.values()]:
        for c in net.connections:
            relevant_refs.add(c.component_ref)

    parts_lines = ["## DECLARED PARTS (subset — delta-relevant only)"]
    for ref in sorted(relevant_refs):
        pins = template_result.declared_parts.get(ref)
        if not pins:
            continue
        symbol = comp_symbol.get(ref, "")
        header = f"- {ref} ({symbol}):" if symbol else f"- {ref}:"
        pin_annotations = []
        for num in pins.by_number:
            name = pins.number_to_name.get(num, "")
            pin_annotations.append(f"pin {num} ({name})" if name else f"pin {num}")
        parts_lines.append(f"{header} {', '.join(pin_annotations)}")

    emit_lines = ["\n## NETS TO EMIT"]
    resolution_errors: list[str] = []
    for kind, iterable in (("added", delta.added.items()), ("changed", delta.changed.items())):
        for name, payload in iterable:
            net = payload if kind == "added" else payload[1]
            pieces: list[str] = []
            for c in net.connections:
                try:
                    pin_num = _resolve_pin_to_number(
                        c.component_ref, c.pin_name, template_result.declared_parts
                    )
                    pieces.append(f"{c.component_ref}[{pin_num}]")
                except PinReferenceError as exc:
                    resolution_errors.append(str(exc))
                    pieces.append(f"{c.component_ref}[?{c.pin_name!r}]")
            emit_lines.append(f"- {name} [{kind}]: {', '.join(pieces)}")

    removed_lines = ["\n## NETS REMOVED "]
    for name in sorted(delta.removed):
        removed_lines.append(f"- {name}")
    if len(delta.removed) == 0:
        removed_lines.append("- (none)")

    message = "\n".join(parts_lines + emit_lines + removed_lines)
    message += (
        "\n\nEmit one NET_BEGIN/NET_END block per net under 'NETS TO EMIT'. "
        "Do NOT emit blocks for nets in 'NETS REMOVED' or for any other nets."
    )

    if resolution_errors:
        message += (
            "\n\n## PIN REFERENCE ERRORS — blueprint defects detected\n"
            + "\n".join(f"- {e}" for e in resolution_errors)
            + "\n\nDo NOT wire connections marked with '?'. "
            "Report the error as a Python comment inside the affected block."
        )

    if error_context:
        message += (
            f"\n\n## PREVIOUS ATTEMPT FAILED\n{error_context}"
            "\n\nFix the error and re-emit the affected blocks only."
        )

    return message


def generate_fill_zone_delta(
    template_result: TemplateResult,
    blueprint: SystemBlueprint,
    delta,  # netlist_diff.NetDelta
    model: str,
    temperature: float,
    error_context: str | None = None,
) -> str:
    system_prompt = _load_delta_prompt()
    user_message = _build_delta_user_message(
        template_result, blueprint, delta, error_context
    )

    logger.info(
        "Calling LLM coder in DELTA mode (model=%s, added=%d, changed=%d, removed=%d, retry=%s)",
        model,
        len(delta.added),
        len(delta.changed),
        len(delta.removed),
        error_context is not None,
    )

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    )

    raw = response.choices[0].message.content or ""
    delta_text = _strip_markdown(raw)
    logger.debug("LLM delta fill zone output:\n%s", delta_text)
    return delta_text
