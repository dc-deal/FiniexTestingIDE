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

### 📋 Core Issue C#003: Trade Simulation (NEXT - HIGH Priority) ⚠️
**Goal:** Realistic trade execution with portfolio management

**Phase 1: Core Trade Simulator (4-5 days)**

**A) BrokerConfig Importer (MQL5)**
- [ ] New MQL5 tool: `TraderDefaultsImporter`
- [ ] Import: Order types, commission, lot sizes, margin requirements
- [ ] Export as JSON

**B) TradeSimulator - Core Components**
- [ ] **PortfolioManager**: Balance/equity tracking, open positions
- [ ] **OrderManager**: Active trades, trade history
- [ ] **RiskManager**: Max positions (default: 1), max drawdown (default: 30%)
- [ ] **ExecutionEngine**: 
  - Fixed latency (100ms)
  - Fixed slippage (0.5 pips)
  - Market + limit orders only
  - Order fully filled or rejected (no partial fills)
- [ ] **EventBus**: Events to DecisionLogic (TradeExecuted, OrderRejected, MarginWarning)

**C) DecisionLogic Integration**
- [ ] Query: `get_account_info()`, `get_open_positions()`, `get_trade_history()`
- [ ] Send orders: `TradeSimulator.send_order()`
- [ ] Receive events via EventBus

**D) Realism Features**
- [ ] Spread dynamics from demo data
- [ ] Commission consideration
- [ ] Basic margin checks

**Simplifications (Postponed to Post-MVP):**
- ❌ ECN market simulation → Standard broker sufficient
- ❌ Partial fills → Order fully filled or rejected
- ❌ Connection-lost events → Unrealistic for backtest
- ❌ Swap/rollover → Config option (default: 0)
- ❌ Adaptive tick processing → Process every tick
- ❌ Liquidity simulation → Assume infinite liquidity

**Effort:** 4-5 days (instead of 7-10 through simplifications)  
**Priority:** **HIGH** - Critical for realistic tests

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

1. **C#003** (4-5 days) → **NEXT** - Trade simulation ⚠️ HIGH PRIORITY
2. **C#004** (3-4 days) → **Parallel to C#003** - Performance validation 🚀 POC
3. **C#001** (1-2 days) - Logging & TUI (Low priority polish)

**Optional Path (If time permits):** +2-3 days

4. **C#005** (2-3 days) - Blackbox system 🔒 OPTIONAL

**Foundation Path (Pre-Post-MVP):** +5-8 days

5. **C#006** (2-3 days + 1-2 days refactor) - Code Guidelines & CI 📏 FOUNDATION
6. **C#007** (2-3 days) - Automated Test System 🧪 FOUNDATION

**Total Estimated:** 9-25 days (2-5 weeks) depending on optional features

**Critical for MVP Release:** C#003 + C#004 (Trade Simulation + Performance Validation)

**Decision Point:** Assess schedule after C#003 & C#004 completion

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
- Use the **"🔍 Scenario Generator"** to automatically generate scenarios from your data
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
✅ Success: True  |  📊 Scenarios: 6  |  ⏱️  Time: 5.87s
⚙️  Batch Mode: Parallel (4 scenarios concurrent)
------------------------------------------------------------------------------------------------------------------------
📊 PERFORMANCE DETAILS (PER SCENARIO)
------------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------------
📊 SCENARIO PERFORMANCE: EURUSD_window_01
   Workers: 2 workers (Parallel)  |  Ticks: 1,000  |  Calls: 2,000  |  Decisions: 1000

   📊 WORKER DETAILS:
      RSI              Calls:  1000  |  Avg:  0.025ms  |  Range:  0.003- 0.169ms  |  Total:    25.02ms
      Envelope         Calls:  1000  |  Avg:  0.003ms  |  Range:  0.002- 0.007ms  |  Total:     2.98ms

   ⚡ PARALLEL EFFICIENCY:
      Time saved:     0.00ms total  |  Avg/tick:  0.000ms  |  Status: ≈ Equal

   🧠 DECISION LOGIC: simple_consensus (CORE/simple_consensus)
      Decisions: 1000  |  Avg:  0.006ms  |  Range:  0.004- 0.131ms  |  Total:     6.04ms


························································································································

------------------------------------------------------------------------------------------------------------------------
📊 SCENARIO PERFORMANCE: EURUSD_window_02
   Workers: 2 workers (Sequential)  |  Ticks: 1,000  |  Calls: 2,000  |  Decisions: 1000

   📊 WORKER DETAILS:
      RSI              Calls:  1000  |  Avg:  0.043ms  |  Range:  0.032- 0.181ms  |  Total:    42.60ms
      Envelope         Calls:  1000  |  Avg:  0.026ms  |  Range:  0.021- 0.095ms  |  Total:    26.40ms

   🧠 DECISION LOGIC: simple_consensus (CORE/simple_consensus)
      Decisions: 1000  |  Avg:  0.006ms  |  Range:  0.003- 0.035ms  |  Total:     6.33ms


························································································································

------------------------------------------------------------------------------------------------------------------------
📊 SCENARIO PERFORMANCE: EURUSD_window_03
   Workers: 2 workers (Parallel)  |  Ticks: 1,000  |  Calls: 2,000  |  Decisions: 1000

   📊 WORKER DETAILS:
      RSI              Calls:  1000  |  Avg:  0.057ms  |  Range:  0.038- 0.221ms  |  Total:    57.43ms
      Envelope         Calls:  1000  |  Avg:  0.035ms  |  Range:  0.002- 0.133ms  |  Total:    35.21ms

   ⚡ PARALLEL EFFICIENCY:
      Time saved:     0.00ms total  |  Avg/tick:  0.000ms  |  Status: ≈ Equal

   🧠 DECISION LOGIC: simple_consensus (CORE/simple_consensus)
      Decisions: 1000  |  Avg:  0.009ms  |  Range:  0.004- 0.056ms  |  Total:     9.42ms


························································································································

------------------------------------------------------------------------------------------------------------------------
📊 SCENARIO PERFORMANCE: AUDUSD_window_01
   Workers: 2 workers (Parallel)  |  Ticks: 1,000  |  Calls: 2,000  |  Decisions: 1000

   📊 WORKER DETAILS:
      RSI              Calls:  1000  |  Avg:  0.004ms  |  Range:  0.002- 0.039ms  |  Total:     4.27ms
      Envelope         Calls:  1000  |  Avg:  0.003ms  |  Range:  0.002- 0.042ms  |  Total:     3.16ms

   ⚡ PARALLEL EFFICIENCY:
      Time saved:     0.00ms total  |  Avg/tick:  0.000ms  |  Status: ≈ Equal

   🧠 DECISION LOGIC: simple_consensus (CORE/simple_consensus)
      Decisions: 1000  |  Avg:  0.006ms  |  Range:  0.004- 0.027ms  |  Total:     6.06ms


························································································································

------------------------------------------------------------------------------------------------------------------------
📊 SCENARIO PERFORMANCE: AUDUSD_window_02
   Workers: 2 workers (Parallel)  |  Ticks: 1,000  |  Calls: 2,000  |  Decisions: 1000

   📊 WORKER DETAILS:
      RSI              Calls:  1000  |  Avg:  0.049ms  |  Range:  0.036- 0.144ms  |  Total:    49.14ms
      Envelope         Calls:  1000  |  Avg:  0.041ms  |  Range:  0.028- 0.188ms  |  Total:    40.71ms

   ⚡ PARALLEL EFFICIENCY:
      Time saved:     0.00ms total  |  Avg/tick:  0.000ms  |  Status: ≈ Equal

   🧠 DECISION LOGIC: simple_consensus (CORE/simple_consensus)
      Decisions: 1000  |  Avg:  0.005ms  |  Range:  0.004- 0.019ms  |  Total:     5.47ms


························································································································

------------------------------------------------------------------------------------------------------------------------
📊 SCENARIO PERFORMANCE: AUDUSD_window_03
   Workers: 2 workers (Parallel)  |  Ticks: 1,000  |  Calls: 2,000  |  Decisions: 1000

   📊 WORKER DETAILS:
      RSI              Calls:  1000  |  Avg:  0.049ms  |  Range:  0.037- 0.173ms  |  Total:    49.34ms
      Envelope         Calls:  1000  |  Avg:  0.041ms  |  Range:  0.027- 0.282ms  |  Total:    40.75ms

   ⚡ PARALLEL EFFICIENCY:
      Time saved:     0.00ms total  |  Avg/tick:  0.000ms  |  Status: ≈ Equal

   🧠 DECISION LOGIC: simple_consensus (CORE/simple_consensus)
      Decisions: 1000  |  Avg:  0.006ms  |  Range:  0.004- 0.039ms  |  Total:     6.48ms


------------------------------------------------------------------------------------------------------------------------
📊 AGGREGATED SUMMARY (ALL SCENARIOS)
------------------------------------------------------------------------------------------------------------------------

   📈 OVERALL:
      Total Ticks: 6,000  |  Total Signals: 779  |  Total Decisions: 6,000

   👷 WORKERS (AGGREGATED):
      RSI              Total Calls:   6000  |  Total Time:   227.80ms  |  Avg:  0.038ms  |  Scenario Avg:  0.038ms
      Envelope         Total Calls:   6000  |  Total Time:   149.21ms  |  Avg:  0.025ms  |  Scenario Avg:  0.025ms

   🧠 DECISION LOGIC (AGGREGATED):
      Total Decisions: 6000  |  Total Time:    39.80ms  |  Avg:  0.007ms  |  Scenario Avg:  0.006ms


------------------------------------------------------------------------------------------------------------------------
⚠️  BOTTLENECK ANALYSIS (Worst Performers)
------------------------------------------------------------------------------------------------------------------------

   🐌 SLOWEST SCENARIO:
      EURUSD_window_03  |  Avg/tick: 0.102ms  |  Total: 102.06ms
      → This scenario took the longest time per tick

   🐌 SLOWEST WORKER:
      RSI  |  Avg: 0.038ms (across all scenarios)
      → Worst in scenario 'EURUSD_window_03': 0.057ms

   💡 RECOMMENDATIONS:
      ✅ All components performing well! No major bottlenecks detected.

========================================================================================================================
  6s 222ms - StrategyRunner            - INFO    - ✅ All tests passed!
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
**Problem:** 80% of time is spent on parameter tuning, but tools are code-centric.

**Solution:** Parameters are first-class citizens. Strategies define parameter requirements, IDE orchestrates testing.

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

**Status:** Active MVP development - Core Issue C#002 completed, C#003 next

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