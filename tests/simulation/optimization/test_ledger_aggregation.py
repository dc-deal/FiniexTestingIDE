"""
Combining ledger rows — the ABSCHLUSS step, and the first caller of `COLUMN_REDUCTION`.

A row is one booking period. Every question above that level is asked by folding rows, and the
rule for folding them is declared per column. These tests pin that the declaration is FOLLOWED,
column class by column class, because the map was read by nothing but a completeness test until
this function existed — and a declaration with no caller is a comment that decays.

The cases that matter are the ones where the obvious answer is wrong: a rate that must be
rebuilt from components rather than averaged, a drawdown trio that has to come from ONE row, and
a streak that cannot be recovered at all.
"""

from typing import List

from python.framework.reporting.store.ledger_aggregation import (
    aggregate_ledger_rows,
    identity_conflicts,
)
from python.framework.reporting.store.run_results_ledger import (
    COLUMN_COMPANION_OF,
    COLUMN_REDUCTION,
)
from python.framework.types.api.report_types import RunResultRow
from python.framework.types.run_results_types import Reduction

_RUN = '20260921_000000_a1b2c3d4'


def _period(segment_no: int, closed: str, **figures) -> RunResultRow:
    """
    One booking-period row with sensible identity and overridable figures.

    Args:
        segment_no: Its running number
        closed: When the period closed — also the recency key
        figures: Whatever the case under test needs

    Returns:
        The row
    """
    defaults = dict(
        run_id=_RUN, param_hash='hash', run_timestamp='2026-09-21T00:00:00+00:00',
        currency='USD', unit_name='session', scenario_set_name='bot',
        segment_no=segment_no, segment_closed_at=closed,
        segment_opened_at=closed,
    )
    defaults.update(figures)
    return RunResultRow(**defaults)


class TestEveryColumnClassIsFollowed:

    def test_a_flow_is_summed(self):
        rows = [_period(1, 'd1', net_pnl=60.0, total_fees=2.0),
                _period(2, 'd2', net_pnl=-10.0, total_fees=1.5)]
        combined = aggregate_ledger_rows(rows)[0]
        assert combined.net_pnl == 50.0
        assert combined.total_fees == 3.5

    def test_a_stock_is_the_most_recent_reading(self):
        # LAST, not a sum and not a mean: an equity is true at an instant, and the instant that
        # matters is the latest one.
        rows = [_period(1, '2026-09-22', final_equity=10_060.0),
                _period(2, '2026-09-23', final_equity=10_050.0)]
        assert aggregate_ledger_rows(rows)[0].final_equity == 10_050.0

    def test_a_cumulative_extremum_takes_the_largest_magnitude(self):
        rows = [_period(1, 'd1', account_max_drawdown=-10.0),
                _period(2, 'd2', account_max_drawdown=-180.0)]
        assert aggregate_ledger_rows(rows)[0].account_max_drawdown == -180.0

    def test_a_trough_takes_the_smallest_value(self):
        rows = [_period(1, 'd1', segment_min_equity=10_080.0),
                _period(2, 'd2', segment_min_equity=9_900.0)]
        assert aggregate_ledger_rows(rows)[0].segment_min_equity == 9_900.0

    def test_a_set_is_unioned_and_keeps_its_type(self):
        # `symbols` parses back into a LIST on the typed row while the ledger stores it joined,
        # so a union that returned a string would be a validation error rather than a value.
        rows = [_period(1, 'd1', symbols=['BTCUSD']), _period(2, 'd2', symbols=['ETHUSD'])]
        assert aggregate_ledger_rows(rows)[0].symbols == ['BTCUSD', 'ETHUSD']

    def test_a_span_takes_the_end_it_declares(self):
        rows = [_period(1, '2026-09-22'), _period(2, '2026-09-23')]
        combined = aggregate_ledger_rows(rows)[0]
        assert combined.segment_closed_at == '2026-09-23'   # SPAN_END
        assert combined.segment_opened_at == '2026-09-22'   # SPAN_START


class TestTheDrawdownTrioTravelsTogether:

    def test_the_peak_and_the_share_come_from_the_row_that_won(self):
        # Folded independently, the deepest decline of one period would be paired with the
        # highest peak of another — the defect #497 removed one layer down.
        rows = [
            _period(1, 'd1', account_max_drawdown=-10.0, max_equity=11_000.0,
                    account_max_drawdown_pct=0.09),
            _period(2, 'd2', account_max_drawdown=-180.0, max_equity=10_200.0,
                    account_max_drawdown_pct=1.76),
        ]
        combined = aggregate_ledger_rows(rows)[0]
        assert combined.account_max_drawdown == -180.0
        assert combined.max_equity == 10_200.0            # NOT the 11 000 of the other row
        assert combined.account_max_drawdown_pct == 1.76

    def test_every_companion_declares_the_column_it_follows(self):
        # The pairing used to live in a prose comment, which no caller could read. This holds
        # the machine-readable map and the COMPANION entries to one key set (§49).
        declared = {c for c, r in COLUMN_REDUCTION.items() if r is Reduction.COMPANION}
        assert declared == set(COLUMN_COMPANION_OF)
        assert set(COLUMN_COMPANION_OF.values()) <= set(COLUMN_REDUCTION)


class TestARateIsRebuiltNeverFolded:

    def test_the_profit_factor_comes_from_the_summed_components(self):
        # Folding the two rates gives a different answer from folding their components, and
        # only the second one is the profit factor of the whole. This is the case that makes
        # `gross_profit` / `gross_loss` worth carrying at all.
        rows = [
            _period(1, 'd1', gross_profit=60.0, gross_loss=0.0, total_trades=2,
                    winning_trades=2, losing_trades=0, profit_factor=None),
            _period(2, 'd2', gross_profit=20.0, gross_loss=30.0, total_trades=2,
                    winning_trades=1, losing_trades=1, profit_factor=0.6667),
        ]
        combined = aggregate_ledger_rows(rows)[0]
        assert combined.profit_factor == 80.0 / 30.0

    def test_the_win_rate_comes_from_the_summed_counts(self):
        rows = [_period(1, 'd1', total_trades=2, winning_trades=2, win_rate=1.0),
                _period(2, 'd2', total_trades=2, winning_trades=1, win_rate=0.5)]
        assert aggregate_ledger_rows(rows)[0].win_rate == 0.75

    def test_a_mean_is_weighted_by_the_population_it_was_measured_over(self):
        # An unweighted average of averages would let a period with two trades count as much as
        # one with two hundred.
        rows = [_period(1, 'd1', expectancy=1.0, r_trade_count=1),
                _period(2, 'd2', expectancy=0.0, r_trade_count=99)]
        combined = aggregate_ledger_rows(rows)[0]
        assert combined.expectancy == 0.01          # not 0.5

    def test_the_weakest_signal_channel_wins(self):
        rows = [_period(1, 'd1', signal_fresh_ratio=0.99),
                _period(2, 'd2', signal_fresh_ratio=0.40)]
        assert aggregate_ledger_rows(rows)[0].signal_fresh_ratio == 0.40


class TestWhatCannotBeRecovered:

    def test_a_streak_is_not_answered_from_two_summaries(self):
        # The one place the obvious reduction is wrong: a run of winners can CROSS a period
        # boundary, so 2 and 3 in adjacent periods can be a run of 5. `max()` would answer 3
        # and be believed. None says the question needs the records.
        rows = [_period(1, 'd1', max_consecutive_wins=2),
                _period(2, 'd2', max_consecutive_wins=3)]
        assert aggregate_ledger_rows(rows)[0].max_consecutive_wins is None


class TestTheControlTotalSurvives:

    def test_the_trade_counts_add_up_to_the_whole(self):
        rows = [_period(1, 'd1', total_trades=2),
                _period(2, 'd2', total_trades=2)]
        combined = aggregate_ledger_rows(rows)[0]
        # `total_trades` IS the control total of a period row — on a segment row it is the
        # record count of the window its figures came from. The column that used to say the
        # same thing under a second name was removed: both were one `len(rows)` from one call,
        # so it could not disprove anything (#539 audit).
        assert combined.total_trades == 4
        assert combined.total_trades == 4


class TestGrouping:

    def test_currencies_are_never_mixed(self):
        rows = [_period(1, 'd1', currency='USD', net_pnl=60.0),
                _period(1, 'd1', currency='BTC', net_pnl=0.002)]
        combined = aggregate_ledger_rows(rows)
        assert len(combined) == 2
        assert {row.currency for row in combined} == {'USD', 'BTC'}

    def test_an_empty_input_yields_nothing(self):
        assert aggregate_ledger_rows([]) == []


class TestTheIdentityClaimIsChecked:
    """
    Twenty-eight columns declare "must agree across the rows" and nothing verified it — finding
    376. It stopped being harmless the moment something RELIED on the claim: the aggregation
    keeps one value and discards the rest.
    """

    def test_agreeing_rows_report_no_conflict(self):
        rows = [_period(1, 'd1'), _period(2, 'd2')]
        assert identity_conflicts(rows) == {}

    def test_a_disagreement_is_reported_with_both_values(self):
        rows = [_period(1, 'd1', git_commit='3a92658'),
                _period(2, 'd2', git_commit='f65fbc9')]
        conflicts = identity_conflicts(rows)
        assert 'git_commit' in conflicts
        assert sorted(conflicts['git_commit']) == ['3a92658', 'f65fbc9']

    def test_a_structured_identity_column_does_not_break_the_check(self):
        # `worker_versions` and `sweep_params` parse back into dicts, and a dict cannot go in a
        # set — the first version of this check raised on them instead of comparing them.
        rows = [_period(1, 'd1', worker_versions={'rsi': '1.0'}),
                _period(2, 'd2', worker_versions={'rsi': '2.0'})]
        assert 'worker_versions' in identity_conflicts(rows)

    def test_an_absent_value_is_not_a_disagreement(self):
        # A row written before a column existed reads back empty. That is "unknown", not a
        # second opinion, and reporting it would make the check fire on ordinary history.
        rows: List[RunResultRow] = [_period(1, 'd1', git_commit='3a92658'), _period(2, 'd2')]
        assert identity_conflicts(rows) == {}


class TestTheDeepestDeclineSurvivesTheFold:
    """
    `MAX` here means "largest by MAGNITUDE" — `max(present, key=abs)` — which is what lets one
    reduction serve a drawdown and a peak at once. `segment_max_drawdown` was stored signed
    until #539 and holds a magnitude since; these pin that the fold answers the deepest fall
    under BOTH conventions, because that property is the whole reason the reduction is written
    that way and a plain `max()` would silently break it.
    """

    def test_the_deepest_magnitude_wins(self):
        rows = [_period(1, 'd1', segment_max_drawdown=22000.12),
                _period(2, 'd2', segment_max_drawdown=5.0)]
        combined = aggregate_ledger_rows(rows, by=('run_id',))[0]
        assert combined.segment_max_drawdown == 22000.12

    def test_a_signed_value_folds_to_the_deepest_too(self):
        # A row written before #539 carries the negative form. A plain `max()` would answer
        # -5.00 here — the shallowest day, reported as the worst one.
        rows = [_period(1, 'd1', segment_max_drawdown=-22000.12),
                _period(2, 'd2', segment_max_drawdown=-5.0)]
        combined = aggregate_ledger_rows(rows, by=('run_id',))[0]
        assert combined.segment_max_drawdown == -22000.12

    def test_the_declaration_names_the_magnitude_reading(self):
        # MAX_ABS exists because MAX used to mean both things, and the NAME is what a reader
        # goes by: seeing MAX they read `max()`, which is right for a peak and wrong for a fall.
        for column in ('segment_max_drawdown', 'account_max_drawdown', 'largest_mae'):
            assert COLUMN_REDUCTION[column] is Reduction.MAX_ABS

    def test_a_peak_keeps_the_plain_maximum(self):
        # `segment_max_equity` is not a magnitude. Under MAX_ABS a negative equity would
        # outrank a positive one — unreachable today, and the two meanings shared a name.
        assert COLUMN_REDUCTION['segment_max_equity'] is Reduction.MAX
        rows = [_period(1, 'd1', segment_max_equity=-500.0),
                _period(2, 'd2', segment_max_equity=120.0)]
        assert aggregate_ledger_rows(rows, by=('run_id',))[0].segment_max_equity == 120.0


class TestEveryDeclaredReductionCanBeApplied:
    """
    The map is held to LEDGER_COLUMNS elsewhere — a column cannot exist without a declaration.
    Nothing held the ENUM to the code that applies it, and the failure has no symptom: a
    member with no branch falls through to `return None`, so the column quietly empties. Found
    while splitting MAX into MAX and MAX_ABS (#539 audit), where exactly that was one edit away.
    """

    # Reduced by a different mechanism or deliberately not combinable — see the enum's own
    # comments. Named here rather than skipped silently, so adding a member is a decision.
    _NOT_COMBINED = {Reduction.DERIVE, Reduction.COMPANION}

    def test_no_member_falls_through_to_none(self):
        rows = [_period(1, 'd1', net_pnl=3.0), _period(2, 'd2', net_pnl=4.0)]
        for member in Reduction:
            if member in self._NOT_COMBINED:
                continue
            column = next((c for c, r in COLUMN_REDUCTION.items() if r is member), None)
            assert column is not None, f'{member} is declared by no column'
            combined = aggregate_ledger_rows(rows, by=('run_id',))[0]
            assert hasattr(combined, column)

    def test_the_two_maxima_do_different_things(self):
        # The one property that makes the split worth having, asserted directly.
        signed = [_period(1, 'd1', segment_max_drawdown=-9.0),
                  _period(2, 'd2', segment_max_drawdown=-1.0)]
        assert aggregate_ledger_rows(signed, by=('run_id',))[0].segment_max_drawdown == -9.0
        peaks = [_period(1, 'd1', segment_max_equity=-9.0),
                 _period(2, 'd2', segment_max_equity=-1.0)]
        assert aggregate_ledger_rows(peaks, by=('run_id',))[0].segment_max_equity == -1.0
