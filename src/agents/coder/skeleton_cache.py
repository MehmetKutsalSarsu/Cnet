
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src.agents.coder.netlist_diff import CACHE_SCHEMA_VERSION
from src.agents.coder.template_composer import PartPins, TemplateResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedSkeleton:
    components_hash: str
    schema_version: int
    skeleton: str
    declared_parts: Dict[str, dict]
    net_names: List[str]

    @classmethod
    def from_template_result(
        cls, tr: TemplateResult, components_hash: str
    ) -> "CachedSkeleton":
        return cls(
            components_hash=components_hash,
            schema_version=CACHE_SCHEMA_VERSION,
            skeleton=tr.skeleton,
            declared_parts={
                ref: {
                    "by_number": list(pp.by_number),
                    "name_to_number": dict(pp.name_to_number),
                    "number_to_name": dict(pp.number_to_name),
                    "number_to_etype": dict(pp.number_to_etype),
                }
                for ref, pp in tr.declared_parts.items()
            },
            net_names=sorted(tr.net_names),
        )

    def to_template_result(self) -> TemplateResult:
        return TemplateResult(
            skeleton=self.skeleton,
            declared_parts={
                ref: PartPins(
                    by_number=tuple(d["by_number"]),
                    name_to_number=dict(d["name_to_number"]),
                    number_to_name=dict(d["number_to_name"]),
                    number_to_etype=dict(d["number_to_etype"]),
                )
                for ref, d in self.declared_parts.items()
            },
            net_names=set(self.net_names),
        )


@dataclass(frozen=True)
class CachedFillZone:
    components_hash: str
    nets_hash: str
    schema_version: int
    fill_zone: str
    model: str
    created_at: str
    net_names: List[str]
    normalized_nets: List[dict]  # snapshots


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".tmp-", suffix=".json", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Cache read failed for %s: %s", path, exc)
        return None


@dataclass
class _LRUIndex:
   #tracker

    path: Path
    skeleton_entries: Dict[str, float] = field(default_factory=dict)
    fillzone_entries: Dict[str, float] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "_LRUIndex":
        data = _read_json(path) or {}
        return cls(
            path=path,
            skeleton_entries=dict(data.get("skeletons", {})),
            fillzone_entries=dict(data.get("fillzones", {})),
        )

    def save(self) -> None:
        _atomic_write_json(
            self.path,
            {
                "schema_version": CACHE_SCHEMA_VERSION,
                "skeletons": self.skeleton_entries,
                "fillzones": self.fillzone_entries,
            },
        )

    def touch_skeleton(self, key: str) -> None:
        self.skeleton_entries[key] = time.time()

    def touch_fillzone(self, key: str) -> None:
        self.fillzone_entries[key] = time.time()

    def evict_skeletons(self, max_entries: int, root: Path) -> None:
        self._evict(self.skeleton_entries, max_entries, root / "skeletons")

    def evict_fillzones(self, max_entries: int, root: Path) -> None:
        self._evict(self.fillzone_entries, max_entries, root / "fillzones")

    @staticmethod
    def _evict(entries: Dict[str, float], max_entries: int, dir_: Path) -> None:
        if len(entries) <= max_entries:
            return
        ordered = sorted(entries.items(), key=lambda kv: kv[1])
        for key, _ in ordered[: len(entries) - max_entries]:
            entries.pop(key, None)
            target = dir_ / f"{key}.json"
            try:
                target.unlink()
            except FileNotFoundError:
                pass


class SkeletonCache:
    def __init__(self, root: Path, max_entries: int = 64):
        self.root = Path(root)
        self.max_entries = max_entries
        self._index = _LRUIndex.load(self.root / "index.json")

    def _path(self, components_hash: str) -> Path:
        return self.root / "skeletons" / f"{components_hash}.json"

    def get(self, components_hash: str) -> Optional[CachedSkeleton]:
        data = _read_json(self._path(components_hash))
        if not data or data.get("schema_version") != CACHE_SCHEMA_VERSION:
            return None
        self._index.touch_skeleton(components_hash)
        self._index.save()
        return CachedSkeleton(**data)

    def put(self, entry: CachedSkeleton) -> None:
        if entry.schema_version != CACHE_SCHEMA_VERSION:
            raise ValueError(
                f"Refusing to cache entry with schema_version={entry.schema_version} "
                f"(expected {CACHE_SCHEMA_VERSION})"
            )
        _atomic_write_json(self._path(entry.components_hash), asdict(entry))
        self._index.touch_skeleton(entry.components_hash)
        self._index.evict_skeletons(self.max_entries, self.root)
        self._index.save()


class FillZoneCache:
    def __init__(self, root: Path, max_entries: int = 256):
        self.root = Path(root)
        self.max_entries = max_entries
        self._index = _LRUIndex.load(self.root / "index.json")

    def _path(self, components_hash: str, nets_hash: str) -> Path:
        return self.root / "fillzones" / f"{components_hash}_{nets_hash}.json"

    @staticmethod
    def _key(components_hash: str, nets_hash: str) -> str:
        return f"{components_hash}_{nets_hash}"

    def get(
        self, components_hash: str, nets_hash: str
    ) -> Optional[CachedFillZone]:
        data = _read_json(self._path(components_hash, nets_hash))
        if not data or data.get("schema_version") != CACHE_SCHEMA_VERSION:
            return None
        self._index.touch_fillzone(self._key(components_hash, nets_hash))
        self._index.save()
        return CachedFillZone(**data)

    def find_any_for_components(
        self, components_hash: str
    ) -> Optional[CachedFillZone]:
        candidates = [
            (key, ts)
            for key, ts in self._index.fillzone_entries.items()
            if key.startswith(components_hash + "_")
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda kv: kv[1], reverse=True)
        for key, _ in candidates:
            _, _, nets_hash = key.partition("_")
            hit = self.get(components_hash, nets_hash)
            if hit:
                return hit
        return None

    def put(self, entry: CachedFillZone) -> None:
        if entry.schema_version != CACHE_SCHEMA_VERSION:
            raise ValueError(
                f"Refusing to cache entry with schema_version={entry.schema_version} "
                f"(expected {CACHE_SCHEMA_VERSION})"
            )
        _atomic_write_json(
            self._path(entry.components_hash, entry.nets_hash), asdict(entry)
        )
        self._index.touch_fillzone(
            self._key(entry.components_hash, entry.nets_hash)
        )
        self._index.evict_fillzones(self.max_entries, self.root)
        self._index.save()
