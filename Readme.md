# FiniexTestingIDE

**© 2025 Frank Krätzig. Alle Rechte vorbehalten.**

---

## Trading-Strategy-Testing IDE - MVP Development

**Vision:** Parameter-zentrierte Testing-Platform für Trading-Strategien mit Fokus auf reproduzierbare Ergebnisse und IP-Schutz.

**Current Phase:** Core Framework Implementation (MVP)

---

## 🎯 MVP Status - Was funktioniert bereits  (Pre-Alpha V0.6)

### ✅ Data Pipeline (Production-Ready)
- **MQL5 TickCollector v1.03** - Live-Tick-Sammlung mit Error-Classification
- **JSON → Parquet Conversion** - Quality-Aware Processing mit Metadata
- **Multi-Symbol Support** - EURUSD, AUDUSD, GBPUSD, EURCHF
- **Quality Metrics** - 3-Level Error-Classification (Negligible/Serious/Fatal)
- **Data-Modes** - Clean/Realistic/Raw für verschiedene Test-Szenarien

**Sample Output:** [AUDUSD Ticks](./data/samples/AUDUSD_20250916_223859_ticks.json)

### ✅ Testing Framework (Functional)
- **Batch Orchestrator** - Multi-Scenario-Testing (sequential + parallel)
- **Worker System** - RSI, SMA, Envelope Workers mit Bar-Processing
- **Worker Parallelization** - ThreadPool für Worker-Execution (11ms+ speedup per tick)
- **Bar Rendering** - Multi-Timeframe-Support mit Warmup-Management
- **Signal Generation** - Decision-Coordinator generiert Trading-Signals

### ✅ Configuration System
- **Scenario-Configs** - JSON-basiert, Support für Parameter + Execution-Settings
- **Scenario Generator** - Automatische Scenario-Erstellung aus Tick-Daten
- **Flexible Parameters** - Strategy-Config (RSI/Envelope-Settings) + Execution-Config (Parallelization)

---

## 🚧 MVP Roadmap - Was noch kommt

### 📋 Issue 1: Logging & TUI (Low Priority)
**Ziel:** Statisches TUI-Dashboard mit Live-Metriken

- [ ] Logging-Modul (Print → Logger migration)
- [ ] TUI-Dashboard mit `rich` (Scenarios + Performance + Logs)
- [ ] Error-Pinning (Warnings/Errors persistent anzeigen)
- [ ] Log-File-Output

**Aufwand:** 1-2 Tage  
**Priorität:** Niedrig (Nice-to-have, polish)

---

### 📋 Issue 2: Architecture Refactoring (HIGH Priority) ⚠️
**Ziel:** Worker-Factory + DecisionLogic-Separation

**A) Worker-Factory Pattern**
- [ ] Config-basierte Worker-Erstellung (kein Hardcoding mehr)
- [ ] Scenario definiert `worker_types: ["rsi", "sma", "envelope"]`
- [ ] Factory instanziiert Worker dynamisch

**B) DecisionLogic-Klasse**
- [ ] Neue `DecisionLogic`-Klasse (Kern der Trading-Entscheidungen)
- [ ] `DecisionCoordinator` → nur Koordination (Worker orchestrieren, Contracts sammeln)
- [ ] DecisionLogic bekommt Worker injected (nicht selbst auswählen)
- [ ] DecisionLogic-Typ in Scenario-Config wählbar

**C) Integration**
- [ ] Scenario/Generator-Anpassungen für neue Config-Struktur
- [ ] Migration bestehender Scenarios
- [ ] Tests für Worker-Factory + DecisionLogic-Flow

**Aufwand:** 4-5 Tage  
**Priorität:** **HOCH** - Fundament für Issue 3

**Architektur:**
```
Scenario Config → Factory → Worker-Instanzen → DecisionCoordinator → DecisionLogic
```

---

### 📋 Issue 3: Trade Simulation (HIGH Priority) ⚠️
**Ziel:** Realistische Trade-Ausführung mit Portfolio-Management

**Phase 1: Core Trade-Simulator (4-5 Tage)**

**A) BrokerConfig Importer (MQL5)**
- [ ] Neues MQL5-Tool: `TraderDefaultsImporter`
- [ ] Import: Order-Types, Commission, Lot-Sizes, Margin-Requirements
- [ ] Export als JSON

**B) TradeSimulator - Core Components**
- [ ] **PortfolioManager**: Balance/Equity-Tracking, Open Positions
- [ ] **OrderManager**: Active Trades, Trade History
- [ ] **RiskManager**: Max Positions (default: 1), Max Drawdown (default: 30%)
- [ ] **ExecutionEngine**: 
  - Fixed Latency (100ms)
  - Fixed Slippage (0.5 pips)
  - Market + Limit Orders only
  - Order fully filled or rejected (no partial fills)
- [ ] **EventBus**: Events zu DecisionLogic (TradeExecuted, OrderRejected, MarginWarning)

**C) DecisionLogic Integration**
- [ ] Query: `get_account_info()`, `get_open_positions()`, `get_trade_history()`
- [ ] Send Orders: `TradeSimulator.send_order()`
- [ ] Receive Events via EventBus

**D) Realismus-Features**
- [ ] Spread-Dynamik aus Demo-Daten
- [ ] Commission-Berücksichtigung
- [ ] Basic Margin-Checks

**Vereinfachungen (Post-MVP verschoben):**
- ❌ ECN-Markt-Simulation → Standard-Broker reicht
- ❌ Partial Fills → Order fully filled or rejected
- ❌ Connection-Lost Events → Unrealistisch für Backtest
- ❌ Swap/Rollover → Config-Option (default: 0)
- ❌ Adaptive Tick-Processing → Process every tick
- ❌ Liquidity-Simulation → Assume infinite liquidity

**Aufwand:** 4-5 Tage (statt 7-10 durch Vereinfachungen)  
**Priorität:** **HOCH** - Kritisch für realistische Tests

---

## 📊 MVP Timeline

**Gesamt:** ~10-12 Tage (2-3 Wochen)

1. **Issue 2** (4-5 Tage) → Start hier, Fundament
2. **Issue 3** (4-5 Tage) → Hauptaufwand, Trade-Simulation
3. **Issue 1** (1-2 Tage) → Optional, polish

**Milestone:** Funktionierendes End-to-End-System mit realistischer Trade-Simulation

---

## 🚀 Quick Start

### Data Collection (MQL5)
```bash
# 1. TickCollector installieren
cp mql5/TickCollector.mq5 [MetaTrader]/MQL5/Experts/

# 2. Auf Chart starten → Generiert JSON in C:/FiniexData/
```

### Python Environment
```bash
# Docker Container starten
docker-compose up -d
docker-compose exec finiex-dev bash -i

# Test ausführen
python python/strategy_runner_enhanced.py
```

### Current Test Output
```
✅ Success:            True
📊 Scenarios:          3
⏱️  Execution time:     41.77s
⚙️  Parallel Mode:     False
⚙️  Max. Workers:      4

📋 Scenario 1: EURUSD_window_01
  Ticks processed:    1,000
  Signals generated:  0
  Worker calls:       3,000
```

---

## 🏗️ Architecture Overview

### Current System
```
MQL5 TickCollector → JSON → Parquet (Quality-Aware)
                                ↓
                    Data Loader (Multi-Mode: Clean/Realistic/Raw)
                                ↓
                    Batch Orchestrator (Multi-Scenario)
                                ↓
                    Worker Coordinator (Parallel Workers)
                                ↓
                    Decision Coordinator (Signal Generation)
```

### Post-MVP (Issue 2+3)
```
Scenario Config → Worker Factory → Worker Instances
                                        ↓
                            Decision Coordinator → Decision Logic
                                        ↓
                            Trade Simulator (Portfolio/Risk/Orders)
                                        ↓
                            Event Bus → Results/Metrics
```

---

## 📁 Project Structure (Pre-Alpha V0.6)

```
FiniexTestingIDE/
├── mql5/
│   └── TickCollector.mq5          # Live-Tick-Sammlung
├── python/
│   ├── data_worker/               # Data-Pipeline
│   │   └── data_loader/           # Parquet-Loading
│   ├── framework/
│   │   ├── batch_orchestrator.py  # Multi-Scenario-Testing
│   │   ├── workers/               # RSI, SMA, Envelope Workers
│   │   │   └── worker_coordinator.py
│   │   ├── bars/                  # Bar-Rendering + Warmup
│   │   └── tick_data_preparator.py
│   ├── scenario/
│   │   ├── config_loader.py       # Scenario-Loading
│   │   └── generator.py           # Scenario-Generation
│   └── strategy_runner_enhanced.py # Main Entry Point
├── configs/
│   └── scenarios/                 # JSON-Scenario-Configs
├── data/
│   ├── raw/                       # MQL5 JSON-Output
│   └── parquet/                   # Processed Tick-Data
└── docs/                          # Architecture + Post-MVP Plans
```

---

## 🔧 Configuration Example (Pre-Alpha V0.6)

```json
{
  "name": "EURUSD_window_01",
  "symbol": "EURUSD",
  "start_date": "2024-09-16 00:00:00",
  "end_date": "2024-09-18 23:59:59",
  "data_mode": "realistic",
  "max_ticks": 1000,
  "strategy_config": {
    "rsi_period": 14,
    "rsi_timeframe": "M5",
    "envelope_period": 20,
    "envelope_deviation": 0.02
  },
  "execution_config": {
    "parallel_workers": true,
    "max_parallel_scenarios": 4,
    "worker_parallel_threshold_ms": 1.0
  }
}
```

---

## 🎯 Core Concepts

### Parameter-Centric Development
**Problem:** 80% der Zeit wird für Parameter-Tuning aufgewendet, aber Tools sind code-zentrisch.

**Lösung:** Parameter sind First-Class-Citizens. Strategies definieren Parameter-Requirements, IDE orchestriert Testing.

### Quality-Aware Data
**Problem:** Backtests mit schlechten Daten → unrealistische Ergebnisse.

**Lösung:** 3-Level Error-Classification unterscheidet Market-Anomalien (behalten) vs System-Errors (filtern).

### IP-Protected Strategies
**Problem:** Strategy-Code muss geheim bleiben, aber Parameter müssen optimierbar sein.

**Lösung:** Blackbox-Framework mit Parameter-Contract-System (geplant Post-MVP).

---

## 📈 Post-MVP Vision

### Phase 4: UX-Layer (6-8 Wochen)
- Web-Frontend mit Multi-Tab-Interface
- Real-time Progress-Updates via WebSocket
- Interactive Charts mit Timeline-Scrubber
- Visual Parameter-Panels

### Phase 5: Intelligence-Layer (8-12 Wochen)
- Parameter-Synergy-Detection
- AI-Enhanced Parameter-Suggestions
- Market-Regime-Analysis
- Predictive Performance-Analysis

### Phase 6: Enterprise-Platform (12+ Wochen)
- Cloud-native SaaS-Platform
- Multi-Tenancy + Token-based Billing
- Advanced Risk-Management
- Live-Trading-Integration (FiniexAutoTrader)

---

## 🧪 Testing & Quality

**Current Status:** Manual Testing only

**Post-MVP:**
- Unit-Tests für Core-Components (pytest)
- Integration-Tests für End-to-End-Flows
- GitHub Actions CI/CD Pipeline
- Performance-Benchmarks

---

## 📜 License & Trademarks

**License:** MIT License - see [LICENSE](LICENSE)

**Trademarks:** Finiex™ and all related marks are exclusive property of Frank Krätzig - see [TRADEMARK.md](TRADEMARK.md)

---

## 👤 Contact & Contributions

**Maintainer:** Frank Krätzig ([dc-deal](https://github.com/dc-deal))

**Status:** Active MVP Development - Focus on core framework before expanding features.

**Issues:** [GitHub Issues](https://github.com/dc-deal/FiniexTestingIDE/issues)

---

## 💙 Acknowledgments

Danke an alle die mich unterstützen!

*Go build something amazing!* ⚡

---


*Building the foundation for parameter-centric trading strategy development - one issue at a time.*