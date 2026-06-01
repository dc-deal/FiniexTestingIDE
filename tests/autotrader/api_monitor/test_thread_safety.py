"""
ApiPerfMonitor thread-safety (#351).

record() is called from the tick-loop thread AND worker threads (#319/#320/#327),
so it must not lose updates under concurrency.
"""

import threading
from unittest.mock import MagicMock

from python.framework.reporting.api_perf_monitor import ApiPerfMonitor
from python.framework.types.config_types.autotrader_defaults_config_types import ApiMonitorConfig


def test_concurrent_record_no_lost_updates():
    monitor = ApiPerfMonitor(ApiMonitorConfig(enabled=True), MagicMock())
    per_thread = 200
    n_threads = 8

    def worker():
        for _ in range(per_thread):
            monitor.record('/0/private/OpenOrders', 10.0)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = monitor.get_snapshot()
    assert len(snap.endpoints) == 1
    assert snap.endpoints[0].count == per_thread * n_threads


def test_concurrent_record_mixed_endpoints_and_errors():
    monitor = ApiPerfMonitor(ApiMonitorConfig(enabled=True), MagicMock())

    def ok_worker():
        for _ in range(100):
            monitor.record('/0/private/Balance', 5.0)

    def err_worker():
        for _ in range(100):
            monitor.record('/0/private/AddOrder', 5.0, success=False, error='x')

    threads = [threading.Thread(target=ok_worker) for _ in range(4)]
    threads += [threading.Thread(target=err_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = monitor.get_snapshot()
    by_ep = {s.endpoint: s for s in snap.endpoints}
    assert by_ep['/0/private/Balance'].count == 400
    assert by_ep['/0/private/AddOrder'].count == 400
    assert by_ep['/0/private/AddOrder'].error_count == 400
    assert snap.total_errors == 400
