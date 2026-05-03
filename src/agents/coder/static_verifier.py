
from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from src.agents.coder.template_composer import FILL_ZONE_START, FILL_ZONE_END, PartPins, inject_fill_zone

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:

    passed: bool
    stage_failed: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)


def _check_ast(fill_zone: str) -> tuple[ast.Module | None, str | None]:

    try:
        tree = ast.parse(fill_zone)
        return tree, None
    except SyntaxError as exc:
        return None, f"syntacerror at line {exc.lineno}: {exc.msg}"


def _check_symbols(
    tree: ast.Module,
    declared_parts: dict[str, PartPins],
    net_names: set[str],
) -> list[str]:

    errors: list[str] = []

    for node in ast.walk(tree):
        #checks
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and isinstance(
                node.slice, ast.Constant
            ):
                part_ref = node.value.id
                pin_val = str(node.slice.value)

                if part_ref in declared_parts:
                    valid_pins = declared_parts[part_ref].by_number
                    if valid_pins and pin_val not in valid_pins:
                        errors.append(
                            f"Invalid pin '{pin_val}' for part '{part_ref}'. "
                            f"Valid pin numbers: {list(valid_pins)}"
                        )
                elif part_ref != "NC":  # NC is a SKiDL built-in
                    errors.append(
                        f"Undeclared part '{part_ref}' referenced in "
                        f"{part_ref}[{pin_val!r}]"
                    )

        # Net(name)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "Net":
                if node.args and isinstance(node.args[0], ast.Constant):
                    name = str(node.args[0].value)
                    if name not in net_names:
                        errors.append(
                            f"Net name '{name}' not in blueprint. "
                            f"Valid nets: {sorted(net_names)}"
                        )

    return errors


def _check_output_pin_connectivity(
    tree: ast.Module,
    declared_parts: dict[str, PartPins],
) -> list[str]:

    wired: dict[str, set[str]] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Subscript):
                    if (
                        isinstance(sub.value, ast.Name)
                        and isinstance(sub.slice, ast.Constant)
                    ):
                        ref = sub.value.id
                        pin_val = str(sub.slice.value)
                        wired.setdefault(ref, set()).add(pin_val)

    errors: list[str] = []
    for ref, part_pins in declared_parts.items():
        for num, etype in part_pins.number_to_etype.items():
            if etype == "output":
                if num not in wired.get(ref, set()):
                    pin_name = part_pins.number_to_name.get(num, "")
                    name_hint = f" ({pin_name})" if pin_name else ""
                    errors.append(
                        f"Output pin {num}{name_hint} of '{ref}' is not wired to any net."
                    )

    return errors


def _run_skidl_dry_run(skeleton: str, fill_zone: str) -> tuple[bool, str | None, list[str]]:

    script = inject_fill_zone(skeleton, fill_zone)

    lines = script.splitlines()
    lines = [l for l in lines if "generate_netlist(" not in l and "generate_svg(" not in l]
    script = "\n".join(lines) + "\n"

    tmp_dir = tempfile.mkdtemp(prefix="skidl_verify_")
    script_path = os.path.join(tmp_dir, "verify_script.py")

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=tmp_dir,
            env=os.environ.copy(),
        )

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        combined = stdout + "\n" + stderr

        if result.returncode == 0:
            warnings: list[str] = [
                line.strip()
                for line in combined.splitlines()
                if "WARNING" in line.strip().upper()
            ]
            return True, None, warnings

        warnings = []
        erc_errors: list[str] = []

        for line in combined.splitlines():
            stripped = line.strip()
            upper = stripped.upper()
            if not stripped:
                continue
            if "WARNING" in upper:
                warnings.append(stripped)
            elif "ERROR" in upper:
                erc_errors.append(stripped)

        error_detail = stderr if stderr else "Unknown error"
        if erc_errors:
            return False, f"Script execution failed (rc={result.returncode}):\n" + "\n".join(erc_errors), warnings

        return False, f"Script execution failed (rc={result.returncode}):\n{error_detail}", warnings

    except subprocess.TimeoutExpired:
        return False, "SKiDL dry-run timed out after 60 seconds", []

    finally:
        for fname in os.listdir(tmp_dir):
            try:
                os.unlink(os.path.join(tmp_dir, fname))
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


def verify(
    fill_zone: str,
    declared_parts: dict[str, PartPins],
    net_names: set[str],
    skeleton: str,
) -> VerificationResult:
    logger.info("Verifier check 1: AST parse")
    tree, parse_error = _check_ast(fill_zone)
    if tree is None:
        return VerificationResult(
            passed=False,
            stage_failed="ast_parse",
            error_message=parse_error,
        )

    logger.info("Verifier check 2: Symbol check")
    symbol_errors = _check_symbols(tree, declared_parts, net_names)
    if symbol_errors:
        return VerificationResult(
            passed=False,
            stage_failed="symbol_check",
            error_message="\n".join(symbol_errors),
        )

    logger.info("Verifier check 3: Output-pin connectivity")
    output_errors = _check_output_pin_connectivity(tree, declared_parts)
    if output_errors:
        return VerificationResult(
            passed=False,
            stage_failed="output_pin_connectivity",
            error_message="\n".join(output_errors),
        )

    logger.info("Verifier check 4: SKiDL dry run")
    erc_passed, erc_error, erc_warnings = _run_skidl_dry_run(skeleton, fill_zone)
    if not erc_passed:
        return VerificationResult(
            passed=False,
            stage_failed="skidl_erc",
            error_message=erc_error,
            warnings=erc_warnings,
        )

    return VerificationResult(passed=True, warnings=erc_warnings)
