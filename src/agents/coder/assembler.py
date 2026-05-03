"""this code produces .net and .svg"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from src.agents.coder.template_composer import (
    NETLIST_PLACEHOLDER,
    SVG_PLACEHOLDER,
    inject_fill_zone,
)
from src.core.config import get as cfg
from src.core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)


@dataclass
class AssemblyResult:

    netlist_path: str
    svg_path: str | None = None


def _find_netlistsvg() -> str | None:

    from_env = os.environ.get("NETLISTSVG_PATH")
    if from_env and os.path.isfile(from_env):
        return from_env

    configured = cfg("agents", "coder", "netlistsvg_path", default=None)
    if configured and os.path.isfile(configured):
        return configured

    return shutil.which("netlistsvg")


def _ensure_svg(svg_base: str, timeout: float = 10.0) -> str | None:

    svg_file = svg_base + ".svg"
    json_file = svg_base + ".json"
    skin_file = svg_base + "_skin.svg"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.isfile(svg_file) and os.path.getsize(svg_file) > 0:
            return svg_file
        time.sleep(0.3)

    if not os.path.isfile(json_file):
        logger.warning("generate_svg did not produce %s — skipping SVG", json_file)
        return None

    netlistsvg = _find_netlistsvg()
    if netlistsvg is None:
        logger.warning("netlistsvg binary not found skipping ")
        return None

    cmd = [netlistsvg, json_file, "-o", svg_file]
    if os.path.isfile(skin_file):
        cmd += ["--skin", skin_file]

    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("netlistsvg fallback failed: %s", exc)
        return None

    if os.path.isfile(svg_file) and os.path.getsize(svg_file) > 0:
        return svg_file
    return None


def assemble(skeleton: str, fill_zone: str, output_path: str) -> AssemblyResult:

    output_path = str(Path(output_path).resolve())
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    svg_base = os.path.splitext(output_path)[0]

    script = inject_fill_zone(skeleton, fill_zone)
    script = script.replace(NETLIST_PLACEHOLDER, output_path)
    script = script.replace(SVG_PLACEHOLDER, svg_base)

    logger.info("Assembling netlist → %s", output_path)

    tmp_dir = tempfile.mkdtemp(prefix="skidl_assemble_")
    script_path = os.path.join(tmp_dir, "generate_netlist.py")

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)

    env = os.environ.copy()
    netlistsvg_bin = _find_netlistsvg()
    if netlistsvg_bin:
        netlistsvg_dir = os.path.dirname(netlistsvg_bin)
        env["PATH"] = netlistsvg_dir + os.pathsep + env.get("PATH", "")

    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=tmp_dir,
            env=env,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Netlist generation script failed (rc={result.returncode}):\n"
                f"{result.stderr}"
            )

        if not os.path.exists(output_path):
            raise RuntimeError(
                f"Expected netlist file not found at {output_path}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

        logger.info("Netlist generated successfully: %s", output_path)

        svg_path = _ensure_svg(svg_base)
        if svg_path:
            logger.info("SVG schematic generated: %s", svg_path)
        else:
            logger.warning("SVG schematic was not generated")

        return AssemblyResult(netlist_path=output_path, svg_path=svg_path)

    except subprocess.TimeoutExpired:
        raise RuntimeError("Netlist generation timed out after 120 seconds")

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
