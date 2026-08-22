# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup (requires Python 3.14 — HA 2026.4 dropped 3.13)
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Full quality gate
pytest -q                              # full suite
ruff check custom_components tests     # lint
mypy custom_components                 # types (strict)

# Single test
pytest tests/test_parsers.py::test_parse_payment_detail_extracts_all_line_items -v

# Live smoke test against the real ParentPay portal
cp .env.example .env                   # edit PARENTPAY_USERNAME / PASSWORD
python scripts/live_test.py
```

`asyncio_mode = "auto"` is set in `pyproject.toml`, so async tests don't need `@pytest.mark.asyncio`. The `hass` fixture comes from `pytest-homeassistant-custom-component`; `tests/conftest.py` auto-enables custom integrations.

## Architecture

Standard HA HACS integration layout — entry point in `custom_components/parentpay/__init__.py`, four platforms (sensor/calendar/todo/diagnostics), config flow, and a `DataUpdateCoordinator`. The non-obvious bits are below.

### ParentPay has no API — everything is HTML scraping

All endpoints documented in `docs/endpoints.md` if present, otherwise reverse-engineered from Burp captures stored in `tests/fixtures/`. `client.py` keeps a single `aiohttp.ClientSession`; auth cookies (`ppauth`, `pp2auth`, `XSRF-TOKEN`) are set automatically by the JSON login response. Login is a **JSON API** (not WebForms): `POST /public/api/security/authentication/login` with form-urlencoded `username`/`password`, returns 200 + `{"isActivated": true, ...}` on success, 400 + `{"message": "..."}` on failure.

### Data flow per poll (`coordinator._async_update_data`)

Three GETs (home page for balances + recent payments, payment items page, archive page for ~last 8 rows) plus a one-shot backfill POST against the archive page on the first successful poll. The backfill exercises `__EVENTTARGET=ctl00$cmdSearch` with a 12-month date range to pull all historical rows in a single round-trip; the success flag is persisted in `parentpay.backfill_v1`. Then an enrichment step for recent payments (see below). Everything merges into a dedup store keyed by row hash; history accumulates across polls.

Poll-window gating skips everything except the first tick after HA restart.

### Recent-payments enrichment (non-obvious; the reason `fetch_payment_detail` exists)

The home-page "Recent payments" mini-table **server-side truncates item names to ~14 chars** (`English Macbet`, `Year7MedievalH`) and has no `cid` attribute — no child association. The `data-gtm-label` is truncated too.

To resolve, `coordinator._enrich_recent_payments` extracts `TID`+`U` from each row's receipt URL and calls `client.fetch_payment_detail(tid, u)` which GETs `PaymentDetailsViewerFX.aspx`. That page lists **all line items sharing one `?U=` payment** — so a single fetch caches many TIDs. Cache lives in `store._payment_details_store` keyed by TID, so each TID is fetched at most once ever. Rows that can't resolve a child (fetch error, no first-name match against sidebar `data-consumer-data`) are **dropped** rather than polluting the store.

### Per-child entity discovery

Platforms (`sensor.py`, `calendar.py`, `todo.py`) discover children by reading `coordinator.store.purchases` / `coordinator.data["balances"]` during `async_setup_entry`. `__init__.py` runs `async_config_entry_first_refresh()` **before** `async_forward_entry_setups()`, so the store is always populated before platforms enumerate. `_child_name_for(coordinator, child_id)` looks up the display name from `child_name` fields on Balance/PaymentItem rows (same helper duplicated in each platform — YAGNI, don't hoist).

### Store versioning + migration

Bumping `STORE_VERSION` triggers HA's migration path. The default `Store` raises `NotImplementedError` — we subclass with `_MigratingStore` in `store.py` that returns `None` from the migrator (wipes old data; next poll repopulates). **Bump `STORE_VERSION` whenever stored row schemas change**, otherwise existing installs will silently read incompatible data.

### Domain invariants (enforced across parsers + tests)

- `child_id` is always the numeric ParentPay `ConsumerId` as `str` (e.g. `"11111111"` in scrubbed fixtures, real 8-digit id in production).
- `payment_method` is exactly `"Meal"` or `"Parent Account"` (with the space) — `ArchiveRow.is_meal` / `is_parent_payment` compare literal strings.
- Amounts are stored as `int` pence, not `Decimal`.

### Fixtures are PII-scrubbed

`tests/fixtures/*.html` are real captures with PII replaced (`Lauren`→`Alice`, `Bethany`→`Bob`, `18416154`→`11111111`, `23176880`→`22222222`, `Cheam High School`→`Test School`, all `TID=` collapsed, all `U=` collapsed to `2000000001`, ASP.NET `__VIEWSTATE`/`__EVENTVALIDATION` values blanked). When tests assert on fixture content, use the **scrubbed** values. If you add a new fixture, apply the same scrub — including base64-decoding `__VIEWSTATE` to verify no PII leaks through the blob.

### Archive backfill — `cmdSearch` POST, not the calendar postback

`MS_Archive.aspx` is a plain ASP.NET WebForms search form. The CLAUDE.md v1 hint about a `__EVENTTARGET=ctl00$calChooseStartDate` calendar postback was wrong — the actual mechanic is much simpler:

1. GET `MS_Archive.aspx` → parse `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION` via `parsers.parse_webforms_state`.
2. POST `MS_Archive.aspx` with `__EVENTTARGET=ctl00$cmdSearch`, the three state tokens echoed back, `ctl00$selChoosePupil=0` ("All"), `ctl00$selChooseService=0` ("All payment items"), and `ctl00$txtChooseStartDate` / `ctl00$txtChooseEndDate` in `DD/MM/YYYY` format.
3. Pass the response through `parse_archive`.

`tests/fixtures/archive_initial.html` is an older GET-with-rows capture kept for parser regression coverage; `tests/fixtures/archive_sample.html` is the POST response (1988 rows across two children). Both are scrubbed; state-token values were replaced with deterministic placeholders (`TESTVIEWSTATE_INITIAL`, etc.) so the round-trip tests can assert the POST body echoes them back.

The coordinator runs the backfill on every poll until it succeeds, then never again (flag stored in `parentpay.backfill_v1`). On failure it logs at WARNING and continues with the normal poll.

**Recent rows use the same POST (v2.2+).** As of 2026 ParentPay's raw GET of `MS_Archive.aspx` returns an empty "No results found" panel — there is no GET-only "last ~8 rows" path any more. `client.fetch_archive()` is a thin wrapper around `fetch_archive_range(today - ARCHIVE_WINDOW_DAYS, today)`, where `ARCHIVE_WINDOW_DAYS = 60` (const.py). The window is 60 rather than 30 days so it still overlaps real transactions across the ~7.5-week summer holiday.

**Two distinct empty shapes (v2.3+).** `parse_archive` soft-fails (returns `[]`) for *both*:
1. `<table summary="Payments">` present with no data rows.
2. **No table at all**, just `<div class="alert alert-danger">No results found</div>` — this is what ParentPay actually returns for a zero-result search, and the original v2.2 assumption that shape (1) always appeared was wrong. It caused a total outage on 2026-08-22: the 30-day window fell entirely inside the summer holiday (last transaction 2026-07-15), `parse_archive` raised, the coordinator turned that into `UpdateFailed`, and *every* entity went unavailable — balances and payment items included, even though those fetches had succeeded. HA logs it only once (`update_coordinator` gates the error log on `last_update_success`), so it looks silent. Fixture: `tests/fixtures/archive_empty.html`.

A response with neither marker still raises, so the next UI change doesn't silently swallow all data.

## Conventions

### Commits + releases

- Plain commit subjects. **No `Co-Authored-By: Claude` trailers or any AI attribution** (durable user preference).
- All commits + tags are **SSH-signed** via 1Password. Local `user.email = rob.emmerson@gmail.com` overrides the global work email. Signing key: `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILA1g5akMfE8z9m6f676SHw1SiImJt0sQGRGr7VRTRqt`.
- Release tags follow `v{MAJOR}.{MINOR}` (two-component, not SemVer 3-component). Bump `MINOR` by 1 each release; `MAJOR` for breaking changes. Keep `manifest.json` `version` in sync with the tag.

### Code style

ruff + mypy are **strict** — no `# type: ignore` without a comment explaining why, no bare `Any` where a narrower type works. `bs4` has real type stubs in 4.14+; `.get()` returns `str | AttributeValueList | None` — coerce with `str(...)` at the call site. Don't add the bs4 mypy override back.
