"""
The figure catalog (#557) — one declaration per figure of the run-results ledger.

How each column combines when several rows are read as one, what its empty cell means, which column
a companion follows and which count weights a mean — declared, not remembered. These were three
maps beside the ledger's column list, each held to the others by a test; one entry per figure now
says all of it, and the maps a reader asks one question of are derived from it below.

`deployment_history_builder` once carried the reasoning for ONE of these in a comment ("max(),
never sum(): each live row carries the RUNNING decline against the inherited peak"), and nothing
said it for the other fifty-six. A reader combining rows has to know per column, and the dangerous
pairs look identical: `net_pnl` and `win_rate` are both aggregates, one sums and the other cannot
be combined at all (CLAUDE.md §48).

A test holds the catalog and `LEDGER_COLUMNS` to the same key set, so a column cannot be added
without an answer, and another holds every nullable figure of the typed row to a declared
`null_meaning`, both ways.

The weighted means add in a loop, as they always have (`Addition.SEQUENTIAL`); #657 decides whether
they move to the compensated sum the rest of the fold uses. The rates' derivations and the rest of
what the fold needs for the ledger are in `tables/ledger_figure_table.py`.
"""

from typing import Dict

from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    NullMeaning,
    Reduction,
)

LEDGER_FIGURES: Dict[str, FigureDeclaration] = {
    # --- identity, provenance: must agree across the rows, or they are not comparable
    'logic_version': FigureDeclaration(Reduction.IDENTITY),
    'param_hash': FigureDeclaration(Reduction.IDENTITY),
    'status': FigureDeclaration(Reduction.IDENTITY),
    'error': FigureDeclaration(Reduction.IDENTITY),
    'run_id': FigureDeclaration(Reduction.IDENTITY),
    'sweep_id': FigureDeclaration(Reduction.IDENTITY),
    'sweep_params': FigureDeclaration(Reduction.IDENTITY),
    'sweep_objective': FigureDeclaration(Reduction.IDENTITY),
    'sweep_maximize': FigureDeclaration(Reduction.IDENTITY),
    'scenario_set_name': FigureDeclaration(Reduction.IDENTITY),
    'app_version': FigureDeclaration(Reduction.IDENTITY),
    'git_commit': FigureDeclaration(Reduction.IDENTITY),
    'git_branch': FigureDeclaration(Reduction.IDENTITY),
    'git_dirty': FigureDeclaration(Reduction.IDENTITY),
    'decision_logic_type': FigureDeclaration(Reduction.IDENTITY),
    'decision_version': FigureDeclaration(Reduction.IDENTITY),
    'worker_versions': FigureDeclaration(Reduction.IDENTITY),
    'strategy_config_json': FigureDeclaration(Reduction.IDENTITY),
    'data_broker_type': FigureDeclaration(Reduction.IDENTITY),
    'currency': FigureDeclaration(Reduction.IDENTITY),
    'input_plane': FigureDeclaration(Reduction.IDENTITY),
    'deployment_id': FigureDeclaration(Reduction.IDENTITY),
    'bot_id': FigureDeclaration(Reduction.IDENTITY),
    'profile_hash': FigureDeclaration(Reduction.IDENTITY),
    'run_type': FigureDeclaration(Reduction.IDENTITY),
    # One answer per run. Across a deployment's runs a disagreement is exactly the case worth
    # reporting: simulated and real money in one history.
    'orders_to': FigureDeclaration(Reduction.IDENTITY),
    # One configuration per run, on each of its rows. Across a deployment's sessions a
    # disagreement says the configuration moved between them — reported, as `profile_hash` is.
    'rendered_config_id': FigureDeclaration(Reduction.IDENTITY),
    'broker_config_id': FigureDeclaration(Reduction.IDENTITY),
    # One stand per session, on each of its rows. Across a deployment's sessions a disagreement
    # says the code moved between them.
    'code_stand': FigureDeclaration(Reduction.IDENTITY),
    # One run's outcome and counts, repeated on each of its rows — so they agree within a run.
    # Across runs a combined row has no single outcome, and summing counts that every period row
    # repeats would multiply them.
    'run_outcome': FigureDeclaration(Reduction.IDENTITY),
    'error_count': FigureDeclaration(Reduction.IDENTITY),
    'warning_count': FigureDeclaration(Reduction.IDENTITY),
    'log_warning_count': FigureDeclaration(Reduction.IDENTITY),
    'tick_timespan_seconds': FigureDeclaration(Reduction.IDENTITY),
    # Every row of one sweep was selected from the same search, so the rows agree by
    # construction. Across sweeps the figure is not combinable at all — two searches of 500 are
    # not a search of 1000, and adding them would claim a selection nobody performed.
    'trial_count': FigureDeclaration(Reduction.IDENTITY),
    # Counts of what ONE run read. Identical across that run's currency rows; across RUNS they
    # are not additive, because a file two sessions both read would be counted twice.
    'input_files': FigureDeclaration(Reduction.IDENTITY),
    'unstamped_input_files': FigureDeclaration(Reduction.IDENTITY),

    # --- an instant, and WHICH END belongs in a combined row
    'run_timestamp': FigureDeclaration(Reduction.SPAN_START),
    'recorded_at_utc': FigureDeclaration(Reduction.SPAN_END),
    # An instant like the two above: over a set of rows the earliest and the latest both mean
    # something — when this history first lost evidence, and when it last did.
    'records_pruned_at': FigureDeclaration(Reduction.SPAN_END),

    # --- comma-joined sets: union, never concatenation
    'symbols': FigureDeclaration(Reduction.UNION),
    'data_format_versions': FigureDeclaration(Reduction.UNION),
    'origin_classes': FigureDeclaration(Reduction.UNION),
    'origin_evidence_grades': FigureDeclaration(Reduction.UNION),
    'price_bases': FigureDeclaration(Reduction.UNION),
    'features_beyond_venue': FigureDeclaration(Reduction.UNION),

    # --- flows and counters: the rows partition them, so adding is correct
    # The first of them are what the row of a run that measured nothing leaves empty — an error row
    # without a currency (#646). Not measured rather than undefined: a ranking leaves such a row out
    # by its status anyway, and declared undefined they would refuse every sweep ranked by its P&L
    'net_pnl': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'total_fees': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'total_trades': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'winning_trades': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'losing_trades': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'r_trade_count': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    # Flows and counts a row written before the column existed does not carry — and a booking
    # period's row never carries the order counts
    'gross_profit': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'gross_loss': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'r_win_count': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'r_loss_count': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_submitted': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_adopted': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_executed': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_denied': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_rejected': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_cancelled': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_expired': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_undelivered': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'orders_unaccounted': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'sl_tp_triggered': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),

    # --- cumulative extrema. Summing counts one decline once per row that was still inside it.
    # The trio reduces TOGETHER: `account_max_drawdown` leads, and the other two are taken from
    # the row that won it. Reducing them separately pairs one row's trough with another's peak,
    # which is the defect #497 removed one layer down (report_aggregators.py:160-163).
    # Empty for a reading taken before the account's first valuation, or a row written before
    # the column existed
    'account_max_drawdown': FigureDeclaration(Reduction.MAX_ABS, NullMeaning.UNMEASURED),
    'max_equity': FigureDeclaration(
        Reduction.COMPANION, NullMeaning.UNMEASURED, companion_of='account_max_drawdown'),
    'account_max_drawdown_pct': FigureDeclaration(
        Reduction.COMPANION, NullMeaning.UNMEASURED, companion_of='account_max_drawdown'),

    # --- stocks: read at an instant, so the most recent row is the answer. A reading of ONE
    # account at its close: no value over the rows of several
    'unrealized_pnl': FigureDeclaration(Reduction.LAST, NullMeaning.UNDEFINED),
    'final_equity': FigureDeclaration(Reduction.LAST, NullMeaning.UNDEFINED),
    'open_position_count': FigureDeclaration(Reduction.LAST, NullMeaning.UNDEFINED),

    # --- rates, means, quotients: NOT combinable. A difference or an average of two of these
    # is not one of these — they are re-derived over the records of the wider window
    # The trades had no R on record: a logic keeps its stop itself, so the position carries none. A
    # ranking leaves such a run out and counts it. A row from before this column could be empty
    # reads 0.0 with `r_trade_count` 0, and a fold of it reads empty like this one
    'expectancy': FigureDeclaration(
        Reduction.DERIVE, NullMeaning.UNMEASURED, weighted_by='r_trade_count',
        addition=Addition.SEQUENTIAL),
    'profit_factor': FigureDeclaration(Reduction.DERIVE, NullMeaning.UNDEFINED),   # no losing trade
    'win_rate': FigureDeclaration(Reduction.DERIVE, NullMeaning.UNMEASURED),       # as net_pnl
    # Empty for an empty R subset
    'avg_win_r': FigureDeclaration(
        Reduction.DERIVE, NullMeaning.UNDEFINED, weighted_by='r_win_count',
        addition=Addition.SEQUENTIAL),
    'avg_loss_r': FigureDeclaration(
        Reduction.DERIVE, NullMeaning.UNDEFINED, weighted_by='r_loss_count',
        addition=Addition.SEQUENTIAL),
    # Empty without a signal worker in the run
    'signal_fresh_ratio': FigureDeclaration(Reduction.DERIVE, NullMeaning.UNDEFINED),

    # --- the booking period (#537)
    # Identical across the rows of one unit; across units it is what separates them, the same
    # way `currency` does.
    'unit_name': FigureDeclaration(Reduction.IDENTITY),
    'period_no': FigureDeclaration(Reduction.SPAN_END),
    'period_opened_at': FigureDeclaration(Reduction.SPAN_START),
    'period_closed_at': FigureDeclaration(Reduction.SPAN_END),
    # The LAST period's reason is the one that says whether the books are complete: only a
    # `session_end` means nothing was left open. An earlier row's `anchor` says nothing about it.
    'period_close_reason': FigureDeclaration(Reduction.LAST),
    # The period's own band, ticks and costs are empty on a row that books no period, a period that
    # could value nothing, a row written before the column existed.
    # The band of the widest period, and the deepest single-period decline. NOT the band or the
    # decline of the UNION — a fall that runs across a boundary is deeper than either period's
    # own, and `account_max_drawdown` above is the column that carries that.
    # A PEAK, so a plain maximum — this one is not a magnitude, and under MAX_ABS a negative
    # equity would outrank a positive one. Nothing had noticed because no account here has gone
    # negative; the two meanings simply shared a name.
    'period_max_equity': FigureDeclaration(Reduction.MAX, NullMeaning.UNMEASURED),
    'period_min_equity': FigureDeclaration(Reduction.MIN, NullMeaning.UNMEASURED),
    # A MAGNITUDE since #539, like `account_max_drawdown` and the excursion columns beside it.
    # It used to be the one SIGNED figure among them, and rows of both ages sit in this store —
    # which is exactly what MAX_ABS is for. What the sign cost was never arithmetic but
    # READING: a consumer renders this table beside the portfolio block, and one shared
    # formatter turns one of the two into its own opposite without anything going red. The
    # sign is a DISPLAY decision and lives in the renderers.
    'period_max_drawdown': FigureDeclaration(Reduction.MAX_ABS, NullMeaning.UNMEASURED),
    # The earliest period's opening — what the window began with. The opening of ONE account: no
    # value over the rows of several
    'period_opening_equity': FigureDeclaration(Reduction.FIRST, NullMeaning.UNDEFINED),
    'period_commission_cost': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'period_swap_cost': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'period_spread_cost': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    'period_modelled_spread_cost': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    # The periods partition the run's ticks, so their counts add up
    'period_tick_count': FigureDeclaration(Reduction.SUM, NullMeaning.UNMEASURED),
    # Means, extremes and runs over a set of trades that can be empty.
    # The worst single excursion combines by magnitude; its MEANS do not.
    'largest_mae': FigureDeclaration(Reduction.MAX_ABS, NullMeaning.UNDEFINED),
    'largest_mfe': FigureDeclaration(Reduction.MAX_ABS, NullMeaning.UNDEFINED),
    'avg_mae_winners': FigureDeclaration(
        Reduction.DERIVE, NullMeaning.UNDEFINED, weighted_by='winning_trades',
        addition=Addition.SEQUENTIAL),
    'avg_mae_losers': FigureDeclaration(
        Reduction.DERIVE, NullMeaning.UNDEFINED, weighted_by='losing_trades',
        addition=Addition.SEQUENTIAL),
    'avg_mfe_losers': FigureDeclaration(
        Reduction.DERIVE, NullMeaning.UNDEFINED, weighted_by='losing_trades',
        addition=Addition.SEQUENTIAL),
    'avg_trade_duration_s': FigureDeclaration(
        Reduction.DERIVE, NullMeaning.UNDEFINED, weighted_by='total_trades',
        addition=Addition.SEQUENTIAL),
    # DERIVE, and this is the one where the obvious answer is wrong. A streak can CROSS a
    # period boundary, so `max()` of two periods understates the truth: 2 and 3 in adjacent
    # periods can be a run of 5. It has to be re-derived over the records of the wider window.
    'max_consecutive_wins': FigureDeclaration(Reduction.DERIVE, NullMeaning.UNDEFINED),
    'max_consecutive_losses': FigureDeclaration(Reduction.DERIVE, NullMeaning.UNDEFINED),
}


# The catalog read by one question at a time. Derived, so they cannot disagree with it.

# HOW each column combines when several rows are read as one.
COLUMN_REDUCTION: Dict[str, Reduction] = {
    column: declaration.reduction for column, declaration in LEDGER_FIGURES.items()}

# What the EMPTY cell of a nullable figure means — a ranking reads it.
COLUMN_NULL_MEANING: Dict[str, NullMeaning] = {
    column: declaration.null_meaning for column, declaration in LEDGER_FIGURES.items()
    if declaration.null_meaning is not None}

# WHICH column a COMPANION follows.
COLUMN_COMPANION_OF: Dict[str, str] = {
    column: declaration.companion_of for column, declaration in LEDGER_FIGURES.items()
    if declaration.companion_of is not None}

# Which count weights each mean.
COLUMN_WEIGHTED_BY: Dict[str, str] = {
    column: declaration.weighted_by for column, declaration in LEDGER_FIGURES.items()
    if declaration.weighted_by is not None}
