# AutoTrader Integration Tests

## Purpose

End-to-end validation of the AutoTrader mock pipeline. Runs a complete session with deterministic parquet replay data and asserts on the `AutoTraderResult`.

## Test Suite

| Test | What it validates |
|------|-------------------|
| `test_full_mock_session` | Normal shutdown, tick count (29780), 0 clipping, 0 warnings/errors, trades produced, stats collected |
| `test_log_files_created` | Log directory structure: global, summary, session_logs/, trades CSV, orders CSV |

## Data Dependency

Uses `configs/autotrader_profiles/btcusd_mock.json` with parquet file `data/processed/kraken_spot/ticks/BTCUSD/BTCUSD_20260124_141946.parquet`. Same data as the backtesting baseline tests.

## Runtime

~6 seconds per test (29,780 ticks in replay mode). Two tests = ~12 seconds total.

## Running

```bash
pytest tests/autotrader_integration/ -v --tb=short
```

VS Code: `🧩 Pytest: AutoTrader Integration (All)`
