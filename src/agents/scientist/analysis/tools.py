import json
import logging
import os
import re
from langchain_core.tools import tool

from src.core.paths import ASSETS_DIR

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_KICAD_LIB = os.path.join(_PROJECT_ROOT, "assets", "librarys", "kicad_lib")

logger = logging.getLogger(__name__)

INDEX_PATH = ASSETS_DIR / "json_library_index.json"

_index_cache: list[dict] | None = None


def _check_local_symbol_exists(library: str, symbol: str) -> bool:
    """Return True if the .kicad_sym file exists for this part."""
    if not library:
        return False
    lib_path = os.path.join(_KICAD_LIB, f"{library}.kicad_sym")
    return os.path.isfile(lib_path)


def _load_index() -> list[dict]:
    """Load the JSON library index, caching after first read."""
    global _index_cache
    if _index_cache is not None:
        return _index_cache
    if not INDEX_PATH.exists():
        logger.warning("Library index not found: %s", INDEX_PATH)
        return []
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            _index_cache = json.load(f)
        return _index_cache
    except json.JSONDecodeError as exc:
        logger.error("Corrupt library index at %s: %s", INDEX_PATH, exc)
        return []


def get_index() -> list[dict]:
    """Return the cached component index."""
    return _load_index()


@tool
def search_components(query: str, component_category: str = "") -> list[dict]:
    """
    Search the KiCad component library for parts based on a query.
    Returns a lightweight list of the top 5 components (NO PIN DATA).
    Use this to browse for parts before requesting full pin data.
    """
    index = _load_index()
    query_lower = query.lower()
    cat_lower = component_category.lower()

    scored_results = []
    for comp in index:
        score = 0
        name = comp.get("exact_name", "").lower()
        desc = comp.get("description", "").lower()
        keywords = [k.lower() for k in comp.get("keywords", [])]
        cat = comp.get("category", "").lower()

        if query_lower == name:
            score += 10
        elif query_lower in name:
            overlap_ratio = len(query_lower) / max(len(name), 1)
            score += (overlap_ratio * 5)

        if query_lower in desc:
            score += 5

        if any(query_lower in k for k in keywords):
            score += 3

        if cat_lower and cat_lower in cat:
            score += 2

        if score > 0:
            light_comp = {
                "exact_name": comp.get("exact_name"),
                "library": comp.get("library"),
                "mpn": comp.get("mpn") or comp.get("part_number"),
                "description": comp.get("description"),
                "category": comp.get("category"),
                "footprint": comp.get("footprint"),
                "match_score": round(score, 2),
                "custom_symbol_available": _check_local_symbol_exists(
                    comp.get("library"), comp.get("exact_name")
                ),
            }
            scored_results.append(light_comp)

    scored_results.sort(key=lambda x: x["match_score"], reverse=True)
    return scored_results[:5]


def _sanitize_pins(comp: dict) -> dict:
    """Replace empty or tilde-only pin names with the pin number.

    KiCad uses '~' for visually unnamed pins. Some index builders strip the
    tilde, leaving empty strings that break downstream netlist generation.
    """
    import copy

    pins = comp.get("pins")
    if not pins:
        return comp

    fixed_pins = []
    for pin in pins:
        p = dict(pin)
        name = (p.get("name") or "").strip()
        if not name or name == "~":
            number = (p.get("number") or "").strip()
            if number:
                p["name"] = number
                logger.debug(
                    "replaced empty pin name with number '%s' on %s",
                    number, comp.get("exact_name", "?"),
                )
        fixed_pins.append(p)

    result = copy.copy(comp)
    result["pins"] = fixed_pins
    return result


_PIN_RE = re.compile(
    r'\(pin\s+(\w+)\s+\w+\b.*?\(name\s+"([^"]*)".*?\(number\s+"([^"]*)"',
    re.DOTALL,
)
_EXTENDS_RE = re.compile(r'\(extends\s+"([^"]+)"\s*\)')


def _parse_pins_from_symbol(exact_name: str, _seen: set | None = None) -> list[dict]:
    """Parse pins from a .kicad_sym file, following (extends ...) when needed.

    Used as a fallback when the JSON index has an empty pins list. Returns []
    if the file is missing or no pins can be resolved.
    """
    if _seen is None:
        _seen = set()
    if exact_name in _seen:
        return []
    _seen.add(exact_name)

    path = os.path.join(_KICAD_LIB, f"{exact_name}.kicad_sym")
    if not os.path.isfile(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        logger.warning("Fallback pin parse: cannot read %s: %s", path, exc)
        return []

    pins = [
        {"name": name, "number": number, "electrical_type": etype}
        for etype, name, number in _PIN_RE.findall(content)
    ]
    if pins:
        return pins

    parent_match = _EXTENDS_RE.search(content)
    if parent_match:
        parent = parent_match.group(1)
        logger.info(
            "Fallback pin parse: %s extends %s — recursing", exact_name, parent
        )
        return _parse_pins_from_symbol(parent, _seen)

    return []


@tool
def get_component_pins(exact_name: str) -> dict:
    """
    Retrieve the full pinout and parameters for a specific component.
    You MUST pass the 'exact_name' returned from search_components.
    """
    index = _load_index()
    for comp in index:
        if comp.get("exact_name") == exact_name:
            if not comp.get("pins"):
                fallback = _parse_pins_from_symbol(exact_name)
                if fallback:
                    logger.warning(
                        "get_component_pins: index missing pins for '%s'; "
                        "loaded %d pins from .kicad_sym fallback",
                        exact_name, len(fallback),
                    )
                    comp = {**comp, "pins": fallback}
            return _sanitize_pins(comp)

    return {"error": f"Component '{exact_name}' not found. Please use search_components first."}
