# Swap Cost Accrual Tests

`tests/simulation/swap_cost/` — end-to-end overnight-swap accrual (#365) in the
PortfolioManager: a real MT5 broker config + a controllable canonical clock, opening a
position and advancing the clock across 17:00-NY rollovers, then asserting the signed
swap on the position.

See the model: [Swap / Overnight-Funding Cost Model](../../trading_realism/swap_cost_model.md).

## test_swap_accrual.py (4 tests)

| Test | Description |
|------|-------------|
| `test_long_debit_two_nights` | LONG EURUSD (`swap_long = −7.85`), Mon→Wed = 2 nights → **+15.70** cost (debit) |
| `test_short_credit_triple_wednesday` | SHORT EURUSD (`swap_short = +3.80`), Tue→Thu over the Wednesday triple = 4 swap-days → **−15.20** cost (credit) |
| `test_spot_mode_accrues_no_swap` | spot mode → accrual gated off, swap stays 0 |
| `test_accrual_is_deterministic_and_idempotent` | same inputs twice → identical swap; a second accrual at the same clock adds nothing (no double-count) |
