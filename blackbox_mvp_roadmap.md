# BlackBox MVP Roadmap - 2-3 Wochen Fokus-Plan

**Ziel:** Eine vollständig lauffähige Strategy end-to-end mit Quality-Aware-Daten

**Zeitrahmen:** 2-3 Wochen (bei mäßiger Verfügbarkeit wie bisher)

---

## 🎯 Etappe 1: Core BlackBox Framework (5-6 Tage)

### ✅ Checkliste Etappe 1

- [ ] **Technical Indicators implementieren**
  - [ ] RSI, SMA, EMA, Bollinger Bands, ATR
  - [ ] Unit Tests für alle Indikatoren
  - [ ] Performance-Benchmarks (>1000 calls/sec)

- [ ] **BlackboxBase Framework vervollständigen**  
  - [ ] Parameter-Schema-System ausbauen
  - [ ] Lifecycle-Hooks (on_start, on_tick, on_stop)
  - [ ] Visual-Debug-System (add_line_point, add_arrow)
  - [ ] Built-in Error-Handling

- [ ] **Performance-Calculator implementieren**
  - [ ] Standard-Metriken (Sharpe, MaxDD, Win-Rate, Profit Factor)
  - [ ] Equity-Curve-Generation
  - [ ] Trade-by-Trade-Analysis

- [ ] **Example-Strategy funktionsfähig machen**
  - [ ] BasicRSIStrategy mit echten Parametern
  - [ ] Integration mit neuen Indicators
  - [ ] Debug-Output für Entwicklung

### 📂 Dateien Etappe 1
```
python/
├── blackbox_framework.py          # Erweitern ✅ (bereits begonnen)
├── technical_indicators.py        # Neu erstellen
├── performance_calculator.py      # Neu erstellen
└── tests/
    └── test_blackbox_framework.py # Neu erstellen

examples/
└── basic_strategy_example.py      # Überarbeiten ✅ (bereits vorhanden)
```

---

## 🔧 Etappe 2: Test Engine Foundation (4-5 Tage)

### ✅ Checkliste Etappe 2

- [ ] **TestEngine Kernfunktionen**
  - [ ] Quality-Aware Data-Loading via existing DataLoader
  - [ ] Tick-by-Tick Strategy-Execution
  - [ ] Trade-Signal zu echten Trades konvertieren
  - [ ] Position-Management (Long/Short/Flat)

- [ ] **Trade-Execution-Engine**
  - [ ] Spread-berücksichtigung bei Market-Orders
  - [ ] Slippage-Simulation (konfigurierbar)
  - [ ] Position-Sizing und Risk-Management
  - [ ] Stop-Loss/Take-Profit-Handling

- [ ] **Results-Export-System** 
  - [ ] JSON-Export (config, metrics, summary)
  - [ ] CSV-Export (Trade-List, Equity-Curve)
  - [ ] Debug-Output (Visual-Elements, Logs)
  - [ ] Parquet-Export für große Datasets

- [ ] **Memory-optimierte Execution**
  - [ ] Streaming-Tick-Processing (nicht alles in RAM)
  - [ ] Configurable History-Buffer-Size  
  - [ ] Progress-Tracking für lange Tests

### 📂 Dateien Etappe 2
```
python/
├── test_engine.py                 # Neu erstellen
├── trade_executor.py              # Neu erstellen  
├── results_exporter.py            # Neu erstellen
└── tests/
    └── test_engine.py             # Neu erstellen
```

---

## 🎮 Etappe 3: CLI Runner & Integration (3-4 Tage)

### ✅ Checkliste Etappe 3

- [ ] **Command-Line Interface**
  - [ ] Argument-Parsing (strategy, symbol, params, data-mode)
  - [ ] Config-File-Support (YAML/JSON)
  - [ ] Flexible Parameter-Injection
  - [ ] Output-Directory-Management

- [ ] **End-to-End Integration**
  - [ ] MQL5-JSON → DataLoader → TestEngine → Results
  - [ ] Error-Handling auf allen Pipeline-Stufen
  - [ ] Quality-Mode-Selection (Clean/Realistic/Raw)
  - [ ] Multi-Symbol-Support

- [ ] **Validation & Debugging**
  - [ ] Smoke-Tests mit echten EURUSD/GBPUSD-Daten
  - [ ] Performance-Benchmarks dokumentieren
  - [ ] Memory-Usage-Profiling
  - [ ] Output-Format-Validation

- [ ] **Documentation-Update**
  - [ ] README.md Häkchen aktualisieren
  - [ ] Quick-Start-Guide für ersten Test
  - [ ] CLI-Usage-Examples
  - [ ] Troubleshooting-Section

### 📂 Dateien Etappe 3
```
scripts/
├── run_single_test.py             # Neu erstellen
├── config_examples/
│   ├── basic_rsi_test.yaml        # Beispiel-Configs
│   └── multi_symbol_test.yaml
└── validate_installation.py       # Neu erstellen

docs/
└── quickstart_guide.md            # Neu erstellen
```

---

## 💡 Detaillierte Implementierungs-Hinweise

### Technical Indicators (Etappe 1)

**Warum separates Modul:**
- BlackboxBase wird sonst zu groß
- Indicators können isolated getestet werden
- Einfacher für Custom-Indicators später

**Performance-Kritisch:**
```python
# Statt List-Slicing (langsam):
def sma_slow(prices, period):
    return sum(prices[-period:]) / period  # Creates new list!

# Besser: Deque mit explizitem Indexing  
def sma_fast(prices_deque, period):
    return sum(prices_deque[i] for i in range(-period, 0)) / period
```

### Test Engine Architecture (Etappe 2)

**Separation of Concerns:**
```python
TestEngine:        # Orchestrates entire test
├── DataLoader     # Your existing quality-aware loading ✅
├── TradeExecutor  # Converts signals → actual trades  
├── Strategy       # User's BlackBox implementation
└── Exporter       # Results → Files
```

**Memory-Optimization für große Datasets:**
- Nicht alle Ticks gleichzeitig in RAM laden
- Streaming-Processing mit configurable Chunk-Size
- History-Buffer nur für Strategy-Requirements

### CLI Runner Design (Etappe 3)

**Usage-Pattern:**
```bash
# Einfachster Fall
python scripts/run_single_test.py \
  --strategy examples.basic_strategy_example:BasicRSIStrategy \
  --symbol EURUSD \
  --params rsi_period=14

# Advanced mit Config-File
python scripts/run_single_test.py \
  --config scripts/config_examples/basic_rsi_test.yaml \
  --output results/rsi_test_001/
  
# Quality-Mode-Selection
python scripts/run_single_test.py \
  --strategy examples.basic_strategy_example:BasicRSIStrategy \
  --symbol EURUSD \
  --data-mode clean \  # clean|realistic|raw
  --quality-threshold 0.95
```

---

## 🚀 Success Criteria pro Etappe

### Etappe 1 Complete ✅
- [ ] `BasicRSIStrategy().get_parameter_schema()` returns valid schema
- [ ] `indicators.rsi([1,2,3,...], 14)` returns correct value
- [ ] All indicators have >95% test coverage
- [ ] Strategy can be instantiated and configured

### Etappe 2 Complete ✅  
- [ ] `TestEngine.run()` processes 1000 ticks without error
- [ ] Generates realistic trade-list with P&L
- [ ] Exports results in 3 formats (JSON, CSV, Debug)
- [ ] Memory usage <500MB for 48h EURUSD dataset

### Etappe 3 Complete ✅
- [ ] CLI command runs BasicRSI on real EURUSD data
- [ ] Generates performance report (Sharpe, MaxDD, etc.)
- [ ] Quality-modes (clean/realistic) show different results  
- [ ] End-to-End time <2 minutes for 48h dataset
- [ ] README reflects actual capabilities

---

## 🎛️ Config-Example Preview

```yaml
# scripts/config_examples/basic_rsi_test.yaml
strategy:
  module: "examples.basic_strategy_example"
  class: "BasicRSIStrategy"
  parameters:
    rsi_period: 14
    oversold_threshold: 30.0
    overbought_threshold: 70.0

data:
  symbol: "EURUSD"  
  data_mode: "realistic"  # clean|realistic|raw
  quality_threshold: 0.90
  start_date: null  # null = use all available data
  end_date: null

execution:
  initial_balance: 100000.0
  position_size: 0.02  # 2% risk per trade
  spread_points: 1.5   # Override if not in data
  slippage_points: 0.5

output:
  directory: "results/basic_rsi_test/"
  formats: ["json", "csv", "parquet"]
  include_debug: true
  include_equity_curve: true
```

---

## ⏰ Realistische Zeitschätzung

**Bei deiner bisherigen Pace (1.5 Wochen für Data-Pipeline):**

- **Etappe 1:** 5-6 Tage (Indicators + Framework)
- **Etappe 2:** 4-5 Tage (Test Engine)  
- **Etappe 3:** 3-4 Tage (CLI + Integration)

**Total:** 12-15 Tage = **2-3 Wochen** ✅

**Puffer für Unvorhergesehenes:** +20% (realistische Softwareentwicklung)

---

## 🎯 Prioritäten falls Zeit knapp wird

**Must-Have (MVP):**
- RSI-Indicator + BasicRSIStrategy
- TestEngine basic functionality  
- CLI runner für einen Test

**Nice-to-Have (kann später):**
- Bollinger Bands, ATR (weitere Indicators)
- Multi-Symbol-Support
- Parquet-Export
- Advanced Debug-Features

**Can-Wait (Post-MVP):**
- Performance-Optimizations
- Memory-Profiling  
- Advanced Config-Options
- Comprehensive Error-Handling

---

## 🔄 Iterative Development-Approach

**Week 1:** Get BasicRSI working mit hardcoded parameters
**Week 2:** Add TestEngine + flexible parameters  
**Week 3:** Polish CLI + documentation + validation

**Fail-Fast-Prinzip:** Teste nach jeder Etappe ob End-to-End noch funktioniert.

---

**Das ist dein Fokus-Plan für die nächsten 2-3 Wochen. Danach hast du eine vollständig demonstrierbare Strategy-Testing-Engine! 🚀**