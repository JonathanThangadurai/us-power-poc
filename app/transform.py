"""Pivot CAISO's one-row-per-price-component CSV rows into one row per node-interval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

COMPONENT_FIELD_MAP = {
    "LMP_PRC": "lmp",
    "LMP_ENE_PRC": "energy",
    "LMP_CONG_PRC": "congestion",
    "LMP_LOSS_PRC": "loss",
}


@dataclass
class PriceInterval:
    node: str
    market: str  # "DAM" or "RTM"
    interval_start_utc: datetime
    interval_end_utc: datetime
    lmp: float | None
    energy: float | None
    congestion: float | None
    loss: float | None


def _parse_gmt(ts: str) -> datetime:
    # CAISO format: "2025-09-01T08:00:00-00:00" -- already UTC, offset-aware.
    return datetime.fromisoformat(ts)


def pivot_rows(rows: list[dict], market: str) -> list[PriceInterval]:
    """Group raw CAISO rows by interval start and pivot component rows into columns."""
    buckets: dict[tuple[str, str], dict] = {}

    for row in rows:
        item = row.get("XML_DATA_ITEM")
        field = COMPONENT_FIELD_MAP.get(item)
        if field is None:
            continue  # ignore components we don't track (e.g. LMP_GHG_PRC)

        node = row["NODE"]
        start_raw = row["INTERVALSTARTTIME_GMT"]
        key = (node, start_raw)

        bucket = buckets.setdefault(
            key,
            {
                "node": node,
                "interval_start_utc": start_raw,
                "interval_end_utc": row["INTERVALENDTIME_GMT"],
                "lmp": None,
                "energy": None,
                "congestion": None,
                "loss": None,
            },
        )
        bucket[field] = float(row["MW"])

    intervals = []
    for bucket in buckets.values():
        intervals.append(
            PriceInterval(
                node=bucket["node"],
                market=market,
                interval_start_utc=_parse_gmt(bucket["interval_start_utc"]),
                interval_end_utc=_parse_gmt(bucket["interval_end_utc"]),
                lmp=bucket["lmp"],
                energy=bucket["energy"],
                congestion=bucket["congestion"],
                loss=bucket["loss"],
            )
        )
    intervals.sort(key=lambda i: i.interval_start_utc)
    return intervals
