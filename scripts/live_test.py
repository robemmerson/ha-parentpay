"""Manual end-to-end smoke test against the live ParentPay portal.

Usage:
    cp .env.example .env
    # edit .env with real credentials
    python scripts/live_test.py
"""
from __future__ import annotations

import asyncio
import os
import pprint
import sys

import aiohttp
from dotenv import load_dotenv

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))

from custom_components.parentpay.client import ParentPayClient


async def main() -> None:
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
        print(f"Recent meals: {len(home.recent_meals)}")
        for m in home.recent_meals[:5]:
            pprint.pp(m)
        print(f"Recent payments (home page): {len(home.recent_payments)}")
        for p in home.recent_payments[:5]:
            pprint.pp(p)

        items = await client.fetch_payment_items()
        print(f"Payment items: {len(items)}")
        for it in items[:5]:
            pprint.pp(it)

        rows = await client.fetch_archive()
        print(f"Archive rows (GET, recent): {len(rows)}")
        for r in rows[:10]:
            pprint.pp(r)


if __name__ == "__main__":
    asyncio.run(main())
