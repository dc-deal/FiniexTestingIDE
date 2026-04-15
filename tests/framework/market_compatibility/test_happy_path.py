"""
FiniexTestingIDE - Market Compatibility Tests — Happy Path

Valid worker/broker combinations must pass validation without errors:
- RSI (price-based, metric=None) on any broker
- OBV (metric=volume) on kraken_spot (crypto → volume)
- Mixed valid scenario with multiple workers on a compatible broker
"""

from tests.framework.market_compatibility.conftest import make_scenario


def test_rsi_on_forex_broker_passes(validator, worker_factory, market_config_manager):
    """RSI declares None → compatible with forex (tick_count)."""
    scenario = make_scenario(
        name='rsi_forex',
        data_broker_type='mt5',
        worker_instances={'rsi_main': 'CORE/rsi'},
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )
    assert errors == []


def test_rsi_on_crypto_broker_passes(validator, worker_factory, market_config_manager):
    """RSI declares None → compatible with crypto (volume) as well."""
    scenario = make_scenario(
        name='rsi_crypto',
        data_broker_type='kraken_spot',
        worker_instances={'rsi_main': 'CORE/rsi'},
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )
    assert errors == []


def test_obv_on_crypto_broker_passes(validator, worker_factory, market_config_manager):
    """OBV requires 'volume' → compatible with kraken_spot."""
    scenario = make_scenario(
        name='obv_crypto',
        data_broker_type='kraken_spot',
        worker_instances={'obv_main': 'CORE/obv'},
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )
    assert errors == []


def test_mixed_workers_on_crypto_broker_passes(
    validator, worker_factory, market_config_manager
):
    """RSI + Envelope + OBV together on crypto — all compatible."""
    scenario = make_scenario(
        name='mixed_crypto',
        data_broker_type='kraken_spot',
        worker_instances={
            'rsi_main': 'CORE/rsi',
            'env_main': 'CORE/envelope',
            'obv_main': 'CORE/obv',
        },
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )
    assert errors == []


def test_empty_worker_instances_passes(validator, worker_factory, market_config_manager):
    """Scenario without workers — validator must not crash and return no errors."""
    scenario = make_scenario(
        name='empty',
        data_broker_type='mt5',
        worker_instances={},
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )
    assert errors == []
