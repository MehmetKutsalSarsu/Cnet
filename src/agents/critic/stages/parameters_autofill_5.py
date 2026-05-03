

from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional

from src.interfaces.schemas.scientist import SystemBlueprint

logger = logging.getLogger(__name__)


_STANDARD_VOLTAGE_BINS: List[int] = [6, 10, 16, 25, 35, 50, 63, 100, 200, 400, 630]

_DEFAULT_CAP_VOLTAGE = "50V"
_VOLTAGE_DERATING_FACTOR = 1.5

#diode
_DIODE_TYPES: Dict[str, str] = {
    "1N5817": "Schottky",
    "1N5818": "Schottky",
    "1N5819": "Schottky",
    "1N5820": "Schottky",
    "1N5821": "Schottky",
    "1N5822": "Schottky",
    "SB540":  "Schottky",
    "SS14":   "Schottky",
    "SS34":   "Schottky",
    "SS54":   "Schottky",
    "BAT54":  "Schottky",
    "1N4001": "Rectifier",
    "1N4002": "Rectifier",
    "1N4003": "Rectifier",
    "1N4004": "Rectifier",
    "1N4005": "Rectifier",
    "1N4006": "Rectifier",
    "1N4007": "Rectifier",
    "1N4148": "Signal",
    "1N4733": "Zener",
    "BZX84":  "Zener",
}

# led color
_LED_VF: Dict[str, str] = {
    "red":    "1.8V",
    "green":  "2.0V",
    "yellow": "2.0V",
    "orange": "2.0V",
    "blue":   "3.2V",
    "white":  "3.2V",
}

# regulator
_REGULATOR_PARTS: Dict[str, Dict[str, str]] = {
    "LM317":  {"function": "adjustable voltage regulator", "package": "TO-220"},
    "LM337":  {"function": "adjustable negative voltage regulator", "package": "TO-220"},
    "LM338":  {"function": "adjustable voltage regulator", "package": "TO-220"},
}

_FIXED_REG_RE = re.compile(
    r"(?:L[M]?)?7[89](\d{2})", re.IGNORECASE
)

def _parse_voltage(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    m = re.search(r"([\d.]+)\s*[Vv]", str(raw))
    return float(m.group(1)) if m else None


def _next_standard_voltage(target: float) -> str:
    for v in _STANDARD_VOLTAGE_BINS:
        if v >= target:
            return f"{v}V"
    return f"{_STANDARD_VOLTAGE_BINS[-1]}V"


def _capacitor_voltage(blueprint: dict) -> str:
    constraints = blueprint.get("design_constraints") or {}
    raw = constraints.get("input_voltage")
    v_in = _parse_voltage(raw)
    if v_in is None:
        return _DEFAULT_CAP_VOLTAGE
    return _next_standard_voltage(math.ceil(v_in * _VOLTAGE_DERATING_FACTOR))


def _normalise_value(value: str) -> str:

    return value.strip().replace("\u2126", "\u03A9").replace("\u00B5", "\u03BC")


HandlerFn = Callable[[Dict[str, Any], Dict[str, Any]], Optional[Dict[str, Any]]]


def _handle_resistor(comp: dict, _bp: dict) -> Dict[str, Any]:
    value = _normalise_value(comp.get("value", ""))
    return {
        "resistance": value or "UNKNOWN",
        "power": "0.25W",
        "tolerance": "5%",
    }


def _handle_capacitor_polarized(comp: dict, bp: dict) -> Dict[str, Any]:
    value = _normalise_value(comp.get("value", ""))
    return {
        "capacitance": value or "UNKNOWN",
        "type": "electrolytic",
        "voltage": _capacitor_voltage(bp),
    }


def _handle_capacitor(comp: dict, bp: dict) -> Dict[str, Any]:
    value = _normalise_value(comp.get("value", ""))
    cap_type = "ceramic"
    m = re.search(r"([\d.]+)\s*(µF|uF|mF)", value, re.IGNORECASE)
    if m:
        num = float(m.group(1))
        unit = m.group(2).lower()
        if unit in ("mf",):
            num *= 1000  # mF → µF
        if num >= 1.0:
            cap_type = "electrolytic"

    return {
        "capacitance": value or "UNKNOWN",
        "type": cap_type,
        "voltage": _capacitor_voltage(bp),
    }


def _handle_fuse(comp: dict, _bp: dict) -> Dict[str, Any]:
    value = _normalise_value(comp.get("value", ""))
    return {
        "current_rating": value or "UNKNOWN",
        "type": "slow-blow",
    }


def _handle_diode(comp: dict, _bp: dict) -> Dict[str, Any]:
    value = comp.get("value", "").strip()
    part_upper = value.upper().replace("-", "").replace(" ", "")
    diode_type = "General"
    for part, dtype in _DIODE_TYPES.items():
        if part.upper() in part_upper:
            diode_type = dtype
            break
    return {
        "type": diode_type,
        "part": value or "UNKNOWN",
    }


def _handle_led(comp: dict, _bp: dict) -> Dict[str, Any]:
    value = comp.get("value", "").strip()
    colour = value.lower()
    vf = _LED_VF.get(colour, "2.0V")
    return {
        "color": value or "UNKNOWN",
        "Vf": vf,
        "If": "20mA",
    }


def _handle_regulator(comp: dict, _bp: dict) -> Dict[str, Any]:
    value = comp.get("value", "").strip()
    value_upper = value.upper().replace("-", "").replace(" ", "")

    for part, params in _REGULATOR_PARTS.items():
        if part.upper() in value_upper:
            return dict(params)

    m = _FIXED_REG_RE.search(value_upper)
    if m:
        voltage = m.group(1).lstrip("0") or "0"
        return {
            "output_voltage": f"{voltage}V",
            "max_current": "1.5A",
            "package": "TO-220",
        }

    v = _parse_voltage(value)
    if v is not None:
        return {
            "output_voltage": f"{v:g}V",
            "max_current": "1.5A",
            "package": "TO-220",
        }

    return {
        "function": "voltage regulator",
        "part": value or "UNKNOWN",
        "package": "TO-220",
    }




_HANDLERS: Dict[str, HandlerFn] = {
    "resistor":            _handle_resistor,
    "r":                   _handle_resistor,
    "capacitor":           _handle_capacitor,
    "c":                   _handle_capacitor,
    "cap":                 _handle_capacitor,
    "capacitor_polarized": _handle_capacitor_polarized,
    "c_polarized":         _handle_capacitor_polarized,
    "cp":                  _handle_capacitor_polarized,
    "fuse":                _handle_fuse,
    "diode":               _handle_diode,
    "d":                   _handle_diode,
    "led":                 _handle_led,
    "regulator":           _handle_regulator,
    "voltage_regulator":   _handle_regulator,
}

def autofill_parameters(blueprint: SystemBlueprint) -> SystemBlueprint:
    bp_dict = blueprint.model_dump()
    components = bp_dict.get("components") or []

    for comp in components:
        category_raw = (comp.get("category") or "").strip().lower()
        category = category_raw.replace(" ", "_")

        handler = _HANDLERS.get(category)
        if handler is None:
            logger.debug("No autofill handler for category %r (ref=%s)",
                         category_raw, comp.get("ref", "?"))
            continue

        params = handler(comp, bp_dict)
        if params is not None:
            existing = comp.get("parameters") or {}
            merged = {**params, **existing}
            comp["parameters"] = merged
            logger.debug("Autofilled %s (%s): %s",
                         comp.get("ref", "?"), category_raw, merged)

    return SystemBlueprint.model_validate(bp_dict)
