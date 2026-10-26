"""Incremental fetch of 15-minute river levels into a local parquet cache.

    python pipeline/fetch.py              # top up each station
    python pipeline/fetch.py --full       # re-request the whole backfill span
    python pipeline/fetch.py --station Garthdee --station Park

First run per station backfills `fetch.backfill_years`. Later runs ask only for
what is missing, plus `fetch.overlap_hours` of already-held data: SEPA values are
provisional and the recent tail gets revised, so re-requesting it and preferring
the newer copy keeps the cache honest.

Data: SEPA, provisional, Open Government Licence.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from common import REPO_ROOT, load_config, raw_dir, slugify
from sepa_client import SepaApiError, SepaClient, TimeseriesValues, client_from_config

# SEPA caps a single request at 300,000 points. Stay clear of the edge so a
# boundary-inclusive response can never tip us over into TooManyResults.
SAFE_POINTS_PER_REQUEST = 250_000

# SEPA's `from`/`to` format: "yyyy-MM-ddTHH:mm:ss".
API_DATETIME = "%Y-%m-%dT%H:%M:%S"


def empty_frame() -> pd.DataFrame:
    """An empty cache frame with the right dtypes and a UTC index."""
    frame = pd.DataFrame({"value": pd.Series(dtype="float64"),
                          "quality": pd.Series(dtype="Int16")})
    frame.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    return frame


def to_frame(values: TimeseriesValues) -> pd.DataFrame:
    """Turn a getTimeseriesValues response into a UTC-indexed frame."""
    if not values.rows:
        return empty_frame()

    columns = values.columns or ["Timestamp", "Value", "Quality Code"]
    frame = pd.DataFrame(values.rows, columns=columns)

    def column(name: str, position: int) -> pd.Series:
        if name in frame.columns:
            return frame[name]
        return frame.iloc[:, position]

    out = pd.DataFrame(
        {
            "value": pd.to_numeric(column("Value", 1), errors="coerce").astype("float64"),
            "quality": pd.to_numeric(column("Quality Code", 2), errors="coerce").astype("Int16"),
        }
    )
    # Timestamps arrive as e.g. "2026-10-24T08:00:00.000Z" - already UTC.
    out.index = pd.DatetimeIndex(
        pd.to_datetime(column("Timestamp", 0), utc=True, format="ISO8601"),
        name="timestamp",
    )
    return out.sort_index()


def merge(cached: pd.DataFrame, fresh: pd.DataFrame) -> pd.DataFrame:
    """Combine cache and new data, preferring the newer copy of any timestamp."""
    if cached.empty:
        combined = fresh
    elif fresh.empty:
        combined = cached
    else:
        combined = pd.concat([cached, fresh])
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def read_cache(path: Path) -> pd.DataFrame:
    """Load a station's parquet cache, or an empty frame if there isn't one."""
    if not path.exists():
        return empty_frame()
    frame = pd.read_parquet(path)
    if not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.DatetimeIndex(frame.index, name="timestamp")
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    return frame.sort_index()


def plan_windows(
    start: datetime, end: datetime, freq_minutes: int = 15
) -> list[tuple[datetime, datetime]]:
    """Split [start, end] into request-sized windows under SEPA's point cap."""
    if start >= end:
        return []
    span = timedelta(minutes=freq_minutes * SAFE_POINTS_PER_REQUEST)
    windows, cursor = [], start
    while cursor < end:
        stop = min(cursor + span, end)
        windows.append((cursor, stop))
        cursor = stop
    return windows


def missing_windows(
    cached: pd.DataFrame,
    spec: dict[str, Any],
    now: datetime,
    full: bool,
    backfill_days: float | None = None,
    record_from: datetime | None = None,
) -> list[tuple[datetime, datetime]]:
    """Work out what we actually need from SEPA, and nothing more.

    Three cases, and the first one is why this returns a list:

    * **Older than we hold.** Asking for a longer history must fetch only the
      part before the cache starts. Re-requesting years we already have would
      cost full price every time the window is widened - about 2,400 wasted
      credits on the first extension alone.
    * **Newer than we hold**, plus `overlap_hours` of data we do hold, because
      SEPA revises on a monthly cycle.
    * **Nothing cached**, or `--full`: ask for the whole requested span.
    """
    days = backfill_days or 365.25 * spec["backfill_years"]
    desired_start = now - timedelta(days=days)

    # Never ask for years before the gauge existed. Requesting 1970 from a
    # station installed in 2008 returns nothing useful and inflates SEPA's
    # point estimate, which is what the 300,000-point refusal is based on.
    if record_from is not None and desired_start < record_from:
        desired_start = record_from

    if full or cached.empty:
        return [(desired_start, now)]

    one_sample = timedelta(minutes=spec["freq_minutes"])
    oldest = cached.index.min().to_pydatetime()
    newest = cached.index.max().to_pydatetime()

    windows = []
    if desired_start < oldest - one_sample:
        windows.append((desired_start, oldest))
    windows.append((newest - timedelta(hours=spec["overlap_hours"]), now))
    return windows


def update_station(
    client: SepaClient,
    spec: dict[str, Any],
    station: dict[str, Any],
    *,
    now: datetime,
    full: bool,
    backfill_days: int | None = None,
) -> dict[str, Any]:
    """Top up one station's cache. Returns a summary row for the report.

    `spec` describes the dataset being fetched - level or rainfall - so the
    same code serves both: they differ only in sample interval, cache
    directory and how far back to go.
    """
    path = spec["dir"] / f"{slugify(station['name'])}.parquet"
    cached = read_cache(path)
    rows_before = len(cached)

    freq_minutes = spec["freq_minutes"]
    record_from = station.get("record_from")
    if record_from is not None:
        record_from = datetime.combine(
            record_from, datetime.min.time(), tzinfo=timezone.utc
        )

    fetched = 0
    for gap_start, gap_end in missing_windows(
        cached, spec, now, full, backfill_days, record_from
    ):
        # Each gap may still exceed SEPA's 300,000-point cap, so chunk it.
        for window_start, window_end in plan_windows(gap_start, gap_end, freq_minutes):
            values = client.get_timeseries_values(
                station["ts_id"],
                from_=window_start.strftime(API_DATETIME),
                to=window_end.strftime(API_DATETIME),
            )
            fresh = to_frame(values)
            fetched += len(fresh)
            cached = merge(cached, fresh)

    if cached.empty:
        return {
            "name": station["name"],
            "river": station.get("river") or station.get("catchment", ""),
            "rows": 0,
            "added": 0,
            "fetched": fetched,
            "first": None,
            "last": None,
            "missing_pct": float("nan"),
        }

    cached.to_parquet(path, engine="pyarrow")

    # How much of the regular grid is actually present.
    freq = f"{freq_minutes}min"
    expected = len(pd.date_range(cached.index.min(), cached.index.max(), freq=freq))
    present = int(cached["value"].notna().sum())

    return {
        "name": station["name"],
        "river": station.get("river") or station.get("catchment", ""),
        "rows": len(cached),
        "added": len(cached) - rows_before,
        "fetched": fetched,
        "first": cached.index.min(),
        "last": cached.index.max(),
        "missing_pct": 100.0 * (1 - present / expected) if expected else float("nan"),
    }


def overlap_hours(config_value: float) -> float:
    """Overlap to re-request, in hours.

    CI runs two cadences: a cheap half-hourly top-up that only needs the last
    few hours, and one daily deep run using the full 35-day overlap that
    catches SEPA's monthly quality-control revisions. The deep overlap is what
    costs credits, so it is not worth paying 48 times a day.
    """
    override = os.environ.get("DDW_OVERLAP_HOURS")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return config_value


def dataset_specs(config, which):
    """The datasets this run should fetch: level, rainfall, or both.

    They differ only in sample interval, cache directory and how far back to
    go, so each is just a spec handed to the same `update_station`.
    """
    specs = []
    if which in ("level", "both"):
        specs.append({
            "label": "level", "freq_minutes": config["detect"]["freq_minutes"],
            "backfill_years": config["fetch"]["backfill_years"],
            "overlap_hours": overlap_hours(config["fetch"]["overlap_hours"]),
            "dir": raw_dir(config), "stations": config["stations"],
        })
    if which in ("rain", "both") and config.get("rain_stations"):
        rain = config["rain"]
        directory = REPO_ROOT / rain["raw_dir"]
        directory.mkdir(parents=True, exist_ok=True)
        specs.append({
            "label": "rainfall", "freq_minutes": rain["freq_minutes"],
            "backfill_years": rain["backfill_years"],
            "overlap_hours": overlap_hours(rain["overlap_hours"]),
            "dir": directory, "stations": config["rain_stations"],
        })
    return specs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true",
                        help="re-request the whole backfill span; fresher values "
                             "replace cached ones")
    parser.add_argument("--station", action="append", dest="stations",
                        help="gauge name; repeatable. Default: all in config.yaml")
    parser.add_argument("--days", type=float, dest="backfill_days",
                        help="override backfill_years for this run, in days. Widening it "
                             "extends the cache backwards and only pays for the years "
                             "not already held.")
    parser.add_argument("--years", type=float, help="same as --days, in years")
    parser.add_argument("--only", choices=("level", "rain", "both"), default="both",
                        help="which dataset to fetch (default: both)")
    args = parser.parse_args(argv)
    if args.years:
        args.backfill_days = args.years * 365.25

    config = load_config()
    client = client_from_config(config)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    mode = "full backfill" if args.full else "incremental"

    for spec in dataset_specs(config, args.only):
        stations = spec["stations"]
        if args.stations:
            wanted = {s.lower() for s in args.stations}
            stations = [s for s in stations if s["name"].lower() in wanted]
            if not stations:
                continue

        print(f"\n{spec['label'].upper()} - {len(stations)} gauge(s), {mode}, "
              f"{spec['freq_minutes']}-minute, up to {now:%Y-%m-%d %H:%M} UTC\n")

        summaries = []
        for station in stations:
            print(f"  {station['name']} (ts_id {station['ts_id']}) ...", end=" ", flush=True)
            try:
                summary = update_station(client, spec, station, now=now, full=args.full,
                                         backfill_days=args.backfill_days)
            except SepaApiError as exc:
                print("FAILED")
                print(f"\nSEPA request failed for {station['name']}: {exc}", file=sys.stderr)
                if exc.raw:
                    print(f"Raw response:\n{exc.raw}", file=sys.stderr)
                return 1
            summaries.append(summary)
            print(f"{summary['fetched']:,} values returned, {summary['added']:+,} new rows")

        header = (f"\n{'gauge':<16} {'in':<6} {'rows':>9} {'added':>9} {'gap %':>6}  "
                  f"{'first':<17} {'last':<17}")
        print(header)
        print("-" * (len(header) - 1))
        for s in summaries:
            first = f"{s['first']:%Y-%m-%d %H:%M}" if s["first"] is not None else "-"
            last = f"{s['last']:%Y-%m-%d %H:%M}" if s["last"] is not None else "-"
            print(f"{s['name']:<16} {s['river']:<6} {s['rows']:>9,} {s['added']:>+9,} "
                  f"{s['missing_pct']:>6.2f}  {first:<17} {last:<17}")

    print(f"\nAll timestamps UTC. Credits used this run: {client.credits_used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
