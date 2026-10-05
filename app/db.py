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
    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    sql = schema_path.read_text()
    with get_cursor() as cur:
        cur.execute(sql)


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
    clauses = []
    params: list = []
    if start:
        clauses.append("interval_start_utc >= %s")
        params.append(start)
    if end:
        clauses.append("interval_start_utc < %s")
        params.append(end)
    where = f"AND {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        WITH hourly AS (
            SELECT market, date_trunc('hour', interval_start_utc) AS hour_start_utc, avg(lmp) AS avg_lmp
            FROM prices
            WHERE lmp IS NOT NULL {where}
            GROUP BY market, hour_start_utc
        )
        SELECT
            dam.hour_start_utc,
            dam.avg_lmp AS dam_avg_lmp,
            rtm.avg_lmp AS rtm_avg_lmp,
            (rtm.avg_lmp - dam.avg_lmp) AS dam_rtm_spread
        FROM hourly dam
        JOIN hourly rtm ON rtm.hour_start_utc = dam.hour_start_utc AND rtm.market = 'RTM'
        WHERE dam.market = 'DAM'
        ORDER BY dam.hour_start_utc DESC
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
