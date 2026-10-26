"""Shared paths and config loading for the pipeline."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

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
