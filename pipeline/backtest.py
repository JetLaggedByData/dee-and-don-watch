"""Replay the detectors over a past high-water event and report, honestly.

    python pipeline/backtest.py                 # pick the biggest event from the data
    python pipeline/backtest.py --event 2023-10-20
    python pipeline/backtest.py --json site/data/backtest.json

The event is chosen from the cached record rather than assumed, so this does
not quietly grade itself against a storm someone remembered.

Two numbers are reported together, always:

  * the lead time — how long before the peak each flag first fired
  * the base rate — how much of the whole record that flag is on for

A lead time alone is meaningless. An earlier version of these detectors
"predicted" Storm Babet 66 hours ahead, which sounded impressive until you
noticed the flag was on for 10% of the past five years. Misses and false alarms
are printed, not buried.

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

import numpy as np
import pandas as pd

from common import REPO_ROOT, load_config, raw_dir, slugify
from detect import DetectConfig, build_flags

LOOKBACK_HOURS = 72  # how far before a peak a flag still counts as early warning
CLUSTER_HOURS = 72  # peaks this close together are one basin-wide event
FALSE_ALARM_HOURS = 48  # a flag episode this far from any known peak is a false alarm
TOP_PEAKS = 20

REPORTED_FLAGS = ("is_fast_rise", "is_fast_rise_high", "is_high_level")


def find_peaks(
    values: pd.Series, count: int = TOP_PEAKS, separation_days: int = 7
) -> list[pd.Timestamp]:
    """The `count` highest peaks, each at least `separation_days` apart.

    Greedy rather than clever: take the highest remaining reading, blank out a
    fortnight around it, repeat. Independent events, no double-counting of one
    flood's shoulders.
    """
    remaining = values.dropna().copy()
    separation = timedelta(days=separation_days)
    peaks = []
    for _ in range(count):
        if remaining.empty:
            break
        at = remaining.idxmax()
        peaks.append(at)
        remaining = remaining.loc[
            (remaining.index < at - separation) | (remaining.index > at + separation)
        ]
    return peaks


def pick_event(
    peaks_by_station: dict[str, list[pd.Timestamp]],
) -> tuple[pd.Timestamp, list[str]]:
    """The biggest basin-wide event: most stations, then highest-ranked peaks.

    Breadth alone is not enough to choose by. Several storms bring all five
    gauges to a top-20 peak at once, and ranking those only by how many
    stations took part picks whichever happened first, not whichever was worst.
    `find_peaks` returns each station's peaks strongest-first, so the mean rank
    within a cluster separates a record flood from a merely wet week.
    """
    candidates = [
        (at, station, rank)
        for station, peaks in peaks_by_station.items()
        for rank, at in enumerate(peaks)
    ]
    candidates.sort()
    window = timedelta(hours=CLUSTER_HOURS)

    best_time: pd.Timestamp | None = None
    best_stations: set[str] = set()
    best_score: tuple[int, float] = (0, 0.0)

    for at, _, _ in candidates:
        members = [(s, r) for t, s, r in candidates if at <= t <= at + window]
        # Keep each station's strongest peak inside the window.
        strongest: dict[str, int] = {}
        for station, rank in members:
            strongest[station] = min(rank, strongest.get(station, rank))
        score = (len(strongest), -float(np.mean(list(strongest.values()))))
        if score > best_score:
            best_time, best_stations, best_score = at, set(strongest), score

    return best_time, sorted(best_stations)


def episodes(mask: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Contiguous runs of True, as (start, end) timestamps."""
    flags = mask.to_numpy(dtype=bool)
    if not flags.any():
        return []
    padded = np.concatenate(([False], flags, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [
        (mask.index[a], mask.index[b - 1]) for a, b in zip(edges[::2], edges[1::2])
    ]


def false_alarms(
    mask: pd.Series, peaks: list[pd.Timestamp]
) -> tuple[int, int]:
    """Flag episodes that were not near any of the station's top peaks."""
    window = timedelta(hours=FALSE_ALARM_HOURS)
    total = 0
    unexplained = 0
    for start, end in episodes(mask):
        total += 1
        if not any(start - window <= peak <= end + window for peak in peaks):
            unexplained += 1
    return unexplained, total


def assess(
    name: str, flags: pd.DataFrame, event_time: pd.Timestamp, peaks: list[pd.Timestamp]
) -> dict[str, Any]:
    """How each flag behaved around this station's peak for the event."""
    window = flags.loc[
        event_time - timedelta(hours=CLUSTER_HOURS) : event_time
        + timedelta(hours=CLUSTER_HOURS)
    ]
    if window.empty or window["value"].isna().all():
        return {"station": name, "peak": None}

    peak_at = window["value"].idxmax()
    lookback = flags.loc[peak_at - timedelta(hours=LOOKBACK_HOURS) : peak_at]

    results: dict[str, Any] = {
        "station": name,
        "peak": peak_at,
        "peak_m": float(window["value"].max()),
        "peak_pct": float(flags.loc[peak_at, "level_pct"]),
        "flags": {},
    }
    for column in REPORTED_FLAGS:
        fired = lookback.index[lookback[column]]
        first = fired.min() if len(fired) else None
        unexplained, total = false_alarms(flags[column], peaks)
        # If the flag was already set at the edge of the lookback window, the
        # true lead time is longer than we can see. Say so rather than quoting
        # the window width as if it were a measurement.
        censored = first is not None and not lookback.empty and first == lookback.index[0]
        results["flags"][column] = {
            "first_fired": first,
            "censored": censored,
            "lead_hours": (
                (peak_at - first).total_seconds() / 3600 if first is not None else None
            ),
            "base_rate_pct": float(flags[column].mean() * 100),
            "episodes_total": total,
            "episodes_unexplained": unexplained,
        }
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", help="force an event date, YYYY-MM-DD")
    parser.add_argument("--json", help="also write the results to this path")
    args = parser.parse_args(argv)

    config = load_config()
    cfg = DetectConfig.from_config(config)
    cache_dir = raw_dir(config)

    flags_by_station: dict[str, pd.DataFrame] = {}
    peaks_by_station: dict[str, list[pd.Timestamp]] = {}

    for station in config["stations"]:
        path = cache_dir / f"{slugify(station['name'])}.parquet"
        if not path.exists():
            print(f"No cache for {station['name']} — run fetch.py first", file=sys.stderr)
            return 1
        frame = pd.read_parquet(path)
        values = frame["value"]
        flags = build_flags(values, values, cfg, quality=frame["quality"])
        flags_by_station[station["name"]] = flags
        # Peaks are found on trustworthy readings only. Garthdee's record high
        # sits inside a stretch SEPA marks Suspect, and grading the detectors
        # against a probable sensor fault would flatter them.
        peaks_by_station[station["name"]] = find_peaks(
            flags["value"].where(~flags["is_suspect"])
        )

    if args.event:
        event_time = pd.Timestamp(args.event, tz="UTC")
        involved = sorted(peaks_by_station)
    else:
        event_time, involved = pick_event(peaks_by_station)

    span = next(iter(flags_by_station.values())).index
    print(f"Record: {span.min():%Y-%m-%d} to {span.max():%Y-%m-%d}  "
          f"({len(flags_by_station)} stations)")
    print(f"Event chosen from the data: {event_time:%Y-%m-%d %H:%M} UTC")
    print(f"Stations peaking together within {CLUSTER_HOURS}h: {', '.join(involved)}\n")

    results = [
        assess(name, flags, event_time, peaks_by_station[name])
        for name, flags in flags_by_station.items()
    ]

    for column in REPORTED_FLAGS:
        print(f"--- {column} ---")
        header = (f"  {'station':<10} {'peak':<17} {'level':>6} {'pct':>6}  "
                  f"{'first fired':<17} {'lead':>8}  {'on':>6}  {'false alarms':>13}")
        print(header)
        print("  " + "-" * (len(header) - 3))
        for row in results:
            if row.get("peak") is None:
                print(f"  {row['station']:<10} no data in the event window")
                continue
            detail = row["flags"][column]
            if detail["first_fired"] is None:
                fired, lead = "MISS", "     --"
            else:
                fired = f"{detail['first_fired']:%Y-%m-%d %H:%M}"
                prefix = ">=" if detail["censored"] else ""
                lead = f"{prefix}{detail['lead_hours']:.1f}h"
            alarms = f"{detail['episodes_unexplained']}/{detail['episodes_total']}"
            print(f"  {row['station']:<10} {row['peak']:%Y-%m-%d %H:%M}  "
                  f"{row['peak_m']:6.2f} {row['peak_pct']:5.1f}%  {fired:<17} {lead:>8}  "
                  f"{detail['base_rate_pct']:5.2f}%  {alarms:>13}")
        print()

    misses = [
        (row["station"], column)
        for row in results
        if row.get("peak") is not None
        for column in REPORTED_FLAGS
        if row["flags"][column]["first_fired"] is None
    ]
    print("Misses:", ", ".join(f"{s}/{c}" for s, c in misses) if misses else "none")
    print(
        f"'on' is the share of the whole {len(span) / 96 / 365:.1f}-year record the flag is "
        "set; read every lead time against it."
    )
    print(
        f"'false alarms' counts flag episodes more than {FALSE_ALARM_HOURS}h from any of the "
        f"station's top {TOP_PEAKS} peaks, over the total number of episodes."
    )
    print(
        f">= means the flag was already set {LOOKBACK_HOURS}h before the peak, so the true "
        "lead time is longer than measured here, not exactly this."
    )

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        serialisable = []
        for row in results:
            copy = dict(row)
            copy["peak"] = None if row.get("peak") is None else row["peak"].isoformat()
            copy["flags"] = {
                column: {
                    **detail,
                    "first_fired": (
                        detail["first_fired"].isoformat()
                        if detail["first_fired"] is not None
                        else None
                    ),
                }
                for column, detail in row.get("flags", {}).items()
            }
            serialisable.append(copy)
        out.write_text(
            json.dumps(
                {
                    "event_utc": event_time.isoformat(),
                    "stations_involved": involved,
                    "lookback_hours": LOOKBACK_HOURS,
                    "false_alarm_hours": FALSE_ALARM_HOURS,
                    "top_peaks": TOP_PEAKS,
                    "results": serialisable,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {out.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
