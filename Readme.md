# FiniexTestingIDE

**Parameter-centric backtesting for algorithmic trading strategies**

> ⚠️ **No financial advice.** This software is for educational and research purposes only.

> **Version:** 1.2.0
> **Status:** Alpha
> **Target:** Developers with Python experience who want to systematically backtest trading strategies

---

## What's New in 1.2.0

- **`collected_msc` Tick Timestamp** — Per-tick monotonic collection timestamp (device clock, ms precision). Primary source for inter-tick interval profiling. Replaces non-monotonic `time_msc` which had ~19% negative diffs in MT5 forex data.
- **Data Format V1.3.0** — New parquet field `collected_msc` (int64). Backward compatible: defaults to `0` for pre-V1.3.0 data with automatic `time_msc` fallback.
- **WarningsSummary** — Consolidated global warnings section in batch reports (stress tests, data version notices). Always rendered regardless of `summary_detail` setting.
- **Data Version Tracking** — Parquet metadata `data_format_version` flows through tick index → `SingleScenario` → batch reports. Pre-V1.3.0 files flagged with synthesized interval warning.

### Previous: 1.1.2

- **STOP / STOP_LIMIT Orders** — Breakout entry orders: STOP fills at market price (taker fee), STOP_LIMIT triggers at stop price then fills at limit price (maker fee)
- **Active Order Preservation** — Unfilled limit/stop orders preserved at scenario end, captured in `PendingOrderStats` for post-run inspection
- **Active Order Reporting** — Active order counts displayed in terminal output (executive summary, per-scenario box, portfolio summary)
- **`cancel_limit_order` API** — Cancel active limit orders via `DecisionTradingApi.cancel_limit_order()` (symmetrical with `cancel_stop_order`)
- **`cancel_limit_sequence` Config** — BacktestingDeterministic parameter to cancel a limit order at a configured tick (analogous to `cancel_stop_sequence`)
- **SL/TP & Limit Validation Tests** — Expanded from 8 to 17 scenarios (~82 tests): STOP/STOP_LIMIT triggers, STOP then TP, modify stop, cancel stop/limit

### Previous: 1.1.1

- **Live Trade Executor** — Full LiveTradeExecutor implementation with broker adapter communication
- **LiveOrderTracker** — Time-based pending order management with broker reference tracking and timeout detection
- **MockBrokerAdapter** — 4-mode test adapter (instant_fill, delayed_fill, reject_all, timeout) with real Kraken BTCUSD spec
- **AbstractAdapter Tier 3** — Optional live execution methods (execute_order, check_order_status, cancel_order)
- **Margin Validation Tests** — Margin checks, lot validation, retry logic, edge case coverage
- **Multi-Position Tests** — Concurrent position management, close events, hedging validation
- **Live Executor Tests** — 47 tests covering LiveOrderTracker, LiveTradeExecutor, and MockBrokerAdapter pipeline
- **141 New Tests** — 47 live executor + 35 margin validation + 65 multi-position (some reused from baseline)
- **Pending Order Statistics** — Latency tracking (avg/min/max), outcome counting (filled/rejected/timed_out/force_closed), anomaly detection with individual records for hung orders
- **History Retention Config** — Configurable limits for order_history, trade_history, and bar_history via `app_config.json` with one-time warnings and `deque(maxlen)` auto-trimming

### Previous: 1.1

- Multi-Market Support (Kraken Spot + MT5), TradingContext, OBV Worker
- Parameter Validation (ParameterDef, strict/non-strict), Volume Integration
- Parquet Indexes, Coverage Report Caching, 211 new tests

---

## What is FiniexTestingIDE?

FiniexTestingIDE is a high-performance backtesting framework for forex and crypto trading strategies. It processes real tick data, simulates realistic broker execution, and provides comprehensive performance analysis.

**Core capabilities:**
- ✅ Tick-by-tick backtesting with real market data
- ✅ Realistic trade simulation (spreads, latency, margin)
- ✅ Multi-scenario parallel execution
- ✅ Deterministic, reproducible results (seeded randomness)
- ✅ Multi-market support (Forex via MT5, Crypto via Kraken)
- ✅ Validated accuracy (~570+ tests across 12 test suites)

---

## Features

### Data Pipeline
- **TickCollector (MQL5)** - Live tick collection from MT5 brokers
- **Parquet Storage** - Compressed, indexed tick data with quality metrics
- **Multi-Timeframe Bars** - Auto-rendered M1, M5, M15, M30, H1, H4, D1
- **Gap Detection** - Weekend, holiday, and data quality analysis
- **Discovery Caching** - Unified cache system for all analyses (gap reports, market analysis, extreme moves)

### Backtesting Engine
- **Parallel Execution** - ProcessPoolExecutor for multi-scenario runs
- **7-Phase Orchestration** - Validation → Loading → Execution → Reporting
- **Worker System** - Modular indicator computation (RSI, Envelope, MACD, OBV, ...)
- **Decision Logic** - Pluggable trading strategies with clear separation
- **Parameter Validation** - Schema-based validation with strict/non-strict modes

### Trade Simulation & Live Execution
- **Realistic Execution** - API latency + market execution delays (seeded)
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

→ See [Quickstart Guide](docs/quickstart_guide.md) for step-by-step instructions.

---

## Configuration

FiniexTestingIDE uses a **two-tier configuration system** for flexible customization:

```
configs/              # Default configurations (version controlled)
├── app_config.json        # Application settings
├── import_config.json     # Import pipeline settings (offsets, paths, processing)
├── market_config.json     # Market & broker mappings
├── brokers/              # Broker-specific configs
└── scenario_sets/        # Trading scenario definitions

user_configs/         # Your personal overrides (gitignored)
├── app_config.json        # Optional: override app settings
├── import_config.json     # Optional: override import settings (offsets, paths)
├── market_config.json     # Optional: override market config
└── discoveries_config.json # Optional: override analysis/discovery settings
```

### How It Works

**Default configs** (`configs/`) are tracked in git and provide sensible defaults for all users.

**User overrides** (`user_configs/`) allow you to customize settings without modifying tracked files:
- ✅ Gitignored - your local settings stay private
- ✅ Deep merge - override only what you need
- ✅ Optional - works without any user configs

### Example Override

Want to enable DEBUG logging locally? Create `user_configs/app_config.json`:

```json
{
  "console_logging": {
    "log_level": "DEBUG",
    "scenario": {
      "summary_detail": true
    }
  }
}
```

The system automatically merges this with the base config - all other settings remain unchanged.
`summary_detail` controls whether per-scenario detail blocks appear in the console batch summary (`false` = compact, aggregated only). File logging always gets the full summary.

→ For multi-level scenario configuration, see [Config Cascade Guide](docs/config_cascade_readme.md).

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
└── mt5/
    ├── ticks/
    │   ├── AUDUSD/
    │   ├── EURGBP/
    │   ├── EURUSD/
    │   ├── GBPUSD/
    │   ├── NZDUSD/
    │   ├── USDCAD/
    │   ├── USDCHF/
    │   └── USDJPY/
    └── bars/
        └── (same structure)
```

### Dataset Overview

| Symbol | Time Range | Ticks | Duration |
|--------|------------|-------|----------|
| AUDUSD | 2025-09-17 → 2026-01-02 | 5.3M | 107 days |
| EURGBP | 2025-09-21 → 2026-01-02 | 4.6M | 102 days |
| EURUSD | 2025-09-17 → 2026-01-02 | 5.3M | 107 days |
| GBPUSD | 2025-09-17 → 2026-01-02 | 8.5M | 107 days |
| NZDUSD | 2025-09-21 → 2026-01-02 | 3.5M | 102 days |
| USDCAD | 2025-09-21 → 2026-01-02 | 5.4M | 102 days |
| USDCHF | 2025-09-21 → 2026-01-02 | 4.7M | 102 days |
| USDJPY | 2025-09-17 → 2026-01-02 | 9.9M | 107 days |

**Total: ~47M ticks across 8 forex pairs (~3.5 months)**

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

670+ tests across 12 core suites validate trading mechanics, margin logic, execution pipeline, and data integrity.

```bash
python python/cli/test_runner_cli.py
```

Individual suites can be run separately: `pytest tests/<suite>/ -v`

Configuration: `configs/test_config.json` (excluded suites, fail-fast behavior). Each test suite has its own documentation in [`docs/tests/`](docs/tests/).

---

## Documentation

| Document | Description |
|----------|-------------|
| [CLI Tools Guide](docs/cli_tools_guide.md) | All CLI commands with examples |
| [Quickstart Guide](docs/quickstart_guide.md) | Create your first trading bot |
| [TickCollector README](docs/TickCollector_README.md) | MQL5 data collection setup |
| [Worker Naming](docs/worker_naming_doc.md) | Worker system and naming conventions |
| [Config Cascade](docs/config_cascade_readme.md) | 3-level configuration system |
| [Broker Config](docs/broker_config_guide.md) | Multi-broker setup (MT5, Kraken) |
| [Batch Preparation](docs/batch_preperation_system.md) | 7-phase orchestration system |
| [Process Execution](docs/process_execution_guide.md) | Subprocess architecture |
| [Duplicate Detection](docs/duplicate_detection_usage.md) | Data integrity protection |
| [Execution Layer Architecture](docs/architecture_execution_layer.md) | Sim/Live hybrid execution design |
| [Simulation vs Live Flow](docs/simulation_vs_live_flow.md) | Tick flow comparison, event-driven model |
| [Live Execution Architecture](docs/live_execution_architecture.md) | LiveTradeExecutor, broker polling, LiveOrderTracker |
| [Pending Order Architecture](docs/pending_order_architecture.md) | 3-world lifecycle (latency, limit, stop), modify rationale |
| [Mock Adapter Guide](docs/mock_adapter_guide.md) | MockBrokerAdapter usage and testing |
| [Test Runner](docs/tests/tests_runner_docs.md) | Unified test runner, config, per-suite docs in `docs/tests/` |
| [Data Import Pipeline](docs/data_import_pipeline.md) | Import flow, schema, offset registry, config |
| [Stress Test System](docs/stress_test.md) | Config-driven stress test injection, cascade, reporting |

---

## Current Limitations

- **No Trailing Stop/OCO/Iceberg** - Market, Limit, Stop, and Stop-Limit supported; extended types planned
- **No Partial Fills on Live** - Partial position close supported in backtesting; live execution planned
- **CORE Namespace Only** - Custom workers must be added to framework folders
- **No Frontend** - CLI and VS Code launch configs only

> **Note on Multiple Positions:** The system supports multiple simultaneous positions, validated by the multi-position test suite (65 tests). However, all included bots use single-position strategies. Multi-position strategies require careful margin management.

---

## Roadmap

For the full vision, detailed roadmap, and feature path see **[Issue #138 — Vision & Roadmap](https://github.com/dc-deal/FiniexTestingIDE/issues/138)**.

---

## License

MIT License - see [LICENSE](LICENSE)

**Trademarks:** Finiex™ is property of Frank Krätzig - see [TRADEMARK.md](TRADEMARK.md)