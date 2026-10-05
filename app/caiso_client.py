"""Client for the CAISO OASIS API (http://oasis.caiso.com).

Public, anonymous, no API key. Two query names matter for this POC:
  - PRC_LMP        day-ahead (market_run_id=DAM), hourly intervals
  - PRC_INTVL_LMP  real-time (market_run_id=RTM), 5-minute intervals

Gotcha confirmed against the live API: CAISO always answers HTTP 200.
When a query has no data (including the "wrong query name for this market"
mistake described in the brief), the zip contains an XML error document
with ERR_CODE/ERR_DESC instead of a CSV. That must be treated as a
pipeline failure, not a silent empty success.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime

import httpx

from app import config

logger = logging.getLogger("caiso_client")


class CaisoClientError(Exception):
    """Base error for anything that went wrong talking to CAISO."""


class CaisoEmptyResponseError(CaisoClientError):
    """CAISO returned a valid 200 response with an XML error payload instead of a CSV."""

    def __init__(self, err_code: str | None, err_desc: str | None):
        self.err_code = err_code
        self.err_desc = err_desc
        super().__init__(f"CAISO returned no data (ERR_CODE={err_code}: {err_desc})")


@dataclass
class CaisoRawResult:
    http_status: int
    raw_zip_bytes: bytes
    rows: list[dict]  # raw CSV rows as dicts, one per price component


def _fmt_dt(dt: datetime) -> str:
    """CAISO expects UTC timestamps as YYYYMMDDTHH:MM-0000."""
    return dt.strftime("%Y%m%dT%H:%M-0000")


def build_query_url(queryname: str, market_run_id: str, start: datetime, end: datetime, node: str) -> str:
    params = {
        "resultformat": "6",
        "queryname": queryname,
        "version": "1",
        "startdatetime": _fmt_dt(start),
        "enddatetime": _fmt_dt(end),
        "market_run_id": market_run_id,
        "node": node,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{config.CAISO_BASE_URL}?{query}"


def _parse_zip(raw_bytes: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        names = zf.namelist()
        if not names:
            raise CaisoEmptyResponseError(None, "zip archive was empty")

        csv_names = [n for n in names if n.lower().endswith(".csv")]
        xml_names = [n for n in names if n.lower().endswith(".xml")]

        if not csv_names and xml_names:
            xml_bytes = zf.read(xml_names[0])
            err_code, err_desc = _parse_oasis_error_xml(xml_bytes)
            raise CaisoEmptyResponseError(err_code, err_desc)

        if not csv_names:
            raise CaisoEmptyResponseError(None, f"no CSV or XML payload found, members={names}")

        csv_bytes = zf.read(csv_names[0])
        text = csv_bytes.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)


def _parse_oasis_error_xml(xml_bytes: bytes) -> tuple[str | None, str | None]:
    import re

    text = xml_bytes.decode("utf-8", errors="replace")
    code_match = re.search(r"<(?:\w+:)?ERR_CODE>([^<]*)</(?:\w+:)?ERR_CODE>", text)
    desc_match = re.search(r"<(?:\w+:)?ERR_DESC>([^<]*)</(?:\w+:)?ERR_DESC>", text)
    err_code = code_match.group(1) if code_match else None
    err_desc = desc_match.group(1) if desc_match else "unknown error (no ERR_DESC found)"
    return err_code, err_desc


async def fetch(
    queryname: str,
    market_run_id: str,
    start: datetime,
    end: datetime,
    node: str = config.CAISO_NODE,
    max_attempts: int = 2,
) -> CaisoRawResult:
    """Fetch and parse one CAISO OASIS query, with one retry on transport failure.

    Raises CaisoEmptyResponseError if CAISO answers with its "no data" XML payload
    (callers must record this as a failed run, per the brief).
    """
    url = build_query_url(queryname, market_run_id, start, end, node)

    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(
                timeout=config.HTTP_TIMEOUT_SECONDS, follow_redirects=True
            ) as client:
                resp = await client.get(url)
            resp.raise_for_status()
            rows = _parse_zip(resp.content)
            return CaisoRawResult(http_status=resp.status_code, raw_zip_bytes=resp.content, rows=rows)
        except CaisoEmptyResponseError:
            raise
        except (httpx.HTTPError, zipfile.BadZipFile) as exc:
            last_exc = exc
            logger.warning("CAISO fetch attempt %d/%d failed: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                import asyncio

                await asyncio.sleep(config.RETRY_BACKOFF_SECONDS * attempt)

    raise CaisoClientError(f"CAISO fetch failed after {max_attempts} attempts: {last_exc}") from last_exc
