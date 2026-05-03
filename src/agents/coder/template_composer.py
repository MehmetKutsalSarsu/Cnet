from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.agents.coder.part_library import PART_LIBRARY_MAP
from src.core.config import get as cfg
from src.interfaces.schemas.scientist import ComponentDef, SystemBlueprint

#lib import
_PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, os.pardir)
)
_LOCAL_SKIDL_SRC = os.path.join(_PROJECT_ROOT, "skidl-master", "src")
_LOCAL_KICAD_LIB = os.path.join(_PROJECT_ROOT, "assets", "librarys", "kicad_lib")

class PartResolutionError(Exception):
    pass



FILL_ZONE_START = "# fill zone start"
FILL_ZONE_END = "# fill zone end"
NETLIST_PLACEHOLDER = "__NETLIST_OUTPUT__"
SVG_PLACEHOLDER = "__SVG_OUTPUT__"



@dataclass(frozen=True)
class PartPins:
   
    by_number: tuple[str, ...]
    name_to_number: dict[str, str]   # unique names only; duplicates dropped
    number_to_name: dict[str, str]
    number_to_etype: dict[str, str]  # for output-pin connectivity check


@dataclass
class TemplateResult:
   
    skeleton: str
    declared_parts: dict[str, PartPins]
    net_names: set[str]


def _format_part_declaration(comp: ComponentDef) -> str:
    
    library = comp.library
    symbol = comp.exact_part_name

    if not library or not symbol:
        resolved = PART_LIBRARY_MAP.get(comp.category.lower())
        if resolved:
            library, symbol = resolved
      
    kwargs = [
        f"library={library!r}",
        f"symbol={symbol!r}",
        f"mpn={(comp.mpn or comp.value or '')!r}",
        f"ref={comp.ref!r}",
    ]
    if comp.footprint:
        kwargs.append(f"footprint={comp.footprint!r}")
    if comp.value:
        kwargs.append(f"value={comp.value!r}")
    all_kwargs = ", ".join(kwargs)
    return f"{comp.ref} = load_part({all_kwargs})"


def inject_fill_zone(skeleton: str, fill_zone: str) -> str:
    marker = f"{FILL_ZONE_START}\n{FILL_ZONE_END}"
    replacement = f"{FILL_ZONE_START}\n{fill_zone}\n{FILL_ZONE_END}"
    return skeleton.replace(marker, replacement)


def compose_template(blueprint: SystemBlueprint) -> TemplateResult:
    kicad_lib_path = cfg(
        "agents", "coder", "kicad_lib_path",
        default=os.path.join(_PROJECT_ROOT, "assets", "librarys", "kicad_sym"),
    ) or os.path.join(_PROJECT_ROOT, "assets", "librarys", "kicad_sym")

    part_lines: list[str] = []
    declared_parts: dict[str, PartPins] = {}

    for comp in blueprint.components:
        part_lines.append(_format_part_declaration(comp))

        by_number: list[str] = []
        number_to_name: dict[str, str] = {}
        number_to_etype: dict[str, str] = {}
        name_counts: dict[str, int] = {}

        for pin in comp.pins:
            if not pin.number:
                continue
            by_number.append(pin.number)
            number_to_name[pin.number] = pin.name or ""
            number_to_etype[pin.number] = (pin.electrical_type or "").lower()
            if pin.name:
                name_counts[pin.name] = name_counts.get(pin.name, 0) + 1

        name_to_number: dict[str, str] = {}
        for pin in comp.pins:
            if not pin.name or not pin.number:
                continue
            if name_counts.get(pin.name, 0) > 1:
                continue
            name_to_number[pin.name] = pin.number

        declared_parts[comp.ref] = PartPins(
            by_number=tuple(by_number),
            name_to_number=name_to_number,
            number_to_name=number_to_name,
            number_to_etype=number_to_etype,
        )

    net_names = {net.name for net in blueprint.nets}

    lines = [
        "import logging",
        "import os",
        "import sys",
        "",
        "# vendored skidl",
        f"_skidl_src = {repr(_LOCAL_SKIDL_SRC)}",
        "if _skidl_src not in sys.path:",
        "    sys.path.insert(0, _skidl_src)",
        "",
        "# kicad import ",
        "for _env_var in list(os.environ):",
        "    if _env_var.startswith('KICAD') and _env_var.endswith('_SYMBOL_DIR'):",
        "        del os.environ[_env_var]",
        "",
        "import skidl as _skidl",
        "from skidl import Part, Net, ERC, generate_netlist, generate_svg, lib_search_paths, KICAD",
        "",
        "# skidl config",
        "_safe = os.path.splitext(os.path.basename(sys.argv[0]))[0].replace(' ', '_')",
        "_skidl.config.backup_lib_name = _safe",
        "",
        f"_KICAD_LIB = {_KICAD_LIB!r}",
        f"_KICAD_SYM = {kicad_lib_path!r}",
        "if not os.path.isdir(_KICAD_LIB):",
        "    raise RuntimeError(",
        "        f\"Custom symbol library missing: {_KICAD_LIB}. \"",
        "        f\"Pipeline requires assets/librarys/kicad_lib/ to exist.\"",
        "    )",
        "if not os.path.isdir(_LOCAL_KICAD_SYM):",
        "    raise RuntimeError(",
        "        f\"Standard symbol library missing: {_LOCAL_KICAD_SYM}. \"",
        "        f\"Pipeline requires assets/librarys/kicad_sym/ to exist.\"",
        "    )",
        "lib_search_paths[KICAD].clear()",
        "lib_search_paths[KICAD].append(_KICAD_LIB)  # Custom symbols first",
        "lib_search_paths[KICAD].append(_LOCAL_KICAD_SYM)  # Standard library second",
        "",
        "_GENERIC_FALLBACKS = {",
        '    "LED":                        ("Device", "LED"),',
        '    "R":                          ("Device", "R"),',
        '    "C":                          ("Device", "C"),',
        '    "L":                          ("Device", "L"),',
        '    "D":                          ("Device", "D"),',
        '    "Q_NPN_BEC":                  ("Device", "Q_NPN_BEC"),',
        '    "Q_PNP_BEC":                  ("Device", "Q_PNP_BEC"),',
        '    "Q_NMOS_GDS":                 ("Device", "Q_NMOS_GDS"),',
        '    "Q_PMOS_GDS":                 ("Device", "Q_PMOS_GDS"),',
        '    "SW_Push":                    ("Switch", "SW_Push"),',
        '    "Crystal":                    ("Device", "Crystal"),',
        '    "Fuse":                       ("Device", "Fuse"),',
        '    "Battery":                    ("Device", "Battery"),',
        '    "Transformer_1P_1S":          ("Device", "Transformer_1P_1S"),',
        '    "Conn_01x02":                 ("Connector_Generic", "Conn_01x02"),',
        '    "Conn_01x03":                 ("Connector_Generic", "Conn_01x03"),',
        '    "Conn_01x04":                 ("Connector_Generic", "Conn_01x04"),',
        '    "U":                          ("Device", "U"),',
        '    # Op-amps',
        '    "LMV321":                     ("Amplifier_Operational", "LMV321"),',
        '    "LM741":                      ("Amplifier_Operational", "LM741"),',
        '    # Voltage regulators',
        '    "AMS1117-3.3":                ("Regulator_Linear", "AMS1117-3.3"),',
        '    "LM317_TO-220":               ("Regulator_Linear", "LM317_TO-220"),',
        '    # MCUs',
        '    "ATmega328P-A":               ("MCU_Microchip_ATmega", "ATmega328P-A"),',
        '    "ESP32-WROOM-32":             ("RF_Module", "ESP32-WROOM-32"),',
        '    "STM32F103C8Tx":              ("MCU_ST_STM32F1", "STM32F103C8Tx"),',
        '    # Connectors',
        '    "USB_C_Receptacle_USB2.0":    ("Connector", "USB_C_Receptacle_USB2.0"),',
        '    # Passives',
        '    "R_Potentiometer":            ("Device", "R_Potentiometer"),',
        '    "D_Zener":                    ("Device", "D_Zener"),',
        '    "D_Schottky":                 ("Device", "D_Schottky"),',
        "}",
        "",
        "def _looks_like_mpn(name: str) -> bool:",
        "    import re",
        "    if len(name) > 20:",
        "        return True",
        "    if re.search(r'-\\d', name):",
        "        return True",
        "    return False",
        "",
        "",
        "def load_part(library, symbol, mpn='', ref='', footprint=None, value=None):",
        "    if not library or not symbol:",
        "        raise RuntimeError(",
        "            f\"PartResolutionError: Component ref='{ref}' has an empty library \"",
        "            f\"('{library}') or symbol ('{symbol}'). \"",
        "            f\"Add a library/symbol to the blueprint or provide a category fallback.\"",
        "        )",
        "",
        "    if _looks_like_mpn(symbol):",
        "        logging.warning(",
        "            \"Symbol '%s' looks like manufacturer\",",
        "            symbol,",
        "        )",
        "",
        "    kwargs = {}",
        "    if ref:",
        "        kwargs['ref'] = ref",
        "    if footprint:",
        "        kwargs['footprint'] = footprint",
        "    if value:",
        "        kwargs['value'] = value",
        "",
        "    try:",
        "        part = Part(library, symbol, **kwargs)",
        "    except (ValueError, FileNotFoundError) as primary_err:",
        "        fb_lib, fb_sym = _GENERIC_FALLBACKS.get(symbol, ('Device', symbol))",
        "        logging.warning(",
        "            \"Symbol '%s' not found in '%s'. Falling back to '%s:%s'. (Original error: %s)\",",
        "            symbol, library, fb_lib, fb_sym, primary_err,",
        "        )",
        "        try:",
        "            part = Part(fb_lib, fb_sym, **kwargs)",
        "        except (ValueError, FileNotFoundError) as fallback_err:",
        "            raise RuntimeError(",
        "                f\"PartResolutionError: Could not resolve symbol '{symbol}' \"",
        "                f\"from library '{library}', and fallback '{fb_lib}:{fb_sym}' also failed. \"",
        "                f\"Primary error: {primary_err}. Fallback error: {fallback_err}.\"",
        "            ) from fallback_err",
        "",
        "    if mpn:",
        "        part.value = mpn",
        "",
        "    return part",
        "",
        *part_lines,
        "",
        FILL_ZONE_START,
        FILL_ZONE_END,
        "",
        "ERC()",
        f"generate_netlist(file_='{NETLIST_PLACEHOLDER}')",
        f"generate_svg(file_='{SVG_PLACEHOLDER}')",
    ]

    skeleton = "\n".join(lines) + "\n"

    return TemplateResult(
        skeleton=skeleton,
        declared_parts=declared_parts,
        net_names=net_names,
    )
