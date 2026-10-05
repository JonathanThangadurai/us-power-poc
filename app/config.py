import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/energytrade"
)

CAISO_BASE_URL = os.environ.get("CAISO_BASE_URL", "http://oasis.caiso.com/oasisapi/SingleZip")
CAISO_NODE = os.environ.get("CAISO_NODE", "TH_NP15_GEN-APND")
CAISO_NODE_LABEL = os.environ.get("CAISO_NODE_LABEL", "NP15")

REALTIME_POLL_SECONDS = int(os.environ.get("REALTIME_POLL_SECONDS", "300"))
DAYAHEAD_POLL_SECONDS = int(os.environ.get("DAYAHEAD_POLL_SECONDS", str(24 * 3600)))

FRESHNESS_THRESHOLD_MINUTES = int(os.environ.get("FRESHNESS_THRESHOLD_MINUTES", "20"))

ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")

HTTP_TIMEOUT_SECONDS = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "30"))
RETRY_BACKOFF_SECONDS = float(os.environ.get("RETRY_BACKOFF_SECONDS", "2"))

DISABLE_SCHEDULER = os.environ.get("DISABLE_SCHEDULER", "false").lower() == "true"
