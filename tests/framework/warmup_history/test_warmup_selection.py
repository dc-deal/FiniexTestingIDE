"""
Warmup Selection — the batch selects each scenario's history for its first tick (#588).

Through the batch's own SharedDataPreparator.prepare_bars, over an archive the importer's
renderer built from a synthetic stream. The selection used to keep every bar whose OPENING
stamp lay before the configured start — and so the bar containing the start, whose close came
hours later — and keyed the result by symbol, timeframe and start alone, so two scenarios
sharing a window overwrote each other's warmup.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.types.process_data_types import BarRequirement
from python.framework.utils.bar_period import is_complete_at, period_start
from tests.framework.warmup_history.warmup_history_builders import (
    BROKER_TYPE,
    SYMBOL,
    ArchiveIndexStub,
    archive_package,
    render_archive,
    tick_frame,
)

UTC = timezone.utc
REQUIRED = {'M1': 30, 'M5': 12, 'H1': 6, 'D1': 1}


class TestEveryBarIsCompleteAtTheFirstTick:

    def test_a_start_inside_a_bar(self, preparator):
        start = datetime(2026, 8, 5, 13, 2, 30, tzinfo=UTC)
        package = archive_package(preparator, REQUIRED, start, first_tick=start)

        assert package.warmup_reference_time == start
        for timeframe, required in REQUIRED.items():
            rows = package.bars[timeframe]
            assert len(rows) == required == package.bar_counts[timeframe]
            assert all(is_complete_at(row['timestamp'], timeframe, start) for row in rows)
            newest = rows[-1]['timestamp'].to_pydatetime()
            assert period_start(start, timeframe) > newest, (
                f'{timeframe}: the bar containing the start ({newest}) was selected')

    def test_a_start_on_a_boundary_takes_the_bar_that_just_ended(self, preparator):
        start = datetime(2026, 8, 5, 13, 0, 3, tzinfo=UTC)
        package = archive_package(preparator, REQUIRED, start, first_tick=start)
        assert package.bars['H1'][-1]['timestamp'].hour == 12


class TestTheSeamClosesAtTheFirstTick:
    """The first tick, not the configured start, is the instant the history is selected for."""

    @pytest.fixture
    def quiet_preparator(self, tmp_path):
        """An archive whose stream falls silent from 12:59:40 to 13:00:20."""
        stream = tick_frame(
            datetime(2026, 8, 4, tzinfo=UTC), datetime(2026, 8, 5, 15, tzinfo=UTC), 7)
        quiet = (stream['timestamp'] >= '2026-08-05 12:59:40+00:00') & (
            stream['timestamp'] < '2026-08-05 13:00:20+00:00')
        stream = stream[~quiet].reset_index(drop=True)
        instance = SharedDataPreparator(MagicMock())
        instance.bar_index_manager = ArchiveIndexStub(render_archive(stream, tmp_path, ['H1']))
        return instance, stream

    def test_a_period_that_ends_before_the_first_tick_is_in_the_history(self, quiet_preparator):
        """
        Started at 12:59:50, first trade at 13:00:2x: the 12:00 bar is complete when the session
        begins, and every one of its ticks lies before the start. Selected at the configured
        start it would be missing, and the first bar the backtest renders would follow a hole.
        """
        preparator, stream = quiet_preparator
        start = datetime(2026, 8, 5, 12, 59, 50, tzinfo=UTC)
        first_tick = stream[stream['timestamp'] >= start]['timestamp'].iloc[0].to_pydatetime()

        package = archive_package(preparator, {'H1': 3}, start, first_tick)

        assert first_tick > datetime(2026, 8, 5, 13, tzinfo=UTC)
        assert package.warmup_reference_time == first_tick
        assert package.bars['H1'][-1]['timestamp'].hour == 12


class TestTheSelectionBelongsToItsScenario:

    def test_two_scenarios_sharing_a_window_keep_their_own_warmup(self, preparator):
        """Same symbol, timeframe and start, different requirements: one overwrote the other."""
        start = datetime(2026, 8, 5, 13, 2, 30, tzinfo=UTC)
        requirements = [
            BarRequirement(scenario_name=name, broker_type=BROKER_TYPE, symbol=SYMBOL,
                           timeframe='H1', warmup_count=count, start_time=start)
            for name, count in (('wide', 6), ('narrow', 3))
        ]
        bars, counts, references = preparator.prepare_bars(
            requirements, {'wide': start, 'narrow': start})

        assert len(bars[('wide', 'H1')]) == 6
        assert len(bars[('narrow', 'H1')]) == 3
        assert counts == {('wide', 'H1'): 6, ('narrow', 'H1'): 3}
        assert references == {'wide': start, 'narrow': start}

    def test_a_scenario_without_a_first_tick_is_selected_at_its_start(self, preparator):
        """No ticks means the scenario will not run; its selection still says what it used."""
        start = datetime(2026, 8, 5, 13, 2, 30, tzinfo=UTC)
        requirement = BarRequirement(scenario_name='silent', broker_type=BROKER_TYPE,
                                     symbol=SYMBOL, timeframe='H1', warmup_count=2,
                                     start_time=start)
        _, _, references = preparator.prepare_bars([requirement], {})
        assert references == {'silent': start}
