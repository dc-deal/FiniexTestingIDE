"""
Phase 1 — MockBrokerAdapter broker truth-pulls + divergence injection (#151).

Validates that the get_broker_* pulls return seeded state and that each
MockDivergenceMode perturbs the return value as documented.
"""

from python.framework.testing.mock_broker_adapter import MockDivergenceMode
from tests.autotrader.reconciliation.conftest import make_broker_order, make_broker_position


def test_get_broker_orders_returns_seeded(mock_adapter):
    mock_adapter.set_broker_orders([make_broker_order('OABC')])
    orders = mock_adapter.get_broker_orders()
    assert len(orders) == 1
    assert orders[0].broker_ref == 'OABC'


def test_drop_orders_mode_returns_empty(mock_adapter):
    mock_adapter.set_broker_orders([make_broker_order('OABC')])
    mock_adapter.set_divergence_mode(MockDivergenceMode.DROP_ORDERS)
    assert mock_adapter.get_broker_orders() == []


def test_get_broker_balances_returns_seeded(mock_adapter):
    mock_adapter.set_broker_balances({'USD': 100.0, 'ETH': 0.01})
    assert mock_adapter.get_broker_balances() == {'USD': 100.0, 'ETH': 0.01}


def test_drop_balance_mode_returns_empty(mock_adapter):
    mock_adapter.set_broker_balances({'USD': 100.0})
    mock_adapter.set_divergence_mode(MockDivergenceMode.DROP_BALANCE)
    assert mock_adapter.get_broker_balances() == {}


def test_get_broker_positions_returns_seeded(mock_adapter):
    mock_adapter.set_broker_positions([make_broker_position('PABC')])
    positions = mock_adapter.get_broker_positions()
    assert len(positions) == 1
    assert positions[0].broker_ref == 'PABC'


def test_phantom_position_mode_adds_ghost(mock_adapter):
    mock_adapter.set_broker_positions([make_broker_position('PABC')])
    mock_adapter.set_divergence_mode(MockDivergenceMode.PHANTOM_POSITION)
    positions = mock_adapter.get_broker_positions()
    assert len(positions) == 2
    assert any(p.broker_ref == 'MOCK-GHOST-001' for p in positions)


def test_stale_price_mode_shifts_entry(mock_adapter):
    mock_adapter.set_broker_positions([make_broker_position('PABC', entry_price=2000.0)])
    mock_adapter.set_divergence_mode(MockDivergenceMode.STALE_PRICE)
    positions = mock_adapter.get_broker_positions()
    assert positions[0].entry_price == 2020.0  # +1%
