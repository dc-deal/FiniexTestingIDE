# FiniexTestingIDE - CLI Usage Guide

## Command Structure

```bash
finiexTest <command> [options]
```

## Library Management

```bash
# Load a blackbox library
finiexTest loadLibrary --path ./libraries/20TradingSnippets/

# List available strategies in a library
finiexTest listStrategies --library "20TradingSnippets"

# Show parameter schema for a specific strategy
finiexTest showParameters --library "20TradingSnippets" --strategy "MACDStrategy"
```

## Collection Management

```bash
# Create/Load a data collection from config
finiexTest createCollection --config ./collections/volatility_scenarios_q3_2025.json

# Validate collection (check if all data files exist, warmup requirements)
finiexTest validateCollection --id "volatility_scenarios_q3_2025"

# List available collections
finiexTest listCollections

# Show collection details
finiexTest showCollection --id "volatility_scenarios_q3_2025"
```

## Test Execution

```bash
# Run test with full JSON config
finiexTest startRun --config ./configs/macd_conservative_test.json

# Run test with inline parameters (quick testing)
finiexTest startRun \
  --name "MACD-Conservative" \
  --library "20TradingSnippets" \
  --blackbox "MACDStrategy" \
  --params "fast_period:12,slow_period:26,signal_period:9,risk_per_trade:0.02" \
  --collection "volatility_scenarios_q3_2025" \
  --processes 8

# Run with parameter ranges (batch testing multiple variations)
finiexTest batchTest \
  --library "20TradingSnippets" \
  --blackbox "MACDStrategy" \
  --param-range "fast_period:10,12,14" \
  --param-range "slow_period:24,26,28" \
  --collection "volatility_scenarios_q3_2025" \
  --processes 12
```

## Results & Analysis

```bash
# Show test results
finiexTest showResults --run-id "macd_conservative_20250119_143022"

# List all completed test runs
finiexTest listRuns --status completed --limit 10

# Export results to file
finiexTest exportResults --run-id "macd_conservative_20250119_143022" --format json
finiexTest exportResults --run-id "macd_conservative_20250119_143022" --format csv

# Compare multiple test runs
finiexTest compareRuns --runs "run1,run2,run3" --metrics "sharpe,max_drawdown,win_rate"
```

## System Management

```bash
# Show system status (running tests, resource usage)
finiexTest status

# Stop running test
finiexTest stopRun --run-id "macd_conservative_20250119_143022"

# Clean up old test artifacts
finiexTest cleanup --older-than 30d

# Show system configuration
finiexTest config --show
```

## Example Workflows

### Quick Strategy Test
```bash
# 1. Load library and show available strategies
finiexTest loadLibrary --path ./libraries/20TradingSnippets/
finiexTest listStrategies --library "20TradingSnippets"

# 2. Check parameter schema
finiexTest showParameters --library "20TradingSnippets" --strategy "MACDStrategy"

# 3. Quick test with inline parameters  
finiexTest startRun \
  --name "MACD-Quick-Test" \
  --library "20TradingSnippets" \
  --blackbox "MACDStrategy" \
  --params "fast_period:12,slow_period:26,signal_period:9,risk_per_trade:0.02" \
  --collection "volatility_scenarios_q3_2025" \
  --processes 6

# 4. Check results
finiexTest showResults --run-id <generated_run_id>
```

### Comprehensive Parameter Exploration
```bash
# 1. Validate collection first
finiexTest validateCollection --id "volatility_scenarios_q3_2025"

# 2. Batch test with parameter ranges
finiexTest batchTest \
  --library "20TradingSnippets" \
  --blackbox "MACDStrategy" \
  --param-range "fast_period:8,10,12,14,16" \
  --param-range "slow_period:20,24,26,28,32" \
  --param-range "risk_per_trade:0.01,0.015,0.02,0.025" \
  --collection "volatility_scenarios_q3_2025" \
  --processes 15

# 3. Compare all results and find best performer
finiexTest listRuns --status completed --sort-by sharpe --limit 5
finiexTest compareRuns --runs "top5_run_ids" --metrics "all"
```

## Output Examples

### Parameter Schema Display
```
$ finiexTest showParameters --library "20TradingSnippets" --strategy "MACDStrategy"

MACDStrategy Parameter Schema:
┌─────────────────────┬──────┬─────────┬─────────────┬───────────────────────────────┐
│ Parameter           │ Type │ Default │ Range       │ Description                   │
├─────────────────────┼──────┼─────────┼─────────────┼───────────────────────────────┤
│ fast_period         │ int  │ 12      │ 5-50        │ Fast EMA period               │
│ slow_period         │ int  │ 26      │ 10-100      │ Slow EMA period               │
│ signal_period       │ int  │ 9       │ 3-30        │ MACD signal line period      │
│ risk_per_trade      │ float│ 0.02    │ 0.005-0.1   │ Position size as % of capital │
│ use_adaptive_stops  │ bool │ true    │ true/false  │ Enable adaptive stop losses   │
│ market_session_filter│ str  │ "all"   │ predefined  │ Trading session filter        │
└─────────────────────┴──────┴─────────┴─────────────┴───────────────────────────────┘

Required warmup bars: 500
Estimated processing time: 2.5ms per tick
Memory footprint: ~15MB
```

### Test Run Results
```
$ finiexTest showResults --run-id "macd_conservative_20250119_143022"

Test Run: MACD-Conservative
Status: COMPLETED
Duration: 00:04:23
Situations tested: 5
Total ticks processed: 104,500

Performance Metrics:
┌─────────────────┬─────────┬──────────┬─────────────┬──────────┐
│ Situation       │ Sharpe  │ Max DD   │ Win Rate    │ Trades   │
├─────────────────┼─────────┼──────────┼─────────────┼──────────┤
│ EURUSD Std Day  │ 1.42    │ -8.5%    │ 68.4%       │ 47       │
│ AUDUSD Calm     │ 0.89    │ -4.2%    │ 72.1%       │ 23       │
│ EURUSD High Vol │ 2.31    │ -12.3%   │ 65.0%       │ 28       │
│ EURCHF Low Vol  │ 0.34    │ -2.1%    │ 58.3%       │ 12       │
│ GBPUSD News     │ -0.12   │ -18.7%   │ 45.0%       │ 20       │
├─────────────────┼─────────┼──────────┼─────────────┼──────────┤
│ OVERALL         │ 1.28    │ -18.7%   │ 64.2%       │ 130      │
└─────────────────┴─────────┴──────────┴─────────────┴──────────┘

Warnings:
⚠️  Poor performance in extreme volatility (GBPUSD News)
⚠️  High drawdown events during gap scenarios

Files generated:
- ./results/macd_conservative_20250119_143022/detailed_trades.csv
- ./results/macd_conservative_20250119_143022/performance_report.json
- ./results/macd_conservative_20250119_143022/execution_log.txt
```