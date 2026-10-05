"""CLI backfill command.

Usage:
    python -m scripts.backfill --market RTM --start 2025-09-01T00:00:00+00:00 --end 2025-09-02T00:00:00+00:00
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from app import db
from app.ingest import backfill


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill CAISO prices for a date range.")
    parser.add_argument("--market", required=True, choices=["DAM", "RTM"])
    parser.add_argument("--start", required=True, help="ISO 8601 UTC, e.g. 2025-09-01T00:00:00+00:00")
    parser.add_argument("--end", required=True, help="ISO 8601 UTC")
    parser.add_argument("--node", default=None)
    args = parser.parse_args()

    start = datetime.fromisoformat(args.start)
    end = datetime.fromisoformat(args.end)

    db.init_schema()
    kwargs = {"node": args.node} if args.node else {}
    results = asyncio.run(backfill(args.market, start, end, **kwargs))
    for r in results:
        print(r)


if __name__ == "__main__":
    main()
