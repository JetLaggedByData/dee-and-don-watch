"""List candidate 15-minute river-level series on the Dee and Don near Aberdeen.

Run this to confirm station ts_ids before trusting them. Station IDs quoted on
third-party sites are not evidence; this asks SEPA directly.

    python pipeline/discover.py

Data: SEPA, provisional, Open Government Licence.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

from common import ABERDEEN_LAT, ABERDEEN_LON, haversine_km, load_config
from sepa_client import SepaApiError, client_from_config

RETURNFIELDS = [
    "station_no",
    "station_name",
    "station_latitude",
    "station_longitude",
    "ts_id",
    "ts_path",
    "catchment_name",
    "coverage",
]


def parse_date(value: str) -> date | None:
    """Parse a `yyyy-MM-dd` coverage bound."""
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def describe(series: dict[str, str], live_within_days: int) -> dict[str, object]:
    """Turn one raw getTimeseriesList row into something printable."""
    try:
        lat = float(series["station_latitude"])
        lon = float(series["station_longitude"])
        distance = haversine_km(ABERDEEN_LAT, ABERDEEN_LON, lat, lon)
    except (KeyError, ValueError):
        lat = lon = distance = float("nan")

    ends = parse_date(series.get("to", ""))
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=live_within_days)
    return {
        "station_no": series.get("station_no", ""),
        "name": series.get("station_name", ""),
        "catchment": series.get("catchment_name", ""),
        "ts_id": series.get("ts_id", ""),
        "ts_path": series.get("ts_path", ""),
        "lat": lat,
        "lon": lon,
        "km_to_aberdeen": distance,
        "from": series.get("from", ""),
        "to": series.get("to", ""),
        "live": bool(ends and ends >= cutoff),
    }


def main() -> int:
    config = load_config()
    discovery = config["discovery"]
    wanted = set(discovery["catchments"])
    client = client_from_config(config)

    print(f"Querying getTimeseriesList for ts_path={discovery['ts_path']} ...")
    try:
        raw = client.get_timeseries_list(
            returnfields=RETURNFIELDS, ts_path=discovery["ts_path"]
        )
    except SepaApiError as exc:
        print(f"\nSEPA request failed: {exc}", file=sys.stderr)
        if exc.raw:
            print(f"Raw response:\n{exc.raw}", file=sys.stderr)
        return 1

    print(f"{len(raw)} 15-minute level series exist nationally.\n")

    candidates = [
        describe(series, discovery["live_within_days"])
        for series in raw
        if series.get("catchment_name") in wanted
    ]
    candidates.sort(key=lambda c: (c["catchment"], c["km_to_aberdeen"]))

    header = f"{'':2} {'station':<16} {'catchment':<20} {'ts_id':<10} {'km':>6}  {'coverage':<24}"
    print(header)
    print("-" * len(header))
    for c in candidates:
        mark = "ok" if c["live"] else " ."
        coverage = f"{c['from']} to {c['to']}"
        print(
            f"{mark:2} {c['name']:<16} {c['catchment']:<20} {c['ts_id']:<10} "
            f"{c['km_to_aberdeen']:>6.1f}  {coverage:<24}"
        )
    print("\n'ok' = updated within "
          f"{discovery['live_within_days']} days. '.' = stale, do not use.")

    # Cross-check config.yaml against what SEPA just said.
    by_ts_id = {c["ts_id"]: c for c in candidates}
    print("\nChecking config.yaml stations against the API:")
    problems = 0
    for station in config["stations"]:
        found = by_ts_id.get(station["ts_id"])
        if found is None:
            print(f"  MISMATCH  {station['name']}: ts_id {station['ts_id']} not in results")
            problems += 1
        elif found["name"] != station["name"]:
            print(
                f"  MISMATCH  ts_id {station['ts_id']} is '{found['name']}', "
                f"config says '{station['name']}'"
            )
            problems += 1
        elif not found["live"]:
            print(f"  STALE     {station['name']}: last value {found['to']}")
            problems += 1
        else:
            print(
                f"  confirmed {station['name']:<10} ({station['river']}) "
                f"ts_id={station['ts_id']}  {found['km_to_aberdeen']:.1f} km from Aberdeen"
            )

    print(f"\nCredits used: {client.credits_used}")
    if problems:
        print(f"{problems} station(s) need attention before fetching.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
