# Swap Cost Accrual Tests

`tests/simulation/swap_cost/` — end-to-end overnight-swap accrual (#365) in the
PortfolioManager: a real MT5 broker config + a controllable canonical clock, opening a
position and advancing the clock across 17:00-NY rollovers, then asserting the signed
swap on the position.

See the model: [Swap / Overnight-Funding Cost Model](../../trading_realism/swap_cost_model.md).

## test_swap_accrual.py (8 tests)

| Test | Description |
|------|-------------|
| `test_long_debit_two_nights` | LONG EURUSD (`swap_long = −7.85`), Mon→Wed = 2 nights → **+15.70** cost (debit) |
| `test_short_credit_triple_wednesday` | SHORT EURUSD (`swap_short = +3.80`), Tue→Thu over the Wednesday triple = 4 swap-days → **−15.20** cost (credit) |
| `test_spot_mode_accrues_no_swap` | spot mode → accrual gated off, swap stays 0 |
| `test_accrual_is_deterministic_and_idempotent` | same inputs twice → identical swap; a second accrual at the same clock adds nothing (no double-count) |

### `TestSwapModeImplemented` — the #407 contract

The `SwapMode.is_implemented` property + `SwapModeNotImplementedError` that both pipelines rely on
(sim validator marks the scenario invalid, AutoTrader startup raises). See the
[batch-validation tests](../framework/batch_validations_tests.md) for the sim-side
`validate_swap_modes` cases.

| Test | Description |
|------|-------------|
| `test_points_and_none_are_implemented` | `POINTS` and `NONE` are modeled (validation passes) |
| `test_other_modes_not_implemented` | `INTEREST_CURRENT` / `INTEREST_OPEN` / `PERCENTAGE` / `UNKNOWN` are not modeled |
| `test_exception_names_symbol_and_mode` | the error names the symbol + the offending mode |
| `test_exception_is_finiex_and_value_error` | multiple inheritance (§10) — catchable as `ValueError` |
