"""Orchestrates one ingestion run: fetch -> pivot -> quality checks -> load (prices
on pass, quarantine_prices on fail) -> record run. Checks run before anything lands
in the serving table, not after - a failing batch never touches `prices` at all."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

from app import config, db
from app.caiso_client import CaisoClientError, CaisoEmptyResponseError, fetch
from app.monitoring import run_all_checks
from app.transform import pivot_rows

logger = logging.getLogger("ingest")

QUERY_NAME = {"DAM": "PRC_LMP", "RTM": "PRC_INTVL_LMP"}


async def run_ingest(market: str, start: datetime, end: datetime, node: str = config.CAISO_NODE) -> dict:
    """Run one ingestion cycle for `market` over [start, end). Always records a pipeline_runs row."""
    started_at = datetime.now(UTC)
    queryname = QUERY_NAME[market]

    try:
        result = await fetch(queryname, market, start, end, node)
    except CaisoEmptyResponseError as exc:
        logger.error("CAISO empty/error response for %s %s-%s: %s", market, start, end, exc)
        db.insert_pipeline_run(
            started_at=started_at,
            market=market,
            status="failure",
            finished_at=datetime.now(UTC),
            rows_loaded=0,
            source_http_status=200,
            error_message=str(exc),
        )
        await _maybe_alert(f"CAISO ingest FAILED for {market}: {exc}")
        return {"status": "failure", "error": str(exc)}
    except (CaisoClientError, httpx.HTTPError) as exc:
        logger.error("CAISO fetch failed for %s %s-%s: %s", market, start, end, exc)
        db.insert_pipeline_run(
            started_at=started_at,
            market=market,
            status="failure",
            finished_at=datetime.now(UTC),
            rows_loaded=0,
            error_message=str(exc),
        )
        await _maybe_alert(f"CAISO ingest FAILED for {market}: {exc}")
        return {"status": "failure", "error": str(exc)}

    intervals = pivot_rows(result.rows, market)
    # Freshness must judge the batch we just fetched, not whatever is already in
    # `prices` - checking the table here (now that validation runs before loading)
    # would just measure how stale the existing data is, not this fetch.
    latest_in_batch = max((iv.interval_start_utc for iv in intervals), default=None)
    checks = run_all_checks(intervals, market, latest_in_batch)
    status = "success" if checks["all_passed"] else "failure"
    rows_loaded = len(intervals) if status == "success" else 0

    run_id = db.insert_pipeline_run(
        started_at=started_at,
        market=market,
        status=status,
        finished_at=datetime.now(UTC),
        rows_loaded=rows_loaded,
        source_http_status=result.http_status,
        checks=checks,
    )

    # Raw zone: the untouched CSV rows this run fetched, kept once per run rather
    # than duplicated inline on every conformed row.
    db.insert_raw_price(run_id, market, {"rows": result.rows})

    if status == "success":
        db.upsert_prices(intervals)
    else:
        failed = ", ".join(c["name"] for c in checks["checks"] if not c["passed"])
        db.insert_quarantine_batch(run_id, intervals, reason=f"failed checks: {failed}")
        await _maybe_alert(f"CAISO ingest quality checks FAILED for {market}: {checks}")

    return {"status": status, "rows_loaded": rows_loaded, "checks": checks}


async def _maybe_alert(message: str) -> None:
    if not config.ALERT_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(config.ALERT_WEBHOOK_URL, json={"text": message})
    except httpx.HTTPError as exc:
        logger.warning("Alert webhook call failed: %s", exc)


async def ingest_realtime_latest() -> dict:
    """Pull the most recent real-time window (last ~15 minutes, covers the 5-min cadence with margin)."""
    now = datetime.now(UTC)
    start = now - timedelta(minutes=15)
    return await run_ingest("RTM", start, now)


async def ingest_dayahead_latest() -> dict:
    """Pull the last 24 hours of day-ahead prices."""
    now = datetime.now(UTC)
    start = now - timedelta(hours=24)
    return await run_ingest("DAM", start, now)


async def backfill(market: str, start: datetime, end: datetime, node: str = config.CAISO_NODE) -> list[dict]:
    """Backfill a date range in CAISO-friendly chunks (<=30 days DAM, <=1 day RTM) with the same
    idempotent upsert path used by live ingestion, so a backfill reproduces live results exactly."""
    chunk = timedelta(days=30) if market == "DAM" else timedelta(hours=12)
    results = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + chunk, end)
        results.append(await run_ingest(market, cursor, chunk_end, node))
        cursor = chunk_end
    return results
