"""
WindowSetSerializer Tests
=========================
Unit tests for the scenario-set assembly: multi-symbol merge into one set, per-symbol
time-ordered IS/OOS role assignment (#367), and the quote-currency balance union (#265).

Tests the pure assembler (`_build_scenario_set_config`) — no file write. The quote-currency
resolution is patched so the test stays free of broker-config data.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from python.framework.types.config_types.robustness_config_types import RobustnessConfig
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy
from python.framework.types.scenario_types.window_set_types import GeneratedWindow, WindowSet
from python.scenario.generator.window_set_serializer import WindowSetSerializer

from conftest import utc


_RESOLVE_PATH = 'python.scenario.generator.window_set_serializer.resolve_quote_currency'


def _blocks_window_set(symbol: str, n: int) -> WindowSet:
    """Build a blocks WindowSet with n consecutive 4h windows for one symbol."""
    windows = [
        GeneratedWindow(
            block_index=i,
            start_time=utc(2025, 10, 1 + i),
            end_time=utc(2025, 10, 1 + i, 4),
            regime=VolatilityRegime.MEDIUM,
            session=TradingSession.LONDON,
            estimated_ticks=0,
            atr=0.0,
            split_reason='constrained',
        )
        for i in range(n)
    ]
    return WindowSet(
        symbol=symbol, broker_type='mt5', strategy=GenerationStrategy.BLOCKS,
        windows=windows, generated_at=datetime.now(timezone.utc), mode='blocks')


@patch(_RESOLVE_PATH, return_value='USD')
class TestMultiSymbolAssembly:
    """Multiple WindowSets merge into one scenario set with per-symbol roles."""

    def test_merges_both_symbols(self, _mock_quote):
        """Scenarios from every symbol are present, with unique (symbol-prefixed) names."""
        sets = [_blocks_window_set('EURUSD', 4), _blocks_window_set('GBPUSD', 3)]
        config = WindowSetSerializer()._build_scenario_set_config(sets, 'multi.json', None)

        symbols = [s['symbol'] for s in config['scenarios']]
        assert symbols.count('EURUSD') == 4
        assert symbols.count('GBPUSD') == 3
        names = [s['name'] for s in config['scenarios']]
        assert len(names) == len(set(names))  # unique across symbols

    def test_roles_assigned_per_symbol(self, _mock_quote):
        """Each symbol gets its OWN time-ordered IS/OOS split (no cross-symbol future leak)."""
        sets = [_blocks_window_set('EURUSD', 4), _blocks_window_set('GBPUSD', 4)]
        rob = RobustnessConfig(enabled=True, oos_split=0.5)
        config = WindowSetSerializer()._build_scenario_set_config(sets, 'multi.json', rob)

        for sym in ('EURUSD', 'GBPUSD'):
            roles = [s['role'] for s in config['scenarios'] if s['symbol'] == sym]
            assert roles[0] == 'in_sample'         # earliest window = IS
            assert roles[-1] == 'out_of_sample'    # latest window = OOS
            assert 'in_sample' in roles and 'out_of_sample' in roles
        assert config['robustness']['enabled'] is True
        assert config['robustness']['oos_split'] == 0.5

    def test_no_roles_without_robustness(self, _mock_quote):
        """Without robustness, scenarios carry no role and no robustness block is written."""
        sets = [_blocks_window_set('EURUSD', 2)]
        config = WindowSetSerializer()._build_scenario_set_config(sets, 'x.json', None)

        assert 'robustness' not in config
        assert all('role' not in s for s in config['scenarios'])

    def test_quote_balance_seeded_once_per_currency(self, _mock_quote):
        """The quote currency is seeded into the set-wide global balance (unioned)."""
        sets = [_blocks_window_set('EURUSD', 2), _blocks_window_set('GBPUSD', 2)]
        config = WindowSetSerializer()._build_scenario_set_config(sets, 'x.json', None)

        balances = config['global']['trade_simulator_config']['balances']
        assert 'USD' in balances
        # Both symbols quote USD → resolved once per distinct currency
        assert _mock_quote.call_count == 2  # called per symbol, deduped before seeding
