"""
The simulation books in periods too — end to end, over the process bridge.

Every piece of the sim booking path is unit-tested elsewhere: the derivation, the recorder, the
ledger row, the table. None of them shows that a SCENARIO reaches the seal at all, and that is
the half where a wiring mistake hides — a scenario runs in a SUBPROCESS, so the periods have to
survive being pickled and handed back before anything can write them.

These run against the same baseline batch the rest of this suite uses, so they cost no extra
scenario execution.
"""

from python.framework.types.process_data_types import ProcessTickLoopResult


class TestTheScenarioBooks:

    def test_the_periods_survive_the_process_bridge(
            self, tick_loop_results: ProcessTickLoopResult):
        # The subprocess cannot write the ledger, so the periods travel back on the result like
        # every other figure. Arriving empty here would mean the seal never ran, or that the
        # field never made it onto the bridge — two different mistakes with one symptom.
        assert tick_loop_results.booking_segments
        assert len(tick_loop_results.booking_segments) >= 1

    def test_a_scenario_counts_its_own_periods_from_one(
            self, tick_loop_results: ProcessTickLoopResult):
        # No floor is carried into a backtest: a scenario has no predecessor to inherit a
        # period count from, unlike a live session continuing a deployment.
        numbers = [s.segment_no for s in tick_loop_results.booking_segments]
        assert numbers == list(range(1, len(numbers) + 1))

    def test_the_last_period_says_the_scenario_finished(
            self, tick_loop_results: ProcessTickLoopResult):
        # A scenario whose data simply ends still books what it has: the final period is closed
        # by the run ending, not by the market.
        assert tick_loop_results.booking_segments[-1].reason.value == 'session_end'

    def test_the_periods_partition_the_scenario_s_trades(
            self, tick_loop_results: ProcessTickLoopResult):
        # The control total: nothing counted twice, nothing lost between the periods.
        booked = sum(s.trade_count for s in tick_loop_results.booking_segments)
        assert booked == len(tick_loop_results.trade_history or [])

    def test_the_periods_sum_to_the_scenario_s_realised_result(
            self, tick_loop_results: ProcessTickLoopResult):
        # The reconciliation the console prints, asserted against the portfolio's own figure —
        # two derivations over the same trades, arrived at by different routes.
        booked = sum(s.figures.net_pnl for s in tick_loop_results.booking_segments)
        stats = tick_loop_results.portfolio_stats
        assert abs(booked - (stats.total_profit - stats.total_loss)) < 0.01

    def test_the_equity_band_is_measured_rather_than_left_at_zero(
            self, tick_loop_results: ProcessTickLoopResult):
        # The band rides the per-tick equity sample. It was silently zero once, because the
        # sampling call did not hand its value back — a plausible-looking number that no test
        # would have questioned, so it gets one.
        stats = tick_loop_results.portfolio_stats
        if stats.account_max_drawdown <= 0:
            return                                  # nothing declined, nothing to assert
        deepest = min(s.segment_max_drawdown for s in tick_loop_results.booking_segments)
        assert deepest < 0
