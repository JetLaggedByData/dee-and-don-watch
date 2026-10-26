"""Unit tests for the detectors.

The cases are the ones agreed in the plan - clean, gap, flatline, spike, fast
rise - plus two for SEPA's quality codes, which the fetch step showed carry the
outage information that missing rows would normally carry.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import (
    PER_DAY,
    PER_HOUR,
    add_ramp,
    add_spike,
    drop_samples,
    freeze,
    index_of,
)
from pipeline.common import load_config
from pipeline.detect import (
    QUALITY_GOOD,
    QUALITY_SUSPECT,
    QUALITY_UNCHECKED,
    DetectConfig,
    build_flags,
    cross_correlation_lag,
    find_flatlines,
    find_gaps,
    hampel_mask,
    percentile_against,
    rate_of_rise,
    to_regular_grid,
)

CFG = DetectConfig()


def test_config_yaml_matches_the_tested_defaults():
    """Every test here runs on the dataclass defaults; CI runs on config.yaml.

    If the two drift apart, the suite is testing a detector nobody runs.
    `from_config` drops unknown keys silently, so a typo is checked for too.
    """
    block = load_config()["detect"]
    assert set(block) <= set(DetectConfig.__dataclass_fields__), "unknown detect key"
    assert DetectConfig.from_config(load_config()) == CFG


# --------------------------------------------------------------------------- #
# 1. Clean data must stay quiet
# --------------------------------------------------------------------------- #


def test_clean_series_raises_no_flags(clean, history):
    flags = build_flags(clean, history, CFG)
    for column in ("is_gap", "is_flatline", "is_spike", "is_fast_rise"):
        assert not flags[column].any(), f"{column} fired on clean data"


# --------------------------------------------------------------------------- #
# 2-3. Gaps
# --------------------------------------------------------------------------- #


def test_six_hour_gap_is_found(clean):
    missing = 6 * PER_HOUR
    gapped = to_regular_grid(drop_samples(clean, 500, missing), CFG.freq_minutes)

    gaps = find_gaps(gapped, CFG)

    assert len(gaps) == 1
    assert gaps[0]["minutes"] == missing * CFG.freq_minutes
    assert gaps[0]["start"] == clean.index[500]
    assert gaps[0]["end"] == clean.index[500 + missing - 1]


def test_single_missing_sample_is_not_a_gap(clean):
    gapped = to_regular_grid(drop_samples(clean, 500, 1), CFG.freq_minutes)

    assert find_gaps(gapped, CFG) == []


# --------------------------------------------------------------------------- #
# 4-5. Flatlines
# --------------------------------------------------------------------------- #


def test_stuck_sensor_is_flagged(clean):
    held = 12 * PER_HOUR
    stuck = freeze(clean, 850, held)

    flatlines = find_flatlines(stuck, CFG)

    assert len(flatlines) == 1
    assert flatlines[0]["start"] == clean.index[850]
    assert flatlines[0]["end"] == clean.index[850 + held - 1]


def test_quantised_recession_is_not_a_flatline():
    """Low flow on a 1 mm sensor repeats values constantly. That is not a fault.

    This is the real-data case: the river is genuinely still for hours at a
    time, so the test series must contain repeated readings and still stay
    unflagged. Only a full 12 hours without a single millimetre counts.
    """
    n = 10 * PER_DAY
    values = np.round((0.9 - np.arange(n) * 0.0005) / 0.001) * 0.001
    recession = pd.Series(values, index=index_of(n), name="value")

    assert (np.diff(recession.to_numpy()) == 0).any(), "fixture must repeat values"
    assert find_flatlines(recession, CFG) == []


# --------------------------------------------------------------------------- #
# 6-7. Spikes, and the suppression rule
# --------------------------------------------------------------------------- #


def test_single_bad_reading_is_flagged_as_a_spike(clean):
    spiked = add_spike(clean, 1000, magnitude=2.0)

    mask = hampel_mask(spiked, CFG)

    assert mask.iloc[1000]
    assert mask.sum() == 1, "a lone spike must not drag in its neighbours"


def test_spike_does_not_also_report_an_unusual_rise(clean, history):
    spiked = add_spike(clean, 1000, magnitude=2.0)

    flags = build_flags(spiked, history, CFG)

    assert flags["is_spike"].iloc[1000]
    assert not flags["is_fast_rise"].iloc[1000], (
        "a sensor spike was reported as an unusual rise"
    )


# --------------------------------------------------------------------------- #
# 8-9. Rate of rise
# --------------------------------------------------------------------------- #


def test_sustained_fast_rise_is_flagged(clean, history):
    rising = add_ramp(clean, 1000, hours=6, per_hour=0.3)

    flags = build_flags(rising, history, CFG)
    window = slice(1000, 1000 + 6 * PER_HOUR)

    assert flags["is_fast_rise"].iloc[window].any()
    assert flags["roc_pct_1h"].iloc[window].max() >= CFG.rise_pct_high
    assert flags["roc_1h"].iloc[window].max() >= CFG.rise_min_m_per_hr


def test_level_above_the_record_is_flagged_high(clean, history):
    raised = add_ramp(clean, 1000, hours=6, per_hour=0.3)   # +1.8 m, then held
    plateau = slice(1000 + 6 * PER_HOUR + 8, 1000 + 6 * PER_HOUR + 8 * PER_HOUR)

    flags = build_flags(raised, history, CFG)

    assert flags["is_high_level"].iloc[plateau].all()
    assert (flags["level_pct"].iloc[plateau] >= CFG.high_level_pct).all()


def test_stuck_sensor_suppresses_a_high_level(clean, history):
    """A sensor frozen at a high reading is a fault, not a high river."""
    raised = add_ramp(clean, 1000, hours=6, per_hour=0.3)
    held = 1000 + 6 * PER_HOUR + 8
    stuck = freeze(raised, held, 12 * PER_HOUR)

    flags = build_flags(stuck, history, CFG)
    frozen = slice(held, held + 12 * PER_HOUR)

    assert flags["is_flatline"].iloc[frozen].all()
    assert not flags["is_high_level"].iloc[frozen].any(), (
        "a stuck sensor was reported as an unusually high river"
    )


def test_rate_of_rise_is_nan_across_a_gap(clean):
    gapped = to_regular_grid(drop_samples(clean, 500, 6 * PER_HOUR), CFG.freq_minutes)

    roc = rate_of_rise(gapped, hours=1.0, cfg=CFG)

    # The first sample back after the outage must not report the whole
    # outage's level change as one hour's worth of rise.
    resumed = gapped.index.get_loc(clean.index[500 + 6 * PER_HOUR])
    assert np.isnan(roc.iloc[resumed])


# --------------------------------------------------------------------------- #
# 10-11. Robust statistics
# --------------------------------------------------------------------------- #


def test_percentile_against_a_degenerate_reference_does_not_blow_up():
    """A reference with no spread, or none at all, must not divide by zero."""
    values = pd.Series([1.0, 1.25, 9.0])

    constant = percentile_against(values, pd.Series(np.full(500, 1.25)))
    assert np.isfinite(constant).all()
    assert (constant >= 0).all() and (constant <= 100).all()

    empty = percentile_against(values, pd.Series(dtype="float64"))
    assert empty.isna().all(), "no reference means no opinion, not a number"


def test_percentile_against_is_bounded_and_ordered(history):
    pct = percentile_against(history, history)

    assert pct.min() >= 0.0
    assert pct.max() <= 100.0
    assert pct.loc[history.idxmax()] > pct.loc[history.idxmin()]
    assert abs(percentile_against(pd.Series([history.median()]), history).iloc[0] - 50.0) < 2.0


# --------------------------------------------------------------------------- #
# 12. Index hygiene
# --------------------------------------------------------------------------- #


def test_regular_grid_sorts_dedupes_and_stays_utc(clean):
    shuffled = pd.concat([clean.iloc[::-1], clean.iloc[100:110]])

    grid = to_regular_grid(shuffled, CFG.freq_minutes)

    assert grid.index.is_monotonic_increasing
    assert not grid.index.has_duplicates
    assert str(grid.index.tz) == "UTC"
    assert len(grid) == len(clean)


# --------------------------------------------------------------------------- #
# 13-14. SEPA quality codes
# --------------------------------------------------------------------------- #


def test_suspect_quality_suppresses_an_unusual_rise(clean, history):
    rising = add_ramp(clean, 1000, hours=6, per_hour=0.3)
    quality = pd.Series(QUALITY_GOOD, index=rising.index, dtype="Int16")
    quality.iloc[1000:] = QUALITY_SUSPECT

    flags = build_flags(rising, history, CFG, quality=quality)

    assert not flags["is_fast_rise"].iloc[1000:].any(), (
        "a rise SEPA has marked Suspect was reported as real"
    )


def test_quantised_river_does_not_flag_constantly(quantised):
    """Regression: the detectors must stay quiet on a realistic, quantised river.

    The first version of this module passed every other test in this file while
    firing `is_fast_rise` on 6-11% of real SEPA samples and `is_flatline` on up
    to 23.9%. Nothing here caught it, because the other fixtures are smooth and
    continuous and the real gauges are neither. This is the assertion that
    would have.
    """
    flags = build_flags(quantised, quantised, CFG)

    fast_rise_rate = flags["is_fast_rise"].mean()
    flatline_rate = flags["is_flatline"].mean()

    assert fast_rise_rate < 0.02, (
        f"is_fast_rise fired on {fast_rise_rate:.1%} of samples; "
        "'statistically unusual' has to be rare"
    )
    assert flatline_rate < 0.02, (
        f"is_flatline fired on {flatline_rate:.1%} of samples; "
        "a still river at 1 mm resolution is not a stuck sensor"
    )


def test_quantised_river_still_notices_a_real_freshet(quantised):
    """...but quiet must not mean deaf: the built-in rain responses still fire."""
    flags = build_flags(quantised, quantised, CFG)

    assert flags["is_fast_rise"].any(), "no rise was detected anywhere"


# --------------------------------------------------------------------------- #
# Travel time between gauges
# --------------------------------------------------------------------------- #


def test_cross_correlation_recovers_a_known_lag(clean):
    """A downstream gauge is the upstream one, delayed. Recover the delay.

    Direction matters: the downstream series must *lag*, so it is the earlier
    slice of the source and the upstream series the later one.
    """
    delay_hours = 5.0
    shift = int(delay_hours * PER_HOUR)

    upstream = clean.iloc[shift:].reset_index(drop=True)
    downstream = clean.iloc[: len(clean) - shift].reset_index(drop=True)
    index = index_of(len(upstream))
    upstream.index, downstream.index = index, index

    result = cross_correlation_lag(upstream, downstream, CFG)

    assert result is not None
    assert result["lag_hours"] == pytest.approx(delay_hours, abs=0.25)
    assert result["correlation"] > 0.9


def test_cross_correlation_declines_without_enough_data(clean):
    short = clean.iloc[:10]

    assert cross_correlation_lag(short, short, CFG) is None


def test_unchecked_quality_does_not_suppress(clean, history):
    """254/Unchecked is the normal state of all recent data.

    Suppressing on it would silence every live reading we ever show.
    """
    rising = add_ramp(clean, 1000, hours=6, per_hour=0.3)
    quality = pd.Series(QUALITY_UNCHECKED, index=rising.index, dtype="Int16")

    flags = build_flags(rising, history, CFG, quality=quality)

    assert flags["is_fast_rise"].iloc[1000 : 1000 + 6 * PER_HOUR].any()
