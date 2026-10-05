from datetime import datetime

from pydantic import BaseModel


class PriceRow(BaseModel):
    node: str
    market: str
    interval_start_utc: datetime
    interval_end_utc: datetime
    lmp: float | None
    energy: float | None
    congestion: float | None
    loss: float | None


class SpreadRow(BaseModel):
    hour_start_utc: datetime
    dam_avg_lmp: float
    rtm_avg_lmp: float
    dam_rtm_spread: float


class CheckResult(BaseModel):
    name: str
    passed: bool


class PipelineRun(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    market: str
    status: str
    rows_loaded: int
    source_http_status: int | None
    error_message: str | None
    checks: dict | None


class PipelineStatus(BaseModel):
    latest_run: PipelineRun | None
    recent_runs: list[PipelineRun]
