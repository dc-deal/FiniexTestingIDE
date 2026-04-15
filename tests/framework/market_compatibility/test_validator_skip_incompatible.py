"""
FiniexTestingIDE - Market Compatibility Tests — Skip & Report

Verifies that incompatible worker/broker combinations are reported as
errors by ScenarioDataValidator.validate_worker_market_compatibility().
The validator returns a list of error strings — empty on success — which
the DataCoverageReportManager wraps into a ValidationResult so the
failing scenario is skipped while the batch continues.
"""

from tests.framework.market_compatibility.conftest import make_scenario


def test_obv_on_forex_broker_is_rejected(
    validator, worker_factory, market_config_manager
):
    """OBV requires 'volume', MT5 (forex) provides 'tick_count' → error."""
    scenario = make_scenario(
        name='obv_forex',
        data_broker_type='mt5',
        worker_instances={'obv_main': 'CORE/obv'},
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )

    assert len(errors) == 1
    msg = errors[0]
    assert 'obv_main' in msg
    assert 'CORE/obv' in msg
    assert "'volume'" in msg
    assert 'mt5' in msg
    assert "'tick_count'" in msg
    assert 'forex' in msg


def test_error_message_is_actionable(validator, worker_factory, market_config_manager):
    """Message must tell the user how to fix the scenario."""
    scenario = make_scenario(
        name='obv_forex',
        data_broker_type='mt5',
        worker_instances={'obv_main': 'CORE/obv'},
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )

    msg = errors[0].lower()
    assert 'remove' in msg or 'switch' in msg


def test_multiple_incompatible_workers_all_reported(
    validator, worker_factory, market_config_manager
):
    """Two incompatible workers → two errors, one per worker."""
    scenario = make_scenario(
        name='double_obv_forex',
        data_broker_type='mt5',
        worker_instances={
            'obv_a': 'CORE/obv',
            'obv_b': 'CORE/obv',
        },
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )

    assert len(errors) == 2
    combined = ' | '.join(errors)
    assert 'obv_a' in combined
    assert 'obv_b' in combined


def test_mixed_valid_and_invalid_workers_only_invalid_reported(
    validator, worker_factory, market_config_manager
):
    """Valid workers don't produce errors; only the incompatible one does."""
    scenario = make_scenario(
        name='mixed_forex',
        data_broker_type='mt5',
        worker_instances={
            'rsi_main': 'CORE/rsi',
            'env_main': 'CORE/envelope',
            'obv_main': 'CORE/obv',
        },
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )

    assert len(errors) == 1
    assert 'obv_main' in errors[0]
    assert 'rsi_main' not in errors[0]
    assert 'env_main' not in errors[0]


def test_unknown_worker_type_reported_not_raised(
    validator, worker_factory, market_config_manager
):
    """Unknown CORE worker must surface as an error, not an exception."""
    scenario = make_scenario(
        name='unknown_worker',
        data_broker_type='mt5',
        worker_instances={'ghost': 'CORE/does_not_exist'},
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )

    assert len(errors) == 1
    assert 'ghost' in errors[0]
    assert 'CORE/does_not_exist' in errors[0]


def test_unknown_broker_reports_single_error(
    validator, worker_factory, market_config_manager
):
    """Unknown broker short-circuits: one error, no per-worker iteration."""
    scenario = make_scenario(
        name='bad_broker',
        data_broker_type='mystery_broker',
        worker_instances={'rsi_main': 'CORE/rsi'},
    )

    errors = validator.validate_worker_market_compatibility(
        scenario, worker_factory, market_config_manager
    )

    assert len(errors) == 1
    assert 'mystery_broker' in errors[0]
