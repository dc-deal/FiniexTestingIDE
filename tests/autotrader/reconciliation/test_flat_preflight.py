"""
Reconciler flat-preflight (is_account_flat) on SPOT (#151).

Flat on spot = no resting broker orders AND no non-quote asset balance above the
dust threshold. The quote currency is resolved from the traded symbol's spec
(ETHUSD → USD on the mock).
"""

from tests.autotrader.reconciliation.conftest import make_broker_order


def test_flat_when_no_orders_and_quote_only(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([])
    mock_adapter.set_broker_balances({'USD': 100.0})
    rec = make_reconciler(mock_adapter)
    result = rec.is_account_flat()
    assert result.is_flat
    assert result.reasons == []


def test_not_flat_with_resting_order(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([make_broker_order('O1')])
    mock_adapter.set_broker_balances({'USD': 100.0})
    rec = make_reconciler(mock_adapter)
    result = rec.is_account_flat()
    assert not result.is_flat
    assert len(result.open_orders) == 1


def test_not_flat_with_asset_balance(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([])
    mock_adapter.set_broker_balances({'USD': 100.0, 'ETH': 0.01})
    rec = make_reconciler(mock_adapter)
    result = rec.is_account_flat()
    assert not result.is_flat
    assert 'ETH' in result.asset_balances


def test_dust_asset_balance_counts_as_flat(mock_adapter, make_reconciler):
    mock_adapter.set_broker_orders([])
    mock_adapter.set_broker_balances({'USD': 100.0, 'ETH': 1e-12})
    rec = make_reconciler(mock_adapter)
    result = rec.is_account_flat()
    assert result.is_flat
