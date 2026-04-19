# ParentPay v2 — design spec

**Date:** 2026-04-19
**Target release:** v2.0
**Status:** approved

## Goal

Add two user-facing capabilities to the ParentPay HACS integration:

1. **Richer meal history** — show real meal item names (e.g. `"PIZZA SLICE, Baked beans"`) on the per-child meals calendar, with history backfilled to roughly 12 months.
2. **Available-to-purchase todo** — a new `todo.<child>_available_to_purchase` entity per child, sourced from the existing `PaymentItems` fetch, with permanent local dismissals.

Drop the v1 home-page meal placeholder (`"School meal"` / `"No meal"`) entirely — it is no longer useful once archive data drives the calendar.

## Scope

### Added

- **Meal history backfill** — first-run-only step in `_async_update_data` that exercises the `MS_Archive.aspx` WebForms search (one GET, one POST) to pull a 12-month window of archive rows. Result is merged into the existing `meals_v1` and `purchases_v1` stores via the existing dedup. Marked done in a new persisted flag; on success it never runs again unless the store is wiped. On failure (network, parse, auth, transient WebForms state mismatch) the flag stays unset and the next scheduled poll retries the whole sequence.
- **Available-to-purchase todo entity** — one entity per child, mirroring the layout of the existing `parent_purchases` todo. Lists current `PaymentItem`s for that child filtered by a local dismissals set. Ticking an item dismisses it permanently (keyed by `(child_id, payment_item_id)`); un-ticking removes the dismissal as an escape hatch.
- **Dismissals store** — new persisted JSON keyed `parentpay.dismissals_v1` holding `{ "<child_id>:<payment_item_id>": {"dismissed_at": "<iso8601>"} }`.
- **Backfill flag store** — new persisted JSON keyed `parentpay.backfill_v1` holding `{"done": true, "done_at": "<iso8601>"}`.

### Removed

- `parsers.parse_home_recent_meals` and the corresponding wiring (`HomeSnapshot.recent_meals`, the `await self.store.async_merge(home.recent_meals)` line in the coordinator). The home page is still fetched for balances and recent payments; only the `Lunchtime meal activity` parsing goes away. Today's meal appears on the calendar once the next archive GET catches it (within the existing 30-minute poll cadence inside the 08:00–16:00 window).

### Schema bump

- `STORE_VERSION` → `3`. Existing `_MigratingStore` policy wipes prior data on upgrade; first poll after upgrade backfills 12 months of archive history into the freshly-empty stores.

### Out of scope (deferred)

- Periodic re-sync of historical archive — normal GETs plus dedup already accumulate new rows.
- Backfill resume / retry-with-backoff — natural retry on the next poll is sufficient given a 30-minute cadence.
- HA service to manually trigger backfill or clear dismissals — clearing the store achieves the same; can be added later if needed.
- Correlation between dismissed items and future re-listings under a different `payment_item_id` — the dismissal key is `payment_item_id`; if the school re-lists with a fresh id, it surfaces again, which is the right behaviour.

## Architecture

### Backfill mechanism

The archive page (`MS_Archive.aspx`) is a plain ASP.NET WebForms search form. CLAUDE.md's earlier hint about a `calChooseStartDate` calendar postback is incorrect — `archive_initial.html` shows the form is just text inputs plus a submit button. The full sequence is one GET followed by one POST:

1. **GET** `MS_Archive.aspx` — parse `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION` from the form.
2. **POST** `MS_Archive.aspx` (form-urlencoded) with:
   - `__EVENTTARGET=ctl00$cmdSearch`
   - `__EVENTARGUMENT=` (empty)
   - the three state tokens echoed back from the GET
   - `ctl00$selChoosePupil=0` (the "All" option, confirmed in fixture)
   - `ctl00$selChooseService=0` (the "All payment items" option)
   - `ctl00$txtChooseStartDate=DD/MM/YYYY` (today − 365 days)
   - `ctl00$txtChooseEndDate=DD/MM/YYYY` (today)
3. Pass the response through the existing `parse_archive`. `archive_sample.html` is the proof of concept: 1988 `<tr cid="...">` rows across both children, parser already handles them.

### Code landing points

- **`models.py`** — new `WebFormsState` dataclass: `viewstate: str`, `viewstategenerator: str`, `eventvalidation: str`. Frozen, slotted.
- **`parsers.py`** — new `parse_webforms_state(html: str) -> WebFormsState`. Uses BeautifulSoup; raises `ParentPayParseError` if any of the three tokens is missing (the search form will reject the POST without all three).
- **`client.py`** — new `ParentPayClient.fetch_archive_range(start: date, end: date) -> list[ArchiveRow]`. Does the GET, calls `parse_webforms_state`, builds the form-encoded body, does the POST, calls `parse_archive`. Reuses the existing session and the `_authed_get` / `_authed_post` retry-on-session-expiry wrappers.
- **`store.py`**
  - `_dismissals_store: Store[dict[str, dict[str, Any]]]` keyed `parentpay.dismissals_v1`. New methods: `is_dismissed(child_id: str, payment_item_id: str) -> bool`, `async_set_dismissed(child_id: str, payment_item_id: str, dismissed: bool) -> None`. Composite key `f"{child_id}:{payment_item_id}"` for O(1) lookup.
  - `_backfill_store: Store[dict[str, Any]]` keyed `parentpay.backfill_v1`. New methods: `backfill_done -> bool` property, `async_mark_backfill_done() -> None`.
  - Both new stores load alongside the existing three in `async_load`.
- **`coordinator.py`**
  - In `_async_update_data`, before the existing home/items/archive fetches: if `not self.store.backfill_done`, call `await self._client.fetch_archive_range(today − timedelta(days=365), today)`, merge with `await self.store.async_merge(rows)`, then `await self.store.async_mark_backfill_done()`. Wrap in `try / except ParentPayError` — on failure, log at `WARNING` and continue with the normal poll (flag stays unset; next poll retries).
  - Drop the `await self.store.async_merge(home.recent_meals)` line.
- **`todo.py`**
  - Add a second platform entity `AvailableToPurchaseTodo(CoordinatorEntity, TodoListEntity)`. Reuses the existing `_child_name_for` helper and per-child `DeviceInfo` identifiers.
  - `async_setup_entry` now adds two entities per child instead of one (`PurchasesTodoList` + `AvailableToPurchaseTodo`). Discovers child ids from the union of `coordinator.store.purchases` and `coordinator.data["items"]`.
- **`const.py`** — add `STORE_KEY_DISMISSALS = "parentpay.dismissals_v1"`, `STORE_KEY_BACKFILL = "parentpay.backfill_v1"`. Bump `STORE_VERSION = 3`.
- **`diagnostics.py`** — surface `backfill_done`, `backfill_done_at`, `dismissal_count_per_child`.
- **`strings.json` + `translations/en.json`** — add the `available_to_purchase` translation key.

### Available-to-purchase entity contract

| Field | Value |
|---|---|
| `entity_id` | `todo.<child_slug>_available_to_purchase` |
| `unique_id` | `{entry.entry_id}_{child_id}_available_to_purchase` |
| Device | existing per-child device (`(DOMAIN, f"{entry_id}_{child_id}")`) |
| `_attr_translation_key` | `"available_to_purchase"` |
| `_attr_name` | `"Available to purchase"` |
| `_attr_supported_features` | `TodoListEntityFeature.UPDATE_TODO_ITEM` |

Per `TodoItem`:

| Field | Value |
|---|---|
| `summary` | `it.name` (full item name from `PaymentItems`) |
| `uid` | `it.payment_item_id` |
| `status` | `COMPLETED` if dismissed, else `NEEDS_ACTION` |
| `description` | `f"£{price:.2f}"`; append availability text if present; append `" · New!"` if `it.is_new` |
| `due` | omitted |

**Display rule:** items where `it.child_id == self._child_id` AND `not store.is_dismissed(child_id, payment_item_id)`. Dismissed items are filtered out entirely — they don't show as completed strikethrough rows.

**Tick handler:** `async_update_todo_item(item)`:
- `item.status == COMPLETED` → `await store.async_set_dismissed(child_id, item.uid, True)`.
- `item.status == NEEDS_ACTION` → `await store.async_set_dismissed(child_id, item.uid, False)`.

### Calendar display change (no code change)

`coordinator.meals_for_child` and `MealsCalendar` are unchanged. The visible behaviour change comes purely from the input data:

- After backfill, the store holds 12 months of real archive rows with item names like `"PIZZA SLICE"` and `"Baked beans"`.
- `meals_for_child` groups by `(child_id, date)` and concatenates item names with `", "`.
- A date with multiple line items (a kid who took two items in one lunch) renders as `"PIZZA SLICE, Baked beans"`.
- A date with no archive row renders no event at all (the v1 placeholder is gone).

## Data flow per poll (v2)

```
_async_update_data:
    if not in poll window and not first run: return cached
    if not store.backfill_done:
        try:
            rows = await client.fetch_archive_range(today - 365d, today)
            await store.async_merge(rows)
            await store.async_mark_backfill_done()
        except ParentPayError as err:
            log warning, do not set flag, continue

    home    = await client.fetch_home()
    items   = await client.fetch_payment_items()
    archive = await client.fetch_archive()                      # GET-only, ~last 8 rows

    enriched = await self._enrich_recent_payments(home.recent_payments)

    # NOTE: home.recent_meals dropped from v2 — no merge call here
    await store.async_merge(enriched)
    await store.async_merge(archive)

    return {
        "balances":  home.balances,
        "items":     items,
        "meals":     store.meals,
        "purchases": store.purchases,
    }
```

## Failure handling

- **Backfill GET or POST raises `ParentPayError`** — log at `WARNING`, leave `backfill_done` unset, continue with the normal poll. The next scheduled poll (≤30 min later) retries the whole sequence. No exponential backoff.
- **Backfill POST returns zero parseable rows** — still mark `backfill_done = True`. A genuinely empty history (e.g. brand-new enrolment) shouldn't trigger perpetual retries.
- **Backfill POST returns non-2xx** — propagates as `ParentPayError` from the existing `_authed_post` wrapper; same retry behaviour as above.
- **Auth failure during backfill** — propagates as `ParentPayAuthError`; the existing coordinator catches that and triggers reauth via `ConfigEntryAuthFailed`. Backfill flag stays unset; runs again after reauth on the next poll.
- **Available-to-purchase tick handler — store write fails** — `Store.async_save` exceptions bubble up; HA logs them. The next poll re-renders from the in-memory dismissals (which were updated optimistically), so the user sees the expected outcome despite the persistence error.

## Testing

### `test_parsers.py`

- `test_parse_webforms_state_extracts_state_tokens` — feeds an inline HTML snippet with non-empty `__VIEWSTATE`/`__VIEWSTATEGENERATOR`/`__EVENTVALIDATION` values; asserts each is extracted exactly.
- `test_parse_webforms_state_raises_when_token_missing` — feeds HTML missing `__EVENTVALIDATION`; asserts `ParentPayParseError`.
- `test_parse_archive_handles_full_history_response` — feeds `archive_sample.html`; asserts ≥ 1900 rows, both child IDs (`11111111` and `22222222`) present, both `"Meal"` and `"Parent Account"` `payment_method` values present.
- Remove all `parse_home_recent_meals` tests.

### `test_client.py`

- `test_fetch_archive_range_does_get_then_post` — registers GET (returning the scrubbed `archive_initial.html` with non-empty test-only state tokens injected) and POST (returning `archive_sample.html`); asserts the POST body contains `__EVENTTARGET=ctl00$cmdSearch`, the date strings, the state tokens echoed from the GET, and `selChoosePupil=0` / `selChooseService=0`.
- `test_fetch_archive_range_uses_ddmm_date_format` — asserts the start/end date params are `DD/MM/YYYY` not `YYYY-MM-DD` or `MM/DD/YYYY`.
- `test_fetch_archive_range_returns_parsed_rows` — asserts the return value matches what `parse_archive(archive_sample.html)` produces.

### `test_coordinator.py`

- `test_first_poll_runs_backfill_and_marks_done` — store has no flag; coordinator runs once; assert `fetch_archive_range` called once, store flag now `True`, store contains backfilled rows.
- `test_second_poll_skips_backfill_when_done` — flag pre-set; coordinator runs; assert `fetch_archive_range` never called.
- `test_backfill_failure_leaves_flag_unset` — `fetch_archive_range` raises `ParentPayError`; coordinator poll completes (normal GETs still run); assert flag stays `False` so the next poll retries.
- `test_backfill_zero_rows_still_marks_done` — `fetch_archive_range` returns `[]`; assert flag is `True` afterwards.
- Update existing coordinator tests to no longer expect `home.recent_meals` to be merged.

### `test_store.py`

- `test_dismissal_round_trip` — `async_set_dismissed(c, p, True)` then `is_dismissed(c, p)` returns `True`; `async_set_dismissed(c, p, False)` then `is_dismissed(c, p)` returns `False`.
- `test_dismissal_distinguishes_children` — same `payment_item_id` dismissed for child A is not dismissed for child B.
- `test_backfill_flag_round_trip` — `backfill_done` defaults to `False`; `async_mark_backfill_done()` then `backfill_done` returns `True`; survives reload.
- `test_store_v3_migration_wipes_v2_data` — pre-load a v2-shaped store via the underlying `Store` API, instantiate `ParentPayStore` (v3), assert `meals`, `purchases`, all v2 caches return empty.

### `test_todo.py`

- `test_available_to_purchase_lists_undismissed_items_only` — coordinator data has 3 `PaymentItem`s for child A; one dismissed; entity `todo_items` returns 2.
- `test_available_to_purchase_filters_by_child` — items for child A and B; child A's entity returns only A's items.
- `test_tick_dismisses_item` — call `async_update_todo_item(TodoItem(uid=p, status=COMPLETED, ...))`; assert `store.is_dismissed(child_id, p)` is `True`.
- `test_untick_undismisses_item` — pre-dismissed; tick to `NEEDS_ACTION`; assert dismissal removed.
- `test_existing_purchases_todo_still_works` — sanity that the v1 entity is unchanged.

### Live smoke (`scripts/live_test.py`)

- Add a `--backfill` CLI flag. When set, calls `client.fetch_archive_range(today − 365d, today)` after login and prints row count, distinct child IDs, distinct payment methods, earliest and latest `date_paid`. Lets us validate against the real portal before tagging.

## Fixture sanitisation

`archive_initial.html` and `archive_sample.html` already exist but were captured before the v1 PII scrub. Apply the same scrub as v1:

- ASP.NET state tokens (`__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`, `__LASTFOCUS`, `__EVENTTARGET`, `__EVENTARGUMENT`, `__PREVIOUSPAGE`, `__SCROLLPOSITIONX`, `__SCROLLPOSITIONY`) — blank all values. Verify by base64-decoding `__VIEWSTATE` and grepping the decoded bytes for any of: `Lauren`, `Bethany`, `Cheam`, `Rob Emmerson`, `rob.emmerson@gmail.com`, the real 8-digit `ConsumerId`s, `UserId=` followed by the real id.
- Real names → `Alice` / `Bob` (already done in fixture).
- Real `ConsumerId`s → `11111111` / `22222222` (already done).
- Real school name → `Test School` (already done).
- All `TID=` query params → fresh sequential ids (`1000000001`, `1000000002`, ...). All `U=` → `2000000001`.
- Receipt code blocks (`PP{TID}`) — same TID substitution.
- Hidden `iUserID` inputs — replace with `88888888`.
- Real teacher / staff names embedded in payment item descriptions — none found in the existing fixture sweep, but verify again.

The plan should include this sanitisation as the first task, before any code changes — no point landing client code that references unscrubbed fixtures.

## Release

- Version: `v2.0` (breaking — store wipes on first run).
- Tag `v2.0`; bump `manifest.json` `"version"` to `"2.0"` (two-component, matches existing `v{MAJOR}.{MINOR}` policy).
- Single combined release covers both features.

## Migration UX

On HA restart after HACS update:

1. `_MigratingStore` wipes `meals_v1`, `purchases_v1`, `payment_details_v1`, `dismissals_v1`, `backfill_v1` (the last two are new keys so they're empty by definition).
2. Calendar, meal item count, and `parent_purchases` todo show empty for ~30 seconds.
3. First poll runs:
   - Backfill POST returns ~12 months of archive rows in one shot → `meals_v1` and `purchases_v1` populate together.
   - Normal home/items/archive GETs run as before.
   - Receipt-detail enrichment runs for any home-page recent payments not already covered by backfill (likely none).
4. Calendar shows real meal item names back to ~12 months ago. `parent_purchases` todo shows full backfilled history (capped to `purchases_list_depth`). New `available_to_purchase` todo appears per child with the school's currently-listed items, none dismissed yet.

## Docs to update

- **`README.md`** — replace v1 "v2 roadmap" bullet with "v2 (current)" feature list. Document the new `todo.<child>_available_to_purchase` entity and its tick-to-dismiss semantics. Note the one-shot 12-month backfill on first install/upgrade.
- **`CLAUDE.md`**:
  - Correct the "v2 roadmap" blurb — mechanism is `ctl00$cmdSearch` POST with text date inputs, not `calChooseStartDate` calendar postback.
  - Document `archive_initial.html` (GET) + `archive_sample.html` (POST response) as the round-trip pair.
  - Replace the "v1 scope" section with v2 reality: backfill on first run, dismissals, no home-page meal parsing.
  - Drop the `"School meal"` / `"No meal"` placeholder rule from the domain invariants section.
  - Bump the `STORE_VERSION` reference to 3.
