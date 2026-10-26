"""Trace a flood peak travelling downstream, gauge by gauge.

    python pipeline/wave.py
    python pipeline/wave.py --event 2022-11-18 --json site/data/wave.json

Takes the biggest event in the record and measures how long its peak took to
reach each successive gauge on the Dee and the Don.

Two independent measurements per reach, deliberately:

  * **peak to peak** - when each gauge recorded its highest value. Simple, but
    it rests on one sample per gauge and a flat crest moves it by hours.
  * **cross-correlation** - the lag that best aligns the two normalised
    hydrographs, using the whole event's shape.

Where the two agree, the travel time is believable. Where they disagree, the
reach is reported as uncertain rather than averaged into a tidy number.

Only **main-stem** gauges form a sequence. Tributary gauges (Feugh, Gairn,
Muick, Urie) sit on different watercourses and peak on their own schedule - the
first version of this mixed them in by sorting on straight-line distance to
Aberdeen, and the ordering collapsed. They are reported separately.

Distances are straight-line to Aberdeen, not along the channel, so speeds are
**apparent** and understate the real path length.

No network: runs off the parquet cache. Data: SEPA, provisional, Open
Government Licence.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from backtest import find_peaks, pick_event
from common import (
    ABERDEEN_LAT,
    ABERDEEN_LON,
    REPO_ROOT,
    haversine_km,
    load_config,
    raw_dir,
    slugify,
)
from detect import DetectConfig, cross_correlation_lag, to_regular_grid

# How much either side of the event to use when correlating hydrographs.
EVENT_WINDOW_HOURS = 96
# How close the two independent estimates must be before a reach counts as
# consistent. Deliberately tight: a 3-hour tolerance would wave through a
# 2.5-hour disagreement on a 4-hour travel time, which is a 60% discrepancy
# dressed up as agreement.
AGREEMENT_HOURS = 1.5


def event_slice(series: pd.Series, event: pd.Timestamp) -> pd.Series:
    window = timedelta(hours=EVENT_WINDOW_HOURS)
    return series.loc[event - window : event + window]


def reach(
    upstream: dict[str, Any], downstream: dict[str, Any], cfg: DetectConfig
) -> dict[str, Any]:
    """Measure one gauge-to-gauge reach two ways."""
    peak_hours = (downstream["peak"] - upstream["peak"]).total_seconds() / 3600
    correlated = cross_correlation_lag(upstream["window"], downstream["window"], cfg)

    difference = (
        None if correlated is None else abs(correlated["lag_hours"] - peak_hours)
    )
    return {
        "from": upstream["name"],
        "to": downstream["name"],
        "km_apart": round(abs(upstream["km"] - downstream["km"]), 1),
        "peak_to_peak_hours": round(peak_hours, 2),
        "xcorr_hours": None if correlated is None else round(correlated["lag_hours"], 2),
        "xcorr_correlation": (
            None if correlated is None else round(correlated["correlation"], 3)
        ),
        "difference_hours": None if difference is None else round(difference, 2),
        "agrees": bool(difference is not None and difference <= AGREEMENT_HOURS),
    }


def trace_river(
    river: str, gauges: list[dict[str, Any]], cfg: DetectConfig
) -> dict[str, Any]:
    """Order a river's main-stem gauges upstream to downstream and measure each reach."""
    gauges = sorted(gauges, key=lambda g: -g["km"])
    reaches = [reach(a, b, cfg) for a, b in zip(gauges, gauges[1:])]

    total_hours = (gauges[-1]["peak"] - gauges[0]["peak"]).total_seconds() / 3600
    span_km = gauges[0]["km"] - gauges[-1]["km"]
    monotonic = all(r["peak_to_peak_hours"] >= 0 for r in reaches)

    return {
        "river": river,
        "monotonic": monotonic,
        "total_hours": round(total_hours, 2),
        "span_km": round(span_km, 1),
        "apparent_kmh": round(span_km / total_hours, 2) if total_hours > 0 else None,
        "gauges": [
            {
                "name": g["name"],
                "km_to_aberdeen": round(g["km"], 1),
                "peak_utc": g["peak"].isoformat(),
                "peak_m": round(float(g["peak_m"]), 3),
                "hours_from_first": round(
                    (g["peak"] - gauges[0]["peak"]).total_seconds() / 3600, 2
                ),
            }
            for g in gauges
        ],
        "reaches": reaches,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", help="force an event date, YYYY-MM-DD")
    parser.add_argument("--json", help="write results to this path")
    args = parser.parse_args(argv)

    config = load_config()
    cfg = DetectConfig.from_config(config)
    cache_dir = raw_dir(config)

    loaded: dict[str, Any] = {}
    peaks_by_station: dict[str, list[pd.Timestamp]] = {}

    for station in config["stations"]:
        path = cache_dir / f"{slugify(station['name'])}.parquet"
        if not path.exists():
            print(f"No cache for {station['name']} - run fetch.py first", file=sys.stderr)
            return 1
        values = to_regular_grid(
            pd.read_parquet(path)["value"], config["detect"]["freq_minutes"]
        )
        loaded[station["name"]] = {
            "station": station,
            "values": values,
            "km": haversine_km(
                ABERDEEN_LAT, ABERDEEN_LON, station["lat"], station["lon"]
            ),
        }
        peaks_by_station[station["name"]] = find_peaks(values)

    event = (
        pd.Timestamp(args.event, tz="UTC")
        if args.event
        else pick_event(peaks_by_station)[0]
    )

    for name, entry in loaded.items():
        window = event_slice(entry["values"], event)
        entry["window"] = window
        if window.dropna().empty:
            entry["peak"] = None
            continue
        entry["peak"] = window.idxmax()
        entry["peak_m"] = window.max()
        entry["name"] = name

    rivers = []
    for river in sorted({
        e["station"]["river"] for e in loaded.values() if e["station"]["main_stem"]
    }):
        gauges = [
            e for e in loaded.values()
            if e["station"]["main_stem"] and e["station"]["river"] == river
            and e["peak"] is not None
        ]
        if len(gauges) >= 2:
            rivers.append(trace_river(river, gauges, cfg))

    # Each tributary is timed against the lowest main-stem gauge of its own
    # catchment: the Urie joins the Don, so Pitcaple is compared with Parkhill,
    # not with Garthdee on the Dee.
    outlets: dict[str, Any] = {}
    for e in loaded.values():
        if e["station"]["main_stem"] and e["peak"] is not None:
            catchment = e["station"]["catchment"]
            if catchment not in outlets or e["km"] < outlets[catchment]["km"]:
                outlets[catchment] = e
    tributaries = []
    for e in loaded.values():
        outlet = outlets.get(e["station"]["catchment"])
        if e["station"]["main_stem"] or e["peak"] is None or outlet is None:
            continue
        tributaries.append({
            "name": e["name"],
            "river": e["station"]["river"],
            "peak_utc": e["peak"].isoformat(),
            "peak_m": round(float(e["peak_m"]), 3),
            "outlet": outlet["name"],
            "hours_before_outlet": round(
                (outlet["peak"] - e["peak"]).total_seconds() / 3600, 2
            ),
        })
    tributaries.sort(key=lambda t: t["peak_utc"])

    # ---- report ---- #
    print(f"Event: {event:%Y-%m-%d %H:%M} UTC\n")
    for r in rivers:
        print(f"{r['river']} main stem - upstream to downstream")
        print(f"  {'km':>6}  {'gauge':<12}{'peak':<18}{'level':>7}{'+hrs':>7}")
        for g in r["gauges"]:
            print(f"  {g['km_to_aberdeen']:>6.1f}  {g['name']:<12}"
                  f"{pd.Timestamp(g['peak_utc']):%Y-%m-%d %H:%M}{g['peak_m']:>7.2f}"
                  f"{g['hours_from_first']:>7.2f}")
        print(f"  {'peak-peak':>10}{'xcorr':>9}{'diff':>7}{'corr':>7}  reach")
        for reach_row in r["reaches"]:
            mark = "consistent" if reach_row["agrees"] else "DIFFER"
            xc = "-" if reach_row["xcorr_hours"] is None else f"{reach_row['xcorr_hours']:.2f}h"
            df = "-" if reach_row["difference_hours"] is None else f"{reach_row['difference_hours']:.2f}"
            cc = "-" if reach_row["xcorr_correlation"] is None else f"{reach_row['xcorr_correlation']:.3f}"
            print(f"  {reach_row['peak_to_peak_hours']:>9.2f}h{xc:>9}{df:>7}{cc:>7}  "
                  f"{reach_row['from']} -> {reach_row['to']} ({mark})")
        state = "monotonic" if r["monotonic"] else "NOT monotonic"
        print(f"  => {state}; {r['total_hours']:.2f} h over {r['span_km']} km "
              f"({r['apparent_kmh']} km/h apparent)\n")

    if tributaries:
        print("Tributaries - not part of either sequence")
        for t in tributaries:
            print(f"  {t['name']:<12}{t['river']:<8}"
                  f"{pd.Timestamp(t['peak_utc']):%Y-%m-%d %H:%M}{t['peak_m']:>7.2f}"
                  f"   {t['hours_before_outlet']:.2f} h before {t['outlet']}")

    print("\nDistances are straight-line to Aberdeen, not along the channel, so the "
          "speeds are apparent and the real path is longer.")

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "event_utc": event.isoformat(),
                    "event_window_hours": EVENT_WINDOW_HOURS,
                    "agreement_hours": AGREEMENT_HOURS,
                    "rivers": rivers,
                    "tributaries": tributaries,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {out.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
