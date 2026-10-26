"""Turn the cached parquet into the small JSON files the site reads.

    python pipeline/export.py

Writes `site/data/stations.json` (one summary row per station, plus the
thresholds and attribution the page must display) and `site/data/<slug>.json`
(the last 14 days of readings and flag spans for one station).

No network: everything comes from `data/raw/*.parquet`, so this costs no SEPA
credits and can be re-run freely.

Data: SEPA, provisional, Open Government Licence.
"""

from __future__ import annotations

import dataclasses
import json
import sys
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
    load_config,
    raw_dir,
    slugify,
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


def iso(ts: pd.Timestamp) -> str:
    """UTC timestamp as the site expects it."""
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def spans(mask: pd.Series) -> list[list[int]]:
    """Boolean mask -> inclusive [start, end] index pairs."""
    flags = mask.to_numpy(dtype=bool)
    if not flags.any():
        return []
    padded = np.concatenate(([False], flags, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [[int(a), int(b - 1)] for a, b in zip(edges[::2], edges[1::2])]


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


def suspect_periods(flags: pd.DataFrame) -> list[dict[str, str]]:
    """Continuous stretches SEPA has marked Suspect, for the page to caveat."""
    periods = []
    for start, end in spans(flags["is_suspect"]):
        periods.append(
            {
                "from": iso(flags.index[start]),
                "to": iso(flags.index[end]),
                "samples": int(end - start + 1),
            }
        )
    return periods


def status_of(latest: pd.Series, stale: bool) -> str:
    """One word for the station card."""
    if stale:
        return "stale"
    if bool(
        latest["is_gap"]
        or latest["is_flatline"]
        or latest["is_spike"]
        or latest["is_suspect"]
    ):
        return "sensor"
    if bool(latest["is_unusual"]):
        return "unusual"
    return "ok"



def daily_catchment_rain(config: dict[str, Any], catchment: str) -> pd.Series:
    """Daily rainfall totals over a catchment, for the whole record.

    The 14-day chart shows hourly rain; the longer ranges need something that
    survives being drawn across ten years, and a daily total is the honest
    aggregate — rain is a sum over a period, unlike level, which is a reading
    at an instant.
    """
    rain_cfg = config.get("rain")
    gauges = [g for g in config.get("rain_stations", []) if g["catchment"] == catchment]
    if not rain_cfg or not gauges:
        return pd.Series(dtype="float64")

    directory = REPO_ROOT / rain_cfg["raw_dir"]
    frames = []
    for gauge in gauges:
        path = directory / f"{slugify(gauge['name'])}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path)["value"])
    if not frames:
        return pd.Series(dtype="float64")

    # Mean across gauges first, then summed per day: the catchment's average
    # depth of rain, not the total caught by however many gauges happen to
    # report that hour.
    hourly = pd.concat(frames, axis=1).mean(axis=1)
    return hourly.resample("1D").sum(min_count=1)


def catchment_rain(
    config: dict[str, Any], catchment: str, start: pd.Timestamp, end: pd.Timestamp
) -> dict[str, Any]:
    """Hourly rainfall over a catchment for the charted window.

    Averaged across that catchment's gauges rather than shown per gauge: twelve
    rain traces would be noise, and the question the page asks is "did rain fall
    on this catchment", not "what did each gauge record".

    Rain is context, never a flag. It is not run through the level detectors —
    percentile thresholds over rain totals would mean nothing.
    """
    rain_cfg = config.get("rain")
    gauges = [g for g in config.get("rain_stations", []) if g["catchment"] == catchment]
    if not rain_cfg or not gauges:
        return {}

    directory = REPO_ROOT / rain_cfg["raw_dir"]
    frames = []
    for gauge in gauges:
        path = directory / f"{slugify(gauge['name'])}.parquet"
        if not path.exists():
            continue
        series = pd.read_parquet(path)["value"].loc[start:end]
        if not series.empty:
            frames.append(series.rename(gauge["name"]))

    if not frames:
        return {}

    wide = pd.concat(frames, axis=1).sort_index()
    mean = wide.mean(axis=1, skipna=True)

    return {
        "gauges": len(frames),
        "freq_minutes": rain_cfg["freq_minutes"],
        "t": [iso(ts) for ts in mean.index],
        "mm": [clean(v, 2) for v in mean],
        "total_mm": clean(float(mean.sum()), 1),
        "peak_mm_per_hr": clean(float(mean.max()), 2),
        # Worst single gauge, so an average cannot hide a local downpour.
        "wettest_gauge": str(wide.sum().idxmax()),
        "wettest_gauge_mm": clean(float(wide.sum().max()), 1),
    }


def export_station(
    station: dict[str, Any],
    config: dict[str, Any],
    cfg: DetectConfig,
    now: datetime,
    cache_dir: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """Write one station's detail file and return its summary row."""
    slug = slugify(station["name"])
    frame = pd.read_parquet(cache_dir / f"{slug}.parquet")

    values, quality = frame["value"], frame["quality"]
    flags = build_flags(values, values, cfg, quality=quality)

    window = flags.loc[flags.index >= flags.index.max() - timedelta(days=WINDOW_DAYS)]
    latest = flags.iloc[-1]
    age_hours = (now - flags.index.max().to_pydatetime()).total_seconds() / 3600
    stale = age_hours > STALE_AFTER_HOURS

    rain = catchment_rain(config, station.get("catchment", ""),
                          window.index.min(), window.index.max())

    detail = {
        "slug": slug,
        "name": station["name"],
        "river": station["river"],
        "generated_utc": iso(pd.Timestamp(now)),
        "window_days": WINDOW_DAYS,
        "t": [iso(ts) for ts in window.index],
        "v": [clean(v) for v in window["value"]],
        "q": [None if pd.isna(q) else int(q) for q in window["quality"]],
        # Percentile against this station's own record, per reading. The level
        # in metres cannot be compared between gauges; this can, which is what
        # the space-time view on the site needs to put them on one scale.
        "pct": [clean(p, 1) for p in window["level_pct"]],
        "spans": {name: spans(window[column]) for name, column in SPAN_FLAGS},
        "record": {
            "from": iso(flags.index.min()),
            "to": iso(flags.index.max()),
            "min": clean(values.min()),
            "median": clean(values.median()),
            "max": clean(values.max()),
        },
        "thresholds": station_thresholds(values, cfg),
        "suspect_periods": suspect_periods(flags),
        "rain": rain,
    }
    (out_dir / f"{slug}.json").write_text(json.dumps(detail), encoding="utf-8")

    # Daily envelope over the whole record, so the site can offer ranges longer
    # than its 14-day window. Daily min/mean/max rather than every reading: ten
    # years at 15 minutes is 350,000 points per gauge, which no browser should
    # be asked to draw and no visitor should be asked to download. Fetched only
    # when a long range is chosen. Dates are implied by the start and the step,
    # which saves about a third of the file.
    daily = values.resample("1D").agg(["min", "mean", "max"])
    rain_daily = daily_catchment_rain(config, station.get("catchment", ""))         .reindex(daily.index)
    (out_dir / f"{slug}_history.json").write_text(json.dumps({
        "from": daily.index.min().strftime("%Y-%m-%d"),
        "step": "day",
        "note": "daily minimum, mean and maximum level; daily total rainfall",
        "lo": [clean(v) for v in daily["min"]],
        "mid": [clean(v) for v in daily["mean"]],
        "hi": [clean(v) for v in daily["max"]],
        "rain_mm": [clean(v, 1) for v in rain_daily],
    }), encoding="utf-8")

    # Hourly series for the space-time view and the flagged-count history.
    # Resampled deliberately: the window is 14 days and the plot is under
    # 1,000 px wide, so 15-minute resolution would be well under a pixel per
    # reading. The mean is over the percentile, not the level, because the
    # percentile is the thing being compared between gauges.
    #
    # These are carried as pandas objects, not lists, because every station's
    # window ends at its own last reading and so starts at its own hour. Lists
    # would line up by position and silently shift a station by an hour or two
    # — in a chart whose whole subject is timing. They are aligned on a common
    # grid in write_spacetime and dropped before the index is written.
    return {
        "_hourly_pct": window["level_pct"].resample("1h").mean(),
        "_hourly_unusual": window["is_unusual"].resample("1h").max().astype(float),
        "_event_level": flags.loc[
            (flags.index >= EVENT_FROM) & (flags.index <= EVENT_TO), "value"
        ].resample("1h").mean(),
        "slug": slug,
        "name": station["name"],
        "river": station["river"],
        "catchment": station.get("catchment", ""),
        "station_no": station["station_no"],
        "ts_id": station["ts_id"],
        "lat": station["lat"],
        "lon": station["lon"],
        "km_to_aberdeen": clean(
            haversine_km(ABERDEEN_LAT, ABERDEEN_LON, station["lat"], station["lon"]), 1
        ),
        "status": status_of(latest, stale),
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
        "suspect_periods": detail["suspect_periods"],
        "rain_14d_mm": rain.get("total_mm"),
        "flag_rates": {
            name: clean(flags[column].mean() * 100, 2) for name, column in SPAN_FLAGS
        },
    }


# The largest event in the ten-year record, as found by backtest.py (peak at
# 2022-11-18 11:45 UTC). Fixed deliberately: this window is evidence for a
# measured claim on the method page, not a live view, so it must not move.
EVENT_FROM = pd.Timestamp("2022-11-17T00:00:00Z")
EVENT_TO = pd.Timestamp("2022-11-20T00:00:00Z")


def write_spacetime_event(summaries: list[dict[str, Any]], out_dir: Path) -> None:
    """The same space-time view, over the 2022 flood instead of the last 14 days.

    On a fortnight the Dee's 13 h 45 m travel time is about 4% of the width and
    the diagonal is invisible; over 72 hours it is roughly a fifth, which is
    the whole point of drawing it.

    Each gauge is scaled to its OWN range within this window, not to its
    ten-year percentile. Percentile has a ceiling, and in the largest event of
    the record every gauge pegs at the 100th for days — leaving no headroom to
    show the one thing the chart is for. Normalising within the event is also
    what the cross-correlation above already does, so the two agree on method.
    """
    grid = pd.date_range(EVENT_FROM, EVENT_TO, freq="1h")
    rivers: dict[str, list[dict[str, Any]]] = {}
    for s in summaries:
        if s["river"] not in ("Dee", "Don"):
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
            "range_m": [clean(lo), clean(hi)],
        })
    for gauges in rivers.values():
        gauges.sort(key=lambda g: -g["km"])

    (out_dir / "spacetime_event.json").write_text(
        json.dumps({
            "window_days": 3,
            "step_hours": 1,
            "from": iso(grid.min()),
            "event_utc": "2022-11-18T11:45:00Z",
            "unit": "of its own rise in this event",
            "rivers": rivers,
        }),
        encoding="utf-8",
    )
    n = sum(len(g) for g in rivers.values())
    print(f"  {'event':<10} -> site/data/spacetime_event.json ({n} gauges)")


def common_grid(summaries: list[dict[str, Any]]) -> pd.DatetimeIndex:
    """The hours every station actually covers.

    Each station's window ends at its own latest reading, so the windows are
    offset from one another by up to a few hours. Taking the overlap means the
    space-time rows are genuinely comparable left to right.
    """
    starts = [s["_hourly_pct"].index.min() for s in summaries]
    ends = [s["_hourly_pct"].index.max() for s in summaries]
    return pd.date_range(max(starts), min(ends), freq="1h")


def write_spacetime(summaries: list[dict[str, Any]], grid: pd.DatetimeIndex,
                    out_dir: Path) -> None:
    """One small file for the space-time view: each main-stem gauge's hourly
    percentile, ordered upstream to downstream, on a shared time grid.

    Main stem only, and this is the whole point. The flood-wave work found that
    the gauges only form a sequence along a single watercourse — mixing the
    tributaries in put 8 of 13 out of order, because straight-line distance to
    Aberdeen is not position along a river. A tributary gauge has no meaningful
    place on this axis, so it is not given one.
    """
    rivers: dict[str, list[dict[str, Any]]] = {}
    for s in summaries:
        if s["river"] not in ("Dee", "Don"):      # tributaries sit elsewhere
            continue
        rivers.setdefault(s["river"], []).append({
            "slug": s["slug"],
            "name": s["name"],
            "km": s["km_to_aberdeen"],
            "pct": [clean(p, 1) for p in s["_hourly_pct"].reindex(grid)],
        })

    for gauges in rivers.values():
        gauges.sort(key=lambda g: -g["km"])       # upstream first

    (out_dir / "spacetime.json").write_text(
        json.dumps({
            "window_days": WINDOW_DAYS,
            "step_hours": 1,
            "from": iso(grid.min()),
            "rivers": rivers,
        }),
        encoding="utf-8",
    )
    total = sum(len(g["pct"]) for gs in rivers.values() for g in gs)
    print(f"  {'spacetime':<10} -> site/data/spacetime.json "
          f"({sum(len(g) for g in rivers.values())} gauges, {total} points)")


def flagged_history(summaries: list[dict[str, Any]],
                    grid: pd.DatetimeIndex) -> dict[str, Any]:
    """How many gauges were flagged, hour by hour, over the window.

    A count on its own says nothing about whether things are building or
    easing, which is the first thing anyone wants to know after "how many".
    """
    counts = sum(s["_hourly_unusual"].reindex(grid).fillna(0) for s in summaries)
    return {
        "from": iso(grid.min()),
        "step_hours": 1,
        "of": len(summaries),
        "counts": [int(c) for c in counts],
    }


def main() -> int:
    config = load_config()
    cfg = DetectConfig.from_config(config)
    cache_dir = raw_dir(config)

    out_dir = REPO_ROOT / "site" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    summaries = []
    for station in config["stations"]:
        slug = slugify(station["name"])
        if not (cache_dir / f"{slug}.parquet").exists():
            print(
                f"No cache for {station['name']} — run fetch.py first", file=sys.stderr
            )
            return 1
        summaries.append(export_station(station, config, cfg, now, cache_dir, out_dir))
        print(f"  {station['name']:<10} -> site/data/{slug}.json")

    summaries.sort(key=lambda s: (s["river"], s["km_to_aberdeen"]))

    grid = common_grid(summaries)
    write_spacetime(summaries, grid, out_dir)
    write_spacetime_event(summaries, out_dir)
    history = flagged_history(summaries, grid)
    # Carried on the summary row only to reach those two; the hourly series has
    # no business bloating the index that every page load fetches.
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
    (out_dir / "stations.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"  {'index':<10} -> site/data/stations.json")

    stale = [s["name"] for s in summaries if s["stale"]]
    print(f"\n{len(summaries)} stations exported, generated {index['generated_utc']}")
    if stale:
        print(f"STALE (no data in {STALE_AFTER_HOURS}h): {', '.join(stale)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
