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

### test_autotrader_trade_scenarios.py (12 Tests)

Targeted scenario tests for specific AutoTrader pipeline behaviors: SL/TP level propagation, duplicate signal suppression, and resilience under minimal warmup data.

Each class runs an independent session from its own profile. Sessions are module-scoped.

> **Architectural note:** In the AutoTrader pipeline, SL/TP triggering is broker-side (live: Kraken handles it). Engine-side SL/TP monitoring (`_check_sl_tp_levels`) runs only in `ExecutorMode.SIMULATION`. `LiveTradeExecutor` uses `LIVE` mode — MockAdapter does not implement broker-side SL/TP monitoring, so positions always close via `SCENARIO_END` in these tests. The SL/TP tests verify the configuration propagation path, not the trigger path.

| Class | Tests | What it validates |
|-------|-------|-------------------|
| `TestStopLossConfiguration` | 3 | SL level flows: decision → executor → TradeRecord.stop_loss == 89200.0; entry_price > 0; no session errors |
| `TestTakeProfitConfiguration` | 3 | TP level flows: decision → executor → TradeRecord.take_profit == 89350.0; entry_price > 0; no session errors |
| `TestDuplicateSignalGuard` | 3 | Exactly 1 position opened despite 490 repeated BUY signals (hold_ticks=5000 > max_ticks=500); SCENARIO_END close; no errors |
| `TestMinimalWarmup` | 3 | Session completes without crash when bar_max_history=30 starves M30 workers; ticks processed; no errors |

**Data Dependency:** All four profiles use BTCUSD parquet `BTCUSD_20260124_141946.parquet`. Profiles: `sl_triggered_test.json`, `tp_triggered_test.json`, `duplicate_signal_guard_test.json`, `minimal_warmup_test.json`.

**Runtime:** ~6 seconds total across all 4 sessions.

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

# Trade scenarios only
pytest tests/autotrader/integration/test_autotrader_trade_scenarios.py -v
```

VS Code: `🧩 Pytest: AutoTrader Integration (All)` — runs all four files.
VS Code: `🧩 Pytest: AutoTrader Trade Lifecycle` — trade lifecycle only.
VS Code: `🧪 AutoTrader: SL Triggered` / `TP Triggered` / `Duplicate Signal Guard` / `Minimal Warmup` — individual scenario CLI runs with live display.
