
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from src.core.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATE: dict[str, dict[str, Any]] = {}


def _path_for(design_id: str) -> Path:
    return PROJECT_ROOT / "output" / design_id / "telemetry.json"


def record(design_id: str, section: str, payload: dict[str, Any]) -> None:

    if not design_id:
        return
    try:
        with _LOCK:
            bucket = _STATE.setdefault(design_id, {})
            section_bucket = bucket.setdefault(section, {})
            section_bucket.update(payload)
            out_path = _path_for(design_id)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(bucket, indent=2, default=str), encoding="utf-8"
            )
    except Exception as exc:  # defensive: telemetry must never crash pipeline
        logger.debug("telemetry.record(%s, %s) failed: %s", design_id, section, exc)


def snapshot(design_id: str) -> dict[str, Any]:
    with _LOCK:
        return json.loads(json.dumps(_STATE.get(design_id, {})))


def reset(design_id: str | None = None) -> None:
    with _LOCK:
        if design_id is None:
            _STATE.clear()
        else:
            _STATE.pop(design_id, None)
