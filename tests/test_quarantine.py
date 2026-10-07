"""A batch that fails a data-quality check must land in quarantine_prices, not prices."""

import csv
import io
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app import db
from app.ingest import run_ingest

NODE = "TH_NP15_GEN-APND"


def _zip_csv(rows: list[dict], fieldnames: list[str]) -> bytes:
    text_buf = io.StringIO()
    writer = csv.DictWriter(text_buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("data.csv", text_buf.getvalue())
    return zip_buf.getvalue()


def _build_bad_dam_csv(hour_start: datetime) -> bytes:
    """Two intervals: the first has all four components, the second is missing
    LMP_PRC entirely - pivot_rows will leave that interval's lmp as None, which
    the null_prices check must catch."""
    fieldnames = [
        "INTERVALSTARTTIME_GMT", "INTERVALENDTIME_GMT", "NODE", "MARKET_RUN_ID",
        "XML_DATA_ITEM", "MW",
    ]
    rows = []
    for offset, components in [(0, ["LMP_PRC", "LMP_ENE_PRC", "LMP_CONG_PRC", "LMP_LOSS_PRC"]),
                                (1, ["LMP_ENE_PRC", "LMP_CONG_PRC", "LMP_LOSS_PRC"])]:  # missing LMP_PRC
        start = hour_start + timedelta(hours=offset)
        end = start + timedelta(hours=1)
        for component in components:
            rows.append({
                "INTERVALSTARTTIME_GMT": start.strftime("%Y-%m-%dT%H:%M:%S-00:00"),
                "INTERVALENDTIME_GMT": end.strftime("%Y-%m-%dT%H:%M:%S-00:00"),
                "NODE": NODE,
                "MARKET_RUN_ID": "DAM",
                "XML_DATA_ITEM": component,
                "MW": "42.0",
            })
    return _zip_csv(rows, fieldnames)


@pytest.mark.asyncio
@respx.mock
async def test_failing_batch_is_quarantined_not_loaded_into_prices():
    hour_start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=5)
    zip_bytes = _build_bad_dam_csv(hour_start)
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, content=zip_bytes))

    result = await run_ingest("DAM", hour_start, hour_start + timedelta(hours=2))

    assert result["status"] == "failure"
    assert result["rows_loaded"] == 0
    null_check = next(c for c in result["checks"]["checks"] if c["name"] == "null_prices")
    assert null_check["passed"] is False

    bad_interval_start = hour_start + timedelta(hours=1)

    with db.get_cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM prices WHERE node=%s AND interval_start_utc IN (%s, %s)",
            (NODE, hour_start, bad_interval_start),
        )
        assert cur.fetchone()["n"] == 0, "failing batch must not reach the prices table"

        cur.execute(
            "SELECT count(*) AS n FROM quarantine_prices WHERE node=%s AND interval_start_utc IN (%s, %s)",
            (NODE, hour_start, bad_interval_start),
        )
        assert cur.fetchone()["n"] >= 2, "both intervals of the failing batch should be quarantined"

        cur.execute(
            "SELECT status, rows_loaded FROM pipeline_runs ORDER BY id DESC LIMIT 1"
        )
        run_row = cur.fetchone()
        assert run_row["status"] == "failure"
        assert run_row["rows_loaded"] == 0

        cur.execute("SELECT count(*) AS n FROM raw_prices")
        assert cur.fetchone()["n"] >= 1, "raw payload should be recorded even for a failing run"
