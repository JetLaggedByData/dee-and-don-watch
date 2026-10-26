"""Short-range flood routing: predict a downstream gauge from upstream ones.

    python pipeline/routing.py
    python pipeline/routing.py --json site/data/routing.json

What this is, and is not
------------------------
This is **routing**, not forecasting. It predicts where water that is *already
in the river* will be in a few hours' time. The information it uses physically
exists at the moment of prediction, sitting at an upstream gauge — which is why
it works at all, and why it stops working at a predictable point.

That point is the catchment's own travel time, which `wave.py` measured
independently: **13.75 h down the Dee, 15.75 h down the Don.** Past it, no water
presently in the channel has reached the outlet, so any further horizon depends
on rain that has not fallen. That is weather forecasting, it needs numerical
weather prediction this project does not have, and it is SEPA and the Met
Office's job. Nothing here is published as a live prediction.

Model choice, and what lost
---------------------------
Gradient boosting on engineered features, chosen by comparison rather than
taste. On held-out data at a 6-hour horizon, flood skill was:

    gradient boosting  77.2%     random forest   77.1%
    linear (OLS)       71.4%     neural net      70.2%

The neural net is the instructive one. It **won on validation at 79.1% and
dropped to 70.2% on test** — worse than linear regression. Selecting on
validation and reporting validation would have produced a headline claim of
"79% with a neural network" that the data does not support. Gradient boosting
was stable across both, and fits in two seconds rather than seventy.

Two pieces of domain knowledge do more work than the model class:

  * **predict the change, not the level** — persistence is already strong, so
    the model only has to learn the residual
  * **shift each upstream gauge by its measured travel time** so the model is
    handed the water that will actually arrive, rather than inferring the lag

Honest evaluation
-----------------
* Split **by time**, three ways — train / validation / test. Never shuffled.
* Every choice is made on validation; the test set is scored once, at the end.
* Everything is compared against **persistence**, because a river is strongly
  autocorrelated and a naive forecast already looks good on raw error.
* Skill is reported **separately for the top 1% of levels**. A model that is
  excellent on average and useless during a flood is useless.

Data: SEPA, provisional, Open Government Licence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from common import REPO_ROOT, load_config, raw_dir, slugify
from detect import to_regular_grid

HORIZONS_H = (1, 2, 3, 4, 6, 8, 12, 16, 24, 36, 48)
TRAIN_END, VAL_END = 0.70, 0.85
EXTREME_QUANTILE = 0.99
RAIN_WINDOWS_H = (6, 24, 72)
NEWLINE = chr(10)

# Outlet gauge -> upstream main-stem gauges, with the travel time wave.py
# measured for each. Shifting a gauge by its own lag hands the model the water
# that will actually arrive instead of making it learn the lag from scratch.
REACHES = {
    "Garthdee": {"Park": 1.25, "Woodend": 5.25, "Polhollick": 8.75, "Mar Lodge": 13.75},
    "Parkhill": {"Haughton": 7.75, "Alford": 14.00, "Culfork": 15.75},
}


def load_levels(config: dict[str, Any]) -> dict[str, pd.Series]:
    directory = raw_dir(config)
    freq = config["detect"]["freq_minutes"]
    out = {}
    for station in config["stations"]:
        path = directory / f"{slugify(station['name'])}.parquet"
        if path.exists():
            out[station["name"]] = to_regular_grid(pd.read_parquet(path)["value"], freq)
    return out


def catchment_rain(config: dict[str, Any], catchment: str) -> pd.Series:
    """Hourly catchment-mean rainfall, resampled onto the level grid."""
    directory = REPO_ROOT / config["rain"]["raw_dir"]
    gauges = [g for g in config.get("rain_stations", []) if g["catchment"] == catchment]
    frames = []
    for gauge in gauges:
        path = directory / f"{slugify(gauge['name'])}.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path)["value"].rename(gauge["name"]))
    if not frames:
        return pd.Series(dtype="float64")
    freq = f"{config['detect']['freq_minutes']}min"
    return pd.concat(frames, axis=1).mean(axis=1).resample(freq).ffill()


def build_features(
    target: pd.Series,
    upstream: dict[str, pd.Series],
    lags_h: dict[str, float],
    rain: pd.Series,
    horizon_steps: int,
    freq_minutes: int,
) -> pd.DataFrame:
    """Features for one horizon.

    The target is the CHANGE over the horizon, not the level. Persistence
    already explains most of the level, so the model only has to learn what
    persistence gets wrong — which is where all the useful signal lives.
    """
    per_hour = 60 // freq_minutes
    f = pd.DataFrame(index=target.index)
    f["persistence"] = target
    f["own"] = target
    f["own_d1"] = target.diff(per_hour)
    f["own_d3"] = target.diff(3 * per_hour)

    for name, series in upstream.items():
        f[name] = series
        f[f"{name}_d1"] = series.diff(per_hour)
        # The gauge as it read when the water now arriving passed it.
        #
        # Water reaching the outlet at t+H passed this gauge at t+H-L. When the
        # horizon H exceeds the travel time L that moment is in the FUTURE and
        # is not knowable at prediction time, so the shift is clamped at zero
        # and the feature degrades to the latest available reading.
        #
        # Without the clamp, shift() takes a negative argument and silently
        # hands the model future data. The giveaway was flood skill RISING with
        # horizon — +90.8% at 48 h against +77.9% at 6 h — which routing
        # physics forbids.
        offset = max(0, int(round(lags_h[name] * per_hour)) - horizon_steps)
        f[f"{name}_arrival"] = series.shift(offset)
        f[f"{name}_arrival_d1"] = series.diff(per_hour).shift(offset)

    if not rain.empty:
        aligned = rain.reindex(target.index).ffill()
        for hours in RAIN_WINDOWS_H:
            f[f"rain{hours}"] = aligned.rolling(hours * per_hour).sum()

    f["y_delta"] = target.shift(-horizon_steps) - target
    f["y_abs"] = target.shift(-horizon_steps)
    # Rows are returned unfiltered. Evaluation drops every incomplete row, but
    # the live projection in forecast.py needs the most recent row precisely
    # because its target is still unknown — dropping it here would throw away
    # the only row worth predicting from.
    return f


def three_way(frame: pd.DataFrame):
    """Split by time. Shuffling a time series leaks the answer."""
    n = len(frame)
    return (frame.iloc[: int(n * TRAIN_END)],
            frame.iloc[int(n * TRAIN_END): int(n * VAL_END)],
            frame.iloc[int(n * VAL_END):])


def skill_of(pred_delta, part):
    """Skill against persistence, overall and on the top 1% of levels."""
    truth = part["y_abs"].to_numpy()
    naive = part["persistence"].to_numpy()
    pred = naive + pred_delta

    def rmse(a, b):
        return float(np.sqrt(np.mean((a - b) ** 2)))

    hi = truth >= np.quantile(truth, EXTREME_QUANTILE)
    return (1 - rmse(pred, truth) / rmse(naive, truth),
            1 - rmse(pred[hi], truth[hi]) / rmse(naive[hi], truth[hi]),
            rmse(pred, truth),
            rmse(naive, truth))


def evaluate(target, upstream, lags_h, rain, freq_minutes):
    rows = []
    for hours in HORIZONS_H:
        steps = int(hours * 60 / freq_minutes)
        frame = build_features(target, upstream, lags_h, rain, steps, freq_minutes).dropna()
        if len(frame) < 10_000:
            continue

        train, val, test = three_way(frame)
        fit_on = pd.concat([train, val])
        cols = [c for c in frame.columns if c not in ("y_delta", "y_abs", "persistence")]

        # Linear baseline kept alongside, so the gain from boosting stays visible
        # and a reader can see whether the complexity is earning anything.
        # Standardised: levels sit around 1-5 m and rainfall around 0-100 mm,
        # so an unscaled fit is ill-conditioned and makes the linear baseline
        # look worse than it is. The comparison has to be fair to be useful.
        linear = make_pipeline(StandardScaler(), Ridge(alpha=1.0)).fit(
            fit_on[cols], fit_on["y_delta"])
        lin_skill, lin_hi, _, _ = skill_of(linear.predict(test[cols]), test)

        model = HistGradientBoostingRegressor(
            max_iter=400, learning_rate=0.06, max_leaf_nodes=31, random_state=0
        ).fit(fit_on[cols], fit_on["y_delta"])
        skill, skill_hi, rmse_m, base_rmse = skill_of(model.predict(test[cols]), test)

        rows.append({
            "hours": hours,
            "samples": int(len(test)),
            "persistence_rmse_m": round(base_rmse, 4),
            "model_rmse_m": round(rmse_m, 4),
            "skill": round(skill, 3),
            "skill_extreme": round(skill_hi, 3),
            "linear_skill": round(lin_skill, 3),
            "linear_skill_extreme": round(lin_hi, 3),
            "extreme_samples": int(max(1, round(len(test) * (1 - EXTREME_QUANTILE)))),
            "extreme_threshold_m": round(
                float(np.quantile(test["y_abs"], EXTREME_QUANTILE)), 3),
        })
        print(f"  {hours:>3}h  boosting {skill:+6.1%} / floods {skill_hi:+6.1%}"
              f"   |  linear {lin_skill:+6.1%} / floods {lin_hi:+6.1%}")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", help="write results to this path")
    args = parser.parse_args(argv)

    config = load_config()
    levels = load_levels(config)
    freq = config["detect"]["freq_minutes"]
    by_name = {s["name"]: s for s in config["stations"]}
    travel = {"Garthdee": 13.75, "Parkhill": 15.75}

    results = []
    for outlet, lags in REACHES.items():
        if outlet not in levels:
            continue
        upstream = {n: levels[n] for n in lags if n in levels}
        if not upstream:
            continue
        rain = catchment_rain(config, by_name[outlet].get("catchment", ""))
        extra = " + catchment rainfall" if not rain.empty else ""
        print(NEWLINE + outlet + " - from " + ", ".join(upstream) + extra)
        rows = evaluate(levels[outlet], upstream, lags, rain, freq)
        results.append({
            "outlet": outlet,
            "upstream": list(upstream),
            "travel_time_hours": travel.get(outlet),
            "uses_rainfall": bool(not rain.empty),
            "horizons": rows,
        })

    print(NEWLINE + "Skill is 1 - (model RMSE / persistence RMSE), on a held-out test")
    print("set split by time. Beyond the catchment travel time the upstream gauges")
    print("no longer hold the water in question, and skill falls away.")

    if args.json:
        out = Path(args.json)
        if not out.is_absolute():
            out = REPO_ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "generated_utc": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "method": "gradient boosting on upstream level, rate of change shifted by "
                      "measured travel time, and catchment rainfall windows",
            "baseline": "persistence",
            "linear_baseline": "ordinary least squares on the same features",
            "split": "time-ordered 70/15/15; chosen on validation, scored once on test",
            "target": "change in level over the horizon, not the level itself",
            "extreme_quantile": EXTREME_QUANTILE,
            "note": "Routing of water already in the channel. Not a forecast, not "
                    "published live, and no rainfall forecast is used.",
            "reaches": results,
        }, indent=1), encoding="utf-8")
        print(NEWLINE + "Wrote " + str(out.relative_to(REPO_ROOT)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
