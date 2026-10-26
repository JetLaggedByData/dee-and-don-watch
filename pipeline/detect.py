"""Detectors for unusual river-level behaviour and likely sensor faults.

Every function here is pure: a pandas Series in, a Series or list out. No
network, no files, no config loading - so all of it is directly testable.

Three families:

  1. data quality   - gaps, flatlines (stuck sensor), Hampel/MAD spikes
  2. rate of rise   - m/hr over 1h and 3h, as a percentile of the station's own
                      history of rises
  3. level          - percentile against the station's own history

Why percentiles and not a robust z-score
----------------------------------------
The first version scored rate of rise as a median/MAD z-score. On real SEPA
data that fired on 6-11% of all samples. The cause is sensor quantisation:
these gauges resolve to 1 mm, and the rivers register no change at all in
13-50% of samples, so the MAD of the hourly rate of rise is 0.001-0.004 m/hr -
one to four sensor ticks. A 3-sigma threshold therefore meant "rose by 3 mm in
an hour". The z-score was measuring the sensor's rounding, not the river.

Scoring against the station's own distribution of rises fixes that and makes
the claim on the page literally checkable: "rising faster than 99.5% of this
station's record". An absolute floor (`rise_min_m_per_hr`) sits on top, so that
during a drought the top 0.5% of a station's own rises cannot be announced as
notable when it is 2 mm an hour.

The rule that matters most
--------------------------
A reading we have reason to distrust never also becomes an "unusual rise". A
stuck sensor coming unstuck, a single bad value, or a stretch SEPA has marked
Suspect will all produce a large apparent rate of rise, and reporting that as a
real river movement is how a tool like this misleads people. `build_flags`
suppresses rise and level flags wherever a data-quality flag is set.

Nothing here forecasts anything. Data: SEPA, provisional, Open Government Licence.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

# SEPA quality codes, from "How data validity may change":
# https://timeseriesdoc.sepa.org.uk/api-documentation/before-you-start/how-data-validity-may-change/
QUALITY_GOOD = 50
QUALITY_ESTIMATED = 100
QUALITY_SUSPECT = 150
QUALITY_UNCHECKED = 254

# Codes that make a reading untrustworthy enough to suppress a rise flag.
#
# 254/Unchecked is deliberately NOT in here. It is the default state of all
# recent data until SEPA's monthly manual QC pass, so every live reading we
# ever display carries it. Suppressing on 254 would silence the entire site.
# It is surfaced as `is_provisional` instead, for the page to caveat.
SUPPRESSING_QUALITY = frozenset({QUALITY_SUSPECT})

# Makes the median absolute deviation comparable to a standard deviation.
MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class DetectConfig:
    """Detector thresholds. Defaults match the `detect` block of config.yaml."""

    freq_minutes: int = 15
    min_gap_minutes: int = 45
    flatline_minutes: int = 720
    flatline_tol_m: float = 0.0
    hampel_window: int = 13
    hampel_sigmas: float = 3.0
    rise_pct_warn: float = 99.5
    rise_pct_high: float = 99.9
    rise_min_m_per_hr: float = 0.05
    high_level_pct: float = 99.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "DetectConfig":
        known = {field.name for field in dataclasses.fields(cls)}
        block = config.get("detect", {}) or {}
        return cls(**{k: v for k, v in block.items() if k in known})


def _true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Half-open [start, stop) spans of consecutive True values."""
    if not mask.any():
        return []
    padded = np.concatenate(([False], mask, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[::2], edges[1::2]))


def to_regular_grid(s: pd.Series, freq_minutes: int = 15) -> pd.Series:
    """Sort, de-duplicate and reindex onto a regular grid, NaN where missing.

    Every other detector assumes this has been done: without it, a missing row
    and a present row are indistinguishable, and `shift` silently compares
    readings that are hours apart.
    """
    if s.empty:
        return s
    out = s.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    grid = pd.date_range(
        out.index.min(),
        out.index.max(),
        freq=f"{freq_minutes}min",
        name=out.index.name or "timestamp",
    )
    return out.reindex(grid)


def find_gaps(s: pd.Series, cfg: DetectConfig) -> list[dict[str, Any]]:
    """Runs of missing data at least `min_gap_minutes` long.

    `minutes` counts the missing samples, so a run of 24 absent 15-minute
    samples reports 360 minutes rather than the 345 minutes spanned by its
    first and last timestamps.
    """
    if s.empty:
        return []
    gaps = []
    for start, stop in _true_runs(s.isna().to_numpy()):
        minutes = (stop - start) * cfg.freq_minutes
        if minutes >= cfg.min_gap_minutes:
            gaps.append(
                {"start": s.index[start], "end": s.index[stop - 1], "minutes": minutes}
            )
    return gaps


def find_flatlines(s: pd.Series, cfg: DetectConfig) -> list[dict[str, Any]]:
    """Stretches where the reading does not move - the stuck-sensor signature.

    At the default tolerance of zero this means not one millimetre of movement
    for 12 hours. Shorter and looser thresholds do not survive contact with
    real data: these gauges resolve to 1 mm and sit at exactly the same reading
    for 13-50% of all samples, so "unchanged for 3 hours" is ordinary low flow,
    not a fault.

    A rolling range rather than a rolling diff: a sensor drifting by less than
    the tolerance each step would pass a diff test while wandering a long way.
    """
    window = max(2, cfg.flatline_minutes // cfg.freq_minutes)
    if len(s) < window:
        return []

    rolling = s.rolling(window)
    is_flat_window = ((rolling.max() - rolling.min()) <= cfg.flatline_tol_m).to_numpy()

    flatlines = []
    for start, stop in _true_runs(is_flat_window):
        # A flat window ending at `start` begins `window - 1` samples earlier.
        first = start - window + 1
        flatlines.append(
            {
                "start": s.index[first],
                "end": s.index[stop - 1],
                "minutes": (stop - first) * cfg.freq_minutes,
            }
        )
    return flatlines


def hampel_mask(s: pd.Series, cfg: DetectConfig) -> pd.Series:
    """Hampel filter: points far from the local median, in local MAD units.

    The MAD is floored at one millimetre so that a perfectly steady stretch
    (MAD of zero) does not make every subsequent wobble infinitely unusual.

    Uses a centred window with no partial windows, so the first and last
    `hampel_window // 2` samples are never flagged. At the default that is a
    90-minute blind spot at the live end of the series.
    """
    window = cfg.hampel_window
    median = s.rolling(window, center=True, min_periods=window).median()
    deviation = (s - median).abs()
    mad = deviation.rolling(window, center=True, min_periods=window).median()
    scale = np.maximum(MAD_TO_SIGMA * mad, 0.001)
    return (deviation > cfg.hampel_sigmas * scale).fillna(False)


def rate_of_rise(s: pd.Series, hours: float, cfg: DetectConfig) -> pd.Series:
    """Metres per hour over the preceding `hours`.

    NaN wherever the window is not fully populated. Without that, the first
    reading after a six-hour outage would report the whole outage's change as
    one hour of rise - a fabricated emergency.
    """
    lag = int(round(hours * 60 / cfg.freq_minutes))
    if lag < 1:
        raise ValueError(f"hours={hours} is shorter than one sample")

    roc = (s - s.shift(lag)) / hours
    complete = s.notna().rolling(lag + 1).sum() == (lag + 1)
    return roc.where(complete)


def percentile_against(values: pd.Series, reference: pd.Series) -> pd.Series:
    """Where each value sits in `reference`'s distribution, as 0-100.

    Always per station: an absolute level means nothing across gauges, since
    each is measured against its own datum, and a rate of rise that is dramatic
    on the Don is ordinary on the Dee.
    """
    ref = np.sort(reference.dropna().to_numpy(dtype="float64"))
    if ref.size == 0:
        return pd.Series(np.nan, index=values.index)

    raw = values.to_numpy(dtype="float64")
    pct = 100.0 * np.searchsorted(ref, raw, side="right") / ref.size
    return pd.Series(np.where(np.isnan(raw), np.nan, pct), index=values.index)


def cross_correlation_lag(
    upstream: pd.Series,
    downstream: pd.Series,
    cfg: DetectConfig,
    max_lag_hours: float = 24.0,
) -> dict[str, Any] | None:
    """Estimate how long a flood peak takes to travel between two gauges.

    Peak-to-peak timing is the obvious method and a fragile one: it depends on a
    single sample at each gauge, and a flat or double-humped crest moves the
    answer by hours. Cross-correlating the whole normalised hydrograph uses the
    shape of the entire event instead, so the two methods agreeing is real
    evidence rather than one number restated.

    Both series must already be on the same regular grid. Returns the best lag
    in hours with its correlation, or None if there is not enough overlapping
    data to judge.
    """
    both = pd.concat([upstream.rename("up"), downstream.rename("down")], axis=1).dropna()
    max_lag = int(round(max_lag_hours * 60 / cfg.freq_minutes))
    if len(both) < max_lag * 2:
        return None

    up = both["up"].to_numpy(dtype="float64")
    down = both["down"].to_numpy(dtype="float64")

    best_lag, best_corr = 0, -2.0
    for lag in range(0, max_lag + 1):
        a = up[: len(up) - lag] if lag else up
        b = down[lag:] if lag else down
        # Pearson on each overlap in turn. Normalising once over the whole
        # series and then slicing is not the same thing, and produces
        # "correlations" above 1.
        if a.std() == 0 or b.std() == 0:
            continue
        corr = float(np.corrcoef(a, b)[0, 1])
        if corr > best_corr:
            best_lag, best_corr = lag, corr

    if best_corr < -1.0:
        return None

    return {
        "lag_hours": best_lag * cfg.freq_minutes / 60,
        "correlation": best_corr,
        "samples": len(both),
    }


def build_flags(
    series: pd.Series,
    reference: pd.Series,
    cfg: DetectConfig,
    quality: pd.Series | None = None,
) -> pd.DataFrame:
    """Run every detector and combine them into one table.

    `series` is the window being assessed; `reference` is the station's longer
    history, which supplies the distributions that "unusual" is measured
    against. `quality` is SEPA's own quality code per timestamp, if available.
    """
    grid = to_regular_grid(series, cfg.freq_minutes)
    reference_grid = to_regular_grid(reference, cfg.freq_minutes)

    frame = pd.DataFrame(index=grid.index)
    frame["value"] = grid
    frame["quality"] = (
        quality.reindex(grid.index)
        if quality is not None
        else pd.Series(pd.NA, index=grid.index, dtype="Int16")
    )

    # --- data quality ---------------------------------------------------- #
    frame["is_gap"] = False
    for gap in find_gaps(grid, cfg):
        frame.loc[gap["start"] : gap["end"], "is_gap"] = True

    frame["is_flatline"] = False
    for flatline in find_flatlines(grid, cfg):
        frame.loc[flatline["start"] : flatline["end"], "is_flatline"] = True

    frame["is_spike"] = hampel_mask(grid, cfg)
    frame["is_suspect"] = frame["quality"].isin(SUPPRESSING_QUALITY)
    frame["is_provisional"] = frame["quality"].eq(QUALITY_UNCHECKED).fillna(False)

    # --- how fast, and how high ------------------------------------------ #
    rising_warn = pd.Series(False, index=grid.index)
    rising_high = pd.Series(False, index=grid.index)

    for hours, label in ((1.0, "1h"), (3.0, "3h")):
        observed = rate_of_rise(grid, hours, cfg)
        reference_rates = rate_of_rise(reference_grid, hours, cfg)
        ranked = percentile_against(observed, reference_rates)

        frame[f"roc_{label}"] = observed
        frame[f"roc_pct_{label}"] = ranked

        # Unusual for this station AND large enough to be worth saying aloud.
        fast_enough = observed >= cfg.rise_min_m_per_hr
        rising_warn |= ((ranked >= cfg.rise_pct_warn) & fast_enough).fillna(False)
        rising_high |= ((ranked >= cfg.rise_pct_high) & fast_enough).fillna(False)

    frame["level_pct"] = percentile_against(grid, reference_grid)

    # --- combine, with data quality taking precedence --------------------- #
    untrustworthy = (
        frame["is_gap"] | frame["is_flatline"] | frame["is_spike"] | frame["is_suspect"]
    )

    frame["is_fast_rise"] = rising_warn & ~untrustworthy
    frame["is_fast_rise_high"] = rising_high & ~untrustworthy
    frame["is_high_level"] = (
        frame["level_pct"] >= cfg.high_level_pct
    ).fillna(False) & ~untrustworthy
    frame["is_unusual"] = frame["is_fast_rise"] | frame["is_high_level"]

    return frame
