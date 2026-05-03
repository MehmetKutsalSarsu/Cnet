

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Set

from src.interfaces.schemas.scientist import SystemBlueprint



class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"
    UNKNOWN  = "UNKNOWN"



@dataclass
class FloatingPin:
    component_ref:    str
    component_type:   str            # category from ComponentDef
    pin_name:         str
    pin_number:       Optional[str]
    pin_electrical_type: Optional[str]
    severity:         Severity
    reason:           str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


INTENTIONAL_NC_NETS: frozenset[str] = frozenset({"NC", "UNCONNECTED"})


def _is_intentional_nc_net(net_name: Optional[str]) -> bool:
    if not net_name:
        return False
    return net_name.strip().upper() in INTENTIONAL_NC_NETS


@dataclass
class IntentionalNCPin:
    component_ref:  str
    component_type: str
    pin_name:       str
    pin_number:     Optional[str]
    pin_electrical_type: Optional[str]
    nc_net_name:    str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FloatingPinReport:
    design_id:     str
    floating_pins: List[FloatingPin] = field(default_factory=list)
    intentional_nc_pins: List[IntentionalNCPin] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.floating_pins)

    @property
    def critical_pins(self) -> List[FloatingPin]:
        return [fp for fp in self.floating_pins if fp.severity == Severity.CRITICAL]

    @property
    def high_pins(self) -> List[FloatingPin]:
        return [fp for fp in self.floating_pins if fp.severity == Severity.HIGH]

    def by_severity(self) -> Dict[str, List[FloatingPin]]:
        result: Dict[str, List[FloatingPin]] = {s.value: [] for s in Severity}
        for fp in self.floating_pins:
            result[fp.severity.value].append(fp)
        return result

    def summary(self) -> str:
        counts = self.by_severity()
        lines = [
            "=== Stage 2: Floating Pin Detection Report ===",
            f"Design ID  : {self.design_id}",
            f"Total      : {len(self.floating_pins)} floating pin(s)",
            f"NC pins    : {len(self.intentional_nc_pins)} intentionally unconnected",
        ]
        for sev in Severity:
            pins = counts[sev.value]
            if pins:
                lines.append(f"  {sev.value:<8}: {len(pins)}")
                for fp in pins:
                    num_str = f" (#{fp.pin_number})" if fp.pin_number else ""
                    lines.append(
                        f"    {fp.component_ref}.{fp.pin_name}{num_str}"
                        f"  [{fp.component_type}]  etype={fp.pin_electrical_type}"
                        f"  — {fp.reason}"
                    )
        if self.intentional_nc_pins:
            lines.append("  [PASS] Intentionally Unconnected:")
            for nc in self.intentional_nc_pins:
                num_str = f" (#{nc.pin_number})" if nc.pin_number else ""
                lines.append(
                    f"    {nc.component_ref}.{nc.pin_name}{num_str}"
                    f"  [{nc.component_type}]  etype={nc.pin_electrical_type}"
                    f"  — on net '{nc.nc_net_name}'"
                )
        if not self.has_issues:
            lines.append("  No floating pins detected.")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "design_id":     self.design_id,
            "floating_pins": [fp.to_dict() for fp in self.floating_pins],
            "intentional_nc_pins": [nc.to_dict() for nc in self.intentional_nc_pins],
        }



_IC_CATEGORIES:        frozenset[str] = frozenset({"ic", "mcu", "microcontroller", "op_amp",
                                                    "opamp", "comparator", "gate", "logic",
                                                    "driver", "amplifier", "timer"})
_REGULATOR_CATEGORIES: frozenset[str] = frozenset({"regulator", "ldo", "voltage_regulator",
                                                    "dc_dc", "converter", "pmic"})
_CAPACITOR_CATEGORIES: frozenset[str] = frozenset({"capacitor", "cap"})
_RESISTOR_CATEGORIES:  frozenset[str] = frozenset({"resistor", "res", "potentiometer",
                                                    "trimmer", "rheostat"})
_LED_CATEGORIES:       frozenset[str] = frozenset({"led", "rgb_led", "diode_led"})


def _normalise_category(raw: str) -> str:
    return raw.strip().lower()


def _is_ic(cat: str)        -> bool: return cat in _IC_CATEGORIES
def _is_regulator(cat: str) -> bool: return cat in _REGULATOR_CATEGORIES
def _is_capacitor(cat: str) -> bool: return cat in _CAPACITOR_CATEGORIES
def _is_resistor(cat: str)  -> bool: return cat in _RESISTOR_CATEGORIES
def _is_led(cat: str)       -> bool: return cat in _LED_CATEGORIES


#clarifcation
def _classify(pin_etype: Optional[str], component_category: str, pin_name: str) -> tuple[Severity, str]:
    """Return (Severity, reason) for a floating pin."""
    etype = (pin_etype or "").strip().lower()
    cat   = component_category  # already normalised by caller

    # critical
    if etype == "power_in" and (_is_ic(cat) or _is_regulator(cat)):
        return (
            Severity.CRITICAL,
            "Power/GND pin not connected — IC will not function.",
        )

    if etype == "power_out" and _is_regulator(cat):
        return (
            Severity.CRITICAL,
            "Regulator output pin is not connected — output rail is floating.",
        )

    if etype == "passive" and (_is_capacitor(cat) or _is_resistor(cat)):
        return (
            Severity.CRITICAL,
            "Passive component has a floating leg — component is non-functional.",
        )

    #high
    if etype == "input" and _is_ic(cat):
        return (
            Severity.HIGH,
            "Floating input pin on IC — can cause oscillation or latch-up.",
        )

    #cirtic
    if etype == "output" and _is_ic(cat):
        return (
            Severity.CRITICAL,
            "IC output pin is floating — an unconnected output produces a broken circuit. "
            "Must be wired to a net.",
        )

    #low
    if etype == "passive" and _is_led(cat):
        return (
            Severity.LOW,
            "Floating passive pin on LED — possibly an unused colour channel (e.g. RGB).",
        )

    if "AREF" in pin_name.upper():
        return (
            Severity.LOW,
            "Floating Analog Reference (AREF) pin. Standard practice allows leaving this floating if ADC is unused.",
        )

    #unknown
    return (
        Severity.UNKNOWN,
        f"Floating pin (etype='{etype}', category='{cat}') — manual review required.",
    )



def run_floating_pin_detection(blueprint: SystemBlueprint) -> FloatingPinReport:
    connected_pins: Set[str] = set()
    nc_pin_nets: Dict[str, str] = {}

    for net in blueprint.nets:
        is_nc_net = _is_intentional_nc_net(net.name)
        for conn in net.connections:
            key = f"{conn.component_ref}::{conn.pin_name}"
            connected_pins.add(key)
            if is_nc_net and key not in nc_pin_nets:
                nc_pin_nets[key] = net.name

    floating: List[FloatingPin] = []
    intentional_nc: List[IntentionalNCPin] = []

    for comp in blueprint.components:
        if not comp.pins:
            continue

        cat = _normalise_category(comp.category)

        for pin in comp.pins:
            key = f"{comp.ref}::{pin.name}"

            if getattr(pin, "no_connect", False):
                intentional_nc.append(IntentionalNCPin(
                    component_ref=comp.ref,
                    component_type=comp.category,
                    pin_name=pin.name,
                    pin_number=pin.number,
                    pin_electrical_type=pin.electrical_type,
                    nc_net_name=nc_pin_nets.get(key, "no_connect"),
                ))
                continue

            if key in nc_pin_nets:
                intentional_nc.append(IntentionalNCPin(
                    component_ref=comp.ref,
                    component_type=comp.category,
                    pin_name=pin.name,
                    pin_number=pin.number,
                    pin_electrical_type=pin.electrical_type,
                    nc_net_name=nc_pin_nets[key],
                ))
                continue

            if (pin.electrical_type or "").strip().lower() == "no_connect":
                continue

            if key in connected_pins:
                continue

            severity, reason = _classify(pin.electrical_type, cat, pin.name)
            floating.append(FloatingPin(
                component_ref=comp.ref,
                component_type=comp.category,
                pin_name=pin.name,
                pin_number=pin.number,
                pin_electrical_type=pin.electrical_type,
                severity=severity,
                reason=reason,
            ))

    return FloatingPinReport(
        design_id=blueprint.design_id,
        floating_pins=floating,
        intentional_nc_pins=intentional_nc,
    )
