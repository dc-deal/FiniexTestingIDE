"""
ApiPerfMonitor aggregation + error/slow logging (#351).

Uses a MagicMock logger to assert the abnormal-only logging (errors + slow calls)
without a real logger backend.
"""

from unittest.mock import MagicMock

from python.framework.reporting.api_perf_monitor import ApiPerfMonitor
from python.framework.types.config_types.autotrader_defaults_config_types import ApiMonitorConfig


def _monitor(threshold: float = 3000.0, logger=None) -> ApiPerfMonitor:
    return ApiPerfMonitor(
        ApiMonitorConfig(enabled=True, slow_call_threshold_ms=threshold),
        logger or MagicMock(),
    )


def test_record_aggregates():
    m = _monitor()
    m.record('/0/private/OpenOrders', 100.0)
    m.record('/0/private/OpenOrders', 300.0)
    snap = m.get_snapshot()
    assert len(snap.endpoints) == 1
    s = snap.endpoints[0]
    assert s.endpoint == '/0/private/OpenOrders'
    assert s.count == 2
    assert s.avg_ms == 200.0
    assert s.min_ms == 100.0
    assert s.max_ms == 300.0
    assert s.last_ms == 300.0
    assert s.last_fired_at is not None


def test_one_row_per_endpoint_repeat_updates_new_adds():
    m = _monitor()
    m.record('/0/private/OpenOrders', 100.0)
    m.record('/0/private/Balance', 50.0)
    m.record('/0/private/OpenOrders', 200.0)  # repeat → updates, NOT a new row
    snap = m.get_snapshot()
    assert len(snap.endpoints) == 2          # one row per endpoint
    assert snap.endpoints[0].endpoint == '/0/private/OpenOrders'  # busiest first
    assert snap.endpoints[0].count == 2


def test_error_counting_and_last_error():
    m = _monitor()
    m.record('/0/private/AddOrder', 80.0)
    m.record('/0/private/AddOrder', 90.0, success=False, error='Kraken API error: ERate:Limit')
    snap = m.get_snapshot()
    s = snap.endpoints[0]
    assert s.count == 2
    assert s.error_count == 1
    assert s.last_error == 'Kraken API error: ERate:Limit'
    assert snap.total_errors == 1


def test_error_logged_as_warning():
    logger = MagicMock()
    m = _monitor(logger=logger)
    m.record('/0/private/AddOrder', 80.0, success=False, error='boom')
    assert any('[API]' in str(c) and 'failed' in str(c) for c in logger.warning.call_args_list)


def test_slow_call_logged_and_counted():
    logger = MagicMock()
    m = _monitor(threshold=1000.0, logger=logger)
    m.record('/0/private/OpenOrders', 1500.0)  # > 1000 → slow
    snap = m.get_snapshot()
    assert snap.slow_count == 1
    assert any('slow' in str(c) for c in logger.warning.call_args_list)


def test_fast_clean_call_is_silent():
    logger = MagicMock()
    m = _monitor(threshold=3000.0, logger=logger)
    m.record('/0/private/OpenOrders', 200.0)
    assert not logger.warning.called
    assert m.get_snapshot().slow_count == 0


def test_shutdown_emits_summary():
    logger = MagicMock()
    m = _monitor(logger=logger)
    m.record('/0/private/Balance', 100.0)
    m.shutdown()
    assert any('API Performance final' in str(c) for c in logger.info.call_args_list)
