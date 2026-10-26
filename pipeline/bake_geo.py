"""Bake real river and coastline geometry into a small static file.

    python pipeline/bake_geo.py

Run once, not on a schedule. The output lands in `site/geo/catchment.json` and
is committed: coastlines do not move, and the site must not make third-party
requests at runtime.

Why not a tile layer
--------------------
Leaflet plus OSM tiles would look like a map immediately, but every page view
would then call a tile server — a third-party request on behalf of a visitor,
which this project does not make. Fetching the geometry once, simplifying it
and serving it from our own origin gives the same picture with no runtime
dependency and nothing to log.

Source and licence
------------------
OpenStreetMap via the Overpass API, under the Open Database Licence (ODbL).
That licence requires attribution, which the site footer carries as
"© OpenStreetMap contributors".

Data: rivers and coast from OpenStreetMap. Gauge data from SEPA, separately.
"""

from __future__ import annotations

import json
import math
import sys
import time
from typing import Any

import requests

from common import REPO_ROOT

OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "dee-and-don-watch/1.0 (portfolio project; one-off geometry bake)"
BBOX = "56.95,-3.60,57.40,-1.95"

# ~50 m. One screen pixel is roughly 82 m at the width this is drawn, so the
# simplification is invisible while cutting the point count by an order of
# magnitude.
EPSILON_DEG = 0.0005

# The wider network is drawn as hairlines, so it can be cut much harder (~150 m).
EPSILON_NETWORK = 0.0015

# The four gauged tributaries, by the name OSM actually uses, mapped to the
# river name SEPA uses in config.yaml. These differ: the watercourse at Pitcaple
# is "River Ury" in OSM and "Urie" in SEPA's station metadata. Neither is
# renamed — the mapping is recorded here instead. "Water of Gairney" is a
# separate burn that a loose "Gairn" match picks up by mistake, hence exact
# names rather than a regex.
TRIBUTARIES = {
    "Water of Feugh": "Feugh",
    "River Gairn": "Gairn",
    "River Muick": "Muick",
    "River Ury": "Urie",
}

QUERIES = {
    "rivers": '[out:json][timeout:90];'
              '(way["waterway"="river"]["name"~"^River (Dee|Don)$"](' + BBOX + '););'
              'out geom;',
    "coast": '[out:json][timeout:90];'
             '(way["natural"="coastline"](' + BBOX + '););'
             'out geom;',
    "tributaries": '[out:json][timeout:120];'
                   '(way["waterway"~"^(river|stream)$"]["name"~"^(' +
                   "|".join(n.replace(" ", "\\\\s") for n in TRIBUTARIES) + ')$"](' +
                   BBOX + '););out geom;',
    # Every other river in the box. This is what makes the picture read as a
    # catchment rather than two lines: the Dee and Don are the trunks, and the
    # network around them is the rest of the drainage.
    "network": '[out:json][timeout:180];'
               '(way["waterway"="river"](' + BBOX + '););'
               'out geom;',
}


def fetch(query: str, attempts: int = 5) -> dict[str, Any]:
    """POST to Overpass, retrying on the 502s the public endpoint returns under
    load. Backoff is linear and deliberately slow: this is a free shared service
    and the bake is a one-off, so there is no reason to lean on it."""
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                OVERPASS, data={"data": query},
                headers={"User-Agent": UA}, timeout=240,
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last = exc
            wait = 12 * (attempt + 1)
            print(f"    attempt {attempt + 1} failed ({type(exc).__name__}); "
                  f"retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Overpass failed after {attempts} attempts: {last}")


def perpendicular(p, a, b) -> float:
    if a == b:
        return math.dist(p, a)
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def simplify(points: list, eps: float) -> list:
    """Ramer-Douglas-Peucker. Iterative, because a 3,000-point river will
    overflow the recursion limit."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        worst, index = 0.0, -1
        for i in range(start + 1, end):
            d = perpendicular(points[i], points[start], points[end])
            if d > worst:
                worst, index = d, i
        if worst > eps and index != -1:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [p for p, k in zip(points, keep) if k]


def stitch(ways: list[dict[str, Any]]) -> list[list]:
    """Join ways that share an endpoint into continuous lines.

    Overpass returns a river as dozens of separate ways in arbitrary order;
    drawn as-is they look like scattered fragments rather than one river.
    """
    lines = [[(round(n["lon"], 5), round(n["lat"], 5)) for n in w["geometry"]]
             for w in ways if w.get("geometry")]
    merged = True
    while merged:
        merged = False
        for i in range(len(lines)):
            for j in range(len(lines)):
                if i == j or not lines[i] or not lines[j]:
                    continue
                if lines[i][-1] == lines[j][0]:
                    lines[i] = lines[i] + lines[j][1:]
                    lines[j] = []
                    merged = True
                elif lines[i][-1] == lines[j][-1]:
                    lines[i] = lines[i] + lines[j][-2::-1]
                    lines[j] = []
                    merged = True
        lines = [ln for ln in lines if ln]
    return lines


def main() -> int:
    out: dict[str, Any] = {
        "attribution": "© OpenStreetMap contributors",
        "licence": "ODbL",
        "source": "Overpass API",
        "simplified_degrees": EPSILON_DEG,
        "rivers": {},
        "tributaries": {},
        "network": [],
        "coast": [],
    }

    print("Fetching from Overpass (one-off; do not schedule this)\n")

    try:
        rivers = fetch(QUERIES["rivers"])
        time.sleep(5)
        coast = fetch(QUERIES["coast"])
        time.sleep(5)
        tribs = fetch(QUERIES["tributaries"])
        time.sleep(5)
        network = fetch(QUERIES["network"])
    except RuntimeError as exc:
        print(f"Overpass request failed: {exc}", file=sys.stderr)
        return 1

    for name in ("River Dee", "River Don"):
        ways = [w for w in rivers["elements"] if w.get("tags", {}).get("name") == name]
        raw = sum(len(w.get("geometry") or []) for w in ways)
        lines = [simplify(ln, EPSILON_DEG) for ln in stitch(ways)]
        lines = [ln for ln in lines if len(ln) > 3]
        lines.sort(key=len, reverse=True)
        lines = lines[:4]          # the trunk and its longest continuations
        kept = sum(len(ln) for ln in lines)
        out["rivers"][name.replace("River ", "")] = lines
        print(f"  {name:<12}{len(ways):>3} ways  {raw:>6} pts -> {kept:>5} pts "
              f"in {len(lines)} line(s)")

    for osm_name, sepa_name in TRIBUTARIES.items():
        ways = [w for w in tribs["elements"]
                if w.get("tags", {}).get("name") == osm_name]
        lines = [simplify(ln, EPSILON_DEG) for ln in stitch(ways)]
        lines = [ln for ln in lines if len(ln) > 3]
        lines.sort(key=len, reverse=True)
        out["tributaries"][sepa_name] = lines[:2]
        label = f"{osm_name} -> {sepa_name}"
        print(f"  {label:<28}{len(ways):>3} ways  "
              f"{sum(len(ln) for ln in lines[:2]):>5} pts")
        if not lines:
            print(f"    NOT FOUND in OSM under this name", file=sys.stderr)

    # Everything already drawn as a trunk or a gauged tributary is excluded, or
    # it would be drawn twice at two different weights.
    drawn = {"River Dee", "River Don"} | set(TRIBUTARIES)
    rest = [w for w in network["elements"]
            if w.get("tags", {}).get("name") not in drawn]
    net_lines = [simplify(ln, EPSILON_NETWORK) for ln in stitch(rest)]
    net_lines = [ln for ln in net_lines if len(ln) > 2]
    out["network"] = net_lines
    print(f"  {'network':<28}{len(rest):>3} ways  "
          f"{sum(len(ln) for ln in net_lines):>5} pts in {len(net_lines)} line(s)")

    coast_lines = [simplify(ln, EPSILON_DEG) for ln in stitch(coast["elements"])]
    coast_lines = [ln for ln in coast_lines if len(ln) > 3]
    out["coast"] = coast_lines
    print(f"  {'coastline':<12}{len(coast['elements']):>3} ways  "
          f"{sum(len(w.get('geometry') or []) for w in coast['elements']):>6} pts -> "
          f"{sum(len(ln) for ln in coast_lines):>5} pts")

    path = REPO_ROOT / "site" / "geo" / "catchment.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    size = path.stat().st_size / 1024
    print(f"\nWrote {path.relative_to(REPO_ROOT)} — {size:.0f} KB")
    print("Committed deliberately: this is static, and the site makes no")
    print("third-party requests at runtime.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
