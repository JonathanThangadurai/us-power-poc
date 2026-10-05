# US Power POC (CAISO prices)

**This is a proof of concept.** It is a small, live pipeline that pulls real CAISO day-ahead and
real-time prices for one hub (NP15), stores them idempotently in PostgreSQL, and exposes them
through a FastAPI service with monitoring built in. It deliberately does not include a dashboard,
a forecasting model, additional ISOs, or heavier engineering tooling (Alembic, mypy, Prometheus) —
see "Known limitations" below.

## Attribution

Price data is sourced from the **California ISO (CAISO) OASIS** system
(`http://oasis.caiso.com`), queried anonymously with no API key or account. CAISO's terms of use
permit reuse of its public materials provided the California ISO is credited, and state that data
is provided "AS IS" with no warranty of accuracy or completeness — decisions based on it are the
user's own responsibility. This project credits CAISO here and in the API description served at
`/docs`.

## Architecture

```
            every 5 min                      once a day
      ┌────────────────────┐          ┌────────────────────┐
      │ CAISO OASIS         │          │ CAISO OASIS         │
      │ PRC_INTVL_LMP (RTM)  │          │ PRC_LMP (DAM)        │
      └──────────┬──────────┘          └──────────┬──────────┘
                 │ zip -> CSV                      │ zip -> CSV
                 ▼                                 ▼
      ┌─────────────────────────────────────────────────────┐
      │   app/scheduler.py  (asyncio loop inside app process) │
      │   app/ingest.py     (fetch -> pivot -> upsert -> check)│
      └──────────────────────────┬────────────────────────────┘
                                  ▼
                 app/transform.py: pivot one-row-per-component
                 CSV rows into one row per node-interval
                                  ▼
                 app/monitoring.py: freshness / duplicates / nulls
                                  ▼
      ┌─────────────────────────────────────────────────────┐
      │ PostgreSQL: prices, pipeline_runs (db/schema.sql)     │
      └──────────────────────────┬────────────────────────────┘
                                  ▼
                 FastAPI (app/main.py, app/api/routes.py)
                 /health  /prices  /pipeline/status  /docs
```

The scheduler runs as an `asyncio` background task inside the same process as the API (started in
FastAPI's `lifespan`), not as GitHub Actions cron, because cron on Actions cannot reliably hit a
5-minute cadence.

## Data lineage

1. **Raw**: the exact CAISO CSV rows (one row per price component: `LMP_PRC`, `LMP_ENE_PRC`,
   `LMP_CONG_PRC`, `LMP_LOSS_PRC`) are kept untouched in `prices.raw_payload` alongside the
   pivoted columns.
2. **Pivot**: `app/transform.py` groups raw rows by `(node, INTERVALSTARTTIME_GMT)` and pivots the
   four components into `lmp`, `energy`, `congestion`, `loss` columns.
3. **Store**: `app/db.py` upserts on the unique key `(node, market, interval_start_utc)`, so a
   replay or backfill of the same window produces identical rows, not duplicates.
4. **Derive**: `GET /prices?market=SPREAD` computes the hourly DAM-RTM spread on the fly
   (`avg(RTM lmp) - avg(DAM lmp)` per UTC hour) via a SQL CTE in `app/db.py`.

All timestamps are stored and returned in UTC (CAISO's `INTERVALSTARTTIME_GMT`/`...ENDTIME_GMT`
are already UTC), which sidesteps DST ambiguity entirely — see
`tests/test_transform.py::test_pivot_handles_dst_transition_without_duplicate_or_missing_intervals`,
which replays a real recorded response spanning the November 2025 US fall-back transition.

## A real CAISO trap this client guards against

CAISO's OASIS API always answers **HTTP 200**, even when a query is wrong or has no data. Querying
the real-time query name with the day-ahead market (or vice versa) does not error — it returns a
zip containing an **XML document with `ERR_CODE`/`ERR_DESC`** instead of a CSV. Verified live:

```
<m:ERROR>
  <m:ERR_CODE>1000</m:ERR_CODE>
  <m:ERR_DESC>No data returned for the specified selection</m:ERR_DESC>
</m:ERROR>
```

`app/caiso_client.py` detects this (zip contains `.xml` instead of `.csv`) and raises
`CaisoEmptyResponseError`, which `app/ingest.py` records as a **failed** `pipeline_runs` row — not
a silent empty success. This is covered by a recorded-fixture test
(`tests/test_caiso_client.py::test_fetch_raises_on_empty_response_xml`), using the real response
captured from the live API.

## Running it

### Locally with Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

- API: http://localhost:8000/docs
- The scheduler starts automatically inside the `app` container and begins pulling real-time
  prices every 5 minutes and day-ahead prices once a day.

### Locally without Docker

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
createdb energytrade   # or point DATABASE_URL at any Postgres
cp .env.example .env   # edit DATABASE_URL if needed
uvicorn app.main:app --reload
```

### Backfill

```bash
python -m scripts.backfill --market RTM --start 2025-09-01T00:00:00+00:00 --end 2025-09-02T00:00:00+00:00
python -m scripts.backfill --market DAM --start 2025-09-01T00:00:00+00:00 --end 2025-09-08T00:00:00+00:00
```

Backfill goes through the same `run_ingest` path as the live scheduler, so it reproduces identical
rows for any window already ingested live (verified: re-running a backfill over a window the
scheduler already loaded leaves row count and values unchanged).

### Tests

```bash
pytest -v        # uses recorded CAISO fixtures in tests/fixtures/ (real responses, incl. empty/DST cases)
ruff check .
```

## API

- `GET /health` — DB connectivity, configured node, server time.
- `GET /prices?market=DAM|RTM|SPREAD&start=...&end=...&limit=&offset=` — price rows, or the
  derived hourly DAM-RTM spread when `market=SPREAD`.
- `GET /pipeline/status` — latest and recent `pipeline_runs` rows (status, rows loaded, checks).
- `GET /docs` — OpenAPI UI (serves as the front end for this POC).

## Monitoring

Three checks run after every load and are written to `pipeline_runs.checks`, visible at
`/pipeline/status`:

1. **Freshness** — age of the newest interval vs. a threshold (`FRESHNESS_THRESHOLD_MINUTES`,
   default 20 min for RTM; DAM gets a 25-hour allowance since it only publishes once a day).
2. **Duplicate keys** — no duplicate `(node, market, interval_start_utc)` within a loaded batch.
3. **Null prices** — no null `lmp` values in a loaded batch.

A failing check marks that `pipeline_runs` row `status = 'failure'` and, if `ALERT_WEBHOOK_URL` is
set, posts a one-line JSON payload to it (Slack/Telegram/generic webhook compatible).

## Known limitations

- Single hub (NP15) and single ISO (CAISO) — no ERCOT/PJM adapter, no dashboard, no forecasting.
- No Alembic — `db/schema.sql` is applied with `CREATE TABLE IF NOT EXISTS` on startup; fine for a
  POC, not a migration strategy for a schema that will evolve.
- No mypy, no Prometheus/Grafana — ruff + pytest only.
- Quarantine-on-failure (separate table for bad batches) is not implemented; a failing batch today
  is simply recorded as a failed `pipeline_runs` row and not written to `prices` at all, since the
  checks run on the whole batch rather than filtering row-by-row.
- CAISO's `/health`-equivalent uptime is not independently monitored by an external service in
  this repo; that is called out as a next step rather than built in.

## Next steps

- Add a second hub or ERCOT/PJM behind the same adapter interface.
- A minimal trader-facing dashboard page over the existing `/prices` endpoint.
- External uptime check on `/health` plus a documented injected-failure drill.
