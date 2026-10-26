"""Turn the cached parquet into the small JSON files the site reads.

    python pipeline/export.py

Writes, into `site/data/`:
  * `stations.json`          one summary row per gauge, plus the thresholds,
                             attribution and the hourly flagged-count history
  * `<slug>.json`            the last 14 days of readings, flag spans and rain
  * `<slug>_history.json`    the whole record as a daily envelope
  * `spacetime_event.json`   the main stems over the 2022 flood

No network: everything comes from `data/raw*/*.parquet`, so this costs no SEPA
credits and can be re-run freely.

Data: SEPA, provisional, Open Government Licence.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from common import (
    ABERDEEN_LAT,
    ABERDEEN_LON,
    REPO_ROOT,
    haversine_km,
    hourly_catchment_rain,
    iso,
    load_config,
    load_station_frames,
    slugify,
    write_json,
)
from detect import (
    QUALITY_ESTIMATED,
    QUALITY_GOOD,
    QUALITY_SUSPECT,
    QUALITY_UNCHECKED,
    DetectConfig,
    build_flags,
    rate_of_rise,
    to_regular_grid,
    true_runs,
)

WINDOW_DAYS = 14
STALE_AFTER_HOURS = 6

LICENCE = "Open Government Licence v3.0"
ATTRIBUTION = (
    "Contains SEPA data © Scottish Environment Protection Agency and "
    "database right. Provisional data, subject to revision."
)
DISCLAIMER = (
    "Not a flood warning service. For warnings see SEPA and the Met Office. "
    "Data: SEPA, provisional, Open Government Licence."
)
QUALITY_NAMES = {
    QUALITY_GOOD: "Good",
    QUALITY_ESTIMATED: "Estimated",
    QUALITY_SUSPECT: "Suspect",
    QUALITY_UNCHECKED: "Unchecked",
}

# Flag columns exported as index spans for the chart to shade.
SPAN_FLAGS = (
    ("gap", "is_gap"),
    ("flatline", "is_flatline"),
    ("spike", "is_spike"),
    ("suspect", "is_suspect"),
    ("fast_rise", "is_fast_rise"),
    ("high_level", "is_high_level"),
)

# The largest event in the ten-year record, as found by backtest.py (peak at
# 2022-11-18 11:45 UTC). Fixed deliberately: this window is evidence for a
# measured claim on the method page, not a live view, so it must not move.
EVENT_FROM = pd.Timestamp("2022-11-17T00:00:00Z")
EVENT_TO = pd.Timestamp("2022-11-20T00:00:00Z")


def spans(mask: pd.Series) -> list[list[int]]:
    """Boolean mask -> inclusive [start, end] index pairs."""
    return [[int(a), int(b - 1)] for a, b in true_runs(mask.to_numpy(dtype=bool))]


def clean(value: Any, digits: int = 3) -> Any:
    """Round for the wire, and turn NaN/NA into null rather than the string 'NaN'."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return None if not np.isfinite(number) else round(number, digits)


def station_thresholds(
    values: pd.Series, cfg: DetectConfig
) -> dict[str, float | None]:
    """The absolute levels and rates this station's percentile rules work out to.

    The page needs these to draw a threshold line, and quoting them makes the
    method checkable rather than a black box.
    """
    grid = to_regular_grid(values, cfg.freq_minutes)
    rates = rate_of_rise(grid, 1.0, cfg).dropna()
    rate_threshold = (
        float(np.percentile(rates, cfg.rise_pct_warn)) if len(rates) else float("nan")
    )
    return {
        "high_level_m": clean(np.nanpercentile(grid.dropna(), cfg.high_level_pct)),
        # The floor can bind on a very placid station, so quote what actually applies.
        "rise_1h_m_per_hr": clean(max(rate_threshold, cfg.rise_min_m_per_hr), 4),
        "rise_percentile": cfg.rise_pct_warn,
        "high_level_percentile": cfg.high_level_pct,
    }


def export_station(
    station: dict[str, Any],
    frame: pd.DataFrame,
    rain: pd.Series,
    cfg: DetectConfig,
    now: datetime,
    out_dir: Path,
) -> dict[str, Any]:
    """Write one station's detail and history files and return its summary row.

    `rain` is the catchment's hourly mean rainfall over the whole record.
    """
    slug = slugify(station["name"])
    values, quality = frame["value"], frame["quality"]
    flags = build_flags(values, values, cfg, quality=quality)

    window = flags.loc[flags.index >= flags.index.max() - timedelta(days=WINDOW_DAYS)]
    latest = flags.iloc[-1]
    age_hours = (now - flags.index.max().to_pydatetime()).total_seconds() / 3600
    stale = age_hours > STALE_AFTER_HOURS
    window_rain = rain.loc[window.index.min():window.index.max()]

    detail = {
        "slug": slug,
        "name": station["name"],
        "river": station["river"],
        "generated_utc": iso(pd.Timestamp(now)),
        "window_days": WINDOW_DAYS,
        "t": [iso(ts) for ts in window.index],
        "v": [clean(v) for v in window["value"]],
        "spans": {name: spans(window[column]) for name, column in SPAN_FLAGS},
        "record": {
            "from": iso(flags.index.min()),
            "to": iso(flags.index.max()),
            "min": clean(values.min()),
            "median": clean(values.median()),
            "max": clean(values.max()),
        },
        "thresholds": station_thresholds(values, cfg),
        # Hourly catchment-mean rainfall over the same fortnight.
        "rain": {
            "t": [iso(ts) for ts in window_rain.index],
            "mm": [clean(v, 2) for v in window_rain],
        } if not window_rain.empty else {},
    }
    write_json(out_dir / f"{slug}.json", detail)

    # Daily envelope over the whole record, so the site can offer ranges longer
    # than its 14-day window. Daily min/mean/max rather than every reading: ten
    # years at 15 minutes is 350,000 points per gauge, which no browser should
    # be asked to draw and no visitor should be asked to download. Fetched only
    # when a long range is chosen. Dates are implied by the start and the step,
    # which saves about a third of the file. Rain is a daily total: a sum over
    # a period, unlike level, which is a reading at an instant.
    daily = values.resample("1D").agg(["min", "mean", "max"])
    rain_daily = rain.resample("1D").sum(min_count=1).reindex(daily.index)
    write_json(out_dir / f"{slug}_history.json", {
        "from": daily.index.min().strftime("%Y-%m-%d"),
        "step": "day",
        "note": "daily minimum, mean and maximum level; daily total rainfall",
        "lo": [clean(v) for v in daily["min"]],
        "mid": [clean(v) for v in daily["mean"]],
        "hi": [clean(v) for v in daily["max"]],
        "rain_mm": [clean(v, 1) for v in rain_daily],
    })

    # `_` keys reach flagged_history and the event view and are dropped before
    # the index is written. Carried as pandas objects, not lists, because every
    # station's window ends at its own last reading: lists would line up by
    # position and silently shift a station by an hour or two.
    return {
        "_hourly_unusual": window["is_unusual"].resample("1h").max().astype(float),
        "_event_level": flags.loc[
            (flags.index >= EVENT_FROM) & (flags.index <= EVENT_TO), "value"
        ].resample("1h").mean(),
        "slug": slug,
        "name": station["name"],
        "river": station["river"],
        "catchment": station["catchment"],
        "main_stem": station["main_stem"],
        "station_no": station["station_no"],
        "ts_id": station["ts_id"],
        "lat": station["lat"],
        "lon": station["lon"],
        "km_to_aberdeen": clean(
            haversine_km(ABERDEEN_LAT, ABERDEEN_LON, station["lat"], station["lon"]), 1
        ),
        "stale": stale,
        "age_hours": clean(age_hours, 1),
        "latest": {
            "t": iso(flags.index.max()),
            "value": clean(latest["value"]),
            "quality": None if pd.isna(latest["quality"]) else int(latest["quality"]),
            "quality_name": QUALITY_NAMES.get(
                None if pd.isna(latest["quality"]) else int(latest["quality"])
            ),
            "level_pct": clean(latest["level_pct"], 1),
            "roc_1h": clean(latest["roc_1h"], 4),
            "roc_3h": clean(latest["roc_3h"], 4),
            **{
                column: bool(latest[column])
                for column in (
                    "is_gap",
                    "is_flatline",
                    "is_spike",
                    "is_suspect",
                    "is_provisional",
                    "is_fast_rise",
                    "is_high_level",
                    "is_unusual",
                )
            },
        },
        "record": detail["record"],
        "thresholds": detail["thresholds"],
        "flag_rates": {
            name: clean(flags[column].mean() * 100, 2) for name, column in SPAN_FLAGS
        },
    }


def write_spacetime_event(summaries: list[dict[str, Any]], out_dir: Path) -> None:
    """The main stems as space and time, over the 2022 flood.

    Over 72 hours the Dee's 13 h 45 m travel time is roughly a fifth of the
    width, which is the whole point of drawing it.

    Each gauge is scaled to its OWN range within this window, not to its
    ten-year percentile. Percentile has a ceiling, and in the largest event of
    the record every gauge pegs at the 100th for days - leaving no headroom to
    show the one thing the chart is for. Normalising within the event is also
    what the cross-correlation already does, so the two agree on method.

    Main stem only: tributary gauges have no meaningful place on a
    distance-downstream axis, because straight-line distance to Aberdeen is not
    position along a river.
    """
    grid = pd.date_range(EVENT_FROM, EVENT_TO, freq="1h")
    rivers: dict[str, list[dict[str, Any]]] = {}
    for s in summaries:
        if not s["main_stem"]:
            continue
        series = s["_event_level"].reindex(grid)
        if series.isna().all():
            continue
        lo, hi = series.min(), series.max()
        shape = (series - lo) / (hi - lo) * 100 if hi > lo else series * 0
        rivers.setdefault(s["river"], []).append({
            "slug": s["slug"],
            "name": s["name"],
            "km": s["km_to_aberdeen"],
            "pct": [clean(v, 1) for v in shape],
        })
    for gauges in rivers.values():
        gauges.sort(key=lambda g: -g["km"])       # upstream first

    write_json(out_dir / "spacetime_event.json", {
        "window_days": 3,
        "step_hours": 1,
        "from": iso(grid.min()),
        "event_utc": "2022-11-18T11:45:00Z",
        "unit": "of its own rise in this event",
        "rivers": rivers,
    })
    n = sum(len(g) for g in rivers.values())
    print(f"  {'event':<10} -> site/data/spacetime_event.json ({n} gauges)")


def flagged_history(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """How many gauges were flagged, hour by hour, over the window.

    A count on its own says nothing about whether things are building or
    easing, which is the first thing anyone wants to know after "how many".
    Only the hours every station covers are counted, since each window ends at
    that station's own latest reading.
    """
    series = [s["_hourly_unusual"] for s in summaries]
    grid = pd.date_range(max(x.index.min() for x in series),
                         min(x.index.max() for x in series), freq="1h")
    counts = sum(x.reindex(grid).fillna(0) for x in series)
    return {
        "from": iso(grid.min()),
        "step_hours": 1,
        "of": len(summaries),
        "counts": [int(c) for c in counts],
    }


def main() -> int:
    config = load_config()
    cfg = DetectConfig.from_config(config)
    frames = load_station_frames(config)
    out_dir = REPO_ROOT / "site" / "data"
    now = datetime.now(timezone.utc).replace(microsecond=0)

    rain = {c: hourly_catchment_rain(config, c)
            for c in {s["catchment"] for s in config["stations"]}}

    summaries = []
    for station in config["stations"]:
        summaries.append(export_station(station, frames[station["name"]],
                                        rain[station["catchment"]], cfg, now, out_dir))
        print(f"  {station['name']:<10} -> site/data/{slugify(station['name'])}.json")

    summaries.sort(key=lambda s: (s["river"], s["km_to_aberdeen"]))

    write_spacetime_event(summaries, out_dir)
    history = flagged_history(summaries)
    for s in summaries:
        for key in [k for k in s if k.startswith("_")]:
            s.pop(key)

    index = {
        "flagged_history": history,
        "generated_utc": iso(pd.Timestamp(now)),
        "licence": LICENCE,
        "attribution": ATTRIBUTION,
        "disclaimer": DISCLAIMER,
        "source": config["api"]["base_url"],
        "stale_after_hours": STALE_AFTER_HOURS,
        "window_days": WINDOW_DAYS,
        "quality_codes": {str(k): v for k, v in QUALITY_NAMES.items()},
        "thresholds": dataclasses.asdict(cfg),
        "stations": summaries,
    }
    write_json(out_dir / "stations.json", index, indent=1)
    print(f"  {'index':<10} -> site/data/stations.json")

    stale = [s["name"] for s in summaries if s["stale"]]
    print(f"\n{len(summaries)} stations exported, generated {index['generated_utc']}")
    if stale:
        print(f"STALE (no data in {STALE_AFTER_HOURS}h): {', '.join(stale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
