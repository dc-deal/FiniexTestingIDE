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

**Data Dependency:** Uses `configs/autotrader_profiles/backtesting/mock_session_test.json` with parquet file `data/processed/kraken_spot/ticks/BTCUSD/BTCUSD_20260124_141946.parquet`.

**Runtime:** ~6 seconds total (session shared across both tests via `scope='module'`).

### test_autotrader_trade_lifecycle.py (15 Tests)

Trade lifecycle validation through the AutoTrader mock pipeline. Uses `mock_session_test.json` (simple_consensus, parquet replay) which produces real fill prices — unlike dry-run live sessions where entry price is 0.

One session is shared across all test classes (`scope='module'`) to avoid running 29780 ticks multiple times.

| Class | Tests | What it validates |
|-------|-------|-------------------|
| `TestNormalCycle` | 6 | Normal shutdown, trades produced, orders recorded, no errors, valid entry/exit prices, valid directions |
| `TestClosePaths` | 3 | All close_reason values are valid enum members, no orphaned positions, finite P&L |
| `TestPortfolioIntegrity` | 4 | Portfolio stats present, trade count matches history, W+L = total, balance changed after trades |
| `TestSessionEndWithOpenPosition` | 1 | SCENARIO_END trades have valid exit prices |
| `TestLogFiles` | 1 | All log files and directories created |

**Data Dependency:** Uses `configs/autotrader_profiles/backtesting/trade_lifecycle_test.json` — same BTCUSD parquet, `max_ticks: 3000`, display off.

**Runtime:** ~6 seconds total (session shared across 14 tests via `scope='module'`, LogFiles test runs own session).

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
pytest tests/autotrader/integration/ -v --tb=short

# Trade lifecycle only
pytest tests/autotrader/integration/test_autotrader_trade_lifecycle.py -v

# Clipping monitor only
pytest tests/autotrader/integration/test_live_clipping_monitor.py -v
```

VS Code: `🧩 Pytest: AutoTrader Integration (All)` — runs all three files.
VS Code: `🧩 Pytest: AutoTrader Trade Lifecycle` — trade lifecycle only.
