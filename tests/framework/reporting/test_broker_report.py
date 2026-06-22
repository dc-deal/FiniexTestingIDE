"""
Broker Report Builder + Render Tests (#391).

`build_broker_report_from_batch` maps the batch's `broker_scenario_map` (broker → its
scenarios + symbols + loaded config) to a `BrokerReport`: one `BrokerInfoRow` per broker
with the static broker spec, scenario list, and per-symbol specs. Tested with REAL
`BrokerSpecification` / `SymbolSpecification` payloads (a typed local config double only
hands them back — the mapped data is real, so a field-name drift fails loudly) and the
real `BrokerScenarioInfo` / `BatchExecutionSummary` / `BrokerType` / `MarketConfigManager`.
The render test feeds the real model into the model-fed `BrokerSummary`.
"""

import io
import re
from contextlib import redirect_stdout

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.reporting.console.broker_summary import BrokerSummary
from python.framework.reporting.builders.broker_report_builder import (
    build_broker_report_from_batch, build_broker_report_from_session)
from python.framework.types.api.report_types import (
    BrokerInfoRow, BrokerReport, BrokerSymbolRow)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.scenario_set_types import BrokerScenarioInfo
from python.framework.types.trading_env_types.broker_types import (
    BrokerSpecification, BrokerType, MarginMode, SwapMode, SymbolSpecification)
from python.framework.utils.console_renderer import ConsoleRenderer


def _broker_spec() -> BrokerSpecification:
    return BrokerSpecification(
        company='Kraken', server='kraken-spot', broker_type=BrokerType.KRAKEN_SPOT,
        trade_mode='demo', leverage=1, margin_mode=MarginMode.NONE,
        margin_call_level=0.0, stopout_level=0.0, stopout_mode='percent',
        trade_allowed=True, expert_allowed=True, hedging_allowed=False, limit_orders=0)


def _symbol_spec(symbol: str, base: str, quote: str) -> SymbolSpecification:
    return SymbolSpecification(
        symbol=symbol, description=f'{base}/{quote}',
        volume_min=0.0001, volume_max=1000.0, volume_step=0.0001, volume_limit=0.0,
        tick_size=0.1, digits=1, contract_size=1,
        base_currency=base, quote_currency=quote, margin_currency=quote,
        trade_allowed=True, swap_mode=SwapMode.NONE, swap_long=0.0, swap_short=0.0,
        swap_rollover3days=3, stops_level=0, freeze_level=0)


class _FakeBrokerConfig:
    """
    Typed test double for BrokerConfig: hands back REAL specs (the payloads the builder
    maps are real domain types; only this thin container is a local stand-in). Attribute
    + method names mirror the real BrokerConfig contract the builder uses
    (`broker_type` enum, `config_hash`, `get_broker_specification`, `get_symbol_specification`).
    """

    def __init__(self, broker_type, broker_spec, symbol_specs, config_hash):
        self.broker_type = broker_type
        self._broker_spec = broker_spec
        self._symbol_specs = symbol_specs
        self.config_hash = config_hash

    def get_broker_specification(self) -> BrokerSpecification:
        return self._broker_spec

    def get_symbol_specification(self, symbol: str) -> SymbolSpecification:
        return self._symbol_specs[symbol]


def _fake_config(symbols, config_hash='abcd1234') -> _FakeBrokerConfig:
    symbol_specs = {sym: _symbol_spec(sym, sym[:3], sym[3:]) for sym in symbols}
    return _FakeBrokerConfig(
        BrokerType.KRAKEN_SPOT, _broker_spec(), symbol_specs, config_hash)


def _batch(symbols, scenarios, config_hash='abcd1234') -> BatchExecutionSummary:
    info = BrokerScenarioInfo(
        config_path='kraken.json', scenarios=scenarios, symbols=set(symbols),
        broker_config=_fake_config(symbols, config_hash))
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        broker_scenario_map={BrokerType.KRAKEN_SPOT: info})


class TestBuild:
    def test_maps_broker_row(self):
        batch = _batch(['BTCUSD'], ['btc_run'])
        report = build_broker_report_from_batch(batch)
        assert len(report.units) == 1
        row = report.units[0]
        assert row.broker_type == 'kraken_spot'
        assert row.market_type == MarketConfigManager().get_market_type('kraken_spot').value
        assert row.company == 'Kraken' and row.server == 'kraken-spot'
        assert row.trade_mode == 'demo' and row.leverage == 1
        assert row.margin_mode == MarginMode.NONE.value
        assert row.hedging_allowed is False
        assert row.config_hash == 'abcd1234'
        assert row.scenarios == ['btc_run']

    def test_symbols_sorted_and_mapped(self):
        batch = _batch(['ETHUSD', 'BTCUSD'], ['run'])
        row = build_broker_report_from_batch(batch).units[0]
        # symbols come from a set → builder sorts them
        assert [s.symbol for s in row.symbols] == ['BTCUSD', 'ETHUSD']
        btc = row.symbols[0]
        assert btc.base_currency == 'BTC' and btc.quote_currency == 'USD'
        assert btc.volume_min == 0.0001 and btc.tick_size == 0.1

    def test_empty_batch_no_units(self):
        batch = BatchExecutionSummary(
            batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0)
        assert build_broker_report_from_batch(batch).units == []


class TestBuildFromSession:
    def test_single_broker_single_symbol_no_scenarios(self):
        # live session: one broker + one symbol, no scenario grid
        report = build_broker_report_from_session(_fake_config(['BTCUSD']), 'BTCUSD')
        assert len(report.units) == 1
        row = report.units[0]
        assert row.broker_type == 'kraken_spot'
        assert row.market_type == MarketConfigManager().get_market_type('kraken_spot').value
        assert row.company == 'Kraken' and row.config_hash == 'abcd1234'
        assert row.scenarios == []
        assert [s.symbol for s in row.symbols] == ['BTCUSD']
        assert row.symbols[0].base_currency == 'BTC' and row.symbols[0].quote_currency == 'USD'


class TestRender:
    def _render(self, report: BrokerReport, compact: bool = False) -> str:
        summary = BrokerSummary(report)
        buf = io.StringIO()
        with redirect_stdout(buf):
            summary.render(ConsoleRenderer(), compact=compact, threshold=2)
        return re.sub(r'\x1b\[[0-9;]*m', '', buf.getvalue())

    def _report(self, scenarios) -> BrokerReport:
        return BrokerReport(units=[BrokerInfoRow(
            broker_type='kraken_spot', market_type='crypto', company='Kraken',
            server='kraken-spot', trade_mode='demo', leverage=1,
            margin_mode='none', hedging_allowed=False, config_hash='abcd1234',
            scenarios=scenarios,
            symbols=[BrokerSymbolRow(
                symbol='BTCUSD', volume_min=0.0001, volume_max=1000.0, contract_size=1,
                tick_size=0.1, base_currency='BTC', quote_currency='USD')])])

    def test_renders_broker_block_and_symbols(self):
        out = self._render(self._report(['btc_run']))
        assert 'BROKER CONFIGURATION' in out
        assert 'Company: Kraken' in out
        assert 'Config:  [abcd1234]' in out
        assert 'TRADED SYMBOLS' in out and 'BTCUSD' in out and 'BTC/USD' in out
        assert '• btc_run' in out

    def test_compact_collapses_scenarios_over_threshold(self):
        out = self._render(self._report(['s1', 's2', 's3']), compact=True)
        assert '3 scenarios — see log for full list' in out
        assert '• s1' not in out

    def test_no_units_message(self):
        out = self._render(BrokerReport(units=[]))
        assert 'No broker configuration available' in out
