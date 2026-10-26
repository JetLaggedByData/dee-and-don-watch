"""Project the next 12 hours at the two outlet gauges.

    python pipeline/forecast.py

What this is
------------
Routing of water that is ALREADY IN THE CHANNEL. Every feature is an observed
reading: upstream level, its rate of change shifted by that reach's measured
travel time, and rainfall that has already fallen. No weather forecast is used,
because SEPA does not publish one and predicting rain is the Met Office's job.

What this is not
----------------
Not a flood forecast and not a warning. It says where the level at one gauge is
likely to go given water that has already been measured upstream, and nothing
about rain still to come. Warnings come from SEPA and the Met Office.

Why only two gauges, and only twelve hours
------------------------------------------
Only Garthdee and Parkhill have gauges above them to route from; the other
eleven are headwaters or sit on tributaries with a single gauge. And skill
collapses around each catchment's travel time (13.75 h on the Dee, 15.75 h on
the Don) because beyond that the water has not entered the river yet. Past 24 h
the model measured WORSE than assuming nothing changes - Parkhill scores -0.23
at 36 h - so the projection stops at 12 h rather than fading out.

The band is the model's own test error at that horizon, read from routing.json,
so what is drawn as uncertainty is measured rather than assumed.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from typing import Any

import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from common import REPO_ROOT, load_config, slugify
from routing import REACHES, build_features, catchment_rain, load_levels

# Capped at twelve hours on purpose. See the module docstring.
PROJECT_HORIZONS_H = (1, 2, 3, 4, 6, 8, 12)

CATCHMENT = {"Garthdee": "Dee", "Parkhill": "Don"}


def iso(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def measured_error(routing: dict[str, Any], outlet: str, hours: int) -> float | None:
    """The model's own RMSE on held-out data at this horizon."""
    for reach in routing.get("reaches", []):
        if reach.get("outlet") != outlet:
            continue
        for row in reach.get("horizons", []):
            if row.get("hours") == hours:
                return row.get("model_rmse_m")
    return None


def project(outlet: str, levels: dict[str, pd.Series], rain: pd.Series,
            routing: dict[str, Any], freq_minutes: int) -> dict[str, Any] | None:
    target = levels[outlet]
    lags = REACHES[outlet]
    upstream = {name: levels[name] for name in lags if name in levels}
    if len(upstream) != len(lags):
        print(f"  {outlet}: missing an upstream gauge, skipped", file=sys.stderr)
        return None

    points = []
    issued_at = None
    issued_level = None

    for hours in PROJECT_HORIZONS_H:
        steps = int(hours * 60 / freq_minutes)
        frame = build_features(target, upstream, lags, rain, steps, freq_minutes)
        cols = [c for c in frame.columns if c not in ("y_delta", "y_abs", "persistence")]

        # Every row whose inputs are complete; the newest of these is the one
        # worth predicting from, and it is exactly the row whose target is
        # still unknown.
        usable = frame.dropna(subset=cols)
        labelled = usable.dropna(subset=["y_delta"])
        if len(labelled) < 10_000 or usable.empty:
            continue

        model = HistGradientBoostingRegressor(
            max_iter=400, learning_rate=0.06, max_leaf_nodes=31, random_state=0
        ).fit(labelled[cols], labelled["y_delta"])

        latest = usable.iloc[[-1]]
        delta = float(model.predict(latest[cols])[0])
        now_level = float(latest["own"].iloc[0])
        at = pd.Timestamp(latest.index[0])

        if issued_at is None:
            issued_at, issued_level = at, now_level

        points.append({
            "hours": hours,
            "t": iso(at + timedelta(hours=hours)),
            "level_m": round(now_level + delta, 3),
            "change_m": round(delta, 3),
            "band_m": (lambda e: round(e, 3) if e else None)(
                measured_error(routing, outlet, hours)),
        })
        print(f"  {outlet:<9} +{hours:>2}h  {now_level + delta:.3f} m "
              f"({delta:+.3f})")

    if not points:
        return None

    return {
        "slug": slugify(outlet),
        "name": outlet,
        "issued_t": iso(issued_at),
        "issued_level_m": round(issued_level, 3),
        "travel_time_hours": max(lags.values()),
        "upstream": list(lags),
        "points": points,
    }


def main() -> int:
    config = load_config()
    levels = load_levels(config)

    routing_path = REPO_ROOT / "site" / "data" / "routing.json"
    routing = json.loads(routing_path.read_text(encoding="utf-8")) \
        if routing_path.exists() else {}
    if not routing:
        print("No routing.json - the band will be omitted", file=sys.stderr)

    sample = next(iter(levels.values()))
    freq_minutes = int(
        pd.Series(sample.index).diff().dropna().mode().iloc[0].total_seconds() // 60)

    print(f"Routing water already in the channel ({freq_minutes}-minute data)\n")

    out: dict[str, Any] = {}
    for outlet in REACHES:
        if outlet not in levels:
            continue
        result = project(outlet, levels, catchment_rain(config, CATCHMENT[outlet]),
                         routing, freq_minutes)
        if result:
            out[slugify(outlet)] = result

    payload = {
        "generated_utc": iso(pd.Timestamp.now(tz="UTC")),
        "horizon_cap_hours": max(PROJECT_HORIZONS_H),
        "method": "gradient boosting on upstream level, rate of change shifted by "
                  "measured travel time, and rainfall already fallen",
        "band": "the model's own RMSE on held-out data at that horizon",
        "not_a_forecast": (
            "Routing of water already measured upstream. No weather forecast is "
            "used. This is not a flood warning service - for warnings see SEPA "
            "and the Met Office."
        ),
        "reaches": out,
    }

    path = REPO_ROOT / "site" / "data" / "forecast.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    print(f"\nWrote {path.relative_to(REPO_ROOT)} - {len(out)} reach(es)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
