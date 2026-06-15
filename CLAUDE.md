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

Three GETs: home page (173 KB — balances + recent meals + recent payments in one fetch), payment items page, archive page (GET-only returns ~last 8 rows). Then an enrichment step for recent payments (see below). Everything merges into a dedup store keyed by row hash; history accumulates across polls.

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
- Home-page meal rows carry only a **price**, not the food name — parser emits `"School meal"` / `"No meal"`. Real food names like `"PIZZA SLICE"` come from the archive GET only and are deduped into the same store.
- Amounts are stored as `int` pence, not `Decimal`.

### Fixtures are PII-scrubbed

`tests/fixtures/*.html` are real captures with PII replaced (`Lauren`→`Alice`, `Bethany`→`Bob`, `18416154`→`11111111`, `23176880`→`22222222`, `Cheam High School`→`Test School`, all `TID=` collapsed, all `U=` collapsed to `2000000001`, ASP.NET `__VIEWSTATE`/`__EVENTVALIDATION` values blanked). When tests assert on fixture content, use the **scrubbed** values. If you add a new fixture, apply the same scrub — including base64-decoding `__VIEWSTATE` to verify no PII leaks through the blob.

### v1 scope — archive is GET-only

The archive page's full date-range query uses ASP.NET WebForms postback (`__EVENTTARGET=ctl00$calChooseStartDate`, opaque `__EVENTARGUMENT=V{int}` day keys scraped from calendar DOM, plus `__VIEWSTATE`/`__VIEWSTATEGENERATOR`/`__EVENTVALIDATION` round-trip). **Deferred to v2.** Don't add `fetch_archive(start, end)` or backfill logic; historical rows accumulate naturally via the 8-row GET.

## Conventions

### Commits + releases

- Plain commit subjects. **No `Co-Authored-By: Claude` trailers or any AI attribution** (durable user preference).
- All commits + tags are **SSH-signed** via 1Password. Local `user.email = rob.emmerson@gmail.com` overrides the global work email. Signing key: `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILA1g5akMfE8z9m6f676SHw1SiImJt0sQGRGr7VRTRqt`.
- Release tags follow `v{MAJOR}.{MINOR}` (two-component, not SemVer 3-component). Bump `MINOR` by 1 each release; `MAJOR` for breaking changes. Keep `manifest.json` `version` in sync with the tag.

### Code style

ruff + mypy are **strict** — no `# type: ignore` without a comment explaining why, no bare `Any` where a narrower type works. `bs4` has real type stubs in 4.14+; `.get()` returns `str | AttributeValueList | None` — coerce with `str(...)` at the call site. Don't add the bs4 mypy override back.
