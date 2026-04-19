# ParentPay for Home Assistant

Unofficial Home Assistant custom integration (installable via HACS) that exposes data from the [ParentPay](https://www.parentpay.com/) parent portal.

Tested with two children under a single parent login at Test School.

## Features

Per child:
- `sensor.<child>_dinner_balance` — remaining dinner-money balance (GBP, monetary device class).
- `sensor.<child>_available_items_count` — number of active payment items available to purchase; full list in attributes.
- `calendar.<child>_meals` — calendar entity; each day's school meals are grouped into a single 12:00–13:00 event, items comma-separated. After the v2 backfill, history goes back ~12 months.
- `todo.<child>_parent_purchases` — todo list of parent-account purchases (school trips, books, etc.). Items can be marked complete to dismiss them from view.
- `todo.<child>_available_to_purchase` — todo list of payment items currently offered by the school for that child. Tick an item to permanently dismiss it (it will not reappear unless the school re-lists it under a fresh `payment_item_id`). When you actually purchase the item via ParentPay, it falls off this list automatically and appears in `parent_purchases` instead.

## Installation

1. Add this repository as a custom HACS repository (category: Integration).
2. Install "ParentPay" from HACS.
3. Restart Home Assistant.
4. Go to **Settings → Devices & Services → Add Integration → ParentPay**.
5. Enter your ParentPay email and password.

## Configuration

Options (Settings → Devices & Services → ParentPay → Configure):

- **Poll interval (minutes)** — default `30`.
- **Poll window start / end (HH:MM)** — default `08:00` / `16:00`. No polls run outside this window except the one-shot refresh on Home Assistant start.
- **Purchases list depth** — default `10`. Caps the number of items in the parent-purchases todo list.

## How it works

ParentPay has no public API. This integration:

1. Posts your credentials to the ParentPay JSON login endpoint (cookie-based session).
2. **On first run only**, posts the archive-page search form (`MS_Archive.aspx`, `__EVENTTARGET=ctl00$cmdSearch`) with a 12-month date range to backfill historical meal + parent-purchase rows in a single round-trip. The backfill flag is persisted; subsequent restarts skip it.
3. Polls three pages every 30 minutes during school hours (configurable):
   - The home page (`Default.aspx`) for balances and recent parent-account payments.
   - The payment items page for available items per child.
   - The archive page (GET) for the latest ~8 posted rows.
4. Dedups rows into a persistent store (keyed by row hash) so history accumulates across polls.

For each home-page parent-purchase row, the integration follows the receipt link (`PaymentDetailsViewerFX.aspx`) once and caches the line items by transaction id — this is how the truncated home-page item names (`English Macbet…`) are resolved to full names (`English Macbeth - The Complete Play`).

## Development

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Live smoke test against the real portal:

```bash
cp .env.example .env
# edit .env with real credentials
python scripts/live_test.py
```

## License

MIT — see `LICENSE`.

## Disclaimer

Not affiliated with ParentPay Ltd. Use at your own risk. The integration makes a good-faith effort to be a well-behaved HTTP client (identifiable User-Agent, low request volume, respects a configurable quiet-hours window).
