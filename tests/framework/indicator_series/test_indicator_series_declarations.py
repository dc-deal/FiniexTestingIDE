"""
A Worker's Indicator Series — declared once, checked when the worker is created (#379).

A worker states how its outputs are drawn — on the candles or in a pane of its own, its scale, its
reference lines — and writes no drawing code. What a declaration can be wrong about on its own, its
types refuse while it is built; what needs the worker — a plot of an output it does not have — is
refused when the worker is created, for a CORE worker and a user's worker file alike. Every CORE
worker either declares its series or is listed as drawing nothing, with the reason.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from python.framework.exceptions.indicator_series_errors import IndicatorSeriesDeclarationError
from python.framework.factory.worker_factory import CORE_WORKERS_DRAWING_NOTHING, WorkerFactory
from python.framework.types.chart_types.indicator_series_types import (
    FilledArea,
    IndicatorPane,
    IndicatorSeriesDeclaration,
    Plot,
    PriceFormat,
    PriceLine,
    ValueRange,
)
from python.framework.validators.indicator_series_validator import validate_indicator_series
from tests.shared.null_logger import NullLogger


@pytest.fixture(scope='module')
def factory() -> WorkerFactory:
    return WorkerFactory(NullLogger())


def _core_registry(factory: WorkerFactory) -> dict:
    return {key: cls for key, (cls, _) in factory._registry.items() if key.startswith('CORE/')}


class TestEveryCoreWorkerAnswers:
    """A CORE worker draws its series, or says why it draws nothing — never neither."""

    def test_each_declares_a_series_or_is_listed_as_drawing_nothing(self, factory):
        for key, cls in _core_registry(factory).items():
            declares = bool(cls.get_indicator_series())
            listed = key in CORE_WORKERS_DRAWING_NOTHING
            assert declares != listed, (f'{key}: declares a series: {declares}, listed as drawing '
                                        f'nothing: {listed} — exactly one of the two')

    def test_the_list_names_only_registered_workers(self, factory):
        assert set(CORE_WORKERS_DRAWING_NOTHING) <= set(_core_registry(factory))

    def test_every_declaration_passes_the_check(self, factory):
        for key, cls in _core_registry(factory).items():
            validate_indicator_series(cls, key)

    def test_rsi_sits_in_its_own_pane_on_0_to_100(self, factory):
        (rsi,) = _core_registry(factory)['CORE/rsi'].get_indicator_series()
        assert rsi.pane is IndicatorPane.OWN
        assert (rsi.range.min_value, rsi.range.max_value) == (0, 100)
        assert [line.price for line in rsi.price_lines] == [70, 30]

    def test_bollinger_sits_on_the_candles_with_its_channel_filled(self, factory):
        (bands,) = _core_registry(factory)['CORE/bollinger'].get_indicator_series()
        assert bands.pane is IndicatorPane.PRICE
        assert [plot.id for plot in bands.plots] == ['upper', 'middle', 'lower']
        assert bands.filled_areas[0].between == ('plot:upper', 'plot:lower')


class TestADeclarationRefusesWhatCannotBeDrawn:
    """Wrong on its own: refused while it is built."""

    def _declare(self, **values) -> IndicatorSeriesDeclaration:
        fields = {'pane': IndicatorPane.OWN, 'title': 'X', 'plots': (Plot(id='a'),)}
        return IndicatorSeriesDeclaration(**{**fields, **values})

    def test_a_fixed_range_needs_a_pane_of_its_own(self):
        with pytest.raises(ValidationError, match='keeps the price scale'):
            self._declare(pane=IndicatorPane.PRICE, range=ValueRange(min_value=0, max_value=1))

    def test_a_filled_area_never_mixes_a_plot_and_a_price_line(self):
        with pytest.raises(ValidationError, match='never one of each'):
            self._declare(price_lines=(PriceLine(price=1),),
                          filled_areas=(FilledArea(between=('plot:a', 'price_line:1')),))

    def test_a_filled_area_names_edges_of_its_own_series(self):
        with pytest.raises(ValidationError, match='never one of each'):
            self._declare(filled_areas=(FilledArea(between=('plot:a', 'plot:b')),))

    def test_a_plot_is_declared_once(self):
        with pytest.raises(ValidationError, match='repeated'):
            self._declare(plots=(Plot(id='a'), Plot(id='a')))

    def test_a_series_draws_at_least_one_plot(self):
        with pytest.raises(ValidationError):
            self._declare(plots=())

    def test_a_name_holds_no_colon_and_no_slash(self):
        for name in ('a:b', 'a/b'):
            with pytest.raises(ValidationError):
                self._declare(plots=(Plot(id=name),))
            with pytest.raises(ValidationError):
                self._declare(layer_name=name)

    def test_a_price_line_is_named_by_its_exact_price(self):
        names = {price: PriceLine(price=price).get_ref()
                 for price in (70.0, 70.5, 0.00001234, 1.23456789, 1.2345678, -0.0)}
        assert names == {70.0: 'price_line:70', 70.5: 'price_line:70.5',
                         0.00001234: 'price_line:0.00001234',
                         1.23456789: 'price_line:1.23456789', 1.2345678: 'price_line:1.2345678',
                         -0.0: 'price_line:0'}

    def test_two_lines_apart_in_the_seventh_digit_are_two_lines(self):
        self._declare(price_lines=(PriceLine(price=1.2345671), PriceLine(price=1.2345672)))

    def test_a_range_has_its_bottom_below_its_top(self):
        with pytest.raises(ValidationError, match='below'):
            ValueRange(min_value=100, max_value=0)

    def test_the_price_format_is_the_symbols_unless_stated(self):
        assert self._declare().price_format is None
        assert self._declare(price_format=PriceFormat(precision=2)).price_format.precision == 2


_USER_WORKER = '''
from python.framework.types.chart_types.indicator_series_types import (
    IndicatorPane, IndicatorSeriesDeclaration, Plot,
)
from python.framework.workers.core.rsi_worker import RsiWorker


class MyRsi(RsiWorker):
    @classmethod
    def get_indicator_series(cls):
        return (IndicatorSeriesDeclaration(pane=IndicatorPane.OWN, title='Mine',
                                           plots=(Plot(id={plot_id!r}),)),)
'''


_HIGHER_TIMEFRAME_WORKER = '''
from python.framework.types.chart_types.indicator_series_types import (
    IndicatorPane, IndicatorSeriesDeclaration, Plot,
)
from python.framework.workers.core.rsi_worker import RsiWorker


class OnTheSecond(RsiWorker):
    @classmethod
    def get_indicator_series(cls):
        return (IndicatorSeriesDeclaration(pane=IndicatorPane.OWN, title='Lower',
                                           plots=(Plot(id='rsi_value'),)),
                IndicatorSeriesDeclaration(pane=IndicatorPane.OWN, title='Higher',
                                           layer_name='higher', timeframe_position=1,
                                           plots=(Plot(id='avg_gain'),)))
'''


class TestAWorkerIsCheckedWhenCreated:
    """Wrong against the worker: refused when the worker is created — a user's file alike."""

    def _user_worker(self, tmp_path: Path, plot_id: str) -> str:
        path = tmp_path / f'my_rsi_{plot_id}.py'
        path.write_text(_USER_WORKER.format(plot_id=plot_id), encoding='utf-8')
        return str(path)

    def test_a_plot_of_an_output_the_worker_lacks_is_refused(self, tmp_path):
        factory = WorkerFactory(NullLogger())
        with pytest.raises(IndicatorSeriesDeclarationError, match="'no_such_output'"):
            factory.create_worker('mine', self._user_worker(tmp_path, 'no_such_output'),
                                  {'periods': {'M5': 14}})

    def test_a_plot_of_a_number_is_accepted(self, tmp_path):
        factory = WorkerFactory(NullLogger())
        worker = factory.create_worker('mine', self._user_worker(tmp_path, 'avg_gain'),
                                       {'periods': {'M5': 14}})
        assert worker.get_indicator_series()[0].plots[0].id == 'avg_gain'

    def test_a_series_on_a_timeframe_the_periods_do_not_give_is_refused(self, tmp_path):
        path = tmp_path / 'on_the_second.py'
        path.write_text(_HIGHER_TIMEFRAME_WORKER, encoding='utf-8')
        with pytest.raises(IndicatorSeriesDeclarationError, match='timeframe_position 1'):
            WorkerFactory(NullLogger()).create_worker('mine', str(path), {'periods': {'M5': 14}})

    def test_a_series_on_the_second_timeframe_is_accepted_with_two(self, tmp_path):
        path = tmp_path / 'on_the_second.py'
        path.write_text(_HIGHER_TIMEFRAME_WORKER, encoding='utf-8')
        worker = WorkerFactory(NullLogger()).create_worker(
            'mine', str(path), {'periods': {'M5': 14, 'M30': 14}})
        assert [series.timeframe_position for series in worker.get_indicator_series()] == [0, 1]

    def test_a_further_series_names_its_layer(self, factory):
        cls = _core_registry(factory)['CORE/rsi']

        class TwoSeries(cls):
            @classmethod
            def get_indicator_series(cls):
                first = IndicatorSeriesDeclaration(pane=IndicatorPane.OWN, title='A',
                                                   plots=(Plot(id='rsi_value'),))
                return (first, first)

        with pytest.raises(IndicatorSeriesDeclarationError, match='needs a layer_name'):
            validate_indicator_series(TwoSeries, 'two_series')

    def test_a_text_output_is_not_drawn(self, factory):
        cls = _core_registry(factory)['CORE/ma_trend']

        class DrawsDirection(cls):
            @classmethod
            def get_indicator_series(cls):
                return (IndicatorSeriesDeclaration(pane=IndicatorPane.OWN, title='Dir',
                                                   plots=(Plot(id='direction'),)),)

        with pytest.raises(IndicatorSeriesDeclarationError, match='a plot draws a number'):
            validate_indicator_series(DrawsDirection, 'draws_direction')
