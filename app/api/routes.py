from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from app import config, db
from app.models import PipelineStatus, PriceRow, SpreadRow

router = APIRouter()


@router.get("/health")
def health():
    try:
        db.max_interval_start("RTM")
        db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "ok" if db_ok else "unreachable",
        "node": config.CAISO_NODE_LABEL,
        "time": datetime.now(UTC),
    }


@router.get("/prices", response_model=list[PriceRow] | list[SpreadRow])
def get_prices(
    market: str = Query("RTM", description="DAM, RTM, or SPREAD for the derived hourly DAM-RTM spread"),
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
):
    market = market.upper()
    if market not in ("DAM", "RTM", "SPREAD"):
        raise HTTPException(status_code=400, detail="market must be one of DAM, RTM, SPREAD")

    if market == "SPREAD":
        return db.query_hourly_spread(start, end, limit, offset)
    return db.query_prices(market, start, end, limit, offset)


@router.get("/pipeline/status", response_model=PipelineStatus)
def pipeline_status():
    latest = db.latest_pipeline_run()
    recent = db.recent_pipeline_runs(10)
    return {"latest_run": latest, "recent_runs": recent}


@router.get("/mart/daily-summary")
def mart_daily_summary(
    node: str | None = None,
    market: str | None = Query(None, description="DAM or RTM"),
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
):
    if market is not None:
        market = market.upper()
        if market not in ("DAM", "RTM"):
            raise HTTPException(status_code=400, detail="market must be DAM or RTM")
    return db.query_mart_daily_summary(node, market, limit, offset)
