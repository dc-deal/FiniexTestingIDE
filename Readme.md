# FiniexTestingIDE

**© 2025 Frank Krätzig. All rights reserved.**

---

## Trading-Strategy-Testing IDE - MVP Development

**Vision:** Parameter-centric testing platform for trading strategies with focus on reproducible results and IP protection.

**Current Phase:** Core Framework Implementation (MVP)

---

## 🎯 MVP Status - What's Already Working (Pre-Alpha V0.7.1)

### ✅ Data Pipeline (Production-Ready)
- **MQL5 TickCollector v1.03** - Live tick collection with error classification
- **JSON → Parquet Conversion** - Quality-aware processing with metadata
- **Multi-Symbol Support** - EURUSD, AUDUSD, GBPUSD, EURCHF
- **Quality Metrics** - 3-level error classification (Negligible/Serious/Fatal)
- **Data Modes** - Clean/Realistic/Raw for different test scenarios

**Sample Output:** [AUDUSD Ticks](./data/samples/AUDUSD_20250916_223859_ticks.json)

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

### ✅ Enhanced Performance Logging (NEW in V0.7.1) 📊
- **Comprehensive Metrics** - Per-worker, per-scenario, and aggregated performance stats
- **Parallel Efficiency Tracking** - Real-time measurement of parallelization benefits
- **Bottleneck Analysis** - Automatic detection of slowest components
- **Decision Logic Metrics** - Separate tracking for strategy decision time
- **Batch Mode Clarity** - Clear indication of batch vs. scenario parallelization

### ✅ Order Execution System (NEW in V0.7.1 - Issue #003 COMPLETED) 🎯
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

## 🚧 MVP Roadmap - What's Coming

### 📋 Core Issue C#001: Logging & TUI (Low Priority)
**Goal:** Structured logging and live TUI dashboard

- [ ] Logging module (Print → Logger migration)
- [ ] TUI dashboard with `rich` (Scenarios + Performance + Logs)
- [ ] Error pinning (persistent warnings/errors display)
- [ ] Log file output with rotation
- [ ] CLI scripting foundation (headless mode, programmatic access)

**Effort:** 1-2 days  
**Priority:** Low (Nice-to-have, polish)  
**Related:** Issue #27 (Performance Logging parameter hierarchy)

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

### 📋 Core Issue C#004: Performance Validation & Benchmarking (HIGH Priority) 🚀
**Goal:** Validate performance-first architecture and establish production readiness

**POC for Scalability (3-4 days, parallel to C#003):**

**A) Benchmarking Suite**
- [ ] Batch testing: 10 scenarios @ 1,000 ticks in <60s (8 cores)
- [ ] Stress testing: 100k ticks @ 500 Hz simulation
- [ ] Memory profiling: <8GB for 100k ticks, no leaks
- [ ] Linear scaling validation: 16 cores → 2x faster

**B) Performance Profiling**
- [ ] Integrate `cProfile` (optional activation)
- [ ] Hotspot detection (functions using >10% time)
- [ ] Memory profiler integration
- [ ] Auto-generate bottleneck reports

**C) Regression Testing**
- [ ] Baseline performance metrics (JSON)
- [ ] Automatic comparison on every run
- [ ] Alert if performance degrades >20%

**D) Documentation**
- [ ] Performance guide (cores, memory, best practices)
- [ ] Benchmark results in README (vs MT5 comparison)
- [ ] Scalability charts

**Why Critical:**
- **Proof-of-Concept** for production readiness
- **User confidence**: "Is this tool fast enough?"
- **Marketing**: Finiex MVP beats MT5, reaches 70-80% of institutional tools
- **Quality-Aware Data** is unique competitive advantage

**Effort:** 3-4 days (can run parallel to C#003 Phase 1)  
**Priority:** **HIGH** - Critical for MVP validation

---

### 📋 Core Issue C#005: IP-Protected Blackbox System (OPTIONAL MVP) 🔒
**Goal:** Enable deployment of compiled workers for IP protection

**MVP Critical (2-3 days):**

**A) Blackbox Loader**
- [ ] `BlackboxLoader` class for .pyc loading
- [ ] Update feature gate in `WorkerFactory` and `DecisionLogicFactory`
- [ ] Replace `NotImplementedError` with actual loading

**B) Deployment Tooling**
- [ ] `scripts/deploy_blackbox.py` - Compile .py → .pyc
- [ ] Documentation with usage examples

**C) Testing**
- [ ] Test blackbox worker loading
- [ ] Verify parameters still configurable
- [ ] Example blackbox worker in docs

**Status:** OPTIONAL - Will attempt if time permits after C#003 & C#004  
**Fallback:** Can defer to Post-MVP without breaking anything  
**Effort:** 2-3 days  
**Priority:** Optional (Important for vision, not blocking MVP)

---

### 📋 Core Issue C#006: Code Guidelines & CI Pipeline 📏
**Goal:** Establish code quality standards and automated enforcement

**Foundation for Post-MVP (2-3 days + 1-2 days refactor):**

**A) Code Guidelines Documentation**
- [ ] Create `CODE_GUIDELINES.md` (English-only, PEP 8, type hints)
- [ ] Define naming conventions, import order, comment style
- [ ] VSCode settings for local development (autopep8, flake8, mypy)

**B) GitHub Actions CI Pipeline**
- [ ] Linting checks (Black, Flake8, pylint)
- [ ] Type checking (mypy)
- [ ] Language enforcement (detect non-English comments)
- [ ] Build verification (pip install, Docker build)

**C) Mandatory Refactor**
- [ ] Translate German comments to English
- [ ] Add missing type hints
- [ ] Apply consistent formatting
- [ ] Fix guideline violations

**Status:** Foundation for Post-MVP - Prevents technical debt  
**Timing:** After C#005, BEFORE Post-MVP Phase 4  
**Effort:** 2-3 days setup + 1-2 days refactor  
**Priority:** Foundation (Critical for clean Post-MVP development)

---

### 📋 Core Issue C#007: Automated Test System 🧪
**Goal:** Regression protection and confident refactoring

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

**Core Path (Critical):** ~9-12 days

1. ✅ **C#003** (4-5 days) → **COMPLETED** - Trade simulation ✅
2. **C#004** (3-4 days) → **NEXT** - Performance validation 🚀 POC
3. **C#001** (1-2 days) - Logging & TUI (Low priority polish)

**Optional Path (If time permits):** +2-3 days

4. **C#005** (2-3 days) - Blackbox system 🔒 OPTIONAL

**Foundation Path (Pre-Post-MVP):** +5-8 days

5. **C#006** (2-3 days + 1-2 days refactor) - Code Guidelines & CI 📏 FOUNDATION
6. **C#007** (2-3 days) - Automated Test System 🧪 FOUNDATION

**Total Estimated:** 9-25 days (2-5 weeks) depending on optional features

**Critical for MVP Release:** C#003 ✅ + C#004 (Trade Simulation + Performance Validation)

**Decision Point:** Assess schedule after C#004 completion

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
     python python/data_worker/tick_importer.py
     ```
   - JSON data will be automatically converted to Parquet with quality scores

**5. Run trading strategy**
   - **In VS Code:** Start launch configuration **"🔬 Strategy Runner - Batch - Entry"**
   - **Or via command line:**
     ```bash
     python python/strategy_runner_enhanced.py
     ```

**That's it!** The strategy is now running with real market data.

### Next Steps
- Create your own scenarios in `configs/scenario_sets/`
- Use the **"📝 Scenario Generator"** to automatically generate scenarios from your data
- Adjust parameters in scenario configs (RSI, Envelope, etc.)
- Create your own workers/decision logics under `USER/` namespace

---

### Python Environment
```bash
# Start Docker container
docker-compose up -d
docker-compose exec finiex-dev bash -i

# Run test
python python/strategy_runner_enhanced.py
```

### Current Test Output (V0.7.1)
```
============================================================
                    🎉 EXECUTION RESULTS                     
============================================================
✅ Success: True  |  📊 Scenarios: 1  |  ⏱️  Time: 10.93s
⚙️  Batch Mode: Sequential
------------------------------------------------------------
SCENARIO DETAILS
------------------------------------------------------------
┌────────────────────────────────────┐
│ 📋 EURUSD_window_02                 │
│ Symbol: EURUSD                     │
│ Ticks: 4,000                       │
│ Signals: 10 (0.2%)                 │
│ Buy/Sell: 5/5                      │
│ Worker/Calls: 2/0                  │
│ Decisions: 4000                    │
└────────────────────────────────────┘

------------------------------------------------------------
💰 PORTFOLIO & TRADING RESULTS
------------------------------------------------------------

┌────────────────────────────────────┐
│ 💰 EURUSD_window_02                 │
│ Trades: 5 (5W/0L)                  │
│ Win Rate: 100.0%                   │
│ P&L: +$21.37                       │
│ Spread: $12.43                     │
│ Orders: 10                         │
└────────────────────────────────────┘


------------------------------------------------------------
📊 AGGREGATED PORTFOLIO (ALL SCENARIOS)
------------------------------------------------------------

   📈 TRADING SUMMARY:
      Total Trades: 5  |  Win/Loss: 5W/0L  |  Win Rate: 100.0%
      Total P&L: $21.37  |  Profit: $21.37  |  Loss: $0.00
      Profit Factor: 0.00

   📋 ORDER EXECUTION:
      Orders Sent: 10  |  Executed: 10  |  Rejected: 0
      Execution Rate: 100.0%

   💸 COST BREAKDOWN:
      Spread Cost: $12.43  |  Commission: $0.00  |  Swap: $0.00
      Total Costs: $12.43

------------------------------------------------------------
📊 PERFORMANCE DETAILS (PER SCENARIO)
------------------------------------------------------------
------------------------------------------------------------
📊 SCENARIO PERFORMANCE: EURUSD_window_02
   Workers: 2 workers (Parallel)  |  Ticks: 4,000  |  Calls: 8,000  |  Decisions: 4000

   📊 WORKER DETAILS:
      RSI              Calls:  4000  |  Avg:  0.138ms  |  Range:  0.066- 0.400ms  |  Total:   552.73ms
      Envelope         Calls:  4000  |  Avg:  0.053ms  |  Range:  0.004- 0.710ms  |  Total:   213.48ms

   ⚡ PARALLEL EFFICIENCY:
      Time saved:     0.00ms total  |  Avg/tick:  0.000ms  |  Status: ≈ Equal

   🧠 DECISION LOGIC: simple_consensus (CORE/simple_consensus)
      Decisions: 4000  |  Avg:  0.011ms  |  Range:  0.006- 0.138ms  |  Total:    45.67ms


------------------------------------------------------------
📊 AGGREGATED SUMMARY (ALL SCENARIOS)
------------------------------------------------------------

   📈 OVERALL:
      Total Ticks: 4,000  |  Total Signals: 10  |  Total Decisions: 4,000

   👷 WORKERS (AGGREGATED):
      RSI              Total Calls:   4000  |  Total Time:   552.73ms  |  Avg:  0.138ms  |  Scenario Avg:  0.138ms
      Envelope         Total Calls:   4000  |  Total Time:   213.48ms  |  Avg:  0.053ms  |  Scenario Avg:  0.053ms

   🧠 DECISION LOGIC (AGGREGATED):
      Total Decisions: 4000  |  Total Time:    45.67ms  |  Avg:  0.011ms  |  Scenario Avg:  0.011ms


------------------------------------------------------------
⚠️  BOTTLENECK ANALYSIS (Worst Performers)
------------------------------------------------------------

   🐌 SLOWEST SCENARIO:
      EURUSD_window_02  |  Avg/tick: 0.203ms  |  Total: 811.88ms
      → This scenario took the longest time per tick

   🐌 SLOWEST WORKER:
      RSI  |  Avg: 0.138ms (across all scenarios)
      → Worst in scenario 'EURUSD_window_02': 0.138ms

   💡 RECOMMENDATIONS:
      ✅ All components performing well! No major bottlenecks detected.


------------------------------------------------------------------------------------------------------------------------
 11s 344ms - StrategyRunner            - INFO    - ✅ All tests passed!
```

---

## 🗃️ Architecture Overview

### Current System (V0.7.1)
```
MQL5 TickCollector → JSON → Parquet (Quality-Aware)
                                ↓
                    Data Loader (Multi-Mode: Clean/Realistic/Raw)
                                ↓
                    Scenario Config (decision_logic_type + worker_types)
                                ↓
                    ┌─────────────────────────────────┐
                    │  Factory Layer (NEW in V0.7)    │
                    ├─────────────────────────────────┤
                    │  Worker Factory                 │
                    │  DecisionLogic Factory          │
                    └─────────────────────────────────┘
                                ↓
                    Batch Orchestrator (Multi-Scenario)
                                ↓
                    Worker Coordinator (Parallel Workers)
                                ↓
                    Decision Logic (Injected Strategy)
                                ↓
                    Decision Output
```

### Post-MVP (Issue #003+)
```
Decision Logic → Trade Simulator (Portfolio/Risk/Orders)
                        ↓
                Event Bus → Results/Metrics
```

---

## 📁 Project Structure (Pre-Alpha V0.7.1)

```
FiniexTestingIDE/
├── mql5/
│   └── TickCollector.mq5          # Live tick collection
├── python/
│   ├── data_worker/               # Data pipeline
│   │   ├── tick_importer.py       # JSON → Parquet conversion
│   │   └── data_loader/           # Parquet loading
│   ├── framework/
│   │   ├── factory/               # NEW: Worker + DecisionLogic Factories
│   │   │   ├── worker_factory.py
│   │   │   └── decision_logic_factory.py
│   │   ├── batch_orchestrator.py  # Multi-scenario testing
│   │   ├── workers/               # CORE Workers
│   │   │   ├── core/              # RSI, SMA, Envelope
│   │   │   └── worker_coordinator.py
│   │   ├── bars/                  # Bar rendering + warmup
│   │   └── tick_data_preparator.py
│   ├── workers/                   # NEW: Namespace Structure
│   │   ├── core/                  # CORE Workers (builtin)
│   │   ├── user/                  # USER Workers (custom)
│   │   └── blackbox/              # BLACKBOX Workers (Post-MVP, git-ignored)
│   ├── decision_logic/            # NEW: Decision Logic Layer
│   │   ├── core/                  # CORE Logics
│   │   ├── user/                  # USER Logics (custom)
│   │   └── blackbox/              # BLACKBOX Logics (Post-MVP, git-ignored)
│   ├── scenario/
│   │   ├── config_loader.py       # Scenario loading
│   │   └── generator.py           # Scenario generation
│   └── strategy_runner_enhanced.py # Main entry point
├── configs/
│   └── scenario_sets/             # JSON scenario configs
├── data/
│   ├── raw/                       # MQL5 JSON output
│   └── processed/                 # Processed tick data (Parquet)
└── docs/                          # Architecture + Post-MVP plans
```

---

## 🔧 Configuration Example (V0.7.1)

### New Factory-Compatible Config Structure

```json
{
  "name": "EURUSD_window_01",
  "symbol": "EURUSD",
  "start_date": "2024-09-16 00:00:00",
  "end_date": "2024-09-18 23:59:59",
  "data_mode": "realistic",
  "max_ticks": 1000,
  
  "strategy_config": {
    "decision_logic_type": "CORE/simple_consensus",
    "worker_types": ["CORE/rsi", "CORE/envelope"],
    "workers": {
      "CORE/rsi": {
        "period": 14,
        "timeframe": "M5"
      },
      "CORE/envelope": {
        "period": 20,
        "deviation": 0.02,
        "timeframe": "M5"
      }
    },
    "decision_logic_config": {
      "rsi_oversold": 30,
      "rsi_overbought": 70,
      "min_confidence": 0.6
    }
  },
  
  "execution_config": {
    "parallel_workers": true,
    "worker_parallel_threshold_ms": 1.0
  }
}
```

### Key Changes in V0.7:
- ✅ `decision_logic_type` - Explicit strategy selection
- ✅ `worker_types` - Array of workers to use (CORE/USER/BLACKBOX)
- ✅ `workers` - Nested config per worker with namespace prefix
- ✅ `decision_logic_config` - Strategy-specific parameters
- ✅ Each scenario calculates own requirements (no global contract)

---

## 🎯 Core Concepts

### First-Level Parallelism Paradigm

**Vision:** Workers are the atomic, parallel computation units of the system. All work happens on **one level** - there are no nested sub-workers or hidden dependencies.

#### Two Fixed Layers:

```
┌───────────────────────────────────────────────┐
│  WORKER LAYER (Parallel Execution)              │
│  ├── RSI Worker (Compute)                       │
│  ├── Envelope Worker (Compute)                  │
│  ├── News API Worker (API, Long-Running)        │
│  └── AI Panic Detector (Event, Always-On)       │
└───────────────────────────────────────────────┘
                    ↓
┌───────────────────────────────────────────────┐
│  DECISION LAYER (Orchestration)                 │
│  └── DecisionLogic (aggregates all results)     │
└───────────────────────────────────────────────┘
```

**Key Principles:**
- ✅ **Workers are atomic** - No sub-workers, no hidden dependencies
- ✅ **One level of parallelism** - All workers on same hierarchy
- ✅ **DecisionLogic orchestrates** - Aggregation happens one level higher

**Result:** Maximum parallelism, complete transparency, easy debugging through clear responsibilities.

---

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

**Post-MVP: Parameter Intelligence (C#008-A)** - Planned diagnostic system that validates algo fundamentals, identifies failure points (missed trades, false signals), and recommends which hardcoded values should become tunable parameters. See [Issue C#008-A](link-to-issue) for detailed diagnostics framework.

---

### Performance-First Architecture
**Problem:** Backtesting tools are either fast but unrealistic (MT5) or realistic but slow (institutional platforms).

**Solution:** Multi-layer performance optimization from the ground up:
- **CPU Scaling:** Linear performance gains with cores (4 cores = 4x faster)
- **Memory Efficiency:** Apache Arrow + lazy loading for minimal footprint
- **Smart Parallelization:** Only when beneficial (automated threshold detection)

**Validation:** Core Issue C#004 establishes benchmarks proving Finiex beats MT5 in speed while maintaining 80-90% realism of institutional tools. Quality-Aware Data adds a unique edge competitors lack.

---

### Quality-Aware Data
**Problem:** Backtests with bad data → unrealistic results.

**Solution:** 3-level error classification distinguishes market anomalies (keep) vs system errors (filter).

### IP-Protected Strategies (Post-MVP)
**Problem:** Strategy code must remain secret, but parameters must be optimizable.

**Solution:** Blackbox framework with parameter-contract system.
- **Status:** Folder structure prepared, feature-gated for post-MVP
- **Plan:** Encrypted/compiled workers + decision logics
- **Usage:** `"worker_types": ["BLACKBOX/my_secret_strategy"]`

### Worker Type Classification (V0.7.1)
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

**Status:** Active MVP development - Core Issue C#003 completed ✅, C#004 next

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

**Latest:** Pre-Alpha V0.7.1 - Enhanced Performance Logging ✅