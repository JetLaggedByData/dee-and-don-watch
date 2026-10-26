"""Fetch SEPA flow series that give long-record context for the level gauges.

    python pipeline/fetch_flow.py
    python pipeline/fetch_flow.py --only pot

Three series, all parameter Q (flow, in cumecs):

  * **Day.Mean**            daily mean flow, back to 1929 at some gauges
  * **PeaksOverThreshold**  every independent flood peak since 1971
  * **HydrologicalYear.Max** the annual maximum, the input to flood-frequency work

Why these and not more level data
---------------------------------
Level is measured against each gauge's own datum, so metres cannot be compared
between stations and ten years is a short base for "unusual". Flow is in cumecs
and *is* comparable, and these peak series reach back **55 years**, which turns
"99.8th percentile of ten years" into "the Nth largest since 1971".

These are deliberately kept apart from `fetch.py`. Level and rainfall are
regular grids; POT and annual maxima are **irregular event series** - a flood
peak happens when it happens - so the gap and flatline arithmetic that
`fetch.py` applies would be meaningless here.

**Caveat worth carrying downstream:** flow is derived from level through a
rating curve, and SEPA's own documentation says flows beyond the acceptable
extrapolation limit are coded Suspect (150). The largest floods - exactly the
ones these series exist to describe - are the most likely to be estimated.

Data: SEPA, provisional, Open Government Licence.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import pandas as pd

from common import REPO_ROOT, load_config, slugify
from fetch import merge, read_cache, to_frame
from sepa_client import SepaApiError, client_from_config

# SEPA bills sparse series at a flat 365 values per year regardless of how few
# there actually are, so a 55-year peak record costs about 20 credits a gauge.
SERIES = {
    "daily": "Day.Mean",
    "pot": "PeaksOverThreshold",
    "annual_max": "HydrologicalYear.Max",
}

RAW_DIR = REPO_ROOT / "data" / "raw_flow"


def discover(client, station_numbers: str) -> list[dict[str, str]]:
    """Find the flow ts_ids and their coverage, rather than hardcoding them."""
    return client.get_timeseries_list(
        returnfields=["station_no", "station_name", "ts_name", "ts_id", "coverage"],
        station_no=station_numbers,
        stationparameter_no="Q",
    )


def fetch_series(client, row: dict[str, str], key: str) -> dict[str, Any]:
    """Fetch one gauge's full record for one series."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{slugify(row['station_name'])}_{key}.parquet"
    cached = read_cache(path)
    before = len(cached)

    values = client.get_timeseries_values(
        row["ts_id"],
        from_=row["from"][:10],
        to=row["to"][:10],
    )
    fresh = to_frame(values)
    combined = merge(cached, fresh)

    if combined.empty:
        return {"station": row["station_name"], "rows": 0, "added": 0,
                "first": None, "last": None, "peak": None}

    combined.to_parquet(path, engine="pyarrow")
    return {
        "station": row["station_name"],
        "rows": len(combined),
        "added": len(combined) - before,
        "first": combined.index.min(),
        "last": combined.index.max(),
        "peak": float(combined["value"].max()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=tuple(SERIES) + ("all",), default="all")
    args = parser.parse_args(argv)

    config = load_config()
    client = client_from_config(config)
    numbers = ",".join(s["station_no"] for s in config["stations"])

    try:
        catalogue = discover(client, numbers)
    except SepaApiError as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1

    wanted = SERIES if args.only == "all" else {args.only: SERIES[args.only]}

    for key, ts_name in wanted.items():
        rows = [r for r in catalogue if r.get("ts_name") == ts_name and r.get("from")]
        rows.sort(key=lambda r: r["from"])
        print(f"\n{ts_name} - {len(rows)} gauge(s)\n")

        summaries = []
        for row in rows:
            print(f"  {row['station_name']} ({row['from'][:4]}-{row['to'][:4]}) ...",
                  end=" ", flush=True)
            try:
                summary = fetch_series(client, row, key)
            except SepaApiError as exc:
                print("FAILED")
                print(f"  {exc}", file=sys.stderr)
                if exc.raw:
                    print(f"  raw: {exc.raw[:300]}", file=sys.stderr)
                return 1
            summaries.append(summary)
            print(f"{summary['rows']:,} rows")

        header = f"\n  {'gauge':<14}{'rows':>8}{'first':>12}{'last':>12}{'max flow':>11}"
        print(header)
        print("  " + "-" * (len(header) - 3))
        for s in summaries:
            if s["first"] is None:
                print(f"  {s['station']:<14}{'no data':>8}")
                continue
            first = f"{s['first']:%Y-%m-%d}"
            last = f"{s['last']:%Y-%m-%d}"
            print(f"  {s['station']:<14}{s['rows']:>8,}{first:>12}{last:>12}"
                  f"{s['peak']:>11.1f}")

    print(f"\nFlow is in cumecs (m3/s). Credits used: {client.credits_used}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
