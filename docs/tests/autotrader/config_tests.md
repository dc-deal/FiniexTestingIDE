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

## Why This Matters

These three files cover the two questions asked before any money moves: *will this session send
real orders*, and *what does it think a trade costs*. Both were answered wrongly at some point in
this project's history, and neither failure was visible from the outside — one profile field was
read by nobody, one rate was half the truth for an unknown length of time.

## Running

```bash
pytest tests/autotrader/config/ -v --tb=short
```

VS Code: **"🧩 Pytest: AutoTrader Config (All)"** launch configuration.
