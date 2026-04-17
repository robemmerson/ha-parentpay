# ParentPay for Home Assistant

Unofficial Home Assistant custom integration (installable via HACS) that exposes data from the [ParentPay](https://www.parentpay.com/) parent portal.

Tested with two children under a single parent login at Test School.

## Features

Per child:
- `sensor.<child>_dinner_balance` — remaining dinner-money balance (GBP, monetary device class).
- `sensor.<child>_available_items_count` — number of active payment items available to purchase; full list in attributes.
- `calendar.<child>_meals` — calendar entity; each day's school meals are grouped into a single 12:00–13:00 event, items comma-separated.
- `todo.<child>_parent_purchases` — todo list of parent-account purchases (school trips, books, etc.). Items can be marked complete to dismiss them from view.

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
- **Purchases list depth** — default `10`. Caps the number of items in the todo list.

## How it works

ParentPay has no public API. This integration:

1. Posts your credentials to the ParentPay JSON login endpoint (cookie-based session).
2. Polls three pages every 30 minutes during school hours (configurable):
   - The home page (`Default.aspx`) for balances, recent meals, and recent parent-account payments — all in one fetch.
   - The payment items page for available items per child.
   - The archive page (GET) for the latest ~8 posted rows.
3. Dedups rows into a persistent store (keyed by row hash) so history accumulates over time across polls.

### Roadmap

- **v1 (current):** the archive page is fetched via simple GET (latest rows only). Historical rows accumulate in the store over successive polls.
- **v2:** add a one-shot historical backfill that exercises the ASP.NET WebForms calendar postback (`__EVENTTARGET=ctl00$calChooseStartDate`) to pull the full date-ranged archive on first run.

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
