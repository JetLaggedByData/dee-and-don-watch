# Dee & Don Watch

**Live:** https://jetlaggedbydata.github.io/dee-and-don-watch/

Watches 13 SEPA river gauges on the Dee and Don near Aberdeen and flags readings that are
**statistically unusual** for that gauge, or that look like a **sensor fault**.

> **Not a flood warning service.** For warnings see
> [SEPA](https://www.sepa.org.uk/environment/water/flooding/) and the
> [Met Office](https://www.metoffice.gov.uk/weather/warnings-and-advice/uk-warnings).

## Why

SEPA publishes river level every 15 minutes, but a raw "2.5 m" means nothing without that
gauge's history, and nothing in the feed says whether a reading is the river or a failing sensor.

## Data

- **Level:** 13 gauges, 15-minute, 10 years
- **Rainfall:** 12 gauges, hourly

All of it comes from the [SEPA time series API](https://timeseriesdoc.sepa.org.uk/api-documentation/),
and is refreshed every 30 minutes by a GitHub Action. The browser never calls SEPA.

Contains SEPA data © Scottish Environment Protection Agency, used under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Provisional and subject to revision.

## Method

Each gauge is compared only with **its own** record:

1. **Data quality:** gaps, flatlines (12 h of no movement), spikes (Hampel filter).
   A suspect reading can't raise any other flag.
2. **Fast rise:** above the gauge's 99.5th percentile of rises **and** at least 0.05 m/hr.
3. **High level:** above the gauge's 99th percentile.
4. **12-hour projection** at Garthdee and Parkhill only. It routes water already measured
   upstream and uses no weather forecast. It is withheld if an upstream gauge stops reporting.

A textbook robust z-score was tried first and flagged 7-11% of all readings, because the gauges
round to 1 mm. Percentiles fixed it (~0.5%).

## Results

Backtested on the largest flood in the 10-year record, **18-19 Nov 2022**:

- Both rivers peaked strictly in downstream order: **13.75 h** down the Dee, **15.75 h** down the Don.
- The high-level flag fired at every main-stem gauge, 3.5-33.5 h before the peak.
- **Misses:** the strict rise flag missed Garthdee, Park and Polhollick.
- During floods, the projection beats "no change" out to about a day on both rivers, and
  is worse than it by 36 h. The site shows only 12 h.

Full tables are on the [method page](https://jetlaggedbydata.github.io/dee-and-don-watch/method.html).

## Limitations

- "Unusual" is relative to each gauge, so flags can't be compared between gauges.
- Recent data is provisional until SEPA's monthly checks.
- Lead times are fragile: one marginal reading moved Garthdee's by 61 h.
- Garthdee's gauge is noisy (~6% of readings look like spikes).
- Scheduled GitHub runs can be delayed. The page shows a banner if data is over 6 h old.

## Run it

```bash
pip install -r requirements.txt
python pipeline/fetch.py        # top up the SEPA cache
python pipeline/export.py       # write site/data/*.json
pytest -q
python -m http.server -d site 8000
```

## Credits

Data: SEPA, Open Government Licence |
Imagery: *Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH (Contains modified
Copernicus Sentinel data 2016 & 2017)*, [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
Rivers: © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/) |
[Chart.js](https://www.chartjs.org/) | Fonts: Syne, Inter, JetBrains Mono (SIL OFL, see `site/fonts/`)
