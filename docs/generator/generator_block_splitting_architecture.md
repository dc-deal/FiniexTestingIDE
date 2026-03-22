# Generator & Block Splitting Architecture

## Overview

The scenario generator creates time blocks for backtesting runs. It is a **block-splitting tool**, not a warmup-aware system. It splits data into time blocks based on market structure (gaps, volatility). Warmup is the batch orchestrator's responsibility — it loads bars by count from parquet, regardless of time gaps.

This document covers the block splitting analysis, the Generator Profile system, and the Post-Run Correctness Metric.

**Context:** Investigation #212 (closed) analyzed the impact of block splitting on P&L correctness. The findings led to the Generator Profile architecture (#213) and Post-Run Correctness Metric (#214) described here.

---

## Block Splitting — Impact Analysis

When the generator splits a full time range into blocks, each block runs in complete subprocess isolation (`ProcessPoolExecutor`). This resets all algorithm state at every block boundary.

### What Gets Reset

#### Solved by Batch Orchestrator Warmup

| # | Lost State | Impact | Solution |
|---|---|---|---|
| 1 | **Indicator values** (MACD, RSI, EMA, Bollinger...) | MACD(12,26,9) needs ~34 bars = 2.8h on M5 | ✅ Batch orchestrator loads warmup bars by count from parquet |
| 6 | **Bar history depth** | Long-range patterns (S/R over 200+ bars) blind | ✅ Warmup bars from parquet, `max_history=1000` deque is generous |

These are NOT block-splitting problems — they are warmup problems handled entirely by the batch orchestrator. `SharedDataPreparator.prepare_bars()` loads bars by count (`bars_df.tail(warmup_count)`) from parquet, regardless of time gaps. Bars from before gaps (weekends, outages) are valid warmup data for indicators. The generator has no warmup logic — it only splits data into time blocks.

**Evidence:** The warmup bar pipeline: `VectorizedBarRenderer (parquet)` → `SharedDataPreparator.prepare_bars()` → `ProcessDataPackage` → `BarRenderingController.inject_warmup_bars()` → `BarRenderer.initialize_historical_bars()`. Same data source, same rendering algorithm, same quality as live tick-by-tick bars.

#### Structurally Unsolvable (inherent to subprocess isolation)

| # | Lost State | Impact | Why unsolvable |
|---|---|---|---|
| 2 | **Open positions** | Swing trades cut short, artificial P&L | `close_all_remaining_orders()` is required — without it, trades exist in limbo when subprocess ends |
| 3 | **Account balance** | No compounding across blocks | `initial_balance` reset is structural to subprocess isolation |
| 4 | **Decision logic memory** | Cooldowns, sequences, state machines lost | No serialization mechanism for arbitrary decision logic state |
| 5 | **Pending orders** | Limit orders near execution discarded | No cross-block transfer mechanism |

No amount of warmup can fix these. The only solutions are: avoid splitting (continuous mode) or transfer state across blocks (complex, blocks must run sequentially).

---

## Industry Comparison

Professional backtesting platforms do NOT time-split within a single symbol run. Parallelism is always across strategies and parameter combinations:

> Note: Mentioning third-party platforms is nominative reference (factual comparison of technical approaches).

| Platform | Approach |
|---|---|
| MetaTrader Strategy Tester | "Agents" each receive a complete pass with different parameters, not a time slice |
| QuantConnect / Lean | Each algo instance runs as a single continuous timeline. Parallelization across projects (strategy × parameters) |
| Backtrader / Zipline | Vectorized or event-driven, but always full timeline per run |
| Walk-Forward Analysis | Intentionally splits by time — but that is an out-of-sample methodology, forced closure at window boundaries is accepted and part of the evaluation |

**Industry standard: never time-split for P&L calculation. Parallelism is always Symbol × Parameter.**

---

## Block Size vs. P&L Distortion

The distortion from forced closure depends on the ratio of average holding period (T) to block size (B):

- Probability of open position at block end ≈ **T / B**
- Number of forced closures over total runtime ≈ **Total_Time × T / B²**

> **Basis:** Probability theory — uniform distribution assumption. Standard material from quantitative finance / risk management.

Example (6 months data = ~4,320h market time):

| Strategy Type | Avg. Holding (T) | Block 8h | Block 24h | Block 72h | Block 168h |
|---|---|---|---|---|---|
| **Scalping** | 0.5h | ~6% affected | ~2% | <1% | negligible |
| **Intraday** | 4h | ~50% | ~17% | ~6% | ~2% |
| **Swing** | 48h | always | always | ~67% | ~29% |
| **Position** | 168h+ | always | always | always | ~100% |

**Rule of thumb: Block size must be > 10× average holding period for < 10% of trades to be affected by forced closure.** At 10× holding period, P&L distortion drops to approximately 1%.

---

## Generator Profile System

### Two-Mode Architecture

The system operates in two strictly separated modes — **never mixed**:

| | **Generator Profile Run** | **Free Run** |
|---|---|---|
| **Purpose** | Serious P&L analysis, reproducible | Tests, prototyping, quick iteration |
| **Blocks** | Pre-computed by generator, immutable | Direct from ScenarioSet JSON |
| **max_ticks** | Not available | Available (essential for test suites) |
| **Parameter cascade** | Yes, within scenarios | Yes |
| **Correctness Metric** | Yes (metadata present) | No |
| **enabled field** | No (all or nothing) | Yes (per-scenario toggle) |

### Profile Format

JSON files in `configs/generator_profiles/`, organized by mode into subdirectories (`volatility_split/`, `continuous/`). Human-readable but must not be manually edited (documented convention, not enforced via hash).

```json
{
  "profile_meta": {
    "symbol": "EURUSD",
    "broker_type": "mt5",
    "generator_mode": "volatility_split",
    "generated_at": "2026-03-19T14:00:00+00:00",
    "total_coverage_hours": 4320,
    "block_count": 12,
    "discovery_fingerprints": {
      "volatility_profile": "a3f2b1c4...",
      "data_coverage": "c7d4e982...",
      "extreme_moves": "b8e1f9a3..."
    },
    "split_config": {
      "min_block_hours": 2,
      "max_block_hours": 24,
      "atr_percentile_threshold": 10,
      "split_algorithm": "atr_minima"
    }
  },
  "blocks": [
    {
      "block_index": 0,
      "start_time": "2025-10-06T08:00:00+00:00",
      "end_time": "2025-10-10T21:00:00+00:00",
      "block_duration_hours": 109,
      "split_reason": "atr_minima",
      "atr_at_split": 0.00032,
      "regime_at_split": "low",
      "session": "london",
      "estimated_ticks": 54200,
      "distance_to_next_block_hours": 63
    }
  ]
}
```

### CLI Usage

```bash
# Generate a profile with ATR-minima splitting (single symbol)
python python/cli/generator_cli.py generate-profile mt5 EURUSD \
  --start 2025-09-01T00:00:00 --end 2025-10-01T00:00:00 \
  --mode volatility_split

# Generate a continuous profile (one block per region)
python python/cli/generator_cli.py generate-profile mt5 EURUSD \
  --start 2025-09-01T00:00:00 --end 2025-10-01T00:00:00 \
  --mode continuous

# Batch: generate profiles for ALL symbols across ALL brokers
python python/cli/generator_cli.py generate-all-profiles \
  --mt5-start 2025-09-01T00:00:00 --mt5-end 2025-10-01T00:00:00 \
  --kraken-spot-start 2026-01-24T00:00:00 --kraken-spot-end 2026-03-08T00:00:00 \
  --mode volatility_split

# Run with a single profile
python python/cli/strategy_runner_cli.py run my_scenario_set.json \
  --generator-profile configs/generator_profiles/volatility_split/mt5_EURUSD_profile_vol_20260322_1219.json

# Run with multiple profiles (merged into one batch)
python python/cli/strategy_runner_cli.py run my_scenario_set.json \
  --generator-profile profiles/mt5_EURUSD_profile_vol.json profiles/mt5_GBPUSD_profile_vol.json

# Run all profiles in a directory (auto-discovers *.json files)
python python/cli/strategy_runner_cli.py run my_scenario_set.json \
  --generator-profile configs/generator_profiles/volatility_split
```

### Profile Config Resolution

Profile generation parameters are resolved per market type:

1. `market_config.json` → `market_rules.<type>.profile_defaults` (market-specific)
2. `generator_config.json` → `profile` section (global fallback)

| Parameter | Forex | Crypto | Why |
|---|---|---|---|
| `max_block_hours` | 24 | 72 | 24/7 markets have fewer volatility minima |
| `min_block_hours` | 2 | 4 | Crypto blocks below 4h are too small |
| `atr_percentile_threshold` | P10 | P15 | Higher threshold finds more split candidates in flatter ATR distributions |

The `split_algorithm` (always `atr_minima`) remains global in `generator_config.json`.

### Scenario Set Integration

Profile Run is activated via the `--generator-profile` CLI flag on the `run` command. The profile blocks replace the `scenarios[]` array from the scenario set JSON. Global config (strategy, execution, trade_simulator) is still loaded from the scenario set.

- `--generator-profile <path> [<path> ...]` → Profile Run (accepts files and/or directories)
- No flag → Free Run (backward-compatible)

**Multi-Profile Runs:** Multiple profile files or directories can be passed. If a directory is given, all `*.json` files inside are auto-discovered. All profiles are merged into a single batch with globally unique `scenario_index` values. Scenario names follow the pattern `{SYMBOL}_{mode}_{block_index:02d}` (e.g. `BTCUSD_vol_00`, `EURUSD_cont_03`). The batch summary header shows profile count and symbol count.

**Profile directories:**
- `configs/generator_profiles/volatility_split/` — ATR-minima split profiles
- `configs/generator_profiles/continuous/` — continuous (one block per region) profiles

### Generator Modes

| Mode | Description | Use Case |
|---|---|---|
| **continuous** | Single block, full time range per symbol | P&L correctness, no splitting artifacts |
| **volatility_split** | Splits at ATR minima (low-volatility points) | Parallelism within symbol, minimal split cost |

The generator **consumes** `VolatilityProfileAnalyzer` output (volatility profiles, ATR data from `discoveries_config.json`) — it does NOT compute volatility itself.

### Gap Handling

Both generators (BlocksGenerator and ProfileGenerator) treat all gap types the same way for block construction: **weekends, holidays, and short gaps are normal pauses — the algorithm sleeps through them and continues when ticks resume.** Blocks span across these gaps without splitting.

Only **moderate** and **large** gaps (real data collection issues) cause region splits — blocks never span across them.

The `GapCategory` classification (weekend, holiday, short, moderate, large) exists primarily for the **Data Coverage Report** to distinguish expected market closures from actual data problems. For block generation and P&L calculation, there is no difference between a weekend gap and any other pause — no ticks arrive, the algorithm waits, the next tick continues processing.

**Gap boundary splitting (forex only):** When a raw gap exceeds the maximum expected weekend duration (80h), the Data Coverage Report splits it at market boundaries (Friday 20:00 UTC close, Sunday 22:00 UTC open). Each sub-gap is classified independently. This prevents data loss spanning multiple weeks from being masked as a single "weekend" closure. Gaps ≤ 80h pass through unchanged — the existing weekend pattern matching handles normal closures correctly. This splitting only affects classification in the Coverage Report; block generation and P&L calculation are not impacted.

The ProfileGenerator's ATR-minima algorithm skips over gap periods (no volatility data available) when searching for split points, rather than inserting artificial forced splits into empty time ranges.

### Discovery Fingerprints

Each discovery source contributes a SHA256 checksum of its configuration:

```
discoveries_config.json → volatility_profile section → SHA256
discoveries_config.json → data_coverage section     → SHA256
```

On profile load, fingerprints are compared against current config. Mismatch triggers a warning recommending profile regeneration.

---

## Post-Run Correctness Metric

After a tick run with block splitting, the **Block Splitting Disposition** quantifies P&L distortion.

### Data Sources

- Force-closed trades (distinction from natural closes, unrealized P&L at force-close)
- ATR at block boundaries (from profile metadata)
- Balance resets (count, compounding loss estimate)
- Discarded pending orders

### Assessment Thresholds

| Disposition | Assessment | Recommendation |
|---|---|---|
| < 3% | ✅ GOOD | Current profile appropriate |
| 3% – 10% | ⚠️ MODERATE | Consider larger blocks |
| 10% – 25% | 🟡 HIGH | Results significantly distorted |
| > 25% | ❌ UNRELIABLE | Switch to continuous mode |

### Empirical Feedback Loop

The metric enables finding the optimal block size per algorithm:

```
Profile (4 blocks)  → Run → Disposition: 1.2%  ✅
Profile (16 blocks) → Run → Disposition: 8.7%  ⚠️
Profile (64 blocks) → Run → Disposition: 31%   ❌

→ Sweet spot for THIS algo: between 4 and 16 blocks
→ Maximum parallelism with acceptable correctness
```

Different algorithms find different sweet spots because holding duration, trade frequency, and strategy complexity vary. This replaces theoretical estimation with empirical measurement.

### Why Volatility-Based Split Points Are Better

Splitting at low-volatility points (ATR minima) minimizes force-close damage regardless of strategy:

- Low volatility → small unrealized P&L on open positions → force-close costs little
- Low volatility → fewer positions open (fewer signals in calm markets)
- Low volatility → small future price movement → low opportunity cost

This is strategy-independent — it's a market property, not a strategy property.
