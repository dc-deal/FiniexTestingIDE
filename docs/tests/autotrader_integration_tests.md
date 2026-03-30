# AutoTrader Integration Tests

## Purpose

End-to-end validation of the AutoTrader mock pipeline and unit testing of AutoTrader components.

## Test Files

### test_autotrader_mock_session.py (2 Tests)

Full pipeline integration: runs a complete session with deterministic parquet replay data and asserts on the `AutoTraderResult`.

| Test | What it validates |
|------|-------------------|
| `test_full_mock_session` | Normal shutdown, tick count (29780), 0 clipping, 0 warnings/errors, trades produced, stats collected |
| `test_log_files_created` | Log directory structure: global, summary, session_logs/, trades CSV, orders CSV |

**Data Dependency:** Uses `configs/autotrader_profiles/backtesting/btcusd_mock.json` with parquet file `data/processed/kraken_spot/ticks/BTCUSD/BTCUSD_20260124_141946.parquet`.

**Runtime:** ~6 seconds per test (29,780 ticks in replay mode).

### test_live_clipping_monitor.py (22 Tests)

Unit tests for `LiveClippingMonitor` — no external dependencies, no tick data, no time dependency (mocked where needed).

| Class | Tests | What it validates |
|-------|-------|-------------------|
| `TestClippingDetection` | 5 | Core logic: processing_ms > tick_delta_ms triggers clipping, boundary cases (equal, zero, negative delta) |
| `TestCounterAccuracy` | 4 | Mixed sequences, max/avg tracking, processing_times_ms list |
| `TestQueueDepthTracking` | 2 | Max queue depth updates, zero depth |
| `TestPeriodicReports` | 5 | Interval timing, report generation, counter reset, zero-tick intervals, session totals persistence |
| `TestSessionSummaryEdgeCases` | 4 | Empty session, 100% clipping, 0% clipping, single tick |
| `TestStrategy` | 2 | Default and custom strategy |

**Runtime:** <0.5 seconds.

## Running

```bash
# Full suite
pytest tests/autotrader_integration/ -v --tb=short

# Clipping monitor only
pytest tests/autotrader_integration/test_live_clipping_monitor.py -v
```

VS Code: `🧩 Pytest: AutoTrader Integration (All)`
