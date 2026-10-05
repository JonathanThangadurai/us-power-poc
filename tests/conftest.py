import io
import os
import zipfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def zip_fixture(csv_filename: str) -> bytes:
    csv_bytes = (FIXTURES / csv_filename).read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(csv_filename, csv_bytes)
    return buf.getvalue()


def zip_xml_fixture(xml_filename: str) -> bytes:
    xml_bytes = (FIXTURES / xml_filename).read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(xml_filename, xml_bytes)
    return buf.getvalue()


@pytest.fixture(autouse=True, scope="session")
def _test_env():
    os.environ.setdefault("DISABLE_SCHEDULER", "true")
