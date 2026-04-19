"""Manual end-to-end smoke test against the live ParentPay portal.

Usage:
    cp .env.example .env
    # edit .env with real credentials
    python scripts/live_test.py             # standard smoke test
    python scripts/live_test.py --backfill  # also exercise the 12-month archive POST
"""
from __future__ import annotations

import argparse
import asyncio
import os
import pprint
import sys
from datetime import date, timedelta

import aiohttp
from dotenv import load_dotenv

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from custom_components.parentpay.client import ParentPayClient


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Exercise fetch_archive_range(today - 365d, today) and print summary stats.",
    )
    args = parser.parse_args()

    load_dotenv()
    username = os.environ["PARENTPAY_USERNAME"]
    password = os.environ["PARENTPAY_PASSWORD"]

    async with aiohttp.ClientSession() as session:
        client = ParentPayClient(session, username=username, password=password)
        await client.login()
        print("Logged in OK")

        home = await client.fetch_home()
        print("Balances:")
        pprint.pp(home.balances)
        print(f"Recent payments (home page): {len(home.recent_payments)}")
        for p in home.recent_payments[:5]:
            pprint.pp(p)

        items = await client.fetch_payment_items()
        print(f"Payment items: {len(items)}")
        for it in items[:5]:
            pprint.pp(it)

        rows = await client.fetch_archive()
        print(f"Archive rows (last 30 days, POST): {len(rows)}")
        for r in rows[:10]:
            pprint.pp(r)

        if args.backfill:
            today = date.today()
            start = today - timedelta(days=365)
            print(f"\nBackfill POST: {start.isoformat()} -> {today.isoformat()}")
            backfill_rows = await client.fetch_archive_range(start, today)
            print(f"Backfill rows: {len(backfill_rows)}")
            if backfill_rows:
                child_ids = sorted({r.child_id for r in backfill_rows})
                methods = sorted({r.payment_method for r in backfill_rows})
                earliest = min(r.date_paid for r in backfill_rows)
                latest = max(r.date_paid for r in backfill_rows)
                print(f"  child_ids: {child_ids}")
                print(f"  payment_methods: {methods}")
                print(f"  date range: {earliest.isoformat()} -> {latest.isoformat()}")


if __name__ == "__main__":
    asyncio.run(main())
