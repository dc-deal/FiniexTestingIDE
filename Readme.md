# FiniexTestingIDE

**© 2025 Frank Krätzig. All rights reserved.**

---

## Trading-Strategy-Testing IDE - MVP Development

**Vision:** Parameter-centric testing platform for trading strategies with focus on reproducible results and IP protection.

**Current Phase:** Core Framework Implementation (MVP)

---

## 🎯 MVP Status - What's Already Working (Pre-Alpha V0.7)

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

### ⚠️ Blackbox Support (Prepared, Post-MVP)
- **Folder Structure** - `python/workers/blackbox/` and `python/decision_logic/blackbox/`
- **Git-Ignored** - All `.py` files automatically excluded (IP protection)
- **Feature-Gated** - Implementation planned for post-MVP (encrypted/compiled workers)

---

## 🚧 MVP Roadmap - What's Coming

### 📋 Issue 1: Logging & TUI (Low Priority)
**Goal:** Static TUI dashboard with live metrics

- [ ] Logging module (Print → Logger migration)
- [ ] TUI dashboard with `rich` (Scenarios + Performance + Logs)
- [ ] Error pinning (persistent warnings/errors display)
- [ ] Log file output

**Effort:** 1-2 days  
**Priority:** Low (Nice-to-have, polish)

---

### 📋 Issue 3: Trade Simulation (NEXT - HIGH Priority) ⚠️
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

## 📊 MVP Timeline

**Total:** ~10-12 days (2-3 weeks)

1. **Issue 3** (4-5 days) → **NEXT** - Trade simulation
2. **Issue 1** (1-2 days) → Optional, polish

**Milestone:** Working end-to-end system with realistic trade simulation

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

### Current Test Output (V0.7)
```
============================================================
                    🎉 EXECUTION RESULTS                     
============================================================
✅ Success: True  |  📊 Scenarios: 3  |  ⏱️  Time: 3.95s
⚙️  Parallel: False  |  ⚙️  Workers: 0
------------------------------------------------------------
SCENARIO DETAILS
------------------------------------------------------------
┌────────────────────────────────────  ┌────────────────────────────────────  ┌────────────────────────────────────
│ 📋 EURUSD_window_01                   │  │ 📋 EURUSD_window_02                   │  │ 📋 EURUSD_window_03                   │
│ Symbol: EURUSD                       │  │ Symbol: EURUSD                       │  │ Symbol: EURUSD                       │
│ Ticks: 1,000                         │  │ Ticks: 1,000                         │  │ Ticks: 1,000                         │
│ Signals: 0 (0.0%)                    │  │ Signals: 305 (30.5%)                 │  │ Signals: 342 (34.2%)                 │
│ Calls: 2,000                         │  │ Calls: 2,000                         │  │ Calls: 2,000                         │
│ Decisions: 0                         │  │ Decisions: 305                       │  │ Decisions: 342                       │
└────────────────────────────────────  └────────────────────────────────────  └────────────────────────────────────

------------------------------------------------------------------------------------------------------------------------
📊 WORKER STATS   |  Ticks: 1,000  |  Calls: 2,000  |  Decisions: 342
  ⚡ PARALLEL  |  Saved: 0.00ms  |  Avg/tick: 0.000ms  |  Status: ≈ Equal
========================================================================================================================
  4s 191ms - StrategyRunner            - INFO    - ✅ All tests passed!
```

---

## 🗏️ Architecture Overview

### Current System (V0.7)
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

### Post-MVP (Issue 3+)
```
Decision Logic → Trade Simulator (Portfolio/Risk/Orders)
                        ↓
                Event Bus → Results/Metrics
```

---

## 📁 Project Structure (Pre-Alpha V0.7)

```
FiniexTestingIDE/
├── mql5/
│   └── TickCollector.mq5          # Live tick collection
├── python/
│   ├── data_worker/               # Data pipeline
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

## 🔧 Configuration Example (V0.7)

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
┌─────────────────────────────────────────────────┐
│  WORKER LAYER (Parallel Execution)              │
│  ├── RSI Worker (Compute)                       │
│  ├── Envelope Worker (Compute)                  │
│  ├── News API Worker (API, Long-Running)        │
│  └── AI Panic Detector (Event, Always-On)       │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│  DECISION LAYER (Orchestration)                 │
│  └── DecisionLogic (aggregates all results)     │
└─────────────────────────────────────────────────┘
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

### Worker Type Classification (V0.7)
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

**Post-MVP:**
- Unit tests for core components (pytest)
- Integration tests for end-to-end flows
- GitHub Actions CI/CD pipeline
- Performance benchmarks

---

## 📜 License & Trademarks

**License:** MIT License - see [LICENSE](LICENSE)

**Trademarks:** Finiex™ and all related marks are exclusive property of Frank Krätzig - see [TRADEMARK.md](TRADEMARK.md)

---

## 👤 Contact & Contributions

**Maintainer:** Frank Krätzig ([dc-deal](https://github.com/dc-deal))

**Status:** Active MVP development - Issue 2 completed, Issue 3 next

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

**Latest:** Pre-Alpha V0.7 - Factory Architecture Complete ✅