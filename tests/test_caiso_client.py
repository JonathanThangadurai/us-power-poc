from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.caiso_client import CaisoEmptyResponseError, build_query_url, fetch
from tests.conftest import zip_fixture, zip_xml_fixture

START = datetime(2025, 9, 1, tzinfo=UTC)
END = datetime(2025, 9, 2, tzinfo=UTC)
NODE = "TH_NP15_GEN-APND"


def test_build_query_url_contains_expected_params():
    url = build_query_url("PRC_LMP", "DAM", START, END, NODE)
    assert "queryname=PRC_LMP" in url
    assert "market_run_id=DAM" in url
    assert "node=TH_NP15_GEN-APND" in url


@pytest.mark.asyncio
@respx.mock
async def test_fetch_parses_recorded_dam_response():
    zip_bytes = zip_fixture("dam_sample.csv")
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, content=zip_bytes))

    result = await fetch("PRC_LMP", "DAM", START, END, NODE)

    assert result.http_status == 200
    assert len(result.rows) > 0
    assert result.rows[0]["NODE"] == "TH_NP15_GEN-APND"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_raises_on_empty_response_xml():
    """CAISO's 'wrong query name for this market' trap: HTTP 200 but an XML ERR_CODE payload."""
    zip_bytes = zip_xml_fixture("empty_response_error.xml")
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, content=zip_bytes))

    with pytest.raises(CaisoEmptyResponseError) as exc_info:
        await fetch("PRC_LMP", "RTM", START, END, NODE)

    assert exc_info.value.err_code == "1000"
    assert "No data returned" in exc_info.value.err_desc
