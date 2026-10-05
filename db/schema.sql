-- US power POC schema. Plain SQL, no migration framework (POC scope).

CREATE TABLE IF NOT EXISTS prices (
    node                TEXT NOT NULL,
    market              TEXT NOT NULL CHECK (market IN ('DAM', 'RTM')),
    interval_start_utc  TIMESTAMPTZ NOT NULL,
    interval_end_utc    TIMESTAMPTZ NOT NULL,
    lmp                 DOUBLE PRECISION,
    energy              DOUBLE PRECISION,
    congestion          DOUBLE PRECISION,
    loss                DOUBLE PRECISION,
    raw_payload         JSONB,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (node, market, interval_start_utc)
);

CREATE INDEX IF NOT EXISTS idx_prices_market_time ON prices (market, interval_start_utc);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                   SERIAL PRIMARY KEY,
    started_at           TIMESTAMPTZ NOT NULL,
    finished_at          TIMESTAMPTZ,
    market               TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('running', 'success', 'failure')),
    rows_loaded          INTEGER NOT NULL DEFAULT 0,
    source_http_status   INTEGER,
    error_message        TEXT,
    checks               JSONB
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started ON pipeline_runs (started_at DESC);
