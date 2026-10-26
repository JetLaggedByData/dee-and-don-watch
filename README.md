# Dee & Don Watch

Monitors SEPA's 15-minute river-level gauges across the **Dee** and **Don** catchments near
Aberdeen, flags statistically unusual movement and likely sensor faults, traces how a flood peak
travels downstream, and explains honestly how it decides.

Three SEPA datasets: **level** (13 gauges, 10 years, 15-minute), **rainfall** (12 gauges,
10 years, hourly) and **flow** (12 gauges — daily means back to 1929, every flood peak since
1971). The site is two pages: a monitor that answers *is anything unusual right now*, and a
method page carrying the backtest, the flood-wave analysis and the limitations.

**This is not a flood forecasting or warning service.** For warnings see
[SEPA](https://www.sepa.org.uk/environment/water/flooding/) and the
[Met Office](https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings).

Build 2 of 12 in a public series of Scotland-focused data projects.

---

## The problem

SEPA publishes river level every 15 minutes at hundreds of gauges, free and openly licensed.
The raw numbers are hard to read: level is measured against each gauge's own datum, so 2.5 m
means nothing without that station's history, and nothing in the feed tells you whether a
reading is a real river movement or a failing sensor.

This project answers three narrow questions, and refuses to answer anything else:

1. Is the river doing something **unusual for this gauge's own record**?
2. Is this reading **trustworthy**?
3. When a flood does come through, **how does it move down the catchment**?

The second question turned out to matter more than the first, and the third produced the most
interesting result.

## Data, licence and attribution

Source: the [SEPA time series API](https://timeseriesdoc.sepa.org.uk/api-documentation/)
(KiWIS), using only documented requests — `getTimeseriesList`, `getStationList` and
`getTimeseriesValues` — via SEPA's documented **Referer access** mode, which identifies the
caller by a `Referer` header rather than by IP.

**On registering.** SEPA do not publish a daily credit figure for Referer access — only that it
"receives an individual credit allocation". The 5,000/day figure they document applies to
*anonymous* access, which this project does not use, and which they note "is reached quickly
each day" due to a current fault. Every budget here is therefore measured against a number
that may not apply.

More to the point, SEPA are explicit: **"If you intend to include our data in a web product,
you should definitely register"**, and they give *"the last few hours of data for all the sites
multiple times each day"* — precisely this pipeline's half-hourly refresh — as an example of
registered use. The pipeline supports a key via `SEPA_ACCESS_KEY` (sent as
`Authorization: Basic`), and the scheduled workflow already passes it as a secret. Request one
from hydrometry-requests@sepa.org.uk, subject "API Key Request".

The initial ten-year backfill was spread across two days, which is the alternative SEPA
document for large downloads: *"you can either register or spread this download over a few
days"*.

Contains SEPA data © Scottish Environment Protection Agency and database right, used under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
**All SEPA data here is provisional and subject to revision.**

**Level: 13 gauges, 4,556,293 readings**, 2016-10-23 to 2026-10-24. Every `ts_id` confirmed against
`getTimeseriesList`, and every `river` against `getStationList` — not taken from third-party
listings.

| Watercourse | Gauge | `ts_id` | km from Aberdeen | Fast-rise threshold | High-level threshold |
|---|---|---|---|---|---|
| **Dee** | Mar Lodge | 57912010 | 86.0 | 0.125 m/hr | 1.504 m |
| **Dee** | Polhollick | 58106010 | 60.6 | 0.113 m/hr | 1.403 m |
| **Dee** | Woodend | 58482010 | 32.7 | 0.134 m/hr | 1.865 m |
| **Dee** | Park | 58049010 | 16.8 | 0.153 m/hr | 2.562 m |
| **Dee** | Garthdee | 59580010 | 3.9 | 0.195 m/hr | 2.461 m |
| **Don** | Culfork | 57215010 | 61.3 | 0.050 m/hr † | 0.609 m |
| **Don** | Alford | 56957010 | 38.9 | 0.063 m/hr | 1.711 m |
| **Don** | Haughton | 57564010 | 23.1 | 0.070 m/hr | 3.769 m |
| **Don** | Parkhill | 58065010 | 9.5 | 0.052 m/hr | 2.501 m |
| Feugh | Heugh Head | 57581010 | 29.1 | 0.163 m/hr | 2.526 m |
| Gairn | Invergairn | 57645010 | 59.7 | 0.074 m/hr | 0.980 m |
| Muick | Invermuick | 57661010 | 59.0 | 0.092 m/hr | 1.112 m |
| Urie | Pitcaple | 58081010 | 29.5 | 0.050 m/hr † | 1.122 m |

Thresholds differ per gauge because they *are* that gauge's own percentiles.
† For Culfork and Pitcaple the absolute floor binds, not the percentile — their 99.5th
percentile rise is below 0.05 m/hr.

**The watercourse is not the catchment.** Heugh Head is on the Feugh, Invergairn the Gairn,
Invermuick the Muick — all inside the *Dee catchment*; Pitcaple is on the Urie inside the *Don
catchment*. Labelling those "Dee" and "Don" is wrong, and it breaks any downstream analysis.

**Excluded:** Meikle Kinord (`59425010`) is on **Loch Kinord**, not a river. Over the 8 years
measured before it was dropped, its level moved 0.81 m in total and its fastest hourly rise was
0.024 m/hr — under half the 0.05 m/hr floor — so it could never raise a flag. Also excluded as stale: Charr, Aboyne, Littlemill.

### Rainfall — 12 gauges, hourly, 10 years

`RE/Hour.Total`, 1,051,920 readings. Averaged per catchment rather than shown per gauge: the
question is whether rain fell on the catchment, not what each gauge recorded. **Rain is never
run through the level detectors** — it is context, and percentile thresholds over rain totals
would mean nothing.

Validated against climatology: annual totals run 732–1,201 mm/yr with the expected gradient
(lowland Inverurie 736, Tarland 732; hill Glenmuick 1,201, Mongour 1,148).

**Hourly, not 15-minute, and that is a deliberate choice.** 86.2% of hours are exactly 0.0 mm
and every value is a multiple of 0.200 mm — that is the tipping bucket, not the weather. Going
to 15-minute totals would take it to roughly 96% zeros for four times the API cost, and the
catchment cannot resolve it anyway: reach travel times are 1.75–7.75 hours. SEPA publishes no
30-minute series, so the choice is only hourly or 15-minute.

### Flow — long-record context

| Series | Gauges | Earliest | Rows |
|---|---|---|---|
| `Day.Mean` | 12 | **1929** (Woodend) | 228,104 |
| `PeaksOverThreshold` | 9 | **1971** | 2,298 peaks |
| `HydrologicalYear.Max` | 9 | **1971** | 429 water years |

Flow is in cumecs and, unlike level, **is comparable between gauges** — metres are measured
against each gauge's own datum. These series cost 255 credits in total, because SEPA bills
sparse series at the one-credit minimum per request.

**Caveat carried everywhere these are used:** flow is derived from level through a rating curve,
and SEPA codes flows beyond the acceptable extrapolation limit as Suspect (150). The largest
values — exactly the ones a flood record exists to describe — are the least certain.


**Quality codes.** SEPA tags every value **50 Good, 100 Estimated, 150 Suspect, 254 Unchecked**
([How data validity may change](https://timeseriesdoc.sepa.org.uk/api-documentation/before-you-start/how-data-validity-may-change/)).
Recent data defaults to 254 until a **monthly** manual QC pass.

**What `export.py` writes into `site/data/`.** All of it is regenerated on every run and none
of it is committed:

| file | what it carries |
|---|---|
| `stations.json` | the index: every gauge, its latest reading, thresholds, flag rates, and the 14-day history of how many gauges were flagged |
| `<slug>.json` | 14 days at 15 minutes — level, quality code, percentile, rainfall, and the flagged/untrusted spans |
| `<slug>_history.json` | the **whole record** as a daily envelope: min, mean and max level plus daily catchment rainfall. ~76–94 KB per gauge, fetched only when a range longer than 14 days is chosen, because ten years at 15 minutes is 350,000 points per gauge |
| `spacetime.json` | main-stem gauges on one shared hourly grid, for the space-time view |
| `spacetime_event.json` | the same view over the 2022 flood, normalised within the event |
| `forecast.json` | the 12-hour projection at the two outlet gauges |
| `routing.json`, `backtest.json`, `wave.json` | skill curve, lead times, travel times |

**Two bakes are run by hand, not on a schedule**, and their output *is* committed:
`pipeline/bake_geo.py` (river and coast geometry) and `pipeline/bake_satellite.py` (the
basemap image). Coastlines do not move and the imagery is a fixed frame, so neither belongs in
the workflow — and neither is referenced by it.

**Credits.** SEPA charges 1 credit per 1,000 values, 1 per metadata request, capped at 300,000
points per request. Building this cache cost **~4,945 credits** against a 5,000/day anonymous
budget. Scheduled incremental runs cost about **50 credits**. `fetch.py` extends the cache
*backwards* as well as forwards, so widening the history only pays for the years not already
held.

## Method

Each gauge is judged only against **its own** record.

1. **Data quality** — gaps (≥45 min missing), flatlines (no movement at all for ≥12 h), spikes
   (Hampel filter, 13-sample centred window, 3 MAD).
2. **Rate of rise** — m/hr over 1 h and 3 h. Flagged above the **99.5th percentile of that
   gauge's own rises** *and* at least **0.05 m/hr**.
3. **Level** — flagged above the **99th percentile** of that gauge's record.
4. **Travel time** — for a chosen event, each reach is timed two independent ways: peak-to-peak,
   and the lag that best cross-correlates the two normalised hydrographs.

**Bad data never becomes news.** Any reading carrying a data-quality flag, or marked Suspect by
SEPA, has its rise and level flags suppressed. Readings marked **254 Unchecked** do *not*
suppress — that is the normal state of all recent data, so suppressing on it would silence the
entire site. It is surfaced as "provisional" instead.

### Why not a z-score

The first version scored rate of rise as a median/MAD robust z-score, the textbook choice. On
real data it fired on **6.65–10.91% of every sample ever recorded**.

The cause is sensor quantisation. These gauges resolve to 1 mm and the rivers register no change
at all in **13% to 50%** of samples, so the MAD of hourly rise collapses to 0.001–0.004 m/hr —
one to four sensor ticks. A `z ≥ 3` threshold meant *"rose by 3.4 to 16.8 mm in an hour"*. The
statistic was measuring the sensor's rounding, not the river. The same error made flatlines fire
on up to **23.86%** of one gauge's record, because the tolerance equalled the sensor resolution.

Percentiles of each gauge's own distribution, plus an absolute floor, brought fast-rise rates to
**0.47–0.65%** and flatlines to **0.03–2.21%**.

Every unit test passed throughout that failure, because the fixtures were smooth noise-free sine
waves with none of the quantisation that caused it. There is now a regression test on a
1 mm-quantised synthetic river that fails if any flag exceeds 2%.

## Results

### The flood wave

Backtested against the **largest event in the record**, chosen by the script from the data
rather than assumed: **18–19 November 2022**, when every gauge reached its maximum. (I expected
Storm Babet, October 2023; it was lower everywhere.)

Once tributary gauges are separated out, both main stems peak **strictly in downstream order**:

| Dee | Peak (UTC) | | Don | Peak (UTC) |
|---|---|---|---|---|
| Mar Lodge (86.0 km) | 18 Nov 12:30 | | Culfork (61.3 km) | 18 Nov 18:00 |
| Polhollick (60.6 km) | 18 Nov 17:30 | | Alford (38.9 km) | 18 Nov 19:45 |
| Woodend (32.7 km) | 18 Nov 21:00 | | Haughton (23.1 km) | 19 Nov 02:00 |
| Park (16.8 km) | 19 Nov 01:00 | | Parkhill (9.5 km) | 19 Nov 09:45 |
| Garthdee (3.9 km) | 19 Nov 02:15 | | | |
| **13.75 h over 82.2 km** | 5.97 km/h apparent | | **15.75 h over 51.9 km** | 3.29 km/h apparent |

And all four tributaries crested **before** the main-stem wave reached the lowest gauge — Gairn
11:45, Muick 12:15, Feugh 13:15, Urie 14:45. Small steep catchments flash; the main stem
integrates and lags.

**This did not work on the first attempt.** With five gauges it appeared to; with thirteen it
collapsed, because I was ordering gauges by straight-line distance to Aberdeen while mixing two
rivers and four tributaries. The fix was to ask `getStationList` which watercourse each gauge
actually sits on.

### What the 55-year record says

Ten years of level says the November 2022 event hit the 100th percentile everywhere. Fifty-five
years of flood peaks says something more useful — **it was the second largest, not the first**:

| Gauge | Largest on record | Nov 2022 rank | Return period |
|---|---|---|---|
| Woodend | **1,362.5** on 2015-12-30 | 2nd of 396 since 1973 | ~1-in-28 yr |
| Park | **1,282.9** on 2015-12-30 | 2nd of 168 since 1976 | ~1-in-26 yr |
| Haughton | **396.3** on 2016-01-08 | 2nd of 266 since 1971 | ~1-in-28 yr |
| Alford | **340.0** on 2016-01-07 | 2nd of 254 since 1973 | ~1-in-27 yr |
| Polhollick | **898.1** on 2015-12-30 | 9th of 246 | ~1-in-7 yr |
| Mar Lodge | **401.8** on 2015-12-30 | **55th of 271** | ~1-in-1.6 yr |

**30 December 2015 is the largest flood in the record**, biggest at five of nine gauges; the Don
peaked a week later on 7–8 January 2016. Neither storm is named here — that has not been
verified, the same rule applied to the November 2022 event.

**And rainfall explains the anomaly.** Mar Lodge ranked 55th while gauges 50 km downstream
ranked 2nd. In the 48 hours before the peak the headwater gauges recorded 45–49 mm (Inverey
45.2, Derry Lodge 49.4) while the mid and lower catchment took roughly three times as much
(Charr 142.6, Mongour 111.0). **The 2022 flood was generated below the headwaters** — two
independent datasets agreeing on the same mechanism.

Storm Babet (2023-10-20) is the record at only one gauge, Heugh Head on the Feugh — a far more
localised event than its coverage suggested.


### Detector performance

| Gauge | Peak | Fast rise | Strict rise | High level |
|---|---|---|---|---|
| Mar Lodge | 2.22 m | 66.2h | 2.0h | 3.5h |
| Polhollick | 2.40 m | 69.2h | **MISS** | 14.2h |
| Woodend | 4.18 m | ≥72.0h | 7.8h | 28.2h |
| Park | 5.07 m | 12.8h | **MISS** | 32.0h |
| Garthdee | 5.50 m | ≥72.0h | **MISS** | 31.8h |
| Culfork | 1.68 m | 69.5h | 9.8h | 25.5h |
| Alford | 3.33 m | ≥72.0h | 43.0h | 31.5h |
| Haughton | 5.37 m | ≥72.0h | 44.2h | 32.2h |
| Parkhill | 4.50 m | 48.0h | 25.5h | 33.5h |

**Read these against the base rates.** The fast-rise flag is set on 0.47–0.65% of the record —
roughly 25 episodes a year per gauge. An earlier, broken version "predicted" this event 66 hours
ahead while being switched on 10% of the time.

**What failed:**

- **The strict rise flag missed three of the largest peaks** — Garthdee, Park and Polhollick.
  Not diagnosed.
- **Four lead times are censored.** `≥72.0h` means the flag was already on when the lookback
  window opened; the true lead is longer but unmeasurable here. They are not 72-hour results.
- **Travel times are uncertain per reach.** The two independent methods agree within 1.5 h on
  only **3 of 7** reaches, differing by up to 3.0 h elsewhere. The ordering is unambiguous; the
  individual hour figures are approximate.
- **Most flag episodes are not major floods.** A detector firing ~25 times a year on a river
  with ~4 notable peaks a year will mostly catch ordinary rain responses. They are not errors,
  but they are why a single flag is not a warning.

### Lead time is a fragile statistic

Garthdee's reported fast-rise lead has now been **≥72h at 5 years, 10.8h at 8 years, and ≥72h
again at 10** — the same gauge, the same flood, the same raw data, swinging by 61 hours twice.
My first explanation was that more history raises the percentile and makes the trigger harder.
**That was wrong**, and the data says so: Garthdee's 1-hour threshold *fell* at every step,
0.204 → 0.198 → 0.195 m/hr.

The real cause is sharper and more useful. At 2022-11-16 02:15 — the earliest detection in the
old window — the 3-hour rise was **0.1427 m/hr under both references, the identical number**.
Only its rank changed:

| Reference | Percentile of that rise | Clears the 99.5 bar? |
|---|---|---|
| 5 years | 99.501 | yes |
| 8 years | 99.492 | no |

A shift of **0.009 percentile points**, roughly one sample in 11,000, switched off one marginal
detection. Because it was the *earliest* one in the window, the headline lead time moved by two
and a half days.

**The detector is stable; the metric is not.** Lead time is defined by the earliest marginal
detection, so it inherits all the instability of a single threshold crossing. This is the best
argument on this page for quoting base rates alongside every lead time, and for not putting a
lead time in a headline.

### How much history is enough — a prediction that failed

Thresholds were first recomputed on 1- to 8-year **trailing** windows of the data already held.
They appeared to converge: median drift per added year fell to 0.6% (rise) and 1.3% (level) by
year eight. The conclusion was that more history would change nothing.

Then years nine and ten were actually fetched, and the prediction was wrong:

| | Predicted per year | Measured, adding 2016-2018 |
|---|---|---|
| Fast-rise threshold | ~0.6% | **4.1% median, 5.7% max** |
| High-level threshold | ~1.3% | **2.9% median, 6.4% max** |

**Why the test was flawed.** Nested trailing windows all end on the same date and share most of
their data, so each added year is diluted by everything already in the window. That understates
what happens when a genuinely different period is appended.

**And the direction is the tell.** Every single threshold moved *down* — all thirteen rise
thresholds and all thirteen level thresholds. 2016-2018 was simply a quieter couple of years
than 2018-2026, so including it lowers the bar for what counts as unusual.

That is not noise converging away. It is **non-stationarity**, and it does not get better with
more data — it gets worse, because a longer record averages over more periods that may not
describe the river now. The reference window is a modelling choice to be made deliberately and
stated, not a quantity to maximise. This project uses ten years and says so.

What *was* stable across all three reference windows:

- **Flag rates** stayed at ~0.5%, which is true by construction — a percentile threshold always
  flags about the same fraction.
- **The flood-wave result did not move at all**: Dee 13.75 h, Don 15.75 h, both strictly
  monotonic, 3 of 7 reaches consistent. Event-based analysis does not depend on the reference
  distribution, which makes it the most robust thing on this page.

### How far ahead this can be predicted

A downstream gauge can be predicted from the gauges above it, because the water is already in
the channel. Ordinary least squares on upstream level and rate of change, scored against
persistence on held-out data split by time:

| Horizon | Garthdee skill (floods) | Parkhill skill (floods) |
|---|---|---|
| 4 h | **+71.7%** | +73.0% |
| 6 h | +68.6% | +69.7% |
| 12 h | +44.4% | +59.7% |
| 16 h | +23.8% | +46.0% |
| 24 h | **−5.4%** | +12.7% |
| 48 h | −3.3% | **−32.6%** |

Skill is `1 − (model RMSE ÷ persistence RMSE)`, scored on the top 1% of levels — a model that is
excellent on average and useless in a flood is useless.

**The horizon is set by physics, and two independent methods agree on it.** `wave.py` measured
travel times of 13.75 h down the Dee and 15.75 h down the Don by cross-correlating hydrographs.
Flood skill crosses zero just past those figures. Beyond the travel time no water presently in
the river has reached the outlet, so anything further depends on rain that has not fallen.

**Past about 16 hours the model is worse than persistence during floods** — it does not merely
stop helping, it actively misleads exactly when it matters. That is the empirical case for the
limit, not an assumed one.

**This is routing, not weather forecasting.** No rainfall forecast is used; SEPA's API
publishes observations only. Seven-day flood prediction is weather forecasting, it is a
regulated public-safety function, and it remains SEPA's and the Met Office's job.

**What is published.** A 12-hour projection is drawn on the detail chart for **Garthdee and
Parkhill only** — the two gauges with anything upstream to route from; the other eleven are
headwaters or sit on tributaries with a single gauge. It is capped at 12 h because skill
collapses near each catchment's travel time: +0.48 and +0.56 at 12 h, but Parkhill scores
**−0.23 at 36 h**, worse than assuming nothing changes. The shaded band is the model's own RMSE
on held-out data at that horizon, so the drawn uncertainty is measured rather than assumed.

The line starts where the measurements stop, not where the projection was issued — hours that
have since been gauged are dropped rather than drawn twice. And because routing needs *every*
upstream gauge, the projection is **withheld entirely** when one goes quiet for more than three
hours, with a note on the chart saying so. The page never uses the words flood, warning or
risk.


## Limitations

- **Not a warning service, and no forecasting.** It describes what gauges already recorded. A
  quiet page is not an all-clear.
- **Provisional data.** Recent readings are Unchecked until SEPA's monthly QC and may be revised
  or withdrawn. The pipeline re-requests a 35-day overlap so revisions reach the cache.
- **Relative, not absolute.** "Unusual" means unusual for that gauge. Flags are not comparable
  between gauges.
- **Sensitive to record length** — see above.
- **Travel times are approximate**, and distances are straight-line to Aberdeen rather than
  along the channel, so km/h figures are apparent speeds, not wave celerity.
- **Garthdee's gauge is noisy.** 6.05% of readings flag as possible spikes against ~1%
  elsewhere, and SEPA marks three separate past periods Suspect.
- **A 90-minute blind spot.** The spike detector uses a centred window, so the most recent
  90 minutes are never spike-checked.
- **The floor excludes slow water.** The 0.05 m/hr minimum that fixes the quantisation problem
  also means a slow-responding water body can never raise a rise flag — which is why Loch Kinord
  was removed rather than displayed as permanently calm.
- **Scheduled runs are best-effort.** GitHub warns scheduled workflows can be delayed and "some
  queued jobs may be dropped", and that they are disabled after 60 days without repository
  activity. The page shows a stale banner after 6 hours.
- **Flow is modelled, not measured.** Discharge comes from level through a rating curve, and
  SEPA codes flows beyond the acceptable extrapolation limit as Suspect. The largest numbers —
  exactly the ones the flood record exists to describe — are the least certain.
- **Rainfall is hourly and tipping-bucket quantised.** 86.2% of hours read exactly zero and every
  value is a multiple of 0.2 mm. Fine for "did rain precede this rise"; not a precipitation
  product.
- **No forecast input, and none intended.** The rainfall here is what has already fallen.

## How to run

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # macOS/Linux

python pipeline/discover.py                  # confirm gauge ts_ids against the API (1 credit)
python pipeline/fetch.py --years 10          # level + rainfall; extends backwards, pays
                                             #   only for the years not already held
python pipeline/fetch_flow.py                # long-record flow (~255 credits, full record)
python pipeline/fetch.py                     # incremental top-up (~60 credits)
pytest -q                                    # 18 tests
python pipeline/export.py                    # -> site/data/*.json
python pipeline/backtest.py                  # lead times, misses, base rates
python pipeline/wave.py                      # downstream travel times
python pipeline/routing.py                   # how far ahead it can be predicted
python pipeline/forecast.py                  # the live 12-hour projection -> forecast.json
python -m http.server -d site 8000           # preview at http://localhost:8000
```

`pandas` is pinned below 3.x: the 3.1.0 wheel's `_libs/window/indexers` DLL is blocked by
Windows Application Control on the development machine. Pinning also keeps local and CI on the
same version, which matters for reproducible detector output.

### Layout

```
pipeline/   sepa_client.py  discover.py  fetch.py  fetch_flow.py  detect.py
            export.py  backtest.py  wave.py  routing.py  common.py  config.yaml
tests/      test_detect.py  conftest.py
site/       index.html       the monitor: is anything unusual right now?
            method.html      flood wave, backtest, limitations
            css/  js/  fonts/  data/
.github/workflows/update.yml
```

`detect.py` is pure functions only — no network, no file access — so every detector and the lag
estimator are directly unit-testable. Deployment uses the **GitHub Actions** Pages source,
because "deploy from a branch" can only publish the repository root or `/docs`, and the site
lives in `/site`.

## Credits

Data: SEPA, provisional, Open Government Licence.
Satellite imagery: *Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH (Contains
modified Copernicus Sentinel data 2016 & 2017)*, under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The **2016** mosaic is used
deliberately: EOX's 2018 and later mosaics are CC BY-NC-SA, and those non-commercial and
share-alike terms would bind the rest of this site. Fetched once by
`pipeline/bake_satellite.py`, reprojected from Web Mercator to the equirectangular frame this
map uses, and committed as a single 467 KB image — the map never requests a tile, so a visitor
never contacts a third party for it.
River and coast outlines © OpenStreetMap contributors, under the
[Open Database Licence](https://opendatacommons.org/licenses/odbl/). Fetched once by
`pipeline/bake_geo.py`, simplified to ~50 m and committed as a 21 KB file — the site requests
no map tiles, so a visitor never contacts a third party.
Charting by [Chart.js](https://www.chartjs.org/) 4.5.1.
Typefaces [Syne](https://gitlab.com/bonjour-monde/fonderie/syne-typeface),
[Inter](https://github.com/rsms/inter) and
[JetBrains Mono](https://github.com/JetBrains/JetBrainsMono), all under the SIL Open Font
License; licence texts are in `site/fonts/`.

---

*Not a flood warning service. For warnings see SEPA and the Met Office. Data: SEPA, provisional,
Open Government Licence.*
