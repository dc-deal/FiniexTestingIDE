# MQL5 Tick Data Collection System
**TickCollector Enhanced v1.0.5 - Professional Tick Data Acquisition**

---

## Overview

Professional tick data collector for MetaTrader 5 with tiered error tracking and quality assurance. Designed for high-fidelity algorithmic trading backtesting with real market data.

**Key Features:**
- Tiered error classification (Negligible/Serious/Fatal)
- Automatic data quality scoring
- Intelligent anomaly detection (spread jumps, time gaps, price anomalies)
- Adaptive validation with configurable thresholds
- Stream corruption detection
- Comprehensive error reporting with full metadata

## Error Classification System

### Three-Tier Severity Model

#### NEGLIGIBLE (Severity Level 0)
**Characteristics:**
- Minor data quality issues
- Does not impact backtesting accuracy significantly
- Data remains fully usable

**Error Types:**
- **Spread Jumps** < 50% (e.g., 1.1 pips → 1.6 pips)
- **Small Data Gaps** 60-300 seconds (e.g., during low liquidity)
- **Missing Tick Flags** (flag data unavailable)
- **Negative Real Volume** (broker reporting issue)

**Action:** Log for monitoring, no data rejection

---

#### SERIOUS (Severity Level 1)
**Characteristics:**
- Significant data quality issues
- May impact backtesting accuracy
- Data usable with restrictions

**Error Types:**
- **Extreme Spreads** > 5% of price (e.g., 50+ pips on EURUSD)
- **Large Data Gaps** > 5 minutes (connection loss)
- **Price Jumps** > 10% (flash crash / data spike)
- **Millisecond Time Regressions** (time moves backward slightly)
- **Negative Tick Volume** (impossible value)

**Action:** Log with warning, flag affected ticks, consider filtering

---

#### FATAL (Severity Level 2)
**Characteristics:**
- Critical data integrity violations
- Backtesting results unreliable
- Data potentially unusable

**Error Types:**
- **Bid/Ask ≤ 0** (invalid price data)
- **Inverted Spread** (Ask < Bid - impossible)
- **Spread ≤ 0** (zero or negative spread)
- **Time Regressions** (timestamp moving backward)

**Action:** Log as critical, reject data, optionally stop collection

---

## Installation & Setup

### Step 1: Installation

1. Copy `TickCollector.mq5` to your MetaTrader 5 Experts folder:
   ```
   C:\Users\[YourUser]\AppData\Roaming\MetaQuotes\Terminal\[TerminalID]\MQL5\Experts\
   ```

2. Compile in MetaEditor (F7)

3. Attach to any chart (recommended: EURUSD, GBPUSD, USDJPY, AUDUSD)

4. Configure export path: `C:\FiniexData\` (or leave empty for default)

### Step 2: Configuration

**Basic Parameters:**
```cpp
input string ExportPath = "";                    // Empty = MQL5 default folder
input bool CollectTicks = true;                  // Enable/disable collection
input int MaxTicksPerFile = 50000;               // Ticks per file (rotation)
input bool IncludeRealVolume = true;             // Collect real volume
input bool IncludeTickFlags = true;              // Collect tick flags
```

**Error Tracking Configuration:**
```cpp
input bool EnableErrorTracking = true;           // Enable error system
input int MaxErrorsPerFile = 1000;               // Max errors per file
input bool LogNegligibleErrors = true;           // Log negligible errors
input bool LogSeriousErrors = true;              // Log serious errors
input bool LogFatalErrors = true;                // Log fatal errors
input bool StopOnFatalErrors = false;            // Stop collection on fatal
```

**Validation Thresholds:**
```cpp
// Default values (customizable per symbol)
maxSpreadPercent = 5.0;        // Max 5% spread
maxPriceJumpPercent = 10.0;    // Max 10% price jump
maxDataGapSeconds = 300;       // Max 5 min data gap
warningDataGapSeconds = 60;    // Warning at 1 min gap
```

---

## JSON Output Structure

### Complete File Structure

```json
{
  "metadata": { ... },
  "ticks": [ ... ],
  "errors": {
    "by_severity": { ... },
    "details": [ ... ]
  },
  "summary": {
    "data_stream_status": "HEALTHY",
    "quality_metrics": { ... },
    "timing": { ... },
    "recommendations": "..."
  }
}
```

### Metadata Section

```json
{
  "metadata": {
    "symbol": "EURUSD",
    "broker": "Vantage International Group Limited",
    "server": "VantageInternational-Demo",
    "broker_utc_offset_hours": 0,
    "local_device_time": "2025.11.23 20:23:45",
    "broker_server_time": "2025.11.23 21:23:45",
    "start_time": "2025.11.23 21:23:45",
    "start_time_unix": 1763933025,
    "timeframe": "TICK",
    "volume_timeframe": "PERIOD_M1",
    "volume_timeframe_minutes": 1,
    "data_format_version": "1.0.5",
    "data_collector": "mt5",
    "market_type": "forex_cfd",
    "collection_purpose": "backtesting",
    "operator": "automated",
    "symbol_info": {
      "point_value": 0.00001000,
      "digits": 5,
      "tick_size": 0.00001000,
      "tick_value": 0.86841740
    },
    "collection_settings": {
      "max_ticks_per_file": 50000,
      "max_errors_per_file": 1000,
      "include_real_volume": true,
      "include_tick_flags": true,
      "stop_on_fatal_errors": false
    },
    "error_tracking": {
      "enabled": true,
      "log_negligible": true,
      "log_serious": true,
      "log_fatal": true,
      "max_spread_percent": 5.00,
      "max_price_jump_percent": 10.00,
      "max_data_gap_seconds": 300
    }
  }
}
```

**Key Fields:**
- `data_format_version`: "1.0.5" - Current JSON format version
- `market_type`: "forex_cfd" - Market classification
- `collection_purpose`: "backtesting" - Use case identifier
- `volume_timeframe`: "PERIOD_M1" - Volume aggregation period
- `error_tracking.enabled`: true - Error system active

### Tick Data Section

```json
{
  "ticks": [
    {
      "timestamp": "2025.11.23 21:23:45.123",
      "timestamp_unix": 1763933025,
      "bid": 1.05234,
      "ask": 1.05246,
      "volume": 123,
      "flags": 6
    }
  ]
}
```

**Tick Fields:**
- `timestamp`: Human-readable time (broker server time)
- `timestamp_unix`: Unix timestamp (milliseconds)
- `bid`: Bid price
- `ask`: Ask price
- `volume`: Real volume (if available) or tick volume
- `flags`: Tick flags (bid/ask/last/volume changes)

### Error Report Section

```json
{
  "errors": {
    "by_severity": {
      "negligible": 2,
      "serious": 0,
      "fatal": 0
    },
    "details": [
      {
        "severity": "negligible",
        "severity_level": 0,
        "type": "spread_jump",
        "description": "Spread jump: 0.00011 to 0.00018 (63.6% change)",
        "timestamp": "2025.09.16 22:39:09",
        "tick_context": 5,
        "affected_value": 0.00007000,
        "additional_data": "prev_spread=0.00011"
      }
    ]
  }
}
```

**Error Fields:**
- `severity`: "negligible" | "serious" | "fatal"
- `severity_level`: 0 | 1 | 2 (numeric for sorting)
- `type`: Error classification (e.g., "spread_jump")
- `description`: Human-readable explanation
- `timestamp`: When error occurred
- `tick_context`: Tick index in file
- `affected_value`: Problematic value
- `additional_data`: Context information

### Summary Section

```json
{
  "summary": {
    "total_ticks": 38,
    "total_errors": 2,
    "data_stream_status": "HEALTHY",
    "quality_metrics": {
      "overall_quality_score": 0.947368,
      "data_integrity_score": 1.000000,
      "data_reliability_score": 1.000000,
      "negligible_error_rate": 0.052632,
      "serious_error_rate": 0.000000,
      "fatal_error_rate": 0.000000
    },
    "timing": {
      "end_time": "2025.11.23 21:23:54",
      "duration_minutes": 0.1,
      "avg_ticks_per_minute": 380.0
    },
    "recommendations": "Data quality is excellent - no specific recommendations."
  }
}
```

---

## Data Quality Scoring System

### Quality Metrics Calculation

**Overall Quality Score:**
```
overall_quality_score = 1.0 - (total_errors / total_ticks)
```
- **Perfect:** 1.0 (no errors)
- **Excellent:** 0.95-0.99 (< 5% error rate)
- **Good:** 0.90-0.95 (5-10% error rate)
- **Poor:** < 0.90 (> 10% error rate)

**Data Integrity Score:**
```
data_integrity_score = 1.0 - (fatal_errors / total_ticks)
```
- Focuses on critical errors only
- **Must be 1.0** for production use
- **< 1.0** indicates corrupted data

**Data Reliability Score:**
```
data_reliability_score = 1.0 - ((serious_errors + fatal_errors) / total_ticks)
```
- Combines serious and fatal errors
- **> 0.99** recommended for backtesting
- **< 0.95** requires investigation

### Stream Status Classification

**HEALTHY:**
- No fatal errors
- Quality metrics normal
- Data suitable for backtesting

**COMPROMISED:**
- Fatal errors present
- Data integrity affected
- Use with caution

**CORRUPTED:**
- Stream corruption detected
- Collection possibly stopped
- Data unusable

---

## File Rotation System

### Tick-Based Rotation

Files rotate based on **tick count**, not file size:

```cpp
input int MaxTicksPerFile = 50000;  // Default: 50,000 ticks per file
```

**Rotation Workflow:**

```
Tick 49,999: Normal processing
Tick 50,000: Write to current file
─────────────────────────────────
CloseCurrentFile():
  - Finalize JSON structure
  - Append error summary
  - Close file handle
─────────────────────────────────
CreateNewExportFile():
  - Create new file with timestamp
  - Write metadata section
  - Initialize tick array
─────────────────────────────────
Tick 50,001: Write to new file
```

**Key Properties:**
- **Seamless:** No data loss during rotation
- **Predictable:** File size based on tick count
- **Atomic:** Rotation happens between OnTick() calls

### File Naming Convention

```
SYMBOL_YYYYMMDD_HHMMSS_ticks.json

Examples:
EURUSD_20251123_212345_ticks.json
GBPUSD_20251123_143022_ticks.json
USDJPY_20251124_080534_ticks.json
```

---

## Expected Output Characteristics

### Files Per Day (24h Collection)

| Symbol | Files/Day | Variation |
|--------|-----------|-----------|
| **EURUSD** | 8-15 | High volatility: +50% |
| **GBPUSD** | 6-12 | News events: +100% |
| **USDJPY** | 5-10 | Asian session: +30% |
| **AUDUSD** | 4-8 | Sydney/Tokyo: +40% |

*Varies significantly based on market volatility and trading sessions*

### File Sizes (50,000 ticks)

| Symbol | Typical Size | With Errors |
|--------|--------------|-------------|
| **EURUSD** | 18-25 MB | 30 MB (high error rate) |
| **GBPUSD** | 20-28 MB | 35 MB (spread anomalies) |
| **USDJPY** | 15-22 MB | 27 MB (3-digit = compact) |
| **AUDUSD** | 16-24 MB | 30 MB |

**Factors Increasing File Size:**
- **News Events:** Up to 5x more ticks
- **London/NY Overlap:** 2-3x activity
- **Error Rate > 5%:** +20-30% file size
- **Volatile Markets:** Individual files up to 40-50 MB

### Storage Requirements (Estimates)

| Period | Single Symbol | Four Symbols | Parquet Compressed |
|--------|--------------|--------------|-------------------|
| **Daily** | 160-300 MB | 640 MB - 1.2 GB | 80-150 MB |
| **Weekly** | 1.1-2.1 GB | 4.5-8.4 GB | 560 MB - 1 GB |
| **Monthly** | 4.8-9 GB | 18-34 GB | 2-4 GB |

**Compression Factor:** Parquet achieves 8-12x compression vs JSON

### Error Distribution (Typical)

| Severity | Typical Rate | Acceptable Threshold |
|----------|-------------|---------------------|
| **Negligible** | 0.1-2% | < 5% |
| **Serious** | 0.01-0.1% | < 1% |
| **Fatal** | 0-0.001% | 0% (ideally) |

*All figures are estimates based on typical Forex market conditions*

---

## Symbol-Specific Configuration

### Major Pairs (EURUSD, GBPUSD, USDJPY)
**Characteristics:** Tight spreads, high liquidity

```cpp
maxSpreadPercent = 2.0;        // Tight tolerance
maxPriceJumpPercent = 8.0;     // Standard
maxDataGapSeconds = 180;       // 3 minutes max
```

### JPY Pairs (USDJPY, EURJPY, GBPJPY)
**Characteristics:** Different pip structure (0.01)

```cpp
maxSpreadPercent = 3.0;        // Slightly wider
maxPriceJumpPercent = 15.0;    // Higher tolerance
maxDataGapSeconds = 300;       // Standard
```

### Exotic Pairs
**Characteristics:** Wide spreads, lower liquidity

```cpp
maxSpreadPercent = 10.0;       // Wide tolerance
maxPriceJumpPercent = 20.0;    // High volatility expected
maxDataGapSeconds = 600;       // 10 minutes allowed
```

### Cryptocurrencies (if supported)
**Characteristics:** Extreme volatility

```cpp
maxSpreadPercent = 15.0;       // Very wide
maxPriceJumpPercent = 30.0;    // Extreme moves possible
maxDataGapSeconds = 900;       // 15 minutes (exchange downtime)
```

---

## Troubleshooting

### No Files Created?

**Diagnostics:**
1. Check Expert Advisor logs in Terminal
2. Verify export path exists (or use empty for default)
3. Ensure AutoTrading is enabled (green button)
4. Confirm `CollectTicks = true` in settings
5. Check file permissions on export folder

**Common Fixes:**
- Create export directory manually
- Run MetaTrader as administrator
- Use empty path for MQL5 default folder

---

### High Error Rates?

**> 5% Negligible Errors:**
- **Cause:** Poor broker feed quality
- **Action:** Check broker server status, consider switching servers
- **Acceptable:** Up to 2% during news events

**> 1% Serious Errors:**
- **Cause:** Network instability, server performance issues
- **Action:** Check internet connection, ping broker server, monitor latency
- **Acceptable:** Brief spikes during high volatility

**> 0.1% Fatal Errors:**
- **Cause:** Broker connection issues, data corruption
- **Action:** Check broker API status, restart MetaTrader, contact broker support
- **Acceptable:** Never - indicates serious problems

---

### Large File Sizes?

**Problem:** Files exceeding 40-50 MB

**Solutions:**
1. **Reduce MaxTicksPerFile:**
   ```cpp
   input int MaxTicksPerFile = 25000;  // Half the default
   ```

2. **Limit Error Logging:**
   ```cpp
   input bool LogNegligibleErrors = false;  // Skip minor errors
   input int MaxErrorsPerFile = 500;        // Cap error details
   ```

3. **Session Filtering:**
   - Collect only during specific trading sessions
   - Avoid low-liquidity periods (e.g., Asian session for EUR/USD)

4. **Immediate Parquet Conversion:**
   - Convert JSON to Parquet immediately
   - Delete JSON after successful import
   - Saves 90% storage space

---

### Performance Optimization

**For Stable Feeds:**
```cpp
input bool LogNegligibleErrors = false;    // Skip minor logging
input int MaxErrorsPerFile = 100;          // Minimal error tracking
```

**For Multiple Symbols:**
- Run separate MetaTrader instances per 2-3 symbols
- Avoid collecting 10+ symbols on single MT5 instance
- Use SSD for export directory

**For High-Frequency Collection:**
```cpp
input int MaxTicksPerFile = 25000;         // Faster rotation
input bool IncludeTickFlags = false;       // Reduce file size
input bool IncludeRealVolume = false;      // Skip if not needed
```

---

## Error Code Reference

### Complete Error Type Catalog

| Error Type | Severity | Description | Recommended Action |
|------------|----------|-------------|-------------------|
| `tick_unavailable` | SERIOUS | SymbolInfoTick() failed | Check broker connection |
| `invalid_price_zero` | FATAL | Bid/Ask ≤ 0 | Discard data |
| `invalid_spread_zero` | FATAL | Spread ≤ 0 | Discard data |
| `inverted_spread` | FATAL | Ask < Bid | Discard data |
| `spread_extreme` | SERIOUS | Spread > threshold | Note market volatility |
| `spread_jump` | NEGLIGIBLE | Spread jump > 50% | Normal during volatility |
| `data_gap_major` | SERIOUS | Gap > 5 minutes | Check connection |
| `data_gap_minor` | NEGLIGIBLE | Gap 1-5 minutes | Normal outside trading hours |
| `time_regression` | FATAL | Backward time jump | Check server time |
| `time_regression_minor` | SERIOUS | Millisecond regression | Clock sync issue |
| `price_jump_bid` | SERIOUS | Bid jump > threshold | Market volatility |
| `price_jump_ask` | SERIOUS | Ask jump > threshold | Market volatility |
| `missing_tick_flags` | NEGLIGIBLE | Flags unavailable | Non-critical |
| `negative_tick_volume` | SERIOUS | Volume < 0 | Data corruption |
| `negative_real_volume` | NEGLIGIBLE | Real volume < 0 | Broker reporting issue |

---

## Integration with Python Import Pipeline

### Workflow

```
MQL5 TickCollector
    ↓ JSON Files
tick_importer.py
    ↓ Parquet Files (indexed)
bar_importer.py
    ↓ Pre-rendered Bars
FiniexTestingIDE
```

### Data Format Version Tracking

**Version Chain:**
```
TickCollector v1.0.5 → data_format_version: "1.0.5" (JSON metadata)
    ↓
tick_importer.py → Parquet metadata (preserved)
    ↓
bar_importer.py → source_version_min/max in bar files
```

**Purpose:**
- Track data provenance
- Detect format changes
- Validate compatibility
- Debug data quality issues

---

## Best Practices

### Production Collection

**Recommended Settings:**
```cpp
input int MaxTicksPerFile = 50000;
input bool EnableErrorTracking = true;
input bool LogNegligibleErrors = false;     // Reduce noise
input bool LogSeriousErrors = true;
input bool LogFatalErrors = true;
input bool StopOnFatalErrors = false;       // Don't stop on errors
input int MaxErrorsPerFile = 500;           // Limit error details
```

**Why:**
- Balanced file size (~20-25 MB)
- Focus on critical errors
- Resilient to transient issues
- Manageable error logs

### Development/Testing

**Recommended Settings:**
```cpp
input int MaxTicksPerFile = 10000;          // Smaller files
input bool EnableErrorTracking = true;
input bool LogNegligibleErrors = true;      // Full logging
input bool StopOnFatalErrors = true;        // Stop on serious issues
```

**Why:**
- Faster iteration
- Complete error visibility
- Early detection of problems
- Easy to review logs

### 24/7 Automated Collection

**Checklist:**
- [ ] VPS with stable connection
- [ ] AutoTrading enabled by default
- [ ] Export path on dedicated drive (non-system)
- [ ] Automated Parquet conversion pipeline
- [ ] Daily backup of raw JSON files
- [ ] Monitoring script for error rates
- [ ] Alert system for fatal errors

---

## Quality Assurance

### Pre-Import Validation

**Before importing to Parquet:**
1. Check `data_stream_status` = "HEALTHY"
2. Verify `data_integrity_score` = 1.0
3. Confirm `overall_quality_score` > 0.95
4. Review error details for anomalies
5. Validate timestamp continuity

### Post-Import Verification

**After Parquet conversion:**
1. Verify tick count matches JSON
2. Check timestamp range consistency
3. Validate no data loss during conversion
4. Confirm metadata preservation
5. Test bar rendering with sample data

---

## Advanced Features

### Custom Error Thresholds

**Per-Symbol Configuration:**

Edit in MQL5 code before compilation:
```cpp
// In OnInit() function
if (Symbol() == "EURUSD") {
    maxSpreadPercent = 2.0;
    maxPriceJumpPercent = 8.0;
} else if (Symbol() == "EURJPY") {
    maxSpreadPercent = 3.0;
    maxPriceJumpPercent = 15.0;
}
```

### Session-Based Collection

**Collect Only During Specific Hours:**

Add in OnTick():
```cpp
// Only collect during London session (8:00-16:00 UTC)
MqlDateTime dt;
TimeCurrent(dt);
if (dt.hour < 8 || dt.hour >= 16) return;
```
