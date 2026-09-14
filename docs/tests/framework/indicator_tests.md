# Indicator Tests

`tests/framework/indicators/` — the indicator library
(`python/framework/utils/trading_math/indicators/`). Runs under the `framework/indicators`
suite.

## What this suite is for

An indicator that exists twice can disagree with itself, and this project's did. Before the
library there were two RSIs, two ATRs and two EMAs, and the two EMAs seeded differently and
returned different numbers under one name. Two of the indicators also carried an established
name while computing something else: the ATR smoothed with an EMA where Wilder's definition
uses the RMA, and the RSI averaged gains and losses arithmetically, which is Cutler's RSI.

So this suite pins three things the callers cannot: what each name means, that the two forms
of each indicator agree, and how much history each one needs.

## What is NOT here

Worker behaviour. Whether a worker requests the right window, gates its optional outputs or
recomputes on the right basis belongs to `tests/framework/worker_tests/`. This suite sees
arrays and frames, never a `Bar`, a tick or a config.

## The collapse that hid the deviations

Worth stating once, because it explains why a green suite proved nothing before:

> An EMA or an RMA over a window of exactly `period` values has nothing left to recurse over
> and returns its own seed — which is the SMA. So `ma_type: ema` did nothing, and Wilder's
> RSI was arithmetically identical to Cutler's.

Every test that claims two smoothings differ therefore has to supply real history, and
`moving_average_warmup_bars` is what says how much.

## `test_moving_averages.py` — SMA, EMA, RMA and the selector

| Test | Description |
|------|-------------|
| `test_it_averages_the_trailing_window` | SMA over the last `period` values |
| `test_history_before_the_window_changes_nothing` | the SMA has no memory |
| `test_a_window_shorter_than_the_period_is_averaged_whole` | thin warmup degrades, never raises |
| `test_it_matches_the_definition_by_hand` | EMA on a hand-derived case |
| `test_a_flat_series_stays_at_its_level` | a level series smooths to its level |
| `test_a_window_of_exactly_the_period_collapses_into_the_sma` | the collapse, pinned for EMA and RMA |
| `test_on_a_straight_line_it_equals_the_trailing_sma` | both averages lag a constant slope by `(period-1)/2`, so a linear series cannot separate them — the trap that made two earlier attempts at a separation test pass for the wrong reason |
| `test_a_curve_separates_it_from_the_sma` | a step does separate them |
| `test_it_matches_the_recurrence_by_hand` | RMA against its own recurrence |
| `test_it_reacts_more_slowly_than_the_ema` | the defining difference between RMA and EMA |
| `test_it_matches_the_hand_calculation` | four hand-calculated EMA references, carried over from the MACD worker's suite when the EMA moved here |
| `test_the_sma_needs_exactly_its_period` | warmup contract, SMA |
| `test_the_recursive_averages_need_a_multiple` | warmup contract, EMA and RMA |
| `test_the_warmup_leaves_the_seed_under_two_percent` | measures the claim behind the factors 3 and 5 rather than trusting it |
| `test_it_dispatches_to_the_named_average` | the selector adds nothing of its own |
| `test_the_series_form_dispatches_too` | the selector routes the bulk form the same way |
| `test_rows_before_the_seed_are_not_a_number` | a series form says when it cannot answer |
| `test_a_series_too_short_to_seed_is_entirely_nan` | too little history is all-NaN, never a partial guess |

## `test_true_range_and_atr.py`

| Test | Description |
|------|-------------|
| `test_an_inside_bar_is_its_own_span` | no gap to account for |
| `test_a_gap_up_is_measured_from_the_previous_close` | the reason true range exists |
| `test_a_gap_down_is_measured_from_the_previous_close` | the same, downward |
| `test_the_first_bar_of_a_series_is_its_own_span` | no predecessor, so no gap |
| `test_a_constant_range_smooths_to_itself` | any average of a constant is that constant |
| `test_an_empty_window_is_zero_rather_than_an_error` | boundary |
| `test_the_default_is_wilders_smoothing` | the bare name means Wilder |
| `test_the_ema_variant_is_reachable_and_different` | the variant stays available and named |
| `test_the_ema_variant_reacts_harder_to_a_spike` | the concrete consequence: a volatility split cuts elsewhere |
| `test_rows_before_the_seed_are_not_a_number` | series form |

## `test_rsi.py`

| Test | Description |
|------|-------------|
| `test_a_series_that_only_rises_is_fully_overbought` | upper bound |
| `test_a_flat_series_has_no_losses_to_divide_by` | `avg_loss == 0` → 100 |
| `test_mirroring_a_series_mirrors_the_oscillator` | reflecting the path negates every delta, so the two readings sum to 100 — exact, where a hand-built balanced series is not |
| `test_gains_matching_losses_sit_near_the_midpoint` | the approximate form of the same idea |
| `test_it_reports_the_two_averages_it_is_built_from` | `avg_gain` / `avg_loss` |
| `test_a_window_with_no_movement_to_measure_is_neutral` | boundary, unreachable through a worker |
| `test_the_default_is_wilders_smoothing` | the bare name means Wilder |
| `test_cutlers_variant_is_reachable_and_different` | the deviation is named, not hidden |
| `test_exactly_one_period_of_deltas_makes_wilder_into_cutlers` | the mechanism behind the old deviation |
| `test_enough_history_separates_them_again` | and what undoes it |
| `test_it_stays_inside_the_oscillator_bounds` | series form stays in 0-100 |

## `test_standard_deviation.py`

| Test | Description |
|------|-------------|
| `test_a_flat_window_has_no_dispersion` | zero |
| `test_it_ignores_history_before_the_window` | trailing window only |
| `test_it_is_the_population_deviation` | ddof=0, the charting convention |
| `test_it_is_the_population_deviation_too` | the series form agrees on ddof — a split here would widen every band |
| `test_rows_before_the_window_is_full_are_not_a_number` | series form |

## `test_bollinger_bands.py`

| Test | Description |
|------|-------------|
| `test_both_bands_sit_the_same_distance_out` | symmetry |
| `test_the_distance_is_the_deviation_multiple` | the band is the dispersion times the multiplier |
| `test_a_wider_deviation_widens_the_band_proportionally` | scaling |
| `test_a_flat_window_collapses_the_band_onto_the_midline` | zero dispersion |
| `test_the_default_midline_is_the_simple_average` | Bollinger's own choice |
| `test_an_exponential_midline_differs_given_enough_history` | with the warmup the option means something |
| `test_the_dispersion_is_the_same_whichever_midline_is_chosen` | the band measures the window, not the midline |
| `test_it_carries_all_four_columns` | series form shape |

## `test_macd.py`

| Test | Description |
|------|-------------|
| `test_the_histogram_is_the_gap_between_the_two_lines` | identity |
| `test_the_macd_line_is_the_gap_between_the_two_averages` | identity |
| `test_a_flat_series_has_nothing_to_diverge` | all three at zero |
| `test_it_stays_inside_the_range_of_the_line_it_averages` | the discriminating test: a signal line seeded on structurally-zero MACD values lands OUTSIDE the range of the values it is supposed to average |
| `test_a_trending_series_keeps_the_signal_on_one_side_of_zero` | the blunt form of the same thing |
| `test_the_line_begins_where_the_slow_average_is_seeded` | series form |
| `test_the_signal_begins_a_signal_period_after_the_line` | series form |
| `test_the_histogram_identity_holds_row_by_row` | the identity holds for every row, not only the newest |

`MacdWorker` does not use this unit yet: it carries its own signal-line construction, which
has exactly the defect the discriminating test above rejects. Switching it over moves MACD
output in every backtest, so it is its own decision.

## `test_on_balance_volume.py`

| Test | Description |
|------|-------------|
| `test_a_rising_close_adds_its_volume` | sign from the close |
| `test_a_falling_close_subtracts_its_volume` | the same, downward |
| `test_an_unchanged_close_leaves_the_total_alone` | flat bars do not move it |
| `test_the_first_bar_has_no_predecessor_and_no_effect` | its volume can never be signed |
| `test_a_window_too_short_to_compare_is_zero` | boundary |
| `test_it_starts_at_zero` | the level carries no meaning, only the direction |
| `test_it_accumulates_to_the_same_total` | the series form reaches the scalar form's total |

## `test_scalar_series_parity.py` — the promise that keeps one definition one

Every indicator exists twice: a scalar form for the tick loop and a series form for the
analysis plane. That is not a convenience — forcing either onto the other puts pandas inside
the per-tick path or a Python loop across hundreds of thousands of rows.

Nothing else in the suite compares them: the worker tests exercise the scalar form and the
discovery code the series form, so each is otherwise only checked against itself. Every
indicator is compared at its newest value across the parameter combinations that are
realistic for it.

The tests are named for the indicator they compare (`test_moving_averages`, `test_atr`,
`test_rsi`, `test_standard_deviation`, `test_bollinger_bands`, `test_macd`,
`test_on_balance_volume`), each parametrized over the periods, smoothings and deviations that
are realistic for it.

Agreement is to floating-point tolerance rather than bit-for-bit. The two forms accumulate in
a different order — a Python recursion against pandas' own — and demanding identical last
bits would pin the accumulation order instead of the definition.
