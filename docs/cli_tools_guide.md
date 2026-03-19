## Overview

FiniexTestingIDE provides a collection of CLI tools for the complete workflow from data import to backtesting. All tools are accessible via VS Code launch configurations or directly from the terminal.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  WORKFLOW                                                                   │
│                                                                             │
│  TickCollector (MT5) → Import → Profiling → Scenario Generation → Backtest │
│        ↓                 ↓         ↓              ↓                  ↓      │
│    JSON Files      Parquet+Bars  Gaps/ATR  blocks/high_vol       Results   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### CLI Structure

| CLI | Purpose | Commands |
|-----|---------|----------|
| `data_index_cli.py` | Import & Inspection | import, tick-data-report, inspect |
| `tick_index_cli.py` | Tick Index Management | rebuild, status, file-coverage, files |
| `bar_index_cli.py` | Bar Index Management | rebuild, status, report, render |
| `discoveries_cli.py` | Volatility Profiling, Discoveries & Data Coverage | profile, extreme-moves, data-coverage (build/show/validate/status/clear), cache (rebuild-all/status) |
| `scenario_cli.py` | Scenarios | generate |
| `strategy_runner_cli.py` | Backtesting | run, list |

---

## A) Data Pipeline - Import

Tick data is collected by the **TickCollector** (MQL5 Expert Advisor) and exported as JSON. The import converts these to optimized Parquet files and automatically renders bars for all timeframes.

> 📖 See `tick_collector_guide.md` for details on data collection.

### 📥 Import: Offset +3

| | |
|---|---|
| **VS Code** | `📥 Import: Offset +3` |
| **CLI** | `python data_index_cli.py import --time-offset +3 --offset-broker mt5` |
| **Purpose** | Convert JSON tick files to Parquet, render bars |

The `--time-offset` parameter corrects broker timezones to UTC. After import, bars are automatically rendered for all timeframes (M1, M5, M15, M30, H1, H4, D1).

```
📄 Processing: EURGBP_20251128_235635_ticks.json
   Detected Offset: GMT+0
   User Offset:     +3 hours → ALL TIMES WILL BE UTC!
   🕐 Time Offset Applied: +3 hours
      Original: 2025-12-01 00:00:00 → 2025-12-01 11:46:58
      UTC:      2025-11-30 21:00:00 → 2025-12-01 08:46:58
   ✅ Sessions recalculated based on UTC time
✅ mt5/ticks/EURGBP/EURGBP_20251128_235635.parquet: 50,000 Ticks (UTC), Compression 20.0:1

🔄 AUTO-TRIGGERING BAR RENDERING
   ├─ M1: 5,650 bars rendered
   ├─ M5: 1,130 bars rendered
   ├─ M15: 377 bars rendered
   ...
✅ Rendered Bars: 7,470
```

---

## B) Data Overview

### 📊 Tick Data Report

| | |
|---|---|
| **VS Code** | `📊 Tick Data Report` |
| **CLI** | `python data_index_cli.py tick_data_report` |
| **Purpose** | Complete report of all available symbols |

Shows for each symbol: time range, tick count, session distribution, spread statistics.

```
📊 USDJPY
   ├─ Time Range:    2025-09-17 17:58:34 to 2026-01-02 20:56:45
   ├─ Duration:      107 days (2571.0 hours)
   ├─ Trading Days:  77 (excluding 15 weekends)
   ├─ Ticks:         9,920,219
   ├─ Files:         204
   ├─ Size:          179.6 MB
   ├─ Ø Spread:      15.7 Points (0.0105%)
   └─ Frequency:     1.07 Ticks/Second
      Sessions:     new_york: 3,322,449 | sydney_tokyo: 3,240,081 | london: 3,330,993
```

### 📚 Tick Index: Status

| | |
|---|---|
| **VS Code** | `📚 Tick Index: Status` |
| **CLI** | `python tick_index_cli.py status` |
| **Purpose** | Quick overview: number of symbols and files |

```
Index file:   data/processed/.parquet_tick_index.parquet
Last update:  2026-02-03 19:44:18
Broker Types: kraken_spot, mt5
Symbols:      16
Total files:  1462
```

### 🔹 Bar Index: Status

| | |
|---|---|
| **VS Code** | `🔹 Bar Index: Status` |
| **CLI** | `python bar_index_cli.py status` |
| **Purpose** | Overview of all timeframes per symbol |

```
USDJPY:
   Timeframes: D1, H1, H4, M1, M15, M30, M5
   Total bars: 203,864
   Source:     mt5 (v1.0.3 - 1.0.5)
      • D1: 108 bars [Ticks: 9,920,219, Ø 91,853/bar]
      • H1: 2,572 bars [Ticks: 9,920,219, Ø 3,857/bar]
      • M5: 30,853 bars [Ticks: 9,920,219, Ø 321/bar]
      ...
```

### 📚 Tick File Coverage: SYMBOL

| | |
|---|---|
| **VS Code** | `📚 Tick File Coverage: mt5/EURUSD` |
| **CLI** | `python tick_index_cli.py file-coverage mt5 EURUSD` |
| **Purpose** | File list for a symbol |

```
📊 File Coverage: mt5/EURUSD
Files:       113
Ticks:       5,268,906
Size:        100.6 MB
Start:       2025-09-17T17:58:35
End:         2026-01-02T20:56:56

Files:
   • EURUSD_20250917_205834.parquet
   • EURUSD_20250918_092100.parquet
   ...
```

---

## C) Data Quality

### 📊 Data Coverage: Validate All

| | |
|---|---|
| **VS Code** | `🔍 Disc - Data Coverage: Validate All` |
| **CLI** | `python discoveries_cli.py data-coverage validate` |
| **Purpose** | Quick gap check across all symbols |

```
🔍 Validating All Symbols

📂 kraken_spot:
  ✅ ADAUSD: No issues
  ⚠️  BTCUSD: 0 moderate, 1 large gaps
  ✅ DASHUSD: No issues

📂 mt5:
  ⚠️  AUDUSD: 1 moderate, 2 large gaps
  ⚠️  EURUSD: 1 moderate, 2 large gaps
...
Use 'show BROKER_TYPE SYMBOL' for detailed gap analysis
```

### 📊 Data Coverage: Show Gap Report

| | |
|---|---|
| **VS Code** | `🔍 Disc - Data Coverage: mt5/EURUSD` |
| **CLI** | `python discoveries_cli.py data-coverage show mt5 EURUSD` |
| **Purpose** | Detailed gap analysis for a symbol |

Classifies gaps automatically:
- ✅ **Weekend** - Expected market closure (Fri 21:00 → Sun 21:00 UTC)
- ✅ **Holiday** - Holidays (Christmas, New Year)
- ⚠️ **Short** - Small gaps < 30min (MT5 restarts, connection drops)
- ⚠️ **Moderate** - 30min to 4h
- 🔴 **Large** - > 4h (check data collection!)

```
📊 DATA COVERAGE REPORT: GBPUSD
Time Range:   2025-09-17 17:55:00 UTC → 2026-01-02 20:55:00 UTC
Duration:     107d 3h

GAP ANALYSIS:
✅ Weekend:      15 gaps (expected)
✅ Holiday:      2 gaps (expected)
⚠️  Short:        22 gaps (< 30 min)
⚠️  Moderate:     1 gaps (30min - 4h)
🔴 Large:        2 gaps (> 4h)

🔴 LARGE GAP:
   Start:  2025-11-12 15:45:00 UTC
   End:    2025-11-13 21:15:00 UTC
   Gap:    29h 30m
   Reason: 🔴 Large gap - check data collection

💡 RECOMMENDATIONS:
   • Short gaps detected - likely MT5 restarts (usually harmless)
   • 🔴 Large gaps detected - consider re-collecting data
```

### 📊 Data Coverage: Build Cache

| | |
|---|---|
| **VS Code** | `🔍 Disc - Cache: Rebuild All` |
| **CLI** | `python discoveries_cli.py data-coverage build` |
| **Purpose** | Pre-compute gap reports for all symbols |

```
🔧 Building Data Coverage Report Cache
Force Rebuild: DISABLED (skip valid caches)

✅ Data coverage cache built: 16 generated, 0 skipped, 0 failed (16 total) in 8.23s
```

### 📊 Data Coverage: Status

| | |
|---|---|
| **VS Code** | `🔍 Disc - Data Coverage: Status` |
| **CLI** | `python discoveries_cli.py data-coverage status` |
| **Purpose** | Show cache status |

```
📦 Data Coverage Report Cache Status
Cache Dir:     data/processed/.discovery_caches/data_coverage_cache
Cache Files:   16
Cache Size:    0.02 MB
------------------------------------------------------------
Total Symbols: 16
  ✅ Cached:   16
  ⚠️  Stale:    0
  ❌ Missing:  0
```

---

## D) Volatility Profiling & Discoveries

> 📖 See [Discovery System](discovery_system.md) for architecture, cache system and details.

### 📊 VOLATILITY PROFILE

| | |
|---|---|
| **VS Code** | `🔍 Disc - Profile: mt5/GBPUSD` |
| **CLI** | `python discoveries_cli.py profile mt5 USDJPY` |
| **Purpose** | ATR volatility, session activity, cross-instrument ranking |

Results are cached and only recalculated when source data changes.

**Parameters:**
- `--force` — Ignore cache and rebuild profile
- `--timeframe` — Timeframe override (default: M5, non-M5 bypasses cache)

Builds volatility profile for strategic scenario planning:

```
📊 VOLATILITY PROFILE: GBPUSD
Data Range:     2025-09-17 → 2026-01-02 (107 days)
Timeframe:      M5

📈 VOLATILITY DISTRIBUTION (ATR-based)
   Very Low       (<0.50):   186 periods  10.6%  →  7d 18h
   Low        (0.50-0.80):   537 periods  30.6%  → 22d  9h
   Medium     (0.80-1.20):   511 periods  29.1%  → 21d  7h
   High       (1.20-1.80):   425 periods  24.2%  → 17d 17h
   Very High      (>1.80):    97 periods   5.5%  →  4d  1h

📊 SESSION ACTIVITY
   London (584 periods, 24d 8h):
      Avg density:    6,578 ticks/hour
      Regimes:        VL: 0% | L: 1% | M: 34% | H: 52% | VH: 12%

   New York (364 periods, 15d 4h):
      Avg density:    4,340 ticks/hour
      Regimes:        VL: 6% | L: 35% | M: 40% | H: 16% | VH: 3%
```

**Cross-Instrument Ranking** - Compares all symbols:

```
📊 CROSS-INSTRUMENT RANKING

📈 Volatility Ranking (ATR-based):
   1. NZDUSD   100.0%  ██████████ ← Highest
   2. AUDUSD    91.7%  █████████░
   3. USDJPY    76.5%  ████████░░
   ...

💧 Liquidity Ranking (Ticks/Hour):
   1. USDJPY     5,649  ██████████ ← Highest
   2. GBPUSD     4,827  ████████░░
   ...

⚡ Combined Score (Volatility × Liquidity):
   1. USDJPY     76.5   ██████████ ← Highest
   2. GBPUSD     42.7   ██████░░░░
```

### 🔍 EXTREME MOVES

| | |
|---|---|
| **VS Code** | `🔍 Disc - Extreme Moves: mt5/USDJPY` |
| **CLI** | `python discoveries_cli.py extreme-moves mt5 USDJPY` |
| **Purpose** | Find extreme directional price movements (LONG/SHORT) |

Scans bar data with ATR-based normalization over configurable window sizes. Results are cached and only recalculated when source data changes.

```
==================================================================================================================================
EXTREME MOVE DISCOVERY: USDJPY
==================================================================================================================================
Data Source:    mt5
Timeframe:      M5
Bars Scanned:   38,989
Avg ATR:        0.038
Pip Size:       0.01

------------------------------------------------------------------------------------------------------------------------------------------------------
LONG Extreme Moves (top 10)
------------------------------------------------------------------------------------------------------------------------------------------------------
  #  ATR Mult      Pips   Adverse       Entry     Extreme    Adverse@        Exit    W-ATR    Bars     Ticks                 Start                   End
  1    117.04     580.7       0.0     147.471     153.277     147.471     151.231    0.050    2000    680633      2025-10-05 02:35      2025-10-12 01:10
  2     83.22     303.1       2.3     154.865     157.896     154.842     156.846    0.036    2000    599766      2025-11-17 12:15      2025-11-24 10:50
  ...
```

**Columns:**
- `ATR Mult` — Movement as a multiple of the average ATR within the window
- `Pips` — Directional movement Entry → Extreme in pips
- `Adverse` — Maximum drawback against the movement direction (in pips)
- `Entry/Extreme/Adverse@/Exit` — Price levels (entry, extreme point, worst drawback, exit)
- `W-ATR` — Average ATR over the window (raw price units)
- `Bars` — Window width in bars
- `Ticks` — Number of ticks in the time window

**Parameters:**
- `--top` — Number of top results per direction in the display (default: 10, all are cached)
- `--force` — Ignore cache and re-scan
- `--timeframe` — Timeframe override (default: M5)

---

## E) Scenario Generation

### 📊 Scenario Generator - Blocks

| | |
|---|---|
| **VS Code** | `📊 Scenario Generator - Generate Blocks` |
| **CLI** | `python scenario_cli.py generate USDJPY --strategy blocks --block-size 12 --count 40 --sessions new_york` |
| **Purpose** | Chronological time blocks for systematic testing |

Generates consecutive time windows with configurable length. Automatically detects gaps and shortens blocks accordingly.

```
Filtering blocks to sessions: ['new_york']
Coverage: 1752.7h usable, 818.3h gaps filtered (20 gaps: 15 weekend, 2 holiday, 1 moderate, 2 large)

⚠️ Block #07: Short block 5.0h < 12h target
   Time: 2025-09-26 16:00 → 2025-09-26 21:00 UTC (Fri)
   Reason: End of continuous data region - Weekend gap follows (48.0h) 🟢

✅ Generated 40 blocks
Symbol:     USDJPY
Time range: 2025-09-18 → 2025-11-18
Total:      415h (10.4h avg/block)

📂 Config saved to: configs/scenario_sets/USDJPY_blocks_20260109_0742.json
```

**Parameters:**
- `--block-size` - Target block size in hours (default: 12)
- `--count` - Number of blocks
- `--sessions` - Filter: `new_york`, `london`, `sydney_tokyo`

### 📊 Scenario Generator - High Volatility

| | |
|---|---|
| **VS Code** | `⚡ Scenario Gen. - 6 High Volatility - mt5/USDJPY` |
| **CLI** | `python scenario_cli.py generate mt5 EURGBP --strategy high_volatility --count 5` |
| **Purpose** | High-volatility periods for scenario generation |

Automatically finds the most volatile market phases (HIGH/VERY_HIGH ATR) and creates scenarios around these time points.

```
Found 530 high-volatility periods (HIGH/VERY_HIGH) from 1704 total
Generating 5 high-volatility scenarios from 530 high-volatility periods

Checking period: 2025-11-26 11:00 (11,653 ticks)
  Volatility center: 11:30
  Warmup: 2025-11-25 19:00 → 2025-11-26 08:00 (13h)
  Scenario: 2025-11-26 08:00 → 2025-11-26 14:00 (6h)
  ✓ VALID: All checks passed
✓ High-Vol #01: 2025-11-26 11:00 (very_high, 11,653 ticks)

Checking period: 2025-11-26 12:00 (11,546 ticks)
  ✗ SKIP: Overlaps with existing scenario

============================================================
HIGH-VOLATILITY GENERATION SUMMARY
Total candidates: 530
Scenarios generated: 5
Skip reasons: Overlap: 10 (1.9%)

Regime coverage:
   high: 2
   very_high: 3
```

### Output: Scenario Set JSON

Both generators produce a JSON configuration:

```json
{
  "version": "1.0",
  "scenario_set_name": "EURGBP_high_volatility_20260109_0743",
  "global": {
    "strategy_config": {
      "decision_logic_type": "CORE/aggressive_trend",
      "worker_instances": {
        "rsi_fast": "CORE/rsi",
        "envelope_main": "CORE/envelope"
      }
    },
    "trade_simulator_config": {
      "initial_balance": 10000,
      "currency": "EUR"
    }
  },
  "scenarios": [
    {
      "name": "EURGBP_high_volatility_01",
      "symbol": "EURGBP",
      "start_date": "2025-11-26T08:00:00+00:00",
      "end_date": "2025-11-26T14:00:00+00:00",
      "enabled": true
    }
  ]
}
```

---

## F) Backtesting

### 🔬 Run Scenario

| | |
|---|---|
| **VS Code** | `🔬 Run (eurusd_3 - REFERENCE)` |
| **CLI** | `python strategy_runner_cli.py run eurusd_3_windows_reference.json` |
| **Purpose** | Backtesting run with a scenario set configuration |

Executes all scenarios in parallel and shows live progress:

```
🔬 Strategy Runner
Scenario Set: eurusd_3_windows_reference.json

╭─────────────────────── 🔬 Strategy Execution Progress ───────────────────────╮
│ ⚡ System Resources │ CPU: 0.0% │ RAM: 2.7/30.3 GB │ Completed: 3/3          │
│                                                                              │
│  ✅  GBPUSD_window_01  ████████████████████  $99,788.20 (-$211.80)           │
│                        100.0%                Trades: 66 (2W / 64L)           │
│  ✅  GBPUSD_window_02  ████████████████████  $99,876.60 (-$123.40)           │
│                        100.0%                Trades: 39 (1W / 38L)           │
│  ✅  GBPUSD_window_03  ████████████████████  $99,851.79 (-$148.21)           │
│                        100.0%                Trades: 52 (1W / 51L)           │
╰──────────────────────────────────────────────────────────────────────────────╯
```

**Results:**

```
🎉 EXECUTION RESULTS
✅ Success: True  |  📊 Scenarios: 3  |  ⏱️  Time: 37.96s

📊 AGGREGATED PORTFOLIO (ALL SCENARIOS)
   Total Trades: 157 (L/S: 82/75) |  Win/Loss: 4W/153L  |  Win Rate: 2.5%
   Total P&L: -$483.41  |  Profit Factor: 0.01
   Max Drawdown: -$216.50 (0.2%)

⚡ PERFORMANCE
   Tick Run Time:      30.5 seconds
   Ticks/Second:       8,856 (processing rate)
   Speedup:            2,360x (20 hours → 30 seconds)
```

### 🔬 List Scenarios

| | |
|---|---|
| **VS Code** | `🔬 List Scenarios` |
| **CLI** | `python strategy_runner_cli.py list --full-details` |
| **Purpose** | Show available scenario sets |

---

## G) Technical Tools (Advanced)

### 📊 TEST LOAD: Ticks & Bars

| | |
|---|---|
| **VS Code** | `📊 TEST LOAD: Ticks&Bars` |
| **CLI** | `python data_index_cli.py inspect mt5 EURUSD M30` |
| **Purpose** | Display Parquet schema, metadata and sample data |

Useful for understanding the raw data structure:

```
📁 File Information:
   File:       EURUSD_20250917_205834.parquet
   Ticks:      50,000

📋 Parquet Metadata:
   broker          = Vantage International Group Limited
   data_collector  = mt5
   market_type     = forex_cfd

🔧 Schema:
   timestamp       : timestamp[ns]
   bid             : float
   ask             : float
   spread_points   : int32
   session         : string

📊 Sample Data (first 10 rows):
            timestamp       bid      ask  spread_points   session
0 2025-09-17 17:58:35   1.18508  1.18522            14  new_york
1 2025-09-17 17:58:37   1.18509  1.18522            12  new_york
```

### Index Rebuild (Maintenance)

| | |
|---|---|
| **VS Code** | `📚 Tick Index: Rebuild` / `🔹 Bar Index: Rebuild` |
| **CLI** | `python tick_index_cli.py rebuild` / `python bar_index_cli.py rebuild` |
| **Purpose** | Rebuild index in case of inconsistencies |

> ⚠️ Normally not needed - the import updates indices automatically.

### Bar Import / Render

| | |
|---|---|
| **VS Code** | `📥 Bar Render --all` |
| **CLI** | `python bar_index_cli.py render BROKER_TYPE [--clean]` |
| **Purpose** | Re-render bars for a broker type or all |

**Flags:**
- `BROKER_TYPE` — Render a single broker type (e.g. `mt5`)
- `--all` — Render all broker types from `market_config.json`
- `--clean` — Delete existing bars before rendering

> ⚠️ **Warning:** Can take a very long time with large datasets!
>
> Bar rendering runs symbols in parallel via `ProcessPoolExecutor`. Worker count is configured in `import_config.json` → `processing.bar_render_workers` (default: 16). Set to `1` for sequential mode.

---

## Quick Reference

| Task | VS Code Launch | CLI |
|------|----------------|-----|
| **Import data** | `📥 Import: Offset +3` | `data_index_cli.py import --time-offset +3 --offset-broker mt5` |
| **Data overview** | `📊 Tick Data Report` | `data_index_cli.py tick_data_report` |
| **Tick Index Status** | `📚 Tick Index: Status` | `tick_index_cli.py status` |
| **Gap check (all)** | `🔍 Disc - Data Coverage: Validate All` | `discoveries_cli.py data-coverage validate` |
| **Gap details** | `🔍 Disc - Data Coverage: mt5/EURUSD` | `discoveries_cli.py data-coverage show mt5 EURUSD` |
| **Volatility profile** | `🔍 Disc - Profile: mt5/GBPUSD` | `discoveries_cli.py profile mt5 GBPUSD` |
| **Extreme Moves** | `🔍 Disc - Extreme Moves: mt5/USDJPY` | `discoveries_cli.py extreme-moves mt5 USDJPY` |
| **Discovery Cache Status** | `🔍 Disc - Cache: Status` | `discoveries_cli.py cache status` |
| **Discovery Cache Rebuild** | `🔍 Disc - Cache: Rebuild All` | `discoveries_cli.py cache rebuild-all` |
| **Scenarios: Blocks** | `📊 Scenario Generator - Generate Blocks` | `scenario_cli.py generate USDJPY --strategy blocks` |
| **Scenarios: High Volatility** | `⚡ Scenario Gen. - High Volatility` | `scenario_cli.py generate mt5 EURGBP --strategy high_volatility` |
| **Start backtest** | `🔬 Run (eurusd_3 - REFERENCE)` | `strategy_runner_cli.py run <config>.json` |

---

## Typical Workflow

```
1. Collect tick data (TickCollector on MT5)
         ↓
2. Import:          📥 Import: Offset +3
         ↓
3. Build cache:     🔍 Disc - Cache: Rebuild All
         ↓
4. Check quality:   🔍 Disc - Data Coverage: Validate All
         ↓
5. Volatility profile: 🔍 Disc - Profile
         ↓
5b. Extreme Moves:  🔍 Disc - Extreme Moves
         ↓
6. Create scenarios: 📊 Generate Blocks/Stress
         ↓
7. Backtest:        🔬 Run Scenario
```

---

## Index Formats

The indices are stored in Parquet format (since v1.1):

| Index | File | Migration |
|-------|------|-----------|
| Tick Index | `.parquet_tick_index.parquet` | Auto from `.json` |
| Bar Index | `.parquet_bars_index.parquet` | Auto from `.json` |
| Data Coverage Cache | `.discovery_caches/data_coverage_cache/*.parquet` | Gap analysis |
| Extreme Moves Cache | `.discovery_caches/extreme_moves_cache/*.parquet` | Extreme move scan |
| Market Analyzer Cache | `.discovery_caches/market_analyzer_cache/*.parquet` | Volatility profile |

Old JSON indices are automatically migrated and backed up as `.json.bak`.