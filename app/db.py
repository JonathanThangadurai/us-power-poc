from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app import config
from app.transform import PriceInterval

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=5, open=True)
    return _pool


@contextmanager
def get_cursor():
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur


def init_schema() -> None:
    """Brings the schema up to the latest Alembic revision. Safe to call on every
    startup: a fresh database gets the full history applied; an already-current one
    (including the live deployment, whose tables predate Alembic's adoption) just has
    revision 0001 recorded as already-satisfied since it's written as CREATE TABLE
    IF NOT EXISTS, then any new revisions on top of that actually run."""
    from alembic import command
    from alembic.config import Config

    project_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    command.upgrade(cfg, "head")


def upsert_prices(intervals: list[PriceInterval], raw_payload: dict | None = None) -> int:
    """Idempotent upsert keyed on (node, market, interval_start_utc). Returns rows written."""
    if not intervals:
        return 0
    raw_json = json.dumps(raw_payload) if raw_payload is not None else None
    with get_cursor() as cur:
        for iv in intervals:
            cur.execute(
                """
                INSERT INTO prices (node, market, interval_start_utc, interval_end_utc,
                                     lmp, energy, congestion, loss, raw_payload)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (node, market, interval_start_utc) DO UPDATE SET
                    interval_end_utc = EXCLUDED.interval_end_utc,
                    lmp = EXCLUDED.lmp,
                    energy = EXCLUDED.energy,
                    congestion = EXCLUDED.congestion,
                    loss = EXCLUDED.loss,
                    raw_payload = EXCLUDED.raw_payload,
                    ingested_at = now()
                """,
                (
                    iv.node,
                    iv.market,
                    iv.interval_start_utc,
                    iv.interval_end_utc,
                    iv.lmp,
                    iv.energy,
                    iv.congestion,
                    iv.loss,
                    raw_json,
                ),
            )
    return len(intervals)


def insert_raw_price(pipeline_run_id: int, market: str, raw_payload: dict) -> None:
    """The raw zone: the untouched fetch payload, kept once per run rather than
    duplicated inline on every conformed row."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO raw_prices (pipeline_run_id, market, raw_payload) VALUES (%s, %s, %s)",
            (pipeline_run_id, market, json.dumps(raw_payload)),
        )


def insert_quarantine_batch(pipeline_run_id: int, intervals: list[PriceInterval], reason: str) -> int:
    """A batch that failed a data-quality check lands here instead of `prices`."""
    if not intervals:
        return 0
    with get_cursor() as cur:
        for iv in intervals:
            cur.execute(
                """
                INSERT INTO quarantine_prices (pipeline_run_id, node, market, interval_start_utc,
                                                interval_end_utc, lmp, energy, congestion, loss, reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    pipeline_run_id,
                    iv.node,
                    iv.market,
                    iv.interval_start_utc,
                    iv.interval_end_utc,
                    iv.lmp,
                    iv.energy,
                    iv.congestion,
                    iv.loss,
                    reason,
                ),
            )
    return len(intervals)


def query_mart_daily_summary(
    node: str | None, market: str | None, limit: int, offset: int
) -> list[dict]:
    clauses = []
    params: list = []
    if node:
        clauses.append("node = %s")
        params.append(node)
    if market:
        clauses.append("market = %s")
        params.append(market)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT node, market, day_utc, avg_lmp, min_lmp, max_lmp, stddev_lmp, interval_count
        FROM mart_daily_summary
        {where}
        ORDER BY day_utc DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_prices(
    market: str | None, start: datetime | None, end: datetime | None, limit: int, offset: int
) -> list[dict]:
    clauses = []
    params: list = []
    if market:
        clauses.append("market = %s")
        params.append(market)
    if start:
        clauses.append("interval_start_utc >= %s")
        params.append(start)
    if end:
        clauses.append("interval_start_utc < %s")
        params.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT node, market, interval_start_utc, interval_end_utc, lmp, energy, congestion, loss
        FROM prices
        {where}
        ORDER BY interval_start_utc DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_hourly_spread(start: datetime | None, end: datetime | None, limit: int, offset: int) -> list[dict]:
    """Reads the mart_hourly_spread view (see migrations/versions/0002_*) rather than
    recomputing the DAM/RTM join inline - the mart layer owns this aggregate now."""
    clauses = []
    params: list = []
    if start:
        clauses.append("hour_start_utc >= %s")
        params.append(start)
    if end:
        clauses.append("hour_start_utc < %s")
        params.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT hour_start_utc, dam_avg_lmp, rtm_avg_lmp, dam_rtm_spread
        FROM mart_hourly_spread
        {where}
        ORDER BY hour_start_utc DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    with get_cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def insert_pipeline_run(started_at: datetime, market: str, status: str, **kwargs) -> int:
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO pipeline_runs (started_at, finished_at, market, status,
                                        rows_loaded, source_http_status, error_message, checks)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                started_at,
                kwargs.get("finished_at"),
                market,
                status,
                kwargs.get("rows_loaded", 0),
                kwargs.get("source_http_status"),
                kwargs.get("error_message"),
                json.dumps(kwargs["checks"]) if kwargs.get("checks") is not None else None,
            ),
        )
        return cur.fetchone()["id"]


def latest_pipeline_run() -> dict | None:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 1")
        return cur.fetchone()


def recent_pipeline_runs(limit: int = 10) -> list[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT %s", (limit,))
        return cur.fetchall()


def max_interval_start(market: str) -> datetime | None:
    with get_cursor() as cur:
        cur.execute("SELECT max(interval_start_utc) AS m FROM prices WHERE market = %s", (market,))
        row = cur.fetchone()
        return row["m"] if row else None
