import csv
from pathlib import Path

from app.transform import pivot_rows

FIXTURES = Path(__file__).parent / "fixtures"


def _load_rows(filename: str) -> list[dict]:
    with open(FIXTURES / filename) as f:
        return list(csv.DictReader(f))


def test_pivot_dam_sample_produces_one_row_per_hour_with_all_components():
    rows = _load_rows("dam_sample.csv")
    intervals = pivot_rows(rows, "DAM")

    assert len(intervals) == 24  # one calendar day, hourly
    for iv in intervals:
        assert iv.node == "TH_NP15_GEN-APND"
        assert iv.market == "DAM"
        assert iv.lmp is not None
        assert iv.energy is not None
        assert iv.congestion is not None
        assert iv.loss is not None
        # LMP should roughly equal energy + congestion + loss (CAISO decomposition)
        assert abs(iv.lmp - (iv.energy + iv.congestion + iv.loss)) < 0.01


def test_pivot_rtm_sample_produces_5min_intervals():
    rows = _load_rows("rtm_sample.csv")
    intervals = pivot_rows(rows, "RTM")

    assert len(intervals) == 12  # one hour of 5-minute intervals
    deltas = {
        (b.interval_start_utc - a.interval_start_utc).total_seconds()
        for a, b in zip(intervals, intervals[1:], strict=False)
    }
    assert deltas == {300.0}


def test_pivot_handles_dst_transition_without_duplicate_or_missing_intervals():
    """OASIS reports UTC (INTERVALSTARTTIME_GMT), so the US fall-back DST transition on
    2025-11-02 must not create duplicate or skipped intervals once pivoted."""
    rows = _load_rows("rtm_dst_transition.csv")
    intervals = pivot_rows(rows, "RTM")

    starts = [iv.interval_start_utc for iv in intervals]
    assert len(starts) == len(set(starts)), "duplicate interval_start_utc after DST transition"

    deltas = {(b - a).total_seconds() for a, b in zip(starts, starts[1:], strict=False)}
    assert deltas == {300.0}, f"gap or overlap across DST transition: {deltas}"
