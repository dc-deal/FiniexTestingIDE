# Data Integration Tests Documentation

## Overview

The data integration test suite validates the data pipeline integrity from tick import through bar rendering to index generation. Tests ensure volume and tick count data flows correctly across all market types.

**Test Location:** `tests/data_integration/`
**Index Source:** `.parquet_bars_index.json` (auto-loaded via BarsIndexManager)

**Market Type Rules:**
| Market | Volume | Tick Count |
|--------|--------|------------|
| crypto | > 0 (real trade volume in base currency) | > 0 |
| forex | == 0 (CFD has no real volume) | > 0 |

**Total Tests:** 9

---

## Fixtures (conftest.py)

### Core Manager Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `bars_index_manager` | session | BarsIndexManager with loaded index, provides access to all bar files |
| `market_config` | session | MarketConfigManager for broker_type → market_type lookup |

### Index Data Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `available_broker_types` | session | List of broker types in index (e.g., ['kraken_spot', 'mt5']) |
| `index_data` | session | Raw index dict: {broker_type: {symbol: {timeframe: entry}}} |

### Helper Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `bar_file_loader` | session | Factory function to load bar DataFrame by broker/symbol/timeframe |
| `get_market_type` | session | Factory function to get market_type for a broker_type |

---

## Test Files

### test_volume_integrity.py (9 Tests)

Validates volume data consistency across the data pipeline for all markets.

#### TestVolumeSchema (2 Tests)

Schema validation ensuring required columns exist in all bar files.

| Test | Description |
|------|-------------|
| `test_volume_column_exists_in_all_bars` | Every bar file must have a 'volume' column. Tests one timeframe (M30 preferred) per symbol across all broker types. |
| `test_tick_count_column_exists` | Every bar file must have a 'tick_count' column. Same sampling strategy as volume test. |

---

#### TestCryptoVolume (2 Tests)

Validates crypto markets have real trade volume data.

| Test | Description |
|------|-------------|
| `test_crypto_has_positive_volume` | For crypto markets: `sum(volume) > 0` for real bars. Skips if no crypto data available. Only checks bars where `bar_type == 'real'` (excludes synthetic gap-fill bars). |
| `test_crypto_volume_per_bar_positive` | No individual bar should have negative volume. Catches data corruption or calculation errors. |

**Skip Condition:** Test skips with message "No crypto data available" if no broker_type maps to market_type 'crypto'.

---

#### TestForexVolume (1 Test)

Validates forex CFD markets correctly report zero volume.

| Test | Description |
|------|-------------|
| `test_forex_has_zero_volume` | For forex markets: `sum(volume) == 0`. CFD instruments have no real trade volume - only tick frequency. Skips if no forex data available. |

**Skip Condition:** Test skips with message "No forex data available" if no broker_type maps to market_type 'forex'.

---

#### TestTickCount (1 Test)

Validates tick count is positive for all markets regardless of type.

| Test | Description |
|------|-------------|
| `test_all_markets_have_positive_tick_count` | All markets (crypto and forex) must have `sum(tick_count) > 0` for real bars. Tick count represents market activity and should always be positive. |

---

#### TestIndexBarConsistency (2 Tests)

Validates that index statistics match actual bar data in parquet files.

| Test | Description |
|------|-------------|
| `test_index_volume_matches_bar_data` | Index `total_trade_volume` must equal `sum(df['volume'])` from parquet. Tolerance: 0.01 absolute or 0.1% relative. Catches index rebuild issues. |
| `test_index_tick_count_matches_bar_data` | Index `total_tick_count` must exactly equal `sum(df['tick_count'])` from parquet. No tolerance - integers must match exactly. |

---

#### TestAllTimeframes (1 Test)

Cross-timeframe consistency validation.

| Test | Description |
|------|-------------|
| `test_volume_consistent_across_timeframes` | For crypto: total volume should be ~equal across M1, M5, M15, M30, H1, H4, D1 (within 1%). Small differences due to bar boundary alignment are acceptable. Uses `pytest.xfail` for warnings rather than hard failure. |

---

## Architecture Notes

### Test Design Philosophy

The test suite uses **exhaustive scanning** rather than sampling:

1. Iterates all broker_types in the index
2. For each broker_type, iterates all symbols
3. Tests one representative timeframe per symbol (M30 preferred)
4. Collects all errors before asserting (comprehensive error reporting)

### Data Flow Validated

```
Tick Import (JSON)
  └→ real_volume field preserved
       └→ Bar Renderer (vectorized_bar_renderer.py)
            └→ sum(real_volume) → volume column
                 └→ Bar Index (bars_index_manager.py)
                      └→ total_trade_volume, avg_volume_per_bar
```

### Market Type Lookup

Market type is determined via `MarketConfigManager.get_market_type(broker_type)` which returns a `MarketType` enum:

| broker_type | market_type | volume expectation |
|-------------|-------------|-------------------|
| kraken_spot | MarketType.CRYPTO | > 0 |
| mt5 | MarketType.FOREX | == 0 |

### Error Collection Pattern

Tests collect errors across all symbols before asserting:

```python
errors = []
for broker_type in bars_index_manager.list_broker_types():
    for symbol in bars_index_manager.list_symbols(broker_type):
        # ... validation ...
        if problem:
            errors.append(f"{broker_type}/{symbol}: {problem}")

assert not errors, f"Errors found:\n" + "\n".join(errors)
```

This provides comprehensive failure reporting rather than stopping at first error.

---

## Troubleshooting

### "No crypto data available" / "No forex data available"

**Cause:** `MarketConfigManager.get_market_type()` returns unexpected value.

**Debug:**
```python
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.broker_types import MarketType

config = MarketConfigManager()
print(config.get_market_type("kraken_spot"))  # Expected: MarketType.CRYPTO
print(config.get_market_type("mt5"))          # Expected: MarketType.FOREX
```

**Verify:** Returned value must be a `MarketType` enum, not a string.

### Volume Mismatch Between Index and Bars

**Cause:** Index was built before bar re-rendering.

**Fix:**
```bash
python -m python.cli.data_index_cli bars --rebuild
```

### Tick Count Zero for Real Bars

**Cause:** Bar renderer not counting ticks correctly.

**Check:** Verify `bar_type` column - only 'real' bars should have tick_count > 0. Synthetic bars may have tick_count = 0.
