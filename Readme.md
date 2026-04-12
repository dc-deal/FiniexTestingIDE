# FiniexTestingIDE

**Parameter-centric backtesting for algorithmic trading strategies**

> ⚠️ **No financial advice.** This software is for educational and research purposes only.

> **Version:** 1.2.1
> **Status:** Alpha
> **Target:** Developers with Python experience who want to systematically backtest trading strategies

---

## What's New in 1.2.1

- **Millisecond-Based Latency Timing** — Tick-based latency replaced with millisecond-timestamp timing. Fill detection uses `collected_msc` / `time_msc` for realistic broker delay simulation.
- **Inbound-Only Fill Semantics** — Fill price determined at broker arrival (`broker_fill_msc = placed_at_msc + inbound_delay`). Matches industry standard (QuantConnect, Backtrader, Zipline, MetaTrader). Portfolio updated immediately — no outbound delay queue.
- **Tick Processing Budget** — Deterministic per-tick processing time simulation with configurable budget and clipping detection.
- **Filtering Bypass for Broker Simulation** — Flag-based tick loop split ensures filtered ticks still pass through broker simulation (latency drain, active order monitoring).
- **Generator Profile System** — Volatility-aware block generation with profile-based scenario selection.
- **Post-Run Block Splitting Correctness Metric** — Quality metric for generator block boundaries in batch reports.
- **Generator Warmup Removal** — Eliminated redundant warmup time from generator calculations.

### Previous Releases

- **1.2.0** — USER Namespace, trading core completion (all order types), tick data trimming, unified test runner, discovery cache
- **1.1.2** — STOP/STOP_LIMIT orders, active order preservation and reporting
- **1.1.1** — Live Trade Executor, MockBrokerAdapter, margin/multi-position/pending stats test suites
- **1.1** — Multi-market support (Kraken Spot + MT5), parameter validation, OBV worker

---

## What is FiniexTestingIDE?

FiniexTestingIDE is a high-performance backtesting framework for forex and crypto trading strategies. It processes real tick data, simulates realistic broker execution, and provides comprehensive performance analysis.

**Core capabilities:**
- ✅ Tick-by-tick backtesting with real market data
- ✅ Realistic trade simulation (spreads, latency, margin)
- ✅ Multi-scenario parallel execution
- ✅ Deterministic, reproducible results (seeded randomness)
- ✅ Multi-market support (Forex via MT5, Crypto via Kraken)
- ✅ Validated accuracy — comprehensive integration, black-box, and white-box test suites
- ✅ Validated performance — standardized benchmark certificate with regression detection

---

## Features

### Data Pipeline
- **TickCollector (MQL5)** - Live tick collection from MT5 brokers
- **Parquet Storage** - Compressed, indexed tick data with quality metrics
- **Multi-Timeframe Bars** - Auto-rendered M1, M5, M15, M30, H1, H4, D1
- **Gap Detection** - Weekend, holiday, and data quality analysis
- **Discovery Caching** - Unified cache system for all discoveries (gap reports, volatility profiles, extreme moves)

### Backtesting Engine
- **Parallel Execution** - ProcessPoolExecutor for multi-scenario runs
- **7-Phase Orchestration** - Validation → Loading → Execution → Reporting
- **Worker System** - Modular indicator computation (RSI, Envelope, MACD, OBV, ...)
- **Decision Logic** - Pluggable trading strategies with clear separation
- **Parameter Validation** - Schema-based validation with strict/non-strict modes
- **USER Namespace** - Custom workers and decision logic with auto-discovery and hot-reload

### Trade Simulation & Live Execution
- **Realistic Execution** - Inbound latency simulation with ms-timestamp fill detection (seeded)
- **Live Trade Executor** - Broker adapter communication with pending order tracking
- **Spread Calculation** - Live bid/ask spread from tick data
- **Margin Management** - Position sizing with margin checks
- **Order Lifecycle** - PENDING → EXECUTED status tracking (Market, Limit, Stop, Stop-Limit)
- **Limit Orders** - Two-phase lifecycle: latency simulation → price trigger monitoring
- **Stop Orders** - Breakout entry with market fill (STOP) or limit conversion (STOP_LIMIT)
- **Multi-Broker Fees** - Spread-based (MT5) and maker/taker (Kraken, maker fee for limit fills)
- **Mock Testing** - MockBrokerAdapter for deterministic pipeline verification

### Analysis Tools
- **Discovery System** - Unified analysis with Parquet-based caching (auto-invalidation on data change)
  - **Market Analysis** - ATR volatility, session activity, cross-instrument ranking
  - **Extreme Move Scanner** - Directional price movement detection (ATR-normalized)
  - **Data Coverage** - Gap detection and data quality assessment
- **Scenario Generation** - Automatic blocks (chronological) or high-volatility selection
- **Performance Profiling** - Operation-level breakdown, bottleneck detection

> See [Discovery System](docs/discovery_system.md) for architecture details.

→ See [CLI Tools Guide](docs/cli_tools_guide.md) for all available commands.

---

## Quick Start

```
1. Collect tick data    →  TickCollector (MT5)
2. Import to Parquet    →  📥 Import (config-driven offsets)
3. Create your bot      →  Worker + Decision + Config
4. Run backtest         →  🔬 Run Scenario
```

→ See [Quickstart Guide](docs/user_guides/quickstart_guide.md) for step-by-step instructions.

---

## Configuration

Two-tier system: **default configs** (`configs/`, version controlled) and **user overrides** (`user_configs/`, gitignored). The system deep-merges user overrides — override only what you need, everything else stays at defaults.

→ See [Config Cascade Guide](docs/config_cascade_guide.md) for the full configuration system, directory structure, and scenario-level parameter overrides.

---

## Sample Data

A sample dataset is available for testing and learning:

**Download:** [download link](https://drive.google.com/file/d/1GEdkwWDWKV5n7hUoRALvSB2PR7olkUjR/view?usp=sharing)

### Installation

Extract the ZIP contents to `data/processed/`:

```
data/processed/
├── .parquet_tick_index.json
├── .parquet_bars_index.json
├── mt5/
│   ├── ticks/
│   │   ├── AUDUSD/ ... USDJPY/
│   └── bars/
└── kraken_spot/
    ├── ticks/
    │   ├── BTCUSD/ ... XRPUSD/
    └── bars/
```

### Dataset Overview

| Broker | Symbols | Time Range | Ticks |
|--------|---------|------------|-------|
| MT5 (Forex) | AUDUSD, EURGBP, EURUSD, GBPUSD, NZDUSD, USDCAD, USDCHF, USDJPY | Sep 2025 → Mar 2026 | ~96M |
| Kraken Spot | ADAUSD, BTCUSD, DASHUSD, ETHEUR, ETHUSD, LTCUSD, SOLUSD, XRPUSD | Jan → Mar 2026 | ~8M |

**Total: ~104M ticks across 16 instruments (8 Forex pairs + 8 Crypto)**

> ⚠️ **Data Disclaimer:** The provided dataset consists of historical tick and bar data
collected locally via MetaTrader 5 and processed into Parquet format.

The data is provided strictly for research, backtesting and
educational purposes. It is not a licensed market data feed,
may contain gaps or inaccuracies, and must not be used for
live trading or commercial redistribution.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA FLOW                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  MQL5 TickCollector                                             │
│         ↓                                                       │
│  JSON Files (raw ticks)                                         │
│         ↓                                                       │
│  Import CLI (UTC conversion, quality metrics)                   │
│         ↓                                                       │
│  Parquet Files + Bar Rendering (M1→D1)                          │
│         ↓                                                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  BACKTESTING ENGINE                                       │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  Workers (RSI, Envelope, ...)  →  Indicator Values        │  │
│  │         ↓                                                 │  │
│  │  Decision Logic (AggressiveTrend, ...)  →  BUY/SELL       │  │
│  │         ↓                                                 │  │
│  │  Trade Simulator  →  Order Execution + P&L                │  │
│  └───────────────────────────────────────────────────────────┘  │
│         ↓                                                       │
│  Results (Trade History, Performance Metrics, Profiling)        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quality Assurance

Comprehensive test suite covering integration tests, black-box tests, and white-box tests. All suites are runnable with the provided sample dataset.

```bash
python python/cli/test_runner_cli.py
```

Individual suites can be run separately: `pytest tests/<suite>/ -v`

Configuration: `configs/test_config.json` (excluded suites, fail-fast behavior). Each test suite has its own documentation in [`docs/tests/`](docs/tests/).

**Benchmark Certificate:** Each release includes a benchmark report validating throughput performance against registered system baselines (3-run median, tolerance-based regression detection). The certificate is verified automatically in CI. See [`docs/tests/simulation/benchmark_tests.md`](docs/tests/simulation/benchmark_tests.md) for details.

---

## Documentation

See the [Documentation Index](docs/documentation_index.md) for a complete overview of all guides, architecture docs, data pipeline docs, and test suite documentation.

---

## Current Limitations

- **No Trailing Stop/OCO/Iceberg** - Market, Limit, Stop, and Stop-Limit supported; extended types planned
- **No Partial Fills on Live** - Partial position close supported in backtesting; live execution planned
- **No Frontend** - CLI and VS Code launch configs only

> **Note on Multiple Positions:** The system supports multiple simultaneous positions, validated by the multi-position test suite (65 tests). However, all included bots use single-position strategies. Multi-position strategies require careful margin management.

---

## Roadmap

**Horizon 1 — Foundation: Complete (V1.2)**
- Trading simulation core with all order types (Market, Limit, Stop, Stop-Limit, Partial Close)
- USER Namespace — custom workers and decision logic outside framework code
- Multi-market data pipeline (MT5 Forex + Kraken Spot, 16 instruments)
- Unified test infrastructure and discovery cache management

For the full vision, detailed roadmap, and feature path see **[Issue #138 — Vision & Roadmap](https://github.com/dc-deal/FiniexTestingIDE/issues/138)**.

---

## License

MIT License - see [LICENSE](LICENSE)

**Trademarks:** Finiex™ is property of Frank Krätzig - see [TRADEMARK.md](TRADEMARK.md)

## Author

**Frank Krätzig** — Software Developer & Data Engineer, Karlsruhe region

15 years of IT experience focused on data-driven backend solutions,
ETL pipelines, and performance optimization.

- LinkedIn: https://www.linkedin.com/in/frank-kraetzig
- GitHub: https://github.com/dc-deal