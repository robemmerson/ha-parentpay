# ParentPay v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v2.0 of the ParentPay HA integration: backfill ~12 months of meal/parent-purchase history on first run, drop the home-page meal placeholder, and add a per-child "available to purchase" todo with permanent local dismissals.

**Architecture:** One-shot WebForms POST against `MS_Archive.aspx` using the `cmdSearch` button to pull a date-range archive in a single round-trip. New `parentpay.dismissals_v1` and `parentpay.backfill_v1` HA stores hold the new state. New todo entity per child, sourced from the existing `PaymentItems` fetch and filtered by the dismissals store. `STORE_VERSION` bump from 2 → 3 wipes prior caches via the existing `_MigratingStore`.

**Tech Stack:** Python 3.14, Home Assistant ≥ 2026.4.2, aiohttp, BeautifulSoup 4.14, pytest + pytest-homeassistant-custom-component + aioresponses, ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-04-19-parentpay-v2-design.md`

---

## File map

| Path | Change |
|---|---|
| `tests/fixtures/archive_initial.html` | Modify — inject test-only state tokens, verify scrub |
| `tests/fixtures/archive_sample.html` | Modify — verify scrub |
| `custom_components/parentpay/models.py` | Modify — add `WebFormsState`; remove `recent_meals` field on `HomeSnapshot` |
| `custom_components/parentpay/parsers.py` | Modify — add `parse_webforms_state`; remove `parse_home_recent_meals` |
| `custom_components/parentpay/client.py` | Modify — add `fetch_archive_range`; drop `parse_home_recent_meals` import + `recent_meals=` field |
| `custom_components/parentpay/const.py` | Modify — add `STORE_KEY_DISMISSALS`, `STORE_KEY_BACKFILL`; bump `STORE_VERSION = 3` |
| `custom_components/parentpay/store.py` | Modify — add dismissals + backfill store + methods |
| `custom_components/parentpay/coordinator.py` | Modify — backfill in `_async_update_data`; drop `home.recent_meals` merge |
| `custom_components/parentpay/todo.py` | Modify — add `AvailableToPurchaseTodo`; register both entities in `async_setup_entry` |
| `custom_components/parentpay/diagnostics.py` | Modify — surface `backfill_done`, `backfill_done_at`, `dismissal_count_per_child` |
| `custom_components/parentpay/strings.json` | Modify — add `available_to_purchase` entity translation |
| `custom_components/parentpay/translations/en.json` | Modify — same as strings.json |
| `custom_components/parentpay/manifest.json` | Modify — `"version": "2.0"` |
| `tests/test_parsers.py` | Modify — add 3 tests, remove 3 home-meal tests |
| `tests/test_client.py` | Modify — add 3 `fetch_archive_range` tests |
| `tests/test_coordinator.py` | Modify — add 4 backfill tests, add `fetch_archive_range` to mock client |
| `tests/test_store.py` | Modify — add 4 dismissal/backfill/migration tests |
| `tests/test_todo.py` | Modify — add 4 available-to-purchase tests |
| `scripts/live_test.py` | Modify — add `--backfill` flag |
| `README.md` | Modify — replace v1 roadmap with v2-current feature list |
| `CLAUDE.md` | Modify — correct WebForms note, document fixtures, drop School-meal invariant |

---

## Task ordering rationale

Fixtures first (gating — the test-only state tokens and PII scrub block all later test work). Then small leaf changes (model + parser additions) that have no dependencies. Then store + client (independent of each other). Then the coordinator wiring (depends on both). Then the todo entity (depends on store). Then docs + version bump. Final quality gate at the end.

---

## Task 1: Sanitise archive fixtures + inject test-only state tokens

**Files:**
- Modify: `tests/fixtures/archive_initial.html`
- Modify: `tests/fixtures/archive_sample.html`

The two fixtures already exist but were captured against the live portal before the v1 PII scrub. The base64-encoded `__VIEWSTATE` blob can leak real names, ConsumerIds, and email addresses. Inject test-only non-empty values for the three state tokens so `Task 5` tests can assert against them.

- [ ] **Step 1: Verify the existing scrub level via base64 decode**

```bash
cd /Users/rob.emmerson/git/ha-parentpay
python3 -c "
import base64, re, pathlib
needles = [b'Lauren', b'Bethany', b'Cheam', b'Rob Emmerson', b'rob.emmerson@gmail.com', b'18416154', b'23176880', b'29816655']
for f in ['tests/fixtures/archive_initial.html', 'tests/fixtures/archive_sample.html']:
    html = pathlib.Path(f).read_bytes()
    for m in re.finditer(rb'__VIEWSTATE[^>]*value=\"([^\"]*)\"', html):
        v = m.group(1)
        if not v: continue
        try:
            decoded = base64.b64decode(v + b'=' * (-len(v) % 4))
        except Exception as e:
            print(f, 'base64 fail:', e); continue
        for n in needles:
            if n in decoded:
                print(f, 'LEAK:', n.decode())
print('scan done')
"
```

Expected output: `scan done` with no `LEAK:` lines. (Tokens were blanked in v1, so the loop body should never execute.)

- [ ] **Step 2: Replace any non-empty state-token values with test-only placeholders**

For both fixtures, replace the pattern `<input id="__VIEWSTATE" name="__VIEWSTATE" type="hidden" value=""/>` (and the same for `__VIEWSTATEGENERATOR` and `__EVENTVALIDATION`) with non-empty deterministic test values. Use the Edit tool exactly:

In `tests/fixtures/archive_initial.html`:
- Replace `<input id="__VIEWSTATE" name="__VIEWSTATE" type="hidden" value=""/>` → `<input id="__VIEWSTATE" name="__VIEWSTATE" type="hidden" value="TESTVIEWSTATE_INITIAL"/>`
- Replace `<input id="__VIEWSTATEGENERATOR" name="__VIEWSTATEGENERATOR" type="hidden" value=""/>` → `<input id="__VIEWSTATEGENERATOR" name="__VIEWSTATEGENERATOR" type="hidden" value="TESTGEN_INITIAL"/>`
- If no `__EVENTVALIDATION` input exists, add one immediately after the `__VIEWSTATEGENERATOR` line: `<input id="__EVENTVALIDATION" name="__EVENTVALIDATION" type="hidden" value="TESTEV_INITIAL"/>`

In `tests/fixtures/archive_sample.html`:
- Same three replacements but with `TESTVIEWSTATE_SAMPLE`, `TESTGEN_SAMPLE`, `TESTEV_SAMPLE`.

- [ ] **Step 3: Verify both fixtures still parse the existing tests**

Run: `pytest tests/test_parsers.py::test_parse_archive_extracts_meal_and_parent_account_rows tests/test_parsers.py::test_parse_archive_initial_get_returns_recent_rows -v`
Expected: 2 passed (these tests don't look at state tokens, only at `<tr cid="...">` rows).

- [ ] **Step 4: Add a stronger row-count assertion test for the full-history fixture**

Append to `tests/test_parsers.py`:

```python
def test_parse_archive_handles_full_history_response() -> None:
    """archive_sample.html is the POST response — should hold the full 12-month grid."""
    rows = parse_archive(_load_text("archive_sample.html"))
    assert len(rows) >= 1900
    assert {r.child_id for r in rows} >= {"11111111", "22222222"}
    assert {r.payment_method for r in rows} >= {"Meal", "Parent Account"}
```

Run: `pytest tests/test_parsers.py::test_parse_archive_handles_full_history_response -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add tests/fixtures/archive_initial.html tests/fixtures/archive_sample.html tests/test_parsers.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "test(fixtures): inject test-only WebForms state tokens; assert full archive parse"
```

---

## Task 2: Add `WebFormsState` model

**Files:**
- Modify: `custom_components/parentpay/models.py`

- [ ] **Step 1: Add the dataclass at the bottom of the file**

In `custom_components/parentpay/models.py`, append:

```python
@dataclass(frozen=True, slots=True)
class WebFormsState:
    """ASP.NET WebForms hidden state tokens needed to round-trip a postback."""

    viewstate: str
    viewstategenerator: str
    eventvalidation: str
```

- [ ] **Step 2: Run mypy**

Run: `mypy custom_components`
Expected: `Success: no issues found in N source files`

- [ ] **Step 3: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/models.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(models): add WebFormsState dataclass"
```

---

## Task 3: Add `parse_webforms_state` parser

**Files:**
- Modify: `custom_components/parentpay/parsers.py`
- Modify: `tests/test_parsers.py`

- [ ] **Step 1: Add three failing tests in `tests/test_parsers.py`**

Add to the existing imports near the top: change

```python
from custom_components.parentpay.parsers import (
    extract_receipt_ids,
    parse_archive,
    parse_home_balances,
    parse_home_recent_meals,
    parse_home_recent_payments,
    parse_login_response,
    parse_payment_detail,
    parse_payment_items,
)
```

to

```python
from custom_components.parentpay.parsers import (
    extract_receipt_ids,
    parse_archive,
    parse_home_balances,
    parse_home_recent_meals,
    parse_home_recent_payments,
    parse_login_response,
    parse_payment_detail,
    parse_payment_items,
    parse_webforms_state,
)
```

Append at the end of the file:

```python
def test_parse_webforms_state_extracts_three_tokens() -> None:
    html = """
    <html><body><form>
      <input id="__VIEWSTATE" name="__VIEWSTATE" type="hidden" value="STATEV"/>
      <input id="__VIEWSTATEGENERATOR" name="__VIEWSTATEGENERATOR" type="hidden" value="GENV"/>
      <input id="__EVENTVALIDATION" name="__EVENTVALIDATION" type="hidden" value="EVV"/>
    </form></body></html>
    """
    state = parse_webforms_state(html)
    assert state.viewstate == "STATEV"
    assert state.viewstategenerator == "GENV"
    assert state.eventvalidation == "EVV"


def test_parse_webforms_state_reads_archive_initial_fixture() -> None:
    state = parse_webforms_state(_load_text("archive_initial.html"))
    assert state.viewstate == "TESTVIEWSTATE_INITIAL"
    assert state.viewstategenerator == "TESTGEN_INITIAL"
    assert state.eventvalidation == "TESTEV_INITIAL"


def test_parse_webforms_state_raises_when_token_missing() -> None:
    html = """
    <html><body><form>
      <input id="__VIEWSTATE" name="__VIEWSTATE" type="hidden" value="X"/>
      <input id="__VIEWSTATEGENERATOR" name="__VIEWSTATEGENERATOR" type="hidden" value="Y"/>
    </form></body></html>
    """
    with pytest.raises(ParentPayParseError):
        parse_webforms_state(html)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_parsers.py -k webforms_state -v`
Expected: 3 errors with `ImportError: cannot import name 'parse_webforms_state'`.

- [ ] **Step 3: Implement the parser**

In `custom_components/parentpay/parsers.py`, add to the imports near the top of the file (the `from .models import ...` block):

```python
from .models import (
    ArchiveRow,
    Balance,
    PaymentDetailItem,
    PaymentItem,
    WebFormsState,
)
```

Append a new function near the bottom of the file (after `parse_payment_detail`):

```python
def parse_webforms_state(html: str) -> WebFormsState:
    """Extract __VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION from a WebForms page.

    All three tokens must be present — the search POST will be rejected without
    them — so a missing token is a parse error, not an empty-default.
    """
    soup = _soup(html)
    fields: dict[str, str] = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        el = soup.find("input", {"name": name})
        if el is None:
            raise ParentPayParseError(f"Missing WebForms hidden field: {name}")
        value = el.get("value")
        fields[name] = str(value) if value is not None else ""
    return WebFormsState(
        viewstate=fields["__VIEWSTATE"],
        viewstategenerator=fields["__VIEWSTATEGENERATOR"],
        eventvalidation=fields["__EVENTVALIDATION"],
    )
```

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/test_parsers.py -k webforms_state -v`
Expected: 3 passed.

- [ ] **Step 5: Run mypy**

Run: `mypy custom_components`
Expected: `Success: no issues found in N source files`

- [ ] **Step 6: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/parsers.py custom_components/parentpay/models.py tests/test_parsers.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(parsers): add parse_webforms_state for archive postback round-trip"
```

---

## Task 4: Remove `parse_home_recent_meals` and `HomeSnapshot.recent_meals`

**Files:**
- Modify: `custom_components/parentpay/parsers.py`
- Modify: `custom_components/parentpay/models.py`
- Modify: `custom_components/parentpay/client.py`
- Modify: `custom_components/parentpay/coordinator.py`
- Modify: `tests/test_parsers.py`
- Modify: `tests/test_client.py`

This is a deletion task — the v2 calendar gets its data from the archive store after backfill, so the home-page meal placeholder is no longer wanted.

- [ ] **Step 1: Remove the two home-recent-meals tests in `tests/test_parsers.py`**

Delete the entire functions:
- `test_parse_home_recent_meals_picks_up_prices_and_no_meal`
- `test_parse_home_recent_meals_uses_generic_item_label`

Also remove `parse_home_recent_meals` from the import block at the top.

- [ ] **Step 2: Remove the parser**

In `custom_components/parentpay/parsers.py`, delete the entire `parse_home_recent_meals` function (lines 91–132). Also remove the `_MEAL_DATE_RE` and `_MEAL_PRICE_RE` regexes near the top — verify with grep that nothing else uses them:

Run: `grep -nE '_MEAL_DATE_RE|_MEAL_PRICE_RE' custom_components/parentpay/`
Expected: no matches.

- [ ] **Step 3: Remove the model field**

In `custom_components/parentpay/models.py`, edit the `HomeSnapshot` dataclass:

Before:

```python
@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    """Result of a single GET against the ParentPay home page."""

    balances: list[Balance]
    recent_meals: list[ArchiveRow]
    recent_payments: list[ArchiveRow]
```

After:

```python
@dataclass(frozen=True, slots=True)
class HomeSnapshot:
    """Result of a single GET against the ParentPay home page."""

    balances: list[Balance]
    recent_payments: list[ArchiveRow]
```

- [ ] **Step 4: Update the client**

In `custom_components/parentpay/client.py`:

- Remove `parse_home_recent_meals` from the `from .parsers import (...)` block.
- Edit `fetch_home`:

Before:

```python
async def fetch_home(self) -> HomeSnapshot:
    """One-shot fetch: balances + recent meals + recent parent-account payments."""
    body = await self._authed_get(HOME_URL)
    return HomeSnapshot(
        balances=parse_home_balances(body),
        recent_meals=parse_home_recent_meals(body),
        recent_payments=parse_home_recent_payments(body),
    )
```

After:

```python
async def fetch_home(self) -> HomeSnapshot:
    """One-shot fetch: balances + recent parent-account payments.

    The home page also lists today's meal price as a generic placeholder, but
    v2 ignores that — meal data comes from the archive store (backfill plus
    ongoing GETs) so we always have real item names.
    """
    body = await self._authed_get(HOME_URL)
    return HomeSnapshot(
        balances=parse_home_balances(body),
        recent_payments=parse_home_recent_payments(body),
    )
```

- [ ] **Step 5: Update the coordinator**

In `custom_components/parentpay/coordinator.py`, edit `_async_update_data`:

Before:

```python
            await self.store.async_merge(home.recent_meals)
            await self.store.async_merge(enriched_payments)
            await self.store.async_merge(archive_rows)
```

After:

```python
            await self.store.async_merge(enriched_payments)
            await self.store.async_merge(archive_rows)
```

- [ ] **Step 6: Update `tests/test_client.py`**

In `test_fetch_home_returns_balances_meals_and_payments`, remove the assertion `assert isinstance(snapshot.recent_meals, list)` and rename the test to `test_fetch_home_returns_balances_and_payments`. Final body:

```python
async def test_fetch_home_returns_balances_and_payments(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(HOME_URL, status=200, body=_load_text("balances.html"))
        snapshot = await client.fetch_home()
    assert {b.child_id for b in snapshot.balances} == {"11111111", "22222222"}
    assert isinstance(snapshot.recent_payments, list)
```

- [ ] **Step 7: Update `tests/test_coordinator.py`**

In the `client` fixture, change:

```python
    c.fetch_home = AsyncMock(
        return_value=HomeSnapshot(balances=[], recent_meals=[], recent_payments=[])
    )
```

to:

```python
    c.fetch_home = AsyncMock(
        return_value=HomeSnapshot(balances=[], recent_payments=[])
    )
```

- [ ] **Step 8: Run the full suite**

Run: `pytest -q`
Expected: all green. Test count drops by 2 (removed home-meal tests).

- [ ] **Step 9: Run ruff and mypy**

Run: `ruff check custom_components tests && mypy custom_components`
Expected: both clean.

- [ ] **Step 10: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add -u
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "refactor(home): drop parse_home_recent_meals — archive store drives meals in v2"
```

---

## Task 5: Add `fetch_archive_range` client method

**Files:**
- Modify: `custom_components/parentpay/client.py`
- Modify: `tests/test_client.py`

- [ ] **Step 1: Write three failing tests in `tests/test_client.py`**

Add to the existing imports:

```python
from datetime import date

from yarl import URL
```

Append at the end of the file:

```python
def _archive_post_body(m: aioresponses) -> dict[str, str]:
    """Pull the form body out of the most recent POST against ARCHIVE_URL."""
    calls = m.requests.get(("POST", URL(ARCHIVE_URL))) or []
    assert calls, "expected at least one POST against the archive URL"
    data = calls[-1].kwargs.get("data")
    assert isinstance(data, dict), f"expected dict body, got {type(data)!r}"
    return data


async def test_fetch_archive_range_does_get_then_post(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(ARCHIVE_URL, status=200, body=_load_text("archive_initial.html"))
        m.post(ARCHIVE_URL, status=200, body=_load_text("archive_sample.html"))
        rows = await client.fetch_archive_range(
            date(2025, 4, 19), date(2026, 4, 19)
        )
        body = _archive_post_body(m)
    assert body["__EVENTTARGET"] == "ctl00$cmdSearch"
    assert body["__EVENTARGUMENT"] == ""
    assert body["__VIEWSTATE"] == "TESTVIEWSTATE_INITIAL"
    assert body["__VIEWSTATEGENERATOR"] == "TESTGEN_INITIAL"
    assert body["__EVENTVALIDATION"] == "TESTEV_INITIAL"
    assert body["ctl00$selChoosePupil"] == "0"
    assert body["ctl00$selChooseService"] == "0"
    assert body["ctl00$txtChooseStartDate"] == "19/04/2025"
    assert body["ctl00$txtChooseEndDate"] == "19/04/2026"
    # Returned rows come from parse_archive(archive_sample.html) — must be many
    assert len(rows) > 1000


async def test_fetch_archive_range_uses_ddmm_date_format(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(ARCHIVE_URL, status=200, body=_load_text("archive_initial.html"))
        m.post(ARCHIVE_URL, status=200, body=_load_text("archive_sample.html"))
        await client.fetch_archive_range(date(2026, 1, 5), date(2026, 12, 31))
        body = _archive_post_body(m)
    # DD/MM/YYYY — single-digit days/months are zero-padded
    assert body["ctl00$txtChooseStartDate"] == "05/01/2026"
    assert body["ctl00$txtChooseEndDate"] == "31/12/2026"


async def test_fetch_archive_range_returns_parsed_rows(
    http_session: aiohttp.ClientSession,
) -> None:
    client = ParentPayClient(http_session, username="u@example.com", password="pw")
    with aioresponses() as m:
        m.post(LOGIN_URL, status=200, payload=_load_json("login_success.json"))
        m.get(ARCHIVE_URL, status=200, body=_load_text("archive_initial.html"))
        m.post(ARCHIVE_URL, status=200, body=_load_text("archive_sample.html"))
        rows = await client.fetch_archive_range(
            date(2025, 4, 19), date(2026, 4, 19)
        )
    # Both child IDs and both payment_method values present
    assert {r.child_id for r in rows} == {"11111111", "22222222"}
    assert {r.payment_method for r in rows} >= {"Meal", "Parent Account"}
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_client.py -k fetch_archive_range -v`
Expected: 3 errors with `AttributeError: ... has no attribute 'fetch_archive_range'`.

- [ ] **Step 3: Implement `fetch_archive_range`**

In `custom_components/parentpay/client.py`:

Add `from datetime import date` to the top imports. Update the `from .parsers import (...)` block to include `parse_webforms_state`:

```python
from .parsers import (
    parse_archive,
    parse_home_balances,
    parse_home_recent_payments,
    parse_login_response,
    parse_payment_detail,
    parse_payment_items,
    parse_webforms_state,
)
```

Append the new method to the `ParentPayClient` class (after `fetch_archive`):

```python
    async def fetch_archive_range(
        self, start: date, end: date
    ) -> list[ArchiveRow]:
        """Fetch the archive grid filtered to a date range.

        Two HTTP calls: GET to scrape the WebForms hidden state, then POST
        with __EVENTTARGET=ctl00$cmdSearch and the date strings to trigger the
        server-side filter. Returns the rows parsed from the POST response.
        """
        get_body = await self._authed_get(ARCHIVE_URL)
        state = parse_webforms_state(get_body)
        post_body = {
            "__EVENTTARGET": "ctl00$cmdSearch",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": state.viewstate,
            "__VIEWSTATEGENERATOR": state.viewstategenerator,
            "__EVENTVALIDATION": state.eventvalidation,
            "ctl00$selChoosePupil": "0",
            "ctl00$selChooseService": "0",
            "ctl00$txtChooseStartDate": start.strftime("%d/%m/%Y"),
            "ctl00$txtChooseEndDate": end.strftime("%d/%m/%Y"),
        }
        body = await self._authed_post(ARCHIVE_URL, data=post_body)
        return parse_archive(body)
```

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/test_client.py -k fetch_archive_range -v`
Expected: 3 passed.

- [ ] **Step 5: Run mypy + ruff**

Run: `ruff check custom_components tests && mypy custom_components`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/client.py tests/test_client.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(client): add fetch_archive_range using cmdSearch WebForms postback"
```

---

## Task 6: Add new const keys + bump `STORE_VERSION` to 3 (with migration test)

**Files:**
- Modify: `custom_components/parentpay/const.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Edit `const.py`**

Replace the `# --- Store keys (versioned) ---` block at the bottom of the file with:

```python
# --- Store keys (versioned) ----------------------------------------------

STORE_KEY_MEALS: Final = "parentpay.meals_v1"
STORE_KEY_PURCHASES: Final = "parentpay.purchases_v1"
STORE_KEY_PAYMENT_DETAILS: Final = "parentpay.payment_details_v1"
STORE_KEY_DISMISSALS: Final = "parentpay.dismissals_v1"
STORE_KEY_BACKFILL: Final = "parentpay.backfill_v1"
STORE_VERSION: Final = 3  # v3: adds dismissals + backfill flag, drops home-page meal cache pollution
```

- [ ] **Step 2: Add a migration test**

Append to `tests/test_store.py`:

```python
async def test_store_v3_migration_wipes_v2_data(hass, hass_storage) -> None:
    """When STORE_VERSION jumps from 2 to 3, _MigratingStore wipes prior caches."""
    hass_storage["parentpay.meals_v1"] = {
        "version": 2,
        "minor_version": 1,
        "key": "parentpay.meals_v1",
        "data": [
            {
                "hash": "x",
                "child_id": "11111111",
                "date": "2026-01-01",
                "item": "OLD ROW",
                "amount_pence": 100,
            }
        ],
    }
    hass_storage["parentpay.purchases_v1"] = {
        "version": 2,
        "minor_version": 1,
        "key": "parentpay.purchases_v1",
        "data": [
            {
                "hash": "y",
                "child_id": "11111111",
                "date": "2026-01-01",
                "item": "OLD PURCHASE",
                "amount_pence": 200,
                "completed": False,
            }
        ],
    }
    hass_storage["parentpay.payment_details_v1"] = {
        "version": 2,
        "minor_version": 1,
        "key": "parentpay.payment_details_v1",
        "data": {"old-tid": {"tid": "old-tid", "child_id": "11111111"}},
    }

    store = ParentPayStore(hass)  # picks up STORE_VERSION = 3
    await store.async_load()

    assert store.meals == []
    assert store.purchases == []
    assert store.get_payment_detail("old-tid") is None
```

- [ ] **Step 3: Run the new test**

Run: `pytest tests/test_store.py::test_store_v3_migration_wipes_v2_data -v`
Expected: PASS.

- [ ] **Step 4: Run mypy**

Run: `mypy custom_components`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/const.py tests/test_store.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(const): add dismissals + backfill store keys, bump STORE_VERSION to 3"
```

---

## Task 7: Add dismissal store API

**Files:**
- Modify: `custom_components/parentpay/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Write three failing tests in `tests/test_store.py`**

Append:

```python
async def test_dismissal_round_trip(store: ParentPayStore) -> None:
    await store.async_load()
    assert store.is_dismissed("11111111", "9000001") is False
    await store.async_set_dismissed("11111111", "9000001", True)
    assert store.is_dismissed("11111111", "9000001") is True
    await store.async_set_dismissed("11111111", "9000001", False)
    assert store.is_dismissed("11111111", "9000001") is False


async def test_dismissal_distinguishes_children(store: ParentPayStore) -> None:
    await store.async_load()
    await store.async_set_dismissed("11111111", "9000001", True)
    assert store.is_dismissed("11111111", "9000001") is True
    assert store.is_dismissed("22222222", "9000001") is False


async def test_dismissal_count_per_child(store: ParentPayStore) -> None:
    await store.async_load()
    await store.async_set_dismissed("11111111", "9000001", True)
    await store.async_set_dismissed("11111111", "9000002", True)
    await store.async_set_dismissed("22222222", "9000003", True)
    counts = store.dismissal_count_per_child()
    assert counts == {"11111111": 2, "22222222": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_store.py -k dismissal -v`
Expected: errors with `AttributeError: ... no attribute 'is_dismissed'`.

- [ ] **Step 3: Implement the store API**

In `custom_components/parentpay/store.py`:

Update the imports:

```python
from .const import (
    STORE_KEY_BACKFILL,
    STORE_KEY_DISMISSALS,
    STORE_KEY_MEALS,
    STORE_KEY_PAYMENT_DETAILS,
    STORE_KEY_PURCHASES,
    STORE_VERSION,
)
```

In `ParentPayStore.__init__`, add after the existing `_payment_details_store` line:

```python
        self._dismissals_store: Store[dict[str, dict[str, Any]]] = _MigratingStore(
            hass, STORE_VERSION, STORE_KEY_DISMISSALS
        )
```

And add to the in-memory state init (after `self._payment_details: dict[str, dict[str, Any]] = {}`):

```python
        self._dismissals: dict[str, dict[str, Any]] = {}
```

Update `async_load`:

```python
    async def async_load(self) -> None:
        self._meals = (await self._meals_store.async_load()) or []
        self._purchases = (await self._purchases_store.async_load()) or []
        self._payment_details = (
            await self._payment_details_store.async_load()
        ) or {}
        self._dismissals = (await self._dismissals_store.async_load()) or {}
        self._loaded = True
```

Add the three new methods (place near `async_set_purchase_completed`):

```python
    @staticmethod
    def _dismissal_key(child_id: str, payment_item_id: str) -> str:
        return f"{child_id}:{payment_item_id}"

    def is_dismissed(self, child_id: str, payment_item_id: str) -> bool:
        return self._dismissal_key(child_id, payment_item_id) in self._dismissals

    async def async_set_dismissed(
        self, child_id: str, payment_item_id: str, dismissed: bool
    ) -> None:
        if not self._loaded:
            await self.async_load()
        key = self._dismissal_key(child_id, payment_item_id)
        if dismissed:
            from datetime import datetime, timezone
            self._dismissals[key] = {
                "child_id": child_id,
                "payment_item_id": payment_item_id,
                "dismissed_at": datetime.now(tz=timezone.utc).isoformat(),
            }
        else:
            self._dismissals.pop(key, None)
        await self._dismissals_store.async_save(self._dismissals)

    def dismissal_count_per_child(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._dismissals.values():
            cid = str(entry.get("child_id") or "")
            if not cid:
                continue
            counts[cid] = counts.get(cid, 0) + 1
        return counts
```

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/test_store.py -k dismissal -v`
Expected: 3 passed.

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check custom_components tests && mypy custom_components`
Expected: both clean. (The `from datetime import datetime, timezone` inside the method is intentional — keeps the method self-contained without needing top-level imports for this single use.)

- [ ] **Step 6: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/store.py tests/test_store.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(store): add dismissals API for available-to-purchase todo"
```

---

## Task 8: Add backfill flag store API

**Files:**
- Modify: `custom_components/parentpay/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Write two failing tests in `tests/test_store.py`**

Append:

```python
async def test_backfill_flag_starts_false(store: ParentPayStore) -> None:
    await store.async_load()
    assert store.backfill_done is False
    assert store.backfill_done_at is None


async def test_backfill_flag_round_trip(store: ParentPayStore) -> None:
    await store.async_load()
    await store.async_mark_backfill_done()
    assert store.backfill_done is True
    assert store.backfill_done_at is not None
    # Survives a reload
    await store.async_load()
    assert store.backfill_done is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_store.py -k backfill -v`
Expected: errors with `AttributeError: ... no attribute 'backfill_done'`.

- [ ] **Step 3: Extend the store**

In `custom_components/parentpay/store.py`:

Add after the `_dismissals_store` line in `__init__`:

```python
        self._backfill_store: Store[dict[str, Any]] = _MigratingStore(
            hass, STORE_VERSION, STORE_KEY_BACKFILL
        )
```

And add to the in-memory state init:

```python
        self._backfill: dict[str, Any] = {}
```

Update `async_load` to include the new store:

```python
    async def async_load(self) -> None:
        self._meals = (await self._meals_store.async_load()) or []
        self._purchases = (await self._purchases_store.async_load()) or []
        self._payment_details = (
            await self._payment_details_store.async_load()
        ) or {}
        self._dismissals = (await self._dismissals_store.async_load()) or {}
        self._backfill = (await self._backfill_store.async_load()) or {}
        self._loaded = True
```

Add the new properties + method (place near the bottom of the class):

```python
    @property
    def backfill_done(self) -> bool:
        return bool(self._backfill.get("done"))

    @property
    def backfill_done_at(self) -> str | None:
        value = self._backfill.get("done_at")
        return str(value) if value is not None else None

    async def async_mark_backfill_done(self) -> None:
        if not self._loaded:
            await self.async_load()
        from datetime import datetime, timezone
        self._backfill = {
            "done": True,
            "done_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        await self._backfill_store.async_save(self._backfill)
```

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/test_store.py -k backfill -v`
Expected: 2 passed.

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check custom_components tests && mypy custom_components`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/store.py tests/test_store.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(store): add backfill_done flag with timestamp"
```

---

## Task 9: Wire backfill into coordinator

**Files:**
- Modify: `custom_components/parentpay/coordinator.py`
- Modify: `tests/test_coordinator.py`

- [ ] **Step 1: Add `fetch_archive_range` to the mock client fixture in `tests/test_coordinator.py`**

In the `client` fixture, append `c.fetch_archive_range = AsyncMock(return_value=[])` so it reads:

```python
@pytest.fixture
def client() -> AsyncMock:
    c = AsyncMock()
    c.fetch_home = AsyncMock(
        return_value=HomeSnapshot(balances=[], recent_payments=[])
    )
    c.fetch_payment_items = AsyncMock(return_value=[])
    c.fetch_archive = AsyncMock(return_value=[])
    c.fetch_archive_range = AsyncMock(return_value=[])
    return c
```

- [ ] **Step 2: Write four failing tests in `tests/test_coordinator.py`**

Add to the existing imports:

```python
from datetime import timedelta

from custom_components.parentpay.exceptions import ParentPayError
from custom_components.parentpay.models import ArchiveRow
```

Append at the end of the file:

```python
async def test_first_poll_runs_backfill_and_marks_done(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    await coordinator._async_update_data()
    assert client.fetch_archive_range.await_count == 1
    assert coordinator.store.backfill_done is True


async def test_backfill_uses_today_minus_365_days(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    await coordinator._async_update_data()
    args, kwargs = client.fetch_archive_range.await_args
    start, end = args
    assert (end - start) == timedelta(days=365)


async def test_second_poll_skips_backfill_when_done(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    await coordinator._async_update_data()
    assert client.fetch_archive_range.await_count == 1
    await coordinator._async_update_data()
    assert client.fetch_archive_range.await_count == 1  # no second call


async def test_backfill_failure_leaves_flag_unset(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    client.fetch_archive_range.side_effect = ParentPayError("boom")
    await coordinator._async_update_data()
    # Normal poll still completed
    assert client.fetch_home.await_count == 1
    # Flag stays False so the next poll retries
    assert coordinator.store.backfill_done is False


async def test_backfill_zero_rows_still_marks_done(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    client.fetch_archive_range.return_value = []
    await coordinator._async_update_data()
    assert coordinator.store.backfill_done is True


async def test_backfill_merges_rows_into_store(
    coordinator: ParentPayCoordinator,
    client: AsyncMock,
) -> None:
    client.fetch_archive_range.return_value = [
        ArchiveRow(
            child_id="11111111",
            child_name="Alice",
            date_paid=date(2025, 9, 1),
            item="PIZZA SLICE",
            amount_pence=-200,
            payment_method="Meal",
            status=None,
            receipt_url=None,
        )
    ]
    await coordinator._async_update_data()
    assert any(m["item"] == "PIZZA SLICE" for m in coordinator.store.meals)
```

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `pytest tests/test_coordinator.py -k backfill -v`
Expected: 6 failures (no backfill code yet).

- [ ] **Step 4: Implement backfill in the coordinator**

In `custom_components/parentpay/coordinator.py`:

Update imports — change

```python
from datetime import date, time, timedelta
```

(it already has these, no change needed). Verify `ParentPayError` is imported (it is).

Edit `_async_update_data`. Replace the inside of the `try` block:

Before:

```python
        try:
            home = await self._client.fetch_home()
            items = await self._client.fetch_payment_items()
            archive_rows = await self._client.fetch_archive()

            enriched_payments = await self._enrich_recent_payments(
                home.recent_payments
            )

            # Merge meals from home page + archive into store; both tables produce
            # ArchiveRow instances, and the store dedups via row hash.
            await self.store.async_merge(enriched_payments)
            await self.store.async_merge(archive_rows)

            self._first_run_done = True
            return {
                "balances": home.balances,
                "items": items,
                "meals": self.store.meals,
                "purchases": self.store.purchases,
            }
        except ParentPayAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ParentPayError as err:
            raise UpdateFailed(str(err)) from err
```

After:

```python
        try:
            await self._maybe_run_backfill()

            home = await self._client.fetch_home()
            items = await self._client.fetch_payment_items()
            archive_rows = await self._client.fetch_archive()

            enriched_payments = await self._enrich_recent_payments(
                home.recent_payments
            )

            await self.store.async_merge(enriched_payments)
            await self.store.async_merge(archive_rows)

            self._first_run_done = True
            return {
                "balances": home.balances,
                "items": items,
                "meals": self.store.meals,
                "purchases": self.store.purchases,
            }
        except ParentPayAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ParentPayError as err:
            raise UpdateFailed(str(err)) from err

    async def _maybe_run_backfill(self) -> None:
        """Run the one-shot 12-month backfill if it hasn't succeeded yet.

        On failure, log a warning and leave the done flag unset so the next
        scheduled poll retries the whole sequence. No exponential backoff —
        the poll interval already throttles retries.
        """
        if self.store.backfill_done:
            return
        today = dt_util.now().date()
        start = today - timedelta(days=365)
        try:
            rows = await self._client.fetch_archive_range(start, today)
        except ParentPayError as err:
            _LOGGER.warning(
                "Archive backfill failed, will retry next poll: %s", err
            )
            return
        await self.store.async_merge(rows)
        await self.store.async_mark_backfill_done()
```

- [ ] **Step 5: Run all coordinator tests**

Run: `pytest tests/test_coordinator.py -v`
Expected: all green (existing + 6 new).

- [ ] **Step 6: Run ruff + mypy**

Run: `ruff check custom_components tests && mypy custom_components`
Expected: both clean.

- [ ] **Step 7: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/coordinator.py tests/test_coordinator.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(coordinator): one-shot 12-month backfill on first successful poll"
```

---

## Task 10: Add `available_to_purchase` translation strings

**Files:**
- Modify: `custom_components/parentpay/strings.json`
- Modify: `custom_components/parentpay/translations/en.json`

- [ ] **Step 1: Edit `strings.json`**

Add a top-level `entity` section before the closing `}`. Final content:

```json
{
  "config": {
    "step": {
      "user": {
        "title": "ParentPay",
        "data": {
          "username": "Email / username",
          "password": "Password"
        }
      },
      "reauth_confirm": {
        "title": "Re-enter ParentPay password",
        "data": {
          "password": "Password"
        }
      }
    },
    "error": {
      "invalid_auth": "Invalid username or password.",
      "cannot_connect": "Could not connect to ParentPay."
    },
    "abort": {
      "already_configured": "This account is already configured.",
      "reauth_successful": "Re-authentication was successful."
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "ParentPay options",
        "data": {
          "poll_interval_minutes": "Poll interval (minutes)",
          "poll_window_start": "Poll window start (HH:MM)",
          "poll_window_end": "Poll window end (HH:MM)",
          "purchases_list_depth": "Purchases list depth"
        }
      }
    }
  },
  "entity": {
    "todo": {
      "available_to_purchase": {
        "name": "Available to purchase"
      },
      "parent_purchases": {
        "name": "Parent purchases"
      }
    }
  }
}
```

- [ ] **Step 2: Mirror the same change into `translations/en.json`**

Use the same final content as step 1.

- [ ] **Step 3: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/strings.json custom_components/parentpay/translations/en.json
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "i18n(todo): add available_to_purchase entity translation"
```

---

## Task 11: Add `AvailableToPurchaseTodo` entity

**Files:**
- Modify: `custom_components/parentpay/todo.py`
- Modify: `tests/test_todo.py`

- [ ] **Step 1: Write four failing tests in `tests/test_todo.py`**

Update imports:

```python
from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.todo import TodoItem, TodoItemStatus

from custom_components.parentpay.models import PaymentItem
from custom_components.parentpay.todo import (
    AvailableToPurchaseTodo,
    PurchasesTodoList,
)
```

Append the new tests:

```python
def _payment_item(child_id: str, payment_item_id: str, name: str = "Item") -> PaymentItem:
    from decimal import Decimal
    return PaymentItem(
        child_id=child_id,
        child_name="Alice" if child_id == "11111111" else "Bob",
        payment_item_id=payment_item_id,
        name=name,
        price=Decimal("3.35"),
        availability=None,
        is_new=False,
    )


async def test_available_to_purchase_lists_undismissed_items_only() -> None:
    coordinator = MagicMock()
    coordinator.data = {
        "balances": [],
        "items": [
            _payment_item("11111111", "9000001", "Macbeth"),
            _payment_item("11111111", "9000002", "Calculator"),
            _payment_item("11111111", "9000003", "Locker"),
        ],
        "meals": [],
        "purchases": [],
    }
    coordinator.store = MagicMock()
    coordinator.store.is_dismissed = MagicMock(
        side_effect=lambda c, p: (c, p) == ("11111111", "9000002")
    )
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = AvailableToPurchaseTodo(coordinator, entry, "11111111")
    items = todo.todo_items
    assert items is not None
    summaries = [it.summary for it in items]
    assert summaries == ["Macbeth", "Locker"]
    assert all(it.status == TodoItemStatus.NEEDS_ACTION for it in items)


async def test_available_to_purchase_filters_by_child() -> None:
    coordinator = MagicMock()
    coordinator.data = {
        "balances": [],
        "items": [
            _payment_item("11111111", "9000001"),
            _payment_item("22222222", "9000002"),
        ],
        "meals": [],
        "purchases": [],
    }
    coordinator.store = MagicMock()
    coordinator.store.is_dismissed = MagicMock(return_value=False)
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = AvailableToPurchaseTodo(coordinator, entry, "11111111")
    items = todo.todo_items or []
    assert len(items) == 1
    assert items[0].uid == "9000001"


async def test_tick_dismisses_item() -> None:
    coordinator = MagicMock()
    coordinator.data = {"balances": [], "items": [], "meals": [], "purchases": []}
    coordinator.store = MagicMock()
    coordinator.store.async_set_dismissed = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = AvailableToPurchaseTodo(coordinator, entry, "11111111")
    await todo.async_update_todo_item(
        TodoItem(summary="X", uid="9000001", status=TodoItemStatus.COMPLETED)
    )
    coordinator.store.async_set_dismissed.assert_awaited_once_with(
        "11111111", "9000001", True
    )


async def test_untick_undismisses_item() -> None:
    coordinator = MagicMock()
    coordinator.data = {"balances": [], "items": [], "meals": [], "purchases": []}
    coordinator.store = MagicMock()
    coordinator.store.async_set_dismissed = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    entry = MagicMock()
    entry.entry_id = "abc"
    todo = AvailableToPurchaseTodo(coordinator, entry, "11111111")
    await todo.async_update_todo_item(
        TodoItem(summary="X", uid="9000001", status=TodoItemStatus.NEEDS_ACTION)
    )
    coordinator.store.async_set_dismissed.assert_awaited_once_with(
        "11111111", "9000001", False
    )
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `pytest tests/test_todo.py -k 'available_to_purchase or dismiss' -v`
Expected: import errors (`AvailableToPurchaseTodo` doesn't exist).

- [ ] **Step 3: Implement the entity + register it in setup**

Replace the entire contents of `custom_components/parentpay/todo.py` with:

```python
"""Todo platform: per-child parent purchases + available-to-purchase items."""
from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components.todo import (  # type: ignore[attr-defined]
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ParentPayCoordinator
from .models import PaymentItem


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: ParentPayCoordinator = hass.data[DOMAIN][entry.entry_id]
    purchase_child_ids = {r["child_id"] for r in coordinator.store.purchases}
    item_child_ids = {it.child_id for it in (coordinator.data.get("items") or [])}
    entities: list[TodoListEntity] = []
    for child_id in sorted(purchase_child_ids):
        entities.append(PurchasesTodoList(coordinator, entry, child_id))
    for child_id in sorted(item_child_ids):
        entities.append(AvailableToPurchaseTodo(coordinator, entry, child_id))
    async_add_entities(entities)


def _child_name_for(coordinator: ParentPayCoordinator, child_id: str) -> str:
    for b in coordinator.data.get("balances", []) or []:
        if b.child_id == child_id:
            return str(b.child_name)
    for it in coordinator.data.get("items", []) or []:
        if it.child_id == child_id:
            return str(it.child_name)
    return child_id


def _device_info(entry: ConfigEntry, child_id: str, name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{child_id}")},
        manufacturer=MANUFACTURER,
        name=name,
    )


class PurchasesTodoList(CoordinatorEntity[ParentPayCoordinator], TodoListEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "parent_purchases"
    _attr_name = "Parent purchases"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(
        self,
        coordinator: ParentPayCoordinator,
        entry: ConfigEntry,
        child_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._child_id = child_id
        self._attr_unique_id = f"{entry.entry_id}_{child_id}_parent_purchases"
        self._attr_device_info = _device_info(
            entry, child_id, _child_name_for(coordinator, child_id)
        )

    @property
    def todo_items(self) -> list[TodoItem] | None:
        rows = self.coordinator.purchases_for_child(self._child_id)
        return [
            TodoItem(
                summary=row["item"],
                uid=row["hash"],
                status=(
                    TodoItemStatus.COMPLETED
                    if row.get("completed")
                    else TodoItemStatus.NEEDS_ACTION
                ),
                due=date.fromisoformat(row["date"]),
                description=_purchase_description(row),
            )
            for row in rows
        ]

    async def async_update_todo_item(self, item: TodoItem) -> None:
        completed = item.status == TodoItemStatus.COMPLETED
        await self.coordinator.store.async_set_purchase_completed(
            item.uid or "", completed
        )
        await self.coordinator.async_request_refresh()


class AvailableToPurchaseTodo(CoordinatorEntity[ParentPayCoordinator], TodoListEntity):
    """Per-child todo of payment items not yet dismissed by the user."""

    _attr_has_entity_name = True
    _attr_translation_key = "available_to_purchase"
    _attr_name = "Available to purchase"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(
        self,
        coordinator: ParentPayCoordinator,
        entry: ConfigEntry,
        child_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._child_id = child_id
        self._attr_unique_id = (
            f"{entry.entry_id}_{child_id}_available_to_purchase"
        )
        self._attr_device_info = _device_info(
            entry, child_id, _child_name_for(coordinator, child_id)
        )

    @property
    def todo_items(self) -> list[TodoItem] | None:
        items: list[PaymentItem] = self.coordinator.data.get("items") or []
        out: list[TodoItem] = []
        for it in items:
            if it.child_id != self._child_id:
                continue
            if self.coordinator.store.is_dismissed(it.child_id, it.payment_item_id):
                continue
            out.append(
                TodoItem(
                    summary=it.name,
                    uid=it.payment_item_id,
                    status=TodoItemStatus.NEEDS_ACTION,
                    description=_available_description(it),
                )
            )
        return out

    async def async_update_todo_item(self, item: TodoItem) -> None:
        dismissed = item.status == TodoItemStatus.COMPLETED
        await self.coordinator.store.async_set_dismissed(
            self._child_id, item.uid or "", dismissed
        )
        await self.coordinator.async_request_refresh()


def _purchase_description(row: dict[str, Any]) -> str:
    amount = int(row.get("amount_pence", 0)) / 100
    parts = [f"\u00a3{amount:.2f}"]
    if row.get("receipt_url"):
        parts.append(row["receipt_url"])
    return " \u00b7 ".join(parts)


def _available_description(it: PaymentItem) -> str:
    parts = [f"\u00a3{float(it.price):.2f}"]
    if it.availability:
        parts.append(it.availability)
    if it.is_new:
        parts.append("New!")
    return " \u00b7 ".join(parts)
```

- [ ] **Step 4: Run all todo tests**

Run: `pytest tests/test_todo.py -v`
Expected: all green (existing + 4 new).

- [ ] **Step 5: Run ruff + mypy**

Run: `ruff check custom_components tests && mypy custom_components`
Expected: both clean.

- [ ] **Step 6: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/todo.py tests/test_todo.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(todo): add per-child available-to-purchase entity with dismissals"
```

---

## Task 12: Update diagnostics

**Files:**
- Modify: `custom_components/parentpay/diagnostics.py`

- [ ] **Step 1: Replace the file contents**

```python
"""Diagnostics for ParentPay — redacts sensitive fields."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .coordinator import ParentPayCoordinator

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD, "child_id", "receipt_url"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: ParentPayCoordinator = hass.data[DOMAIN][entry.entry_id]
    store = coordinator.store
    return {
        "entry": async_redact_data(
            {"data": entry.data, "options": entry.options}, TO_REDACT
        ),
        "store": {
            "meals_count": len(store.meals),
            "purchases_count": len(store.purchases),
            "backfill_done": store.backfill_done,
            "backfill_done_at": store.backfill_done_at,
            "dismissal_count_per_child": store.dismissal_count_per_child(),
        },
    }
```

- [ ] **Step 2: Run mypy**

Run: `mypy custom_components`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/diagnostics.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "feat(diagnostics): expose backfill state + dismissal counts"
```

---

## Task 13: Add `--backfill` flag to live test script

**Files:**
- Modify: `scripts/live_test.py`

- [ ] **Step 1: Replace the file contents**

```python
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
        print(f"Archive rows (GET, recent): {len(rows)}")
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
```

- [ ] **Step 2: Sanity-check that the script still parses**

Run: `python -c "import ast; ast.parse(open('scripts/live_test.py').read())"`
Expected: no output (success).

- [ ] **Step 3: Run ruff**

Run: `ruff check scripts`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add scripts/live_test.py
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "test(live): add --backfill flag to exercise the 12-month archive POST"
```

---

## Task 14: Update README + CLAUDE.md for v2

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update `README.md`**

Replace the entire `## Features` and `## How it works` sections with:

```markdown
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
```

Delete the entire `### Roadmap` section.

- [ ] **Step 2: Update `CLAUDE.md`**

Find and replace these specific sections:

In the `### Data flow per poll` section, change the opening sentence from:

> Three GETs: home page (173 KB — balances + recent meals + recent payments in one fetch), payment items page, archive page (GET-only returns ~last 8 rows).

to:

> Three GETs (home page for balances + recent payments, payment items page, archive page for ~last 8 rows) plus a one-shot backfill POST against the archive page on the first successful poll. The backfill exercises `__EVENTTARGET=ctl00$cmdSearch` with a 12-month date range to pull all historical rows in a single round-trip; the success flag is persisted in `parentpay.backfill_v1`.

In the `### Domain invariants` section, **delete** the entire bullet:

> - Home-page meal rows carry only a **price**, not the food name — parser emits `"School meal"` / `"No meal"`. Real food names like `"PIZZA SLICE"` come from the archive GET only and are deduped into the same store.

In the `### v1 scope — archive is GET-only` section, replace the entire section with:

```markdown
### Archive backfill — `cmdSearch` POST, not the calendar postback

`MS_Archive.aspx` is a plain ASP.NET WebForms search form. The CLAUDE.md v1 hint about a `__EVENTTARGET=ctl00$calChooseStartDate` calendar postback was wrong — the actual mechanic is much simpler:

1. GET `MS_Archive.aspx` → parse `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION` via `parsers.parse_webforms_state`.
2. POST `MS_Archive.aspx` with `__EVENTTARGET=ctl00$cmdSearch`, the three state tokens echoed back, `ctl00$selChoosePupil=0` ("All"), `ctl00$selChooseService=0` ("All payment items"), and `ctl00$txtChooseStartDate` / `ctl00$txtChooseEndDate` in `DD/MM/YYYY` format.
3. Pass the response through `parse_archive` — same parser used for the GET-only "recent" path.

`tests/fixtures/archive_initial.html` is the GET response; `tests/fixtures/archive_sample.html` is the POST response (1988 rows across two children). Both are scrubbed; state-token values were replaced with deterministic placeholders (`TESTVIEWSTATE_INITIAL`, etc.) so the round-trip tests can assert the POST body echoes them back.

The coordinator runs the backfill on every poll until it succeeds, then never again (flag stored in `parentpay.backfill_v1`). On failure it logs at WARNING and continues with the normal poll.
```

In the `### Store versioning + migration` section, leave the bullet about `_MigratingStore` policy as-is (it still applies). The only versioned reference inside that section is the prose around `STORE_VERSION` — no embedded `STORE_VERSION: Final = 2` literal lives in CLAUDE.md, so nothing further to edit there.

Verify the whole file no longer references any v1-or-v2-only behaviour:

Run: `grep -nE 'School meal|No meal|calChooseStartDate|v1 scope' CLAUDE.md`
Expected: no matches.

- [ ] **Step 3: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add README.md CLAUDE.md
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "docs: update README + CLAUDE.md for v2 (backfill + available-to-purchase)"
```

---

## Task 15: Bump manifest to v2.0

**Files:**
- Modify: `custom_components/parentpay/manifest.json`

- [ ] **Step 1: Edit `manifest.json`**

Change `"version": "1.2"` to `"version": "2.0"`.

- [ ] **Step 2: Commit**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay add custom_components/parentpay/manifest.json
git -C /Users/rob.emmerson/git/ha-parentpay commit -m "chore: bump manifest version to 2.0"
```

---

## Task 16: Final quality gate + tag

**Files:**
- (none — verification + tagging only)

- [ ] **Step 1: Run the full quality gate**

Run: `pytest -q && ruff check custom_components tests && mypy custom_components`
Expected: pytest summary shows all green; ruff and mypy print no findings.

- [ ] **Step 2: Verify the commit log**

Run: `git -C /Users/rob.emmerson/git/ha-parentpay log --oneline main..HEAD`
Expected: ~14 v2 commits in chronological order.

- [ ] **Step 3: (User-action) Tag and push**

```bash
git -C /Users/rob.emmerson/git/ha-parentpay tag -s v2.0 -m "ParentPay v2.0 — meal history backfill + available-to-purchase todo"
git -C /Users/rob.emmerson/git/ha-parentpay push
git -C /Users/rob.emmerson/git/ha-parentpay push --tags
```

This step is a **user action** — do not run it from the implementation subagent. The user will tag and push after manual smoke-testing in HA.
