"""Scenario files: what runs at once, how big the media is, and what counts as good.

A scenario is a YAML file::

    name: sync-small-files
    library_items: 6000          # catalog rows before the run (cost that grows with it)
    duration: 60s                # hard wall-clock cap; active requests are cancelled
    actors:
      - kind: syncing
        devices: 1
        items: 2000
        sizes: {"200KB-1MB": 100%}
        concurrency: 16          # upload workers per device, as the app
        completion_lanes: 1      # complete-batch requests in flight, as the app
        target: {items_per_s: 40}

Sizes are a distribution of ranges with weights; each item draws a range,
then a size uniformly inside it. Targets are not obeyed by the runner: they
let the report say whether the actor met its goal.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_UNITS = {"B": 1, "KB": 1000, "MB": 1000**2, "GB": 1000**3}
_MAX_ITEM_BYTES = 32 * 1024**3
_SIZE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB)\s*$", re.IGNORECASE)
_DURATION = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*(ms|s|m|min|h)?\s*$")
ACTOR_KINDS = {"syncing"}
TARGET_KEYS = {"items_per_s", "mb_per_s", "total_p95_ms", "error_rate"}


class ScenarioError(ValueError):
    """The scenario file is not valid; the message says where."""


def parse_size(text: str) -> int:
    match = _SIZE.match(str(text))
    if not match:
        raise ScenarioError(f"not a size: {text!r} (use e.g. 500KB, 2MB, 1.5GB)")
    amount = float(match.group(1))
    size_bytes = amount * _UNITS[match.group(2).upper()]
    if not math.isfinite(size_bytes) or size_bytes > _MAX_ITEM_BYTES:
        raise ScenarioError(f"size exceeds the lab's 32 GiB per-item limit: {text!r}")
    return int(size_bytes)


def parse_duration(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = _DURATION.match(str(value))
    if not match:
        raise ScenarioError(f"not a duration: {value!r} (use e.g. 90s, 3m)")
    number, unit = float(match.group(1)), (match.group(2) or "s")
    return number * {"ms": 0.001, "s": 1, "m": 60, "min": 60, "h": 3600}[unit]


@dataclass(frozen=True)
class SizeRange:
    low: int
    high: int
    weight: float


@dataclass(frozen=True)
class SizeDistribution:
    ranges: tuple[SizeRange, ...]

    @classmethod
    def parse(cls, raw: Any) -> SizeDistribution:
        if not isinstance(raw, dict) or not raw:
            raise ScenarioError("sizes must map ranges to weights, e.g. {'200KB-2MB': 80%}")
        ranges = []
        for spec, weight in raw.items():
            low_text, separator, high_text = str(spec).partition("-")
            low = parse_size(low_text)
            high = parse_size(high_text) if separator else low
            if low < 1024 or high < low:
                raise ScenarioError(f"size range {spec!r} must be at least 1KB and ascending")
            try:
                share = float(str(weight).rstrip("%"))
            except (TypeError, ValueError) as exc:
                raise ScenarioError(f"size range {spec!r} has an invalid weight") from exc
            if not math.isfinite(share) or share <= 0:
                raise ScenarioError(f"size range {spec!r} needs a positive weight")
            ranges.append(SizeRange(low, high, share))
        return cls(tuple(ranges))

    def sample(self, rng: random.Random) -> int:
        chosen = rng.choices(self.ranges, weights=[r.weight for r in self.ranges])[0]
        return rng.randint(chosen.low, chosen.high)


@dataclass(frozen=True)
class SyncingActor:
    devices: int
    items: int
    sizes: SizeDistribution
    concurrency: int = 16
    completion_lanes: int = 1
    accounts: int = 1
    target: dict[str, float] = field(default_factory=dict)
    kind: str = "syncing"

    @property
    def label(self) -> str:
        return f"syncing×{self.devices}"


@dataclass(frozen=True)
class Scenario:
    name: str
    actors: tuple[SyncingActor, ...]
    duration: float
    library_items: int = 0
    seed: int = 42

    @property
    def accounts(self) -> int:
        return max(actor.accounts for actor in self.actors)


def _positive_int(raw: dict, key: str, default: int | None = None, *, where: str) -> int:
    value = raw.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ScenarioError(f"{where}: {key} must be a positive integer")
    return value


def parse(raw: Any) -> Scenario:
    if not isinstance(raw, dict):
        raise ScenarioError("a scenario is a mapping")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ScenarioError("name is required")
    actors_raw = raw.get("actors")
    if not isinstance(actors_raw, list) or not actors_raw:
        raise ScenarioError("actors must be a non-empty list")
    actors = []
    for index, item in enumerate(actors_raw):
        where = f"actors[{index}]"
        if not isinstance(item, dict):
            raise ScenarioError(f"{where} must be a mapping")
        kind = item.get("kind")
        if kind not in ACTOR_KINDS:
            raise ScenarioError(f"{where}: kind must be one of {sorted(ACTOR_KINDS)}, got {kind!r}")
        target = item.get("target", {}) or {}
        if not isinstance(target, dict):
            raise ScenarioError(f"{where}: target must be a mapping")
        unknown = set(target) - TARGET_KEYS
        if unknown:
            raise ScenarioError(
                f"{where}: unknown targets {sorted(unknown)}; known: {sorted(TARGET_KEYS)}"
            )
        try:
            parsed_target = {key: float(value) for key, value in target.items()}
        except (TypeError, ValueError) as exc:
            raise ScenarioError(f"{where}: target values must be numbers") from exc
        if any(not math.isfinite(value) or value < 0 for value in parsed_target.values()):
            raise ScenarioError(f"{where}: target values must be finite and non-negative")
        actors.append(
            SyncingActor(
                devices=_positive_int(item, "devices", 1, where=where),
                items=_positive_int(item, "items", where=where),
                sizes=SizeDistribution.parse(item.get("sizes")),
                concurrency=_positive_int(item, "concurrency", 16, where=where),
                completion_lanes=_positive_int(item, "completion_lanes", 1, where=where),
                accounts=_positive_int(item, "accounts", 1, where=where),
                target=parsed_target,
            )
        )
    library_items = raw.get("library_items", 0)
    if not isinstance(library_items, int) or isinstance(library_items, bool) or library_items < 0:
        raise ScenarioError("library_items must be zero or a positive integer")
    duration = parse_duration(raw.get("duration", "5m"))
    if not math.isfinite(duration) or duration <= 0:
        raise ScenarioError("duration must be finite and positive")
    seed = raw.get("seed", 42)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ScenarioError("seed must be an integer")
    return Scenario(
        name=name.strip(),
        actors=tuple(actors),
        duration=duration,
        library_items=library_items,
        seed=seed,
    )


def load(path: Path) -> Scenario:
    import yaml

    return parse(yaml.safe_load(path.read_text(encoding="utf-8")))
