"""Shared paths, config loading and small I/O helpers for the pipeline."""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "pipeline" / "config.yaml"

# Aberdeen city centre, used only to order and label stations by how near the
# city they are.
ABERDEEN_LAT, ABERDEEN_LON = 57.1497, -2.0943


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km."""
    radius = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))


def slugify(name: str) -> str:
    """Station name -> filename stem, e.g. 'Heugh Head' -> 'heugh_head'."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read config.yaml."""
    with open(path or CONFIG_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def raw_dir(config: dict[str, Any]) -> Path:
    """Directory holding the parquet cache, created if absent."""
    directory = REPO_ROOT / config["fetch"]["raw_dir"]
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def iso(ts: pd.Timestamp) -> str:
    """UTC timestamp as the site expects it."""
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def load_station_frames(config: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Every configured level gauge's cached parquet, keyed by station name.

    Exits rather than carrying on without a gauge: a missing station would
    quietly change every cross-gauge result built from these.
    """
    directory = raw_dir(config)
    frames = {}
    for station in config["stations"]:
        path = directory / f"{slugify(station['name'])}.parquet"
        if not path.exists():
            sys.exit(f"No cache for {station['name']} - run fetch.py first")
        frames[station["name"]] = pd.read_parquet(path)
    return frames


def hourly_catchment_rain(config: dict[str, Any], catchment: str) -> pd.Series:
    """Hourly rainfall averaged across a catchment's gauges, whole record.

    The mean, not the sum: the catchment's average depth of rain, not the total
    caught by however many gauges happen to report that hour. Rain is context
    only; it is never run through the level detectors.
    """
    directory = REPO_ROOT / config["rain"]["raw_dir"]
    frames = []
    for gauge in config.get("rain_stations", []):
        path = directory / f"{slugify(gauge['name'])}.parquet"
        if gauge["catchment"] == catchment and path.exists():
            frames.append(pd.read_parquet(path)["value"].rename(gauge["name"]))
    if not frames:
        return pd.Series(dtype="float64")
    return pd.concat(frames, axis=1).sort_index().mean(axis=1)


def write_json(path: str | Path, payload: Any, indent: int | None = None) -> Path:
    """Write JSON, resolving a relative path against the repo root."""
    out = Path(path)
    if not out.is_absolute():
        out = REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=indent), encoding="utf-8")
    return out
