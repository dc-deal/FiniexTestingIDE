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

JSON files in `configs/generator_profiles/`. Human-readable but must not be manually edited (documented convention, not enforced via hash).

```json
{
  "profile_meta": {
    "symbol": "EURUSD",
    "broker_type": "mt5",
    "generator_mode": "volatility_split",
    "total_coverage_hours": 4320,
    "block_count": 12,
    "generation_timestamp": "2026-03-19T14:00:00Z",
    "discovery_fingerprints": {
      "volatility_profile": "sha256:a3f2b1...",
      "data_coverage": "sha256:c7d4e9..."
    }
  },
  "blocks": [
    {
      "start_time": "2025-10-06T08:00:00Z",
      "end_time": "2025-10-10T21:00:00Z",
      "block_duration_hours": 109,
      "split_reason": "volatility_minimum",
      "atr_at_split_point": 0.00032,
      "volatility_regime_at_split": "LOW",
      "distance_to_next_block_hours": 63
    }
  ]
}
```

### Scenario Set Integration

```json
{
  "generator_profile": "configs/generator_profiles/eurusd_vol_2026Q1.json",
  "use_generator_profile": true,
  "scenarios": [...]
}
```

- `use_generator_profile: true` → Profile Run
- `use_generator_profile: false` or absent → Free Run (backward-compatible)

### Generator Modes

| Mode | Description | Use Case |
|---|---|---|
| **continuous** | Single block, full time range per symbol | P&L correctness, no splitting artifacts |
| **volatility_split** | Splits at ATR minima (low-volatility points) | Parallelism within symbol, minimal split cost |

The generator **consumes** `VolatilityProfileAnalyzer` output (volatility profiles, ATR data from `discoveries_config.json`) — it does NOT compute volatility itself.

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
