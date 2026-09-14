# AutoTrader Config Test Suite

## Purpose

Unit tests for what the live pipeline resolves BEFORE a session starts — the settings that
decide whether real orders are sent and what they are expected to cost. Everything here runs
offline with a stubbed fetcher; nothing reaches a venue.

## What Is Tested

### `test_dry_run_resolution.py`

How `dry_run` is resolved across the profile, the broker entry and the user override. The near
miss this pins: a profile declared `dry_run: true`, the override said `false`, and the profile
field — declared, documented and parsed — was read by nothing. A session would have sent real
orders on a funded account with no crash and no warning (#304).

### `test_profile_loadability.py`

Every shipped AutoTrader profile loads. A profile that cannot be constructed is a session that
fails at startup, and the operator finds out at the moment they wanted to trade.

### `test_fee_tier_application.py`

`_apply_fee_tier()` — the bridge between the declared rate and the account's real one (#337).
It has TWO jobs, and the second runs even when the first is switched off:

- **APPLY** — with `auto_detect_fee_tier: true`, the venue's rates replace the declared ones for
  the session.
- **REPORT** — a divergence WARNS either way. Measured 2026-09-08, the declared rates were half
  the account's real tier and nothing said so; the warning is what turns that from invisible
  into a line in the session channel (§35).

| Test | Description |
|------|-------------|
| `test_with_the_switch_on_the_session_prices_with_the_venue_s_rates` | Opt-in → the fetched tier reaches `fee_structure` |
| `test_with_the_switch_off_the_declared_rates_stand` | Default → the config's rates are untouched |
| `test_a_venue_without_tiers_changes_nothing` | Fetcher returns `None` → no change, no warning |
| `test_a_divergence_warns_even_with_the_switch_off` | The report half does not depend on the apply half |
| `test_a_divergence_warns_with_the_switch_on_too` | Applying is not a reason to stay quiet |
| `test_agreement_is_quiet` | Rates that match produce no warning |

### `test_startup_guards.py` (#503, finding 205)

`setup_pipeline` is the one path every live session is obliged to walk, and until 2026-09-10 no
test imported it — so every one of its abort conditions was unverified, including the three that
predate this file. §35 puts pre-run problems in the ABORT class precisely because a session that
starts wrong cannot be corrected later: it trades, or refuses to trade, for as long as nobody is
watching.

The tests drive real shipped profiles through the real loader into the real `setup_pipeline` and
assert what it REFUSES, which is reachable before the heavy construction.

| Test | What it verifies |
|---|---|
| `test_a_live_session_refuses_to_start` | A profile-wide `venue_held_protection` opt-in against a venue that cannot carry one aborts the session. Left to the submit path it would reject every protected entry one at a time, and each would read as an isolated incident rather than one wrong line in the profile |
| `test_the_refusal_names_both_sides` | The message names the profile switch AND the broker, and says the per-order route is refused the same way |
| `test_a_mock_rehearsal_is_not_stopped` | The refusal is LIVE-only. A mock session builds a `MockBrokerAdapter` whatever its `broker_type` says, so it can never carry a protective order — refusing there would make an opted-in profile unrehearsable, the same mistake the simulation avoids by accepting the flag and changing nothing. It warns once and ignores the switch for the run |
| `test_the_switch_is_off_by_default` | A profile that never mentions it starts against any venue. Opting in changes what the bot does with real money, so a session must never acquire the behaviour by accident |
| `test_a_profile_that_resolves_no_balances_refuses` | One of the older guards, covered for the first time |
| `test_a_resting_logic_without_a_cold_start_hook_refuses` · `test_and_the_same_profile_starts_with_it` | #493's guard, proven at its WIRING rather than at its rule — the rule has its own suite in `tests/autotrader/cold_start/`; what was never executed is that `setup_pipeline` asks it at all. A bot whose logic can leave an order resting must answer for finding one there after a 03:00 restart, and the boot is the only place that refusal is still cheap |

## Why This Matters

These three files cover the two questions asked before any money moves: *will this session send
real orders*, and *what does it think a trade costs*. Both were answered wrongly at some point in
this project's history, and neither failure was visible from the outside — one profile field was
read by nobody, one rate was half the truth for an unknown length of time.

## Running the Tests

```bash
pytest tests/autotrader/config/ -v --tb=short
```

VS Code: **"🧩 Pytest: AutoTrader Config (All)"** launch configuration.
