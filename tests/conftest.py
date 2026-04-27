"""
FiniexTestingIDE - Root Test Configuration

Auto-marks all tests by their filesystem path so that pytest marks
stay centralized — no marks needed in individual test files.

Mark taxonomy (see docs/tests/test_taxonomy.md):
- simulation    tests/simulation/
- autotrader    tests/autotrader/
- parity        tests/parity/
- framework     tests/framework/
- data          tests/data/
- live_adapter  tests/live_adapters/
- benchmark     tests/simulation/benchmark/
- integration   tests with 'integration' in their path
- unit          order_guard, live_executor, safety, bar_rendering, workers, etc.
"""

import pytest


def pytest_collection_modifyitems(items):
    """Auto-apply pipeline domain marks based on test file path."""
    for item in items:
        path = str(item.fspath)

        # Pipeline domain
        if '/tests/parity/' in path:
            item.add_marker(pytest.mark.parity)
        if '/tests/simulation/' in path:
            item.add_marker(pytest.mark.simulation)
        if '/tests/autotrader/' in path:
            item.add_marker(pytest.mark.autotrader)
        if '/tests/framework/' in path:
            item.add_marker(pytest.mark.framework)
        if '/tests/data/' in path:
            item.add_marker(pytest.mark.data)

        # Live broker adapter tests (excluded from normal runner — require real account)
        if '/tests/live_adapters/' in path:
            item.add_marker(pytest.mark.live_adapter)

        # Benchmark (subset of simulation — excluded from normal runner)
        if '/tests/simulation/benchmark/' in path:
            item.add_marker(pytest.mark.benchmark)

        # Integration: full-pipeline end-to-end runs
        if '/integration/' in path:
            item.add_marker(pytest.mark.integration)

        # Unit: isolated component tests (no full pipeline)
        _UNIT_PATHS = (
            '/tests/autotrader/order_guard/',
            '/tests/autotrader/live_executor/',
            '/tests/autotrader/safety/',
            '/tests/framework/bar_rendering/',
            '/tests/framework/batch_validations/',
            '/tests/framework/worker_tests/',
            '/tests/framework/market_compatibility/',
            '/tests/framework/tick_parquet_reader/',
            '/tests/framework/user_namespace/',
        )
        if any(p in path for p in _UNIT_PATHS):
            item.add_marker(pytest.mark.unit)
