"""
Bar-render admission by memory (#442).

A render worker holds a symbol's whole history in one frame, so two large symbols side by side can
exhaust the machine. A symbol starts only while the estimates of the running ones plus its own fit
the budget; one larger than the whole budget renders alone rather than never.
"""

from python.data_management.importers.bar_importer import admit_render_tasks, estimate_render_mb

_BTC, _ETH, _AUD, _DOT = (('kraken_spot', 'BTCUSD'), ('kraken_spot', 'ETHUSD'),
                          ('mt5', 'AUDUSD'), ('kraken_spot', 'DOTUSD'))
_ESTIMATES = {_BTC: 15_500.0, _ETH: 10_000.0, _AUD: 6_000.0, _DOT: 900.0}


class TestTheEstimate:

    def test_the_measured_symbol_is_estimated_at_its_measured_peak(self):
        # 60.0M ticks peaked at 8.4 GB on 2026-10-13
        assert 8_200 < estimate_render_mb(60_013_484) < 8_900

    def test_an_empty_symbol_costs_the_process_alone(self):
        assert estimate_render_mb(0) > 0


class TestTheAdmission:

    def test_two_large_symbols_never_run_side_by_side(self):
        waiting = [_BTC, _ETH, _AUD, _DOT]
        assert admit_render_tasks(waiting, _ESTIMATES, 0.0, 0, 2, 20_000.0) == [_BTC, _DOT]

    def test_a_freed_worker_takes_the_largest_that_fits(self):
        # ETHUSD's 10 GB does not fit beside BTCUSD's 15.5 GB; AUDUSD's 6 GB does not either
        assert admit_render_tasks([_ETH, _AUD], _ESTIMATES, 15_500.0, 1, 2, 20_000.0) == []
        assert admit_render_tasks([_ETH, _AUD], _ESTIMATES, 900.0, 1, 2, 20_000.0) == [_ETH]

    def test_a_symbol_larger_than_the_budget_renders_alone(self):
        assert admit_render_tasks([_BTC, _DOT], _ESTIMATES, 0.0, 0, 2, 12_000.0) == [_BTC]
        assert admit_render_tasks([_BTC], _ESTIMATES, 900.0, 1, 2, 12_000.0) == []

    def test_the_worker_count_still_caps(self):
        small = {task: 100.0 for task in (_BTC, _ETH, _AUD, _DOT)}
        assert len(admit_render_tasks(
            [_BTC, _ETH, _AUD, _DOT], small, 0.0, 0, 2, 20_000.0)) == 2
