import yaml
from typing import Any

from src.core.paths import CONFIG_PATH

_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        _cache = yaml.safe_load(f) or {}
    return _cache


def reload() -> None:
    global _cache
    _cache = None


def get(section: str, *keys: str, default: Any = None) -> Any:

    data = _load().get(section, {})
    for key in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(key, default)
    return data
