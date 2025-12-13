# FiniexTestingIDE

**© 2025 Frank Krätzig. All rights reserved.**

---

## Trading-Strategy-Testing IDE - MVP Development

**Vision:** Parameter-centric testing platform for trading strategies with focus on reproducible results and IP protection.

**Current Phase:** MVP Foundation - Performance Optimizations Complete

---

## 🎯 MVP Status - What's Already Working (Pre-Alpha V0.9.0)

### ✅ Data Pipeline (Production-Ready)
- **MQL5 TickCollector v1.03** - Live tick collection with error classification
- **JSON → Parquet Conversion** - Quality-aware processing with metadata
- **Multi-Symbol Support** - EURUSD, AUDUSD, GBPUSD, EURCHF
- **Quality Metrics** - 3-level error classification (Negligible/Serious/Fatal)
- **Data Modes** - Clean/Realistic/Raw for different test scenarios

**Sample Output:** [AUDUSD Ticks](./data/samples/AUDUSD_20250916_223859_ticks.json)

### ✅ Bar Pre-Rendering & Indexing (NEW in V0.9.0) 🚀
**Massive warmup speedup: 100ms vs 60s**

- **Vectorized Bar Generation** - Pre-render bars from all tick data using pandas
- **Parquet Bar Files** - One file per symbol/timeframe (M1, M5, M15, M30, H1, H4, D1)
- **Bar Index System** - O(1) file selection for instant warmup loading
- **Fast Warmup Path** - Load pre-rendered bars instead of rendering from ticks
- **Automatic Rendering** - Runs after tick import or manually via `bar_importer.py`

**Performance Impact:**
- **Before:** 60+ seconds warmup (rendering bars from ticks)
- **After:** <100ms warmup (loading from parquet)
- **~600x faster** warmup for typical scenarios

**Technical Details:**
- Uses `pandas.resample()` for vectorized bar generation
- Hybrid bar detection for incomplete periods
- Gap handling with synthetic bar insertion
- Optimized data types (float32) for memory efficiency

### ✅ Live Execution Monitoring (NEW in V0.9.0) 📊
**Real-time progress display during strategy execution**

- **Rich Console UI** - Live updating progress bars with system resources
- **Per-Scenario Stats** - Progress, P&L, trades, execution time
- **System Resources** - CPU/RAM monitoring, running/completed scenario count
- **Thread-Safe Updates** - 300ms refresh rate, no terminal flicker
- **Synchronized Start** - Barrier ensures all scenarios start tick processing simultaneously

**Live Display Example:**
```
╭───────────────────────────────────────────── 🔬 Strategy Execution Progress ─────────────────────────────────────────────╮
│ ⚡ System Resources │ CPU:   0.6% │ RAM:   3.1/31.0 GB │ Running: 0/2 │ Completed: 2/2                                   │
│                                                                                                                          │
│  ✅  GBPUSD_window_01      ████████████████████   29.4s │ $   9,660 ($-340.29)                                           │
│                            100.0%                Trades: 106 (2W / 104L)                                                 │
│  ✅  GBPUSD_window_02      ████████████████████   29.1s │ $   9,867 ($-132.59)                                           │
│                            100.0%                Trades: 64 (3W / 61L)                                                   │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

### ✅ Testing Framework (Functional)
- **Batch Orchestrator** - Multi-scenario testing (sequential + parallel)
- **Worker System** - RSI, SMA, Envelope workers with bar processing
- **Worker Parallelization** - ThreadPool for worker execution (11ms+ speedup per tick)
- **Bar Rendering** - Multi-timeframe support with warmup management
- **Signal Generation** - Decision coordinator generates trading signals

### ✅ Configuration System
- **Scenario Configs** - JSON-based, supports parameters + execution settings
- **Scenario Generator** - Automatic scenario creation from tick data
- **Flexible Parameters** - Strategy config (RSI/Envelope settings) + execution config (parallelization)

### ✅ Factory Architecture (NEW in V0.7) 🎉
- **Worker Factory** - Config-based worker creation, no more hardcoding
- **DecisionLogic Factory** - Separation of concerns, exchangeable strategies
- **Namespace System** - CORE/, USER/, BLACKBOX/ for workers/logics
- **Worker Type Classification** - COMPUTE/API/EVENT (MVP: only COMPUTE)
- **Per-Scenario Requirements** - Each scenario calculates its own warmup requirements
- **Dynamic Loading** - Hot-loading of USER/ workers without restart

### ✅ Enhanced Performance Logging & Profiling (V0.7.1 → V0.9.0) 📊
- **Comprehensive Metrics** - Per-worker, per-scenario, and aggregated performance stats
- **Parallel Efficiency Tracking** - Real-time measurement of parallelization benefits
- **Bottleneck Analysis** - Automatic detection of slowest components
- **Decision Logic Metrics** - Separate tracking for strategy decision time
- **Batch Mode Clarity** - Clear indication of batch vs. scenario parallelization
- **Tick Loop Profiling** - Operation-level breakdown (worker_decision, bar_rendering, etc.)
- **Overhead Analysis** - Worker execution vs coordination overhead visualization

**Performance Optimizations (V0.9.0):**
- Fixed massive datetime/pandas bottleneck in tick iteration (20,000+ eliminated pd.to_datetime calls)
- Bar history caching (100-200x fewer dict rebuilds during tick loop)
- Optimized bar rendering with timestamp pre-parsing
- Streamlined performance reports with visual breakdowns

### ✅ Order Execution System (NEW in V0.8 - Issue #003 COMPLETED) 🎯
**Deterministic order execution with realistic broker delays**

**Seeded Randomness:**
- **API Latency Simulation** - Network and API processing delays (1-3 ticks)
- **Market Execution Delays** - Broker-side order matching time (2-5 ticks)
- **Reproducible Testing** - Seeds ensure identical execution across runs
- **Config-Based Seeds** - `trade_simulator_seeds` in scenario JSON files

**Order Lifecycle:**
```
Order Submitted → PENDING (API delay) → PENDING (Execution delay) → EXECUTED
```

**MVP Implementation:**
- **Tick-Based Delays** - Simple, deterministic delays measured in ticks
- **Always Fill** - Orders always execute (no rejections except margin)
- **Order Status Tracking** - PENDING → EXECUTED lifecycle
- **Execution Statistics** - Track submitted/filled/rejected orders

**Configuration Example:**
```json
{
  "trade_simulator_seeds": {
    "api_latency_seed": 42,
    "market_execution_seed": 123
  }
}
```

**Post-MVP Roadmap:**
- **MS-Based Delays** - Realistic millisecond timing with tick timestamp mapping
- **OrderBook Simulation** - Liquidity-aware order matching with partial fills
- **Extended Order Types** - FOK (Fill-Or-Kill), IOC (Immediate-Or-Cancel)
- **Market Impact** - Price slippage based on order size and liquidity
- **Partial Fills** - Large orders filled incrementally based on available liquidity
- **Dynamic OrderBook** - Seeded liquidity generation from tick volatility patterns
- **Broker-Specific OrderBooks** - AbstractOrderBook with MT5/Kraken implementations
- **FiniexAutoTrader Integration** - Live trading via same DecisionTradingAPI interface

**Architecture Notes:**
- **OrderExecutionEngine** - Manages pending orders with seeded delays
- **DecisionTradingAPI** - Public interface for decision logics (simulator + live-ready)
- **Minimal Code Changes** - Live trading requires ~50 lines (replace simulator with live executor)

### ⚠️ Blackbox Support (Prepared, Post-MVP)
- **Folder Structure** - `python/workers/blackbox/` and `python/decision_logic/blackbox/`
- **Git-Ignored** - All `.py` files automatically excluded (IP protection)
- **Feature-Gated** - Implementation planned for post-MVP (encrypted/compiled workers)

---

## 📁 Project Structure (Pre-Alpha V0.9.0)

```
FiniexTestingIDE/
├── mql5/
│   └── TickCollector.mq5          # Live tick collection
├── python/
│   ├── data_management/               # Data pipeline
│   │   ├── tick_importer.py       # JSON → Parquet conversion
│   │   ├── bar_importer.py        # Pre-render bars from ticks (NEW)
│   │   ├── scenario_generator.py  # Auto-generate test configs
│   │   └── index/           # Parquet index managers
│   ├── framework/                 # Core framework (MVP stable)
│   │   ├── bar_renderer/          # Multi-timeframe bar generation
│   │   ├── workers/               # Worker system + coordinator
│   │   ├── decision_logic/        # Strategy logic (factory-based)
│   │   ├── factories/             # Worker/Logic factories (V0.7)
│   │   ├── performance/           # Performance logging system (V0.7.1)
│   │   ├── reporting/             # Batch summaries + profiling (V0.9)
│   │   ├── trading/               # Trade simulation (V0.8)
│   │   │   ├── order_execution_engine.py    # Order lifecycle
│   │   │   ├── portfolio_manager.py         # Balance/positions
│   │   │   └── decision_trading_api.py      # Public API
│   │   └── types.py               # Shared type definitions
│   ├── components/                # UI components (NEW V0.9)
│   │   ├── display/               # Live progress display
│   │   └── logger/                # Visual console logger
│   ├── workers/                   # Concrete workers
│   │   ├── core/                  # Built-in (RSI, SMA, Envelope)
│   │   ├── user/                  # Custom open-source
│   │   └── blackbox/              # IP-protected (git-ignored)
│   ├── decision_logic/            # Concrete strategies
│   │   ├── core/                  # Built-in (SimpleConsensus)
│   │   ├── user/                  # Custom open-source
│   │   └── blackbox/              # IP-protected (git-ignored)
│   ├── orchestrator/              # Batch testing
│   │   └── batch_orchestrator.py  # Multi-scenario execution
│   ├── experiments/               # Research & benchmarks (NEW)
│   │   └── gil_benchmark/         # Threading vs multiprocessing study
│   └── tests/                     # Test suite (planned)
├── configs/
│   ├── app_config.json            # App-wide settings
│   └── scenario_sets/             # Test scenario definitions
└── data/
    ├── raw/                       # MQL5 JSON exports
    ├── processed/                 # Parquet files (ticks + bars)
    └── cache/                     # Temporary files
```

---

## 🚧 MVP Roadmap - What's Coming

### 📋 Core Issue C#001: Logging & TUI (🚧 In Progress)
**Goal:** Structured logging and live TUI dashboard

**Completed (V0.9.0):**
- ✅ Live progress display with rich console UI
- ✅ Visual console logger with buffered mode
- ✅ Thread-safe logging with scenario grouping
- ✅ Custom error types (validation_error, config_error, hard_error)

**Remaining:**
- [ ] Log file output with rotation
- [ ] CLI scripting foundation (headless mode, programmatic access)

**Effort:** 1-2 days remaining  
**Priority:** Low (Polish)  
**Status:** Partially complete - core features done

---

### ✅ Core Issue C#003: Trade Simulation (COMPLETED) ✅
**Goal:** Realistic trade execution with portfolio management

**COMPLETED Features:**
- ✅ **OrderExecutionEngine** - Deterministic delay simulation with seeds
- ✅ **Seeded Randomness** - Reproducible API and execution delays
- ✅ **Order Lifecycle** - PENDING → EXECUTED status tracking
- ✅ **PortfolioManager** - Balance/equity tracking, open positions
- ✅ **BrokerConfig System** - MT5/Kraken adapter architecture
- ✅ **DecisionTradingAPI** - Public interface for decision logics
- ✅ **Trading Fees** - SpreadFee from live tick data
- ✅ **Risk Management** - Margin checks, position tracking
- ✅ **Market Orders** - Fully functional with realistic delays

**Post-MVP Extensions (Planned):**
- [ ] **MS-Based Timing** - Convert from tick-based to millisecond-based delays
- [ ] **OrderBook Simulation** - Liquidity-aware matching with partial fills
- [ ] **Extended Orders** - FOK, IOC, Stop-Limit with time-in-force
- [ ] **Market Impact** - Order size affects execution price
- [ ] **Dynamic Liquidity** - Seeded orderbook generation from tick volatility
- [ ] **Broker-Specific Books** - AbstractOrderBook with MT5/Kraken variants
- [ ] **FiniexAutoTrader** - Live trading integration via DecisionTradingAPI
- [ ] **EventBus Integration** - TradeExecuted, OrderRejected, MarginWarning events
- [ ] **Swap/Rollover** - Overnight interest calculations
- [ ] **Advanced Slippage** - Non-linear slippage based on market conditions

**Status:** ✅ **COMPLETED** - MVP features fully functional  
**Effort:** 4-5 days (completed as planned)

---

### 📋 Core Issue C#004: Performance Validation & Benchmarking (🚧 In Progress)
**Goal:** Validate performance-first architecture and establish production readiness

**Completed (V0.9.0):**
- ✅ Tick loop profiling with operation-level breakdown
- ✅ Bottleneck analysis and visualization
- ✅ Performance regression detection framework
- ✅ Worker decision overhead analysis
- ✅ GIL benchmark experiment (threading vs multiprocessing study)

**Remaining:**

**A) Benchmarking Suite**
- [ ] Batch testing: 10 scenarios @ 1,000 ticks in <60s (8 cores)
- [ ] Stress testing: 100k ticks @ 500 Hz simulation
- [ ] Memory profiling: <8GB for 100k ticks, no leaks
- [ ] Linear scaling validation: 16 cores → 2x faster

**B) Documentation**
- [ ] Performance guide (cores, memory, best practices)
- [ ] Benchmark results in README (vs MT5 comparison)
- [ ] Scalability charts

**Why Critical:**
- **Proof-of-Concept** for production readiness
- **User confidence**: "Is this tool fast enough?"
- **Design validation**: Architecture can scale to production workloads

**Status:** **In Progress** - Profiling infrastructure complete, benchmarks remaining  
**Effort:** 1-2 days remaining  
**Priority:** HIGH (MVP validation)

---

### 📋 Core Issue C#005: Blackbox System (OPTIONAL)
**Goal:** Encrypted/compiled worker and decision logic loading

- [ ] Worker/Logic encryption utilities
- [ ] Secure loading mechanism
- [ ] License verification system
- [ ] Documentation for blackbox developers

**Status:** OPTIONAL - Only if time permits after C#004  
**Effort:** 2-3 days  
**Priority:** Optional (Nice-to-have for IP protection demo)

---

### 📋 Core Issue C#006: Code Guidelines & CI (FOUNDATION)
**Goal:** Professional code quality standards and automated enforcement

**Code Guidelines (2-3 days):**

**A) Documentation Standards**
- [ ] English-only codebase policy
- [ ] Docstring conventions (Google style)
- [ ] Type hints everywhere
- [ ] Comment guidelines

**B) Code Style**
- [ ] PEP 8 compliance (autopep8)
- [ ] Import sorting (isort)
- [ ] Line length: 79 chars
- [ ] Naming conventions

**C) Quality Gates**
- [ ] Type checking (mypy)
- [ ] Linting (flake8)
- [ ] Complexity limits (McCabe)

**D) CI Pipeline**
- [ ] GitHub Actions workflow
- [ ] Pre-commit hooks
- [ ] PR blocking on violations

**Refactoring Effort (1-2 days):**
- [ ] Convert German comments to English
- [ ] Add missing type hints
- [ ] Fix linting violations
- [ ] Update docstrings

**Why Foundation:**
- **Team Collaboration** - Enables future contributors
- **Code Quality** - Prevents regression
- **Professional Standard** - Industry best practices

**Status:** Foundation for Post-MVP  
**Timing:** After C#004, before Post-MVP Phase 4  
**Effort:** 2-3 days + 1-2 days refactor  
**Priority:** Foundation (Critical for long-term maintenance)

---

### 📋 Core Issue C#007: Automated Test System (FOUNDATION)
**Goal:** Comprehensive test coverage for regression protection and confident refactoring

**Foundation for Post-MVP (2-3 days):**

**A) Unit Tests**
- [ ] Workers: RSI/Envelope computation logic
- [ ] Orchestrator: Decision coordinator contract-lifting
- [ ] Data Import: `tick_hash` uniqueness, `warning_count` accuracy

**B) Integration Tests (E2E Slices)**
- [ ] Warmup + test slice → strategy runner → signal validation
- [ ] Performance metrics assertions (computation time, parallel efficiency)

**C) CI Integration**
- [ ] pytest job in GitHub Actions (extends C#006 pipeline)
- [ ] Coverage reporting and artifacts
- [ ] PR blocking on test failures

**D) Test Infrastructure**
- [ ] Fixtures: Deterministic Parquet/JSON test data (100 ticks)
- [ ] Generator script for reproducible test data
- [ ] Test structure: `tests/unit/`, `tests/integration/`, `tests/fixtures/`

**Status:** Foundation for Post-MVP - Builds on C#006 CI  
**Timing:** After C#006, completes foundation before Post-MVP Phase 4  
**Effort:** 2-3 days  
**Priority:** Foundation (Critical for regression protection)

---

## 📊 MVP Timeline

**Core Path (Critical):** ~6-8 days remaining

1. ✅ **C#003** (4-5 days) → **COMPLETED** - Trade simulation ✅
2. 🚧 **C#004** (1-2 days remaining) → **In Progress** - Performance validation
3. 🚧 **C#001** (<1 day remaining) - Logging & TUI polish

**Optional Path (If time permits):** +2-3 days

4. **C#005** (2-3 days) - Blackbox system 🔒 OPTIONAL

**Foundation Path (Pre-Post-MVP):** +5-8 days

5. **C#006** (2-3 days + 1-2 days refactor) - Code Guidelines & CI 📏 FOUNDATION
6. **C#007** (2-3 days) - Automated Test System 🧪 FOUNDATION

**Total Estimated:** 6-18 days (1-4 weeks) depending on optional features

**Critical for MVP Release:** C#003 ✅ + C#004 (Trade Simulation + Performance Validation)

**Decision Point:** Assess schedule after C#004 completion

---

## 🗃️ Architecture Overview

### System Architecture (V0.9.0)
```
MQL5 TickCollector → JSON → Parquet (Quality-Aware)
                                ↓
                    Data Loader (Multi-Mode: Clean/Realistic/Raw)
                                ↓
                    ┌─────────────────────────────────┐
                    │  Bar Pre-Rendering (NEW V0.9)   │
                    ├─────────────────────────────────┤
                    │  VectorizedBarRenderer          │
                    │  ParquetBarsIndexManager        │
                    │  Bar warmup: <100ms vs 60s      │
                    └─────────────────────────────────┘
                                ↓
                    Scenario Config (decision_logic_type + worker_types)
                                ↓
                    ┌─────────────────────────────────┐
                    │  Factory Layer (V0.7)           │
                    ├─────────────────────────────────┤
                    │  Worker Factory                 │
                    │  DecisionLogic Factory          │
                    └─────────────────────────────────┘
                                ↓
                    Batch Orchestrator (Multi-Scenario)
                                ↓
                    ┌─────────────────────────────────┐
                    │  Live Monitoring (NEW V0.9)     │
                    ├─────────────────────────────────┤
                    │  LiveProgressDisplay            │
                    │  Real-time stats & progress     │
                    └─────────────────────────────────┘
                                ↓
                    Worker Coordinator (Parallel Workers)
                                ↓
                    Decision Logic (Injected Strategy)
                                ↓
                    ┌─────────────────────────────────┐
                    │  Trade Simulator (V0.8)         │
                    ├─────────────────────────────────┤
                    │  Order Execution Engine         │
                    │  Portfolio Manager              │
                    │  Risk Management                │
                    └─────────────────────────────────┘
                                ↓
                    ┌─────────────────────────────────┐
                    │  Performance Analysis (V0.9)    │
                    ├─────────────────────────────────┤
                    │  Profiling & Bottleneck         │
                    │  Worker Decision Breakdown      │
                    │  Aggregated Reports             │
                    └─────────────────────────────────┘
                                ↓
                    Results & Performance Metrics
```

**Key Components:**
- **Bar Pre-Rendering** - Vectorized bar generation with parquet storage (600x faster warmup)
- **Live Progress Display** - Real-time monitoring with rich console UI
- **Performance Profiling** - Operation-level breakdown and bottleneck analysis
- **Factory Architecture** - Dynamic loading of workers and decision logics
- **Trade Simulator** - Realistic order execution with seeded randomness

---

## 🚀 Quick Start

### Get Started with Sample Data

Want to experiment with FiniexTestingIDE immediately? Use our sample data package!

**1. Download sample data**
   - Download [`tick_starter_package.zip`](https://github.com/dc-deal/FiniexTestingIDE/releases/download/V0.6/tick_starter_package.zip) from **v0.6 Pre-Alpha Stable Release**
   - Contains 2 weeks of tick data (60 MB compressed) for EURUSD, AUDUSD, GBPUSD, EURCHF

**2. Extract data**
   ```bash
   # Extract ZIP into the data/raw folder of your project
   unzip tick_starter_package.zip -d data/raw/
   ```

**3. Start Docker container**
   ```bash
   docker-compose up -d
   docker-compose exec finiex-dev bash -i
   ```

**4. Import tick data**
   - **In VS Code:** Start launch configuration **"📊 Data Pipeline: Import Ticks (PROD)"**
   - **Or via command line:**
     ```bash
     python python/data_management/tick_importer.py
     ```
   - JSON data will be automatically converted to Parquet with quality scores
   - **NEW:** Bars are automatically pre-rendered after tick import

**5. Run trading strategy**
   - **In VS Code:** Start launch configuration **"🔬 Strategy Runner - Batch - Entry"**
   - **Or via command line:**
     ```bash
     python python/strategy_runner.py
     ```
   - **NEW:** Watch live progress display during execution!

**That's it!** The strategy is now running with real market data.

### Next Steps
- Create your own scenarios in `configs/scenario_sets/`
- Use the **"📝 Scenario Generator"** to automatically generate scenarios from your data
- Adjust parameters in scenario configs (RSI, Envelope, etc.)
- Experiment with parallel execution settings

---

### Python Environment

**Docker Setup (Recommended):**
```bash
# Start Docker container
docker-compose up -d
docker-compose exec finiex-dev bash -i

# Run strategy test
python python/strategy_runner.py
```

**Development Tools:**
- Python 3.12
- VS Code with Remote Containers support
- Jupyter notebook (available at http://localhost:8888)
- Git configuration mounted from host

**Container Features:**
- Hot-reload code changes (volume mounted)
- Pre-installed dependencies (pandas, pyarrow, numpy, rich)
- Jupyter for data exploration
- htop for system monitoring

---

## 💡 Unique Selling Points

### Parameter-Centric Development
**Problem:** 80% of development time is spent on parameter tuning, but existing tools are code-centric.

**Solution:** Parameters are first-class citizens. The IDE orchestrates testing; strategies define their parameter requirements through worker contracts.

**How it works:**
- **Hierarchical Parameters:** Root-level ratios (M30 vs H1 trend weight) control nested worker configs (envelope periods, MA settings)
- **Worker Contracts:** Workers declare their parameter interface - IDE handles the rest
- **No Code Changes:** Test 1000 market scenarios by varying parameters, not rewriting logic
- **Synergy-Ready:** Parameters can influence each other (spread affects trade frequency, which affects envelope sensitivity)

**Example Trading Strategy:**
```
Root Parameters (UX Sliders):
  ├─ m30_trend_weight: 0.7
  ├─ h1_trend_weight: 0.3
  └─ spread_threshold: 2.0 pips
  
Worker Parameters:
  ├─ M1_Envelope: {period: 20, deviation: 0.02}
  ├─ M30_Trend: {ma_period: 50}
  └─ Volatility: {window: 14}
```

Trade logic runs on M1, but trend filters from M30/H1 influence decisions through root-level weight ratios. Change the strategy? Adjust parameters. Don't touch code.

**Unique Advantage:** Competitors require code modifications for strategy variations. Finiex enables systematic parameter space exploration through pure configuration.

**Post-MVP: Parameter Intelligence (C#008-A)** - Planned diagnostic system that validates algo fundamentals, identifies failure points (missed trades, false signals), and recommends which hardcoded values should become tunable parameters.

---

### Performance-First Architecture
**Problem:** Backtesting tools are either fast but unrealistic (MT5) or realistic but slow (institutional platforms).

**Solution:** Multi-layer performance optimization from the ground up:
- **Bar Pre-Rendering** - 600x faster warmup (100ms vs 60s) via vectorized parquet loading
- **CPU Scaling** - Linear performance gains with cores (4 cores = 4x faster)
- **Memory Efficiency** - Apache Arrow + lazy loading for minimal footprint
- **Smart Parallelization** - Only when beneficial (automated threshold detection)
- **Optimized Tick Processing** - Eliminated datetime bottlenecks, bar history caching

**Validation:** Core Issue C#004 establishes benchmarks proving Finiex beats MT5 in speed while maintaining 80-90% realism of institutional tools. Quality-Aware Data adds a unique edge competitors lack.

---

### Quality-Aware Data
**Problem:** Backtests with bad data → unrealistic results.

**Solution:** 3-level error classification distinguishes market anomalies (keep) vs system errors (filter).

### IP-Protected Strategies (Post-MVP)
**Problem:** Strategy code must remain secret, but parameters must be optimizable.

**Solution:** Blackbox framework with parameter-contract system.

## 🏗️ Extended Features (Post-MVP)

### Blackbox Worker System (Post-MVP)
**Secure deployment of proprietary strategies**

- **Encrypted Workers** - AES-256 encrypted `.fwx` files
- **License Verification** - Hardware-bound activation
- **Zero Source Exposure** - Compiled bytecode execution
- **Status:** Folder structure prepared, feature-gated for post-MVP
- **Plan:** Encrypted/compiled workers + decision logics
- **Usage:** `"worker_types": ["BLACKBOX/my_secret_strategy"]`

### Worker Type Classification (V0.8)
**Three worker types for different use-cases:**

1. **COMPUTE Workers** (✅ MVP)
   - Synchronous calculations (RSI, SMA, Envelope)
   - Short runtime < 10ms typical
   - Performance metrics: computation_time_ms

2. **API Workers** (⚠️ Post-MVP)
   - HTTP requests with caching
   - Variable runtime 100ms - 5s
   - Metrics: latency, timeout_rate, cache_hit_rate

3. **EVENT Workers** (⚠️ Post-MVP)
   - Live connections (WebSocket, AI alerts)
   - Passive event listeners
   - Metrics: connection_status, uptime, events_received

**Current MVP:** Only COMPUTE workers implemented  
**Post-MVP:** API + EVENT for live trading integration

---

## 📈 Post-MVP Vision

### Phase 4: UX Layer (6-8 weeks)
- Web frontend with multi-tab interface
- Real-time progress updates via WebSocket
- Interactive charts with timeline scrubber
- Visual parameter panels

### Phase 5: Intelligence Layer (8-12 weeks)
- Parameter synergy detection
- AI-enhanced parameter suggestions
- Market regime analysis
- Predictive performance analysis

### Phase 6: Enterprise Platform (12+ weeks)
- Cloud-native SaaS platform
- Multi-tenancy + token-based billing
- Advanced risk management
- Live trading integration (FiniexAutoTrader)

### Phase 7: Blackbox Integration
- Encrypted/compiled worker loading
- Blackbox decision logic deployment
- IP-protected strategy marketplace
- Secure parameter-only interface

---

## 🧪 Testing & Quality

**Current Status:** Manual testing only

**MVP Foundation (Core Issues C#006 & C#007):**
- **C#006**: Code guidelines (English-only, PEP 8, type hints), CI pipeline (linting, type checking), VSCode integration (autopep8, flake8, mypy)
- **C#007**: Automated test system (pytest), unit tests (workers, orchestrator, data import), integration tests (E2E slices), CI test job with coverage reporting

**Post-MVP:**
- Expand test coverage to new features
- Performance benchmarks
- Load testing for batch scenarios
- Mutation testing for critical paths

---

## 📜 License & Trademarks

**License:** MIT License - see [LICENSE](LICENSE)

**Trademarks:** Finiex™ and all related marks are exclusive property of Frank Krätzig - see [TRADEMARK.md](TRADEMARK.md)

---

## 👤 Contact & Contributions

**Maintainer:** Frank Krätzig ([dc-deal](https://github.com/dc-deal))

**Status:** Active MVP development - C#003 completed ✅, C#004 in progress 🚧

**Contributing:**
- ✅ Custom workers: Add to `python/workers/user/`
- ✅ Custom decision logics: Add to `python/decision_logic/user/`
- ✅ Bug reports: [GitHub Issues](https://github.com/dc-deal/FiniexTestingIDE/issues)
- ⚠️ Blackbox: Post-MVP feature, structure prepared but not active

---

## 💙 Acknowledgments

Thank you to everyone supporting this project!

*Go build something amazing!* ⚡

---

*Building the foundation for parameter-centric trading strategy development - one issue at a time.*

**Latest:** Pre-Alpha V0.9.0 - Live Display + Performance Optimizations ✅