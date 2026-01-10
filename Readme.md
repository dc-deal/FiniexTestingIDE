# FiniexTestingIDE

**Parameter-centric backtesting for algorithmic trading strategies**

> ⚠️ **No financial advice.** This software is for educational and research purposes only.

> **Version:** 1.0 Alpha  
> **Status:** MVP Complete - Core backtesting validated  
> **Target:** Developers with Python experience who want to systematically backtest trading strategies

---

## What is FiniexTestingIDE?

FiniexTestingIDE is a high-performance backtesting framework for forex and crypto trading strategies. It processes real tick data, simulates realistic broker execution, and provides comprehensive performance analysis.

**1.0 Alpha delivers:**
- ✅ Tick-by-tick backtesting with real market data
- ✅ Realistic trade simulation (spreads, latency, margin)
- ✅ Multi-scenario parallel execution
- ✅ Deterministic, reproducible results (seeded randomness)
- ✅ Validated accuracy (44 baseline tests, 13 benchmark tests)

---

## Features

### Data Pipeline
- **TickCollector (MQL5)** - Live tick collection from MT5 brokers
- **Parquet Storage** - Compressed, indexed tick data with quality metrics
- **Multi-Timeframe Bars** - Auto-rendered M1, M5, M15, M30, H1, H4, D1
- **Gap Detection** - Weekend, holiday, and data quality analysis

### Backtesting Engine
- **Parallel Execution** - ProcessPoolExecutor for multi-scenario runs
- **7-Phase Orchestration** - Validation → Loading → Execution → Reporting
- **Worker System** - Modular indicator computation (RSI, Envelope, MACD, ...)
- **Decision Logic** - Pluggable trading strategies with clear separation

### Trade Simulation
- **Realistic Execution** - API latency + market execution delays (seeded)
- **Spread Calculation** - Live bid/ask spread from tick data
- **Margin Management** - Position sizing with margin checks
- **Order Lifecycle** - PENDING → EXECUTED status tracking

### Analysis Tools
- **Market Analysis** - ATR volatility, session activity, cross-instrument ranking
- **Scenario Generation** - Automatic blocks (chronological) or stress (high-volatility)
- **Performance Profiling** - Operation-level breakdown, bottleneck detection

→ See [CLI Tools Guide](docs/cli_tools_guide.md) for all available commands.

---

## Quick Start

```
1. Collect tick data    →  TickCollector (MT5)
2. Import to Parquet    →  📥 Import: Offset +3
3. Create your bot      →  Worker + Decision + Config
4. Run backtest         →  🔬 Run Scenario
```

→ See [Quickstart Guide](docs/quickstart_guide.md) for step-by-step instructions.

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
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  BACKTESTING ENGINE                                     │    │
│  ├─────────────────────────────────────────────────────────┤    │
│  │  Workers (RSI, Envelope, ...)  →  Indicator Values      │    │
│  │         ↓                                               │    │
│  │  Decision Logic (AggressiveTrend, ...)  →  BUY/SELL     │    │
│  │         ↓                                               │    │
│  │  Trade Simulator  →  Order Execution + P&L              │    │
│  └─────────────────────────────────────────────────────────┘    │
│         ↓                                                       │
│  Results (Trade History, Performance Metrics, Profiling)        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quality Assurance

### Baseline Tests (44 tests)
Validates trading mechanics accuracy:
- Bar snapshots, warmup validation
- P&L calculation (gross, net, fees)
- Trade execution (entry/exit prices, directions)
- Latency determinism (seeded delays)

### Benchmark Tests (13 tests)
Validates performance characteristics:
- Tick processing speed (target: 8,000+ ticks/sec)
- Warmup time, scenario duration
- System-bound tolerances (±10-15%)
- Certificate-based CI validation

→ See [Baseline Tests](docs/tests_baseline_docs.md) and [Benchmark Tests](docs/tests_benchmark_docs.md)

---

## Documentation

| Document | Description |
|----------|-------------|
| [CLI Tools Guide](docs/cli_tools_guide.md) | All CLI commands with examples |
| [Quickstart Guide](docs/quickstart_guide.md) | Create your first trading bot |
| [TickCollector README](docs/TickCollector_README.md) | MQL5 data collection setup |
| [Worker Naming](docs/worker_naming_doc.md) | Worker system and naming conventions |
| [Config Cascade](docs/config_cascade_readme.md) | 3-level configuration system |
| [Baseline Tests](docs/tests_baseline_docs.md) | 44 validation tests |
| [Benchmark Tests](docs/tests_benchmark_docs.md) | 13 performance tests |

---

## Current Limitations (Alpha)

- **Market Orders Only** - Limit/Stop orders planned for post-MVP
- **No Partial Fills** - Full position close only, partial fills planned for post-MVP
- **CORE Namespace Only** - Custom workers must be added to framework folders
- **No Frontend** - CLI and VS Code launch configs only

> **Note on Multiple Positions:** The system supports multiple simultaneous positions, but this is **untested**. All included bots and tests use single-position strategies (one trade at a time, long or short). Use multiple positions at your own risk.

---

## Vision & Roadmap

### Post-MVP (Next)
- Extended order types (Limit, Stop, FOK, IOC)
- Partial fills support
- USER namespace for custom workers
- Additional standard indicators
- **Live Trading Integration** - Core adaptation for FiniexAutoTrader connection

### Worker Types (Planned)
| Type | Purpose | Status |
|------|---------|--------|
| **COMPUTE** | Synchronous indicator calculations (RSI, SMA, MACD) | ✅ MVP |
| **API** | HTTP requests with caching (external data sources) | Planned |
| **EVENT** | Live connections (WebSocket, AI alerts, news feeds) | Planned |

### Phase 4: UX Layer
- Web frontend with real-time progress
- Interactive charts and parameter panels
- Visual scenario builder

### Phase 5: Intelligence Layer
- Parameter optimization
- Market regime detection
- AI-enhanced suggestions

### Phase 6: Enterprise
- Cloud-native SaaS platform
- Multi-tenancy and billing

---

## License

MIT License - see [LICENSE](LICENSE)

**Trademarks:** Finiex™ is property of Frank Krätzig - see [TRADEMARK.md](TRADEMARK.md)

---

*Building the foundation for parameter-centric trading strategy development.*

**1.0 Alpha** - MVP Complete ✅