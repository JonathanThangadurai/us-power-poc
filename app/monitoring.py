"""Three data-quality checks run after every load. Results feed /pipeline/status."""

from __future__ import annotations

from datetime import UTC, datetime

from app import config
from app.transform import PriceInterval

DAYAHEAD_FRESHNESS_BUFFER_MINUTES = 60  # DAM publishes once a day; allow a generous window.


def check_duplicate_keys(intervals: list[PriceInterval]) -> dict:
    keys = [(iv.node, iv.market, iv.interval_start_utc) for iv in intervals]
    duplicates = len(keys) - len(set(keys))
    return {"name": "duplicate_keys", "passed": duplicates == 0, "duplicate_count": duplicates}


def check_null_prices(intervals: list[PriceInterval]) -> dict:
    null_count = sum(1 for iv in intervals if iv.lmp is None)
    return {
        "name": "null_prices",
        "passed": null_count == 0,
        "null_count": null_count,
        "total": len(intervals),
    }


def check_freshness(
    latest_interval_start_utc: datetime | None, market: str, now: datetime | None = None
) -> dict:
    now = now or datetime.now(UTC)
    threshold_minutes = (
        config.FRESHNESS_THRESHOLD_MINUTES
        if market == "RTM"
        else 24 * 60 + DAYAHEAD_FRESHNESS_BUFFER_MINUTES
    )

    if latest_interval_start_utc is None:
        return {
            "name": "freshness",
            "passed": False,
            "age_minutes": None,
            "threshold_minutes": threshold_minutes,
        }

    age_minutes = (now - latest_interval_start_utc).total_seconds() / 60
    return {
        "name": "freshness",
        "passed": age_minutes <= threshold_minutes,
        "age_minutes": round(age_minutes, 1),
        "threshold_minutes": threshold_minutes,
    }


def run_all_checks(
    intervals: list[PriceInterval], market: str, latest_interval_start_utc: datetime | None
) -> dict:
    checks = [
        check_freshness(latest_interval_start_utc, market),
        check_duplicate_keys(intervals),
        check_null_prices(intervals),
    ]
    return {"checks": checks, "all_passed": all(c["passed"] for c in checks)}
