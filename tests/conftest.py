"""Synthetic river-level series for testing the detectors.

Everything here is deterministic: no RNG seeds to drift, no network, no files.
Levels are in metres on a 15-minute UTC grid, matching SEPA's SG/15m.Cmd series.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

FREQ_MINUTES = 15
PER_HOUR = 60 // FREQ_MINUTES
PER_DAY = 24 * PER_HOUR
START = pd.Timestamp("2026-01-01 00:00", tz="UTC")


def index_of(n: int, start: pd.Timestamp = START) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=n, freq=f"{FREQ_MINUTES}min", name="timestamp")


def smooth_series(
    days: int = 30, amplitude: float = 0.4, period_days: float = 3.0, base: float = 1.0
) -> pd.Series:
    """A calm, noise-free river: a slow swell with no sudden moves.

    Chosen so that nothing in it should trip any detector. The amplitude gives a
    peak rate of rise of about 0.035 m/hr, clear of the 0.05 m/hr floor, and
    being continuous it never repeats a value exactly, so it cannot flatline.
    """
    n = days * PER_DAY
    t = np.arange(n)
    values = base + amplitude * np.sin(2 * np.pi * t / (period_days * PER_DAY))
    return pd.Series(values, index=index_of(n), name="value")


def quantised_river(days: int = 120, resolution: float = 0.001) -> pd.Series:
    """A synthetic river with the awkward character of the real SEPA data.

    The smooth fixtures above are too kind: being continuous and noise-free,
    they have none of the quantisation that actually broke the first version of
    the detectors. Real gauges here resolve to 1 mm and the rivers show no
    change at all in 13-50% of samples, which collapsed the MAD of the hourly
    rate of rise to one or two sensor ticks.

    This reproduces that: a slow recession punctuated by occasional freshets,
    rounded to the sensor's resolution. A correctly calibrated detector should
    stay quiet across nearly all of it.
    """
    n = days * PER_DAY
    t = np.arange(n)

    # Long, slow recessions: mostly flat at this resolution.
    recession = 0.6 + 0.25 * np.exp(-((t % (20 * PER_DAY)) / (6.0 * PER_DAY)))

    # A gentle diurnal swing of about a centimetre. Without it the recession
    # asymptotes flatter than any real gauge: the fixture sat at 71% unchanged
    # against 13-50% for the five real stations, which made it look like the
    # flatline detector was broken when it was the river that wasn't realistic.
    diurnal = 0.01 * np.sin(2 * np.pi * t / (12 * PER_HOUR))

    # A handful of genuine rain responses: sharp up, slow down.
    freshets = np.zeros(n)
    for start in range(7 * PER_DAY, n, 23 * PER_DAY):
        rise = min(10 * PER_HOUR, n - start)
        freshets[start : start + rise] += np.linspace(0, 0.9, rise)
        decay_start = start + rise
        decay = min(5 * PER_DAY, n - decay_start)
        if decay > 0:
            freshets[decay_start : decay_start + decay] += 0.9 * np.exp(
                -np.arange(decay) / (1.2 * PER_DAY)
            )
        freshets[decay_start + decay :] += 0.0

    values = np.round((recession + diurnal + freshets) / resolution) * resolution
    return pd.Series(values, index=index_of(n), name="value")


def drop_samples(s: pd.Series, start: int, count: int) -> pd.Series:
    """Remove rows entirely, as a logger outage would."""
    keep = np.ones(len(s), dtype=bool)
    keep[start : start + count] = False
    return s[keep]


def freeze(s: pd.Series, start: int, count: int) -> pd.Series:
    """Hold one value constant: a stuck sensor."""
    out = s.copy()
    out.iloc[start : start + count] = out.iloc[start]
    return out


def add_spike(s: pd.Series, at: int, magnitude: float = 2.0) -> pd.Series:
    """One bad reading."""
    out = s.copy()
    out.iloc[at] += magnitude
    return out


def add_ramp(s: pd.Series, start: int, hours: float, per_hour: float) -> pd.Series:
    """A sustained rise of `per_hour` m/hr, then hold the new level."""
    out = s.copy()
    count = int(hours * PER_HOUR)
    step = per_hour / PER_HOUR
    rise = np.arange(1, count + 1) * step
    out.iloc[start : start + count] += rise
    out.iloc[start + count :] += rise[-1]
    return out


@pytest.fixture
def clean() -> pd.Series:
    return smooth_series()


@pytest.fixture
def history() -> pd.Series:
    """A longer calm record to score the test series against."""
    return smooth_series(days=120)


@pytest.fixture
def quantised() -> pd.Series:
    return quantised_river()
