"""Thin wrapper around SEPA's KiWIS time series API.

Docs: https://timeseriesdoc.sepa.org.uk/api-documentation/

Only documented requests are used here: getTimeseriesList and getTimeseriesValues.
Access uses SEPA's documented "Referer access" mode, which "requires that data
requests are accompanied by a so-called 'Referer' header; the originator of the
request is identified by the value of the Referer header rather than by IP".

Two response shapes to know about, both confirmed against the live API:

  getTimeseriesList, format=json -> header row first, then data rows:
    [["station_no","station_name",...], ["368014","Garthdee",...], ...]

  getTimeseriesValues, format=json -> one object per series:
    [{"ts_id":"59580010", "columns":"Timestamp,Value,Quality Code",
      "data":[["2026-10-24T08:00:00.000Z", 1.296, 254], ...]}]

Errors arrive as an XML ExceptionReport with HTTP 200, so the status code alone
is not enough to tell success from failure.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

import requests

# Fixed for every documented request against this service.
_FIXED_PARAMS = {
    "service": "kisters",
    "type": "queryServices",
    "datasource": "0",
}

# Transient conditions worth a retry. Anything else (e.g. InvalidParameterValue)
# is our bug and retrying just burns credits.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class SepaApiError(RuntimeError):
    """SEPA returned an ExceptionReport, or a response we could not parse."""

    def __init__(self, message: str, *, code: str | None = None, raw: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.raw = raw


@dataclass
class TimeseriesValues:
    """One series as returned by getTimeseriesValues."""

    ts_id: str
    columns: list[str]
    rows: list[list[Any]]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.rows)


class SepaClient:
    """Issues documented KiWIS requests and tracks the credits they cost.

    Credit rates are SEPA's own: "1 credit per 1000 data values" for time series
    data, "1 credit per request" for metadata, and "Credits are not split and a
    minimum of one credit is applied for every request."
    """

    # "a maximum of 300,000 data points are allowed in a single request"
    MAX_POINTS_PER_REQUEST = 300_000

    def __init__(
        self,
        base_url: str,
        referer: str,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 4,
        backoff_seconds: float = 2.0,
        access_key_env: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.credits_used = 0

        self._session = requests.Session()
        self._session.headers["Referer"] = referer

        access_key = os.environ.get(access_key_env) if access_key_env else None
        if access_key:
            # Documented form: "Authorization: Basic B64KEY".
            self._session.headers["Authorization"] = f"Basic {access_key}"

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #

    def _get(self, params: dict[str, Any]) -> Any:
        """GET with backoff on transient failures. Returns parsed JSON."""
        query = {**_FIXED_PARAMS, **params, "format": "json"}
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            if attempt:
                time.sleep(self.backoff_seconds * (2 ** (attempt - 1)))

            try:
                response = self._session.get(
                    self.base_url, params=query, timeout=self.timeout_seconds
                )
            except requests.RequestException as exc:
                last_error = exc
                continue

            if response.status_code in _RETRY_STATUS:
                last_error = SepaApiError(
                    f"HTTP {response.status_code} from SEPA", raw=response.text[:500]
                )
                continue

            response.raise_for_status()
            return self._parse(response.text)

        raise SepaApiError(
            f"SEPA request failed after {self.max_retries} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _parse(body: str) -> Any:
        """Parse a response body, turning an ExceptionReport into an exception."""
        stripped = body.lstrip()

        if stripped.startswith("<"):
            code, text = None, stripped[:500]
            try:
                root = ElementTree.fromstring(stripped)
                node = root.find(".//Exception")
                if node is not None:
                    code = node.get("exceptionCode")
                    message = node.findtext("ExceptionText")
                    text = message or text
            except ElementTree.ParseError:
                pass
            raise SepaApiError(f"SEPA error: {text}", code=code, raw=stripped[:1000])

        try:
            return json.loads(stripped)
        except ValueError as exc:
            raise SepaApiError(
                "SEPA returned a body that is neither JSON nor an ExceptionReport",
                raw=stripped[:1000],
            ) from exc

    def _charge(self, values: int) -> None:
        """Apply SEPA's credit rules: 1 per 1000 values, minimum 1 per request."""
        self.credits_used += max(1, -(-values // 1000))

    # ------------------------------------------------------------------ #
    # Documented requests
    # ------------------------------------------------------------------ #

    def get_timeseries_list(
        self,
        *,
        returnfields: list[str],
        ts_path: str | None = None,
        station_no: str | None = None,
        dateformat: str = "yyyy-MM-dd",
        **extra: Any,
    ) -> list[dict[str, str]]:
        """getTimeseriesList — "Retrieves a list of time series with metadata."

        Returns one dict per series, keyed by the field names in the response's
        own header row (note that asking for `coverage` yields `from` and `to`).
        """
        params: dict[str, Any] = {
            "request": "getTimeseriesList",
            "returnfields": ",".join(returnfields),
            "dateformat": dateformat,
            **extra,
        }
        if ts_path:
            params["ts_path"] = ts_path
        if station_no:
            params["station_no"] = station_no

        payload = self._get(params)
        self._charge(0)  # metadata request: 1 credit

        if not payload:
            return []
        if not isinstance(payload, list) or not isinstance(payload[0], list):
            raise SepaApiError(
                f"Unexpected getTimeseriesList shape: {type(payload).__name__}",
                raw=repr(payload)[:1000],
            )

        header, *rows = payload
        return [dict(zip(header, row)) for row in rows]

    def get_timeseries_values(
        self,
        ts_id: str,
        *,
        from_: str | None = None,
        to: str | None = None,
        period: str | None = None,
        returnfields: list[str] | None = None,
        md_returnfields: list[str] | None = None,
        **extra: Any,
    ) -> TimeseriesValues:
        """getTimeseriesValues — "Retrieves time series data values."

        Dates are `yyyy-MM-dd` or `yyyy-MM-ddTHH:mm:ss`; `period` is ISO-8601-ish
        (`P5Y`, `P7D`, `PT6H`). Timestamps come back in UTC with a `Z` suffix.
        """
        fields = returnfields or ["Timestamp", "Value", "Quality Code"]
        metadata_fields = md_returnfields or ["ts_id", "station_name", "ts_unitsymbol"]

        params: dict[str, Any] = {
            "request": "getTimeseriesValues",
            "ts_id": ts_id,
            "returnfields": ",".join(fields),
            "metadata": "true",
            "md_returnfields": ",".join(metadata_fields),
            **extra,
        }
        if from_:
            params["from"] = from_
        if to:
            params["to"] = to
        if period:
            params["period"] = period

        payload = self._get(params)

        if not isinstance(payload, list) or not payload:
            raise SepaApiError(
                "getTimeseriesValues returned no series",
                raw=repr(payload)[:1000],
            )

        series = payload[0]
        rows = series.get("data") or []
        self._charge(len(rows))

        columns = [c.strip() for c in str(series.get("columns", "")).split(",") if c]
        metadata = {k: v for k, v in series.items() if k not in {"data", "columns"}}

        return TimeseriesValues(
            ts_id=str(series.get("ts_id", ts_id)),
            columns=columns,
            rows=rows,
            metadata=metadata,
        )


def client_from_config(config: dict[str, Any]) -> SepaClient:
    """Build a client from the `api` block of config.yaml."""
    api = config["api"]
    return SepaClient(
        base_url=api["base_url"],
        referer=api["referer"],
        timeout_seconds=api.get("timeout_seconds", 60),
        max_retries=api.get("max_retries", 4),
        backoff_seconds=api.get("backoff_seconds", 2.0),
        access_key_env=api.get("access_key_env"),
    )
