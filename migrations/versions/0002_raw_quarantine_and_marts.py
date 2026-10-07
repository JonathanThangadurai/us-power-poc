"""raw/quarantine zones and mart views

Rounds out raw -> staging -> mart layering:
  - raw_prices: the untouched payload from every fetch attempt, kept separate from
    the conformed `prices` table (which already existed) instead of duplicated
    inline on every row.
  - quarantine_prices: batches that failed a data-quality check land here instead
    of silently being dropped or (the prior behavior) written into `prices` anyway
    before the checks had even run.
  - mart_daily_summary / mart_hourly_spread: the first proper mart layer - views
    other consumers (dashboards, parallel-lives) can read instead of recomputing
    aggregates themselves.

This is purely additive - nothing here renames or drops anything `prices` or
`pipeline_runs` already had, so it's safe to run against the live database with
zero coordination with a deploy.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-07

"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_SQL = """
CREATE TABLE IF NOT EXISTS raw_prices (
    id                SERIAL PRIMARY KEY,
    pipeline_run_id   INTEGER REFERENCES pipeline_runs(id),
    market            TEXT NOT NULL,
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_payload       JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_prices_run ON raw_prices (pipeline_run_id);

CREATE TABLE IF NOT EXISTS quarantine_prices (
    id                   SERIAL PRIMARY KEY,
    pipeline_run_id      INTEGER REFERENCES pipeline_runs(id),
    node                 TEXT,
    market               TEXT NOT NULL,
    interval_start_utc   TIMESTAMPTZ,
    interval_end_utc     TIMESTAMPTZ,
    lmp                  DOUBLE PRECISION,
    energy               DOUBLE PRECISION,
    congestion           DOUBLE PRECISION,
    loss                 DOUBLE PRECISION,
    quarantined_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason               TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quarantine_prices_run ON quarantine_prices (pipeline_run_id);

CREATE OR REPLACE VIEW mart_daily_summary AS
SELECT
    node,
    market,
    date_trunc('day', interval_start_utc) AS day_utc,
    avg(lmp) AS avg_lmp,
    min(lmp) AS min_lmp,
    max(lmp) AS max_lmp,
    stddev_pop(lmp) AS stddev_lmp,
    count(*) AS interval_count
FROM prices
WHERE lmp IS NOT NULL
GROUP BY node, market, date_trunc('day', interval_start_utc);

CREATE OR REPLACE VIEW mart_hourly_spread AS
SELECT
    dam.hour_start_utc,
    dam.avg_lmp AS dam_avg_lmp,
    rtm.avg_lmp AS rtm_avg_lmp,
    (rtm.avg_lmp - dam.avg_lmp) AS dam_rtm_spread
FROM (
    SELECT date_trunc('hour', interval_start_utc) AS hour_start_utc, avg(lmp) AS avg_lmp
    FROM prices WHERE market = 'DAM' AND lmp IS NOT NULL
    GROUP BY 1
) dam
JOIN (
    SELECT date_trunc('hour', interval_start_utc) AS hour_start_utc, avg(lmp) AS avg_lmp
    FROM prices WHERE market = 'RTM' AND lmp IS NOT NULL
    GROUP BY 1
) rtm ON rtm.hour_start_utc = dam.hour_start_utc;
"""

DOWNGRADE_SQL = """
DROP VIEW IF EXISTS mart_hourly_spread;
DROP VIEW IF EXISTS mart_daily_summary;
DROP TABLE IF EXISTS quarantine_prices;
DROP TABLE IF EXISTS raw_prices;
"""


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute(DOWNGRADE_SQL)
