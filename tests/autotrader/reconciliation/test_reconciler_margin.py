"""
Reconciler position diff — MARGIN-gated; inactive on SPOT (#151).

On SPOT the broker has no position object (holdings are balances), so the
position diff is skipped — a synthesized local spot position must NOT read as an
orphan. On MARGIN the same diff lights up (the MT5 path, #209).
"""

from python.framework.testing.mock_broker_adapter import MockDivergenceMode
from python.framework.types.config_types.market_config_types import TradingModel
from tests.autotrader.reconciliation.conftest import make_position, make_broker_position


def test_spot_skips_position_diff_no_false_orphan(mock_adapter, make_reconciler):
    # Broker reports no positions (spot OpenPositions empty); local has one.
    mock_adapter.set_broker_positions([])
    rec = make_reconciler(
        mock_adapter,
        positions=[make_position('p1', 'P1')],
        trading_model=TradingModel.SPOT,
    )
    result = rec.reconcile()
    assert result.orphan_positions == []
    assert result.is_clean


def test_margin_clean_when_positions_match(mock_adapter, make_reconciler):
    mock_adapter.set_broker_positions([make_broker_position('P1')])
    rec = make_reconciler(
        mock_adapter,
        positions=[make_position('p1', 'P1')],
        trading_model=TradingModel.MARGIN,
    )
    result = rec.reconcile()
    assert result.is_clean


def test_margin_ghost_position(mock_adapter, make_reconciler):
    mock_adapter.set_broker_positions([make_broker_position('P1')])
    mock_adapter.set_divergence_mode(MockDivergenceMode.PHANTOM_POSITION)
    rec = make_reconciler(
        mock_adapter,
        positions=[make_position('p1', 'P1')],
        trading_model=TradingModel.MARGIN,
    )
    result = rec.reconcile()
    assert any(p.broker_ref == 'MOCK-GHOST-001' for p in result.ghost_positions)
    assert not result.is_clean


def test_margin_orphan_position(mock_adapter, make_reconciler):
    mock_adapter.set_broker_positions([])
    rec = make_reconciler(
        mock_adapter,
        positions=[make_position('p1', 'P1')],
        trading_model=TradingModel.MARGIN,
    )
    result = rec.reconcile()
    assert [p.broker_ref for p in result.orphan_positions] == ['P1']


def test_margin_stale_position_price(mock_adapter, make_reconciler):
    mock_adapter.set_broker_positions([make_broker_position('P1', entry_price=2000.0)])
    mock_adapter.set_divergence_mode(MockDivergenceMode.STALE_PRICE)  # +1% → 2020
    rec = make_reconciler(
        mock_adapter,
        positions=[make_position('p1', 'P1', entry_price=2000.0)],
        trading_model=TradingModel.MARGIN,
    )
    result = rec.reconcile()
    assert len(result.stale_positions) == 1
