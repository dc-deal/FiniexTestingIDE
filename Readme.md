# FiniexTestingIDE

**© 2025 Frank Krätzung. Alle Rechte vorbehalten.**  
**FiniexTestingIDE** ist eine Marke von Frank Krätzung.

---

## Professionelle Trading-Strategy-Testing & Entwicklungsumgebung

Revolutionäre IDE-artige Plattform für das Testen von Trading-Strategien mit IP-Schutz, massiver Parallelisierung und reproduzierbaren Ergebnissen.

**FiniexTestingIDE** löst das fundamentale Problem der Trading-Strategy-Entwicklung:
*"Wie testet man Strategien schnell, fair und reproduzierbar - ohne das geistige Eigentum preiszugeben?"*

### Kernfeatures

**Intelligente Datenqualitätssicherung** - Unterscheidung zwischen markt-authentischen Anomalien und system-bedingten Fehlern  
**Multi-Tab-Testing-IDE** - Parameter-zentrierte Entwicklungsumgebung mit Live-Feedback  
**Blackbox-API** - Strategien bleiben geheim, Testing bleibt transparent  
**Massive Parallelisierung** - 1000+ Szenarien gleichzeitig  
**Reproduzierbare Ergebnisse** - Deterministische Seeds & unveränderliche Snapshots  
**Production-Ready** - Nahtloser Handover zu Live-Trading-Systemen

### Technische Architektur

```
MQL5 TickCollector → JSON Export → Python Pipeline → Parquet Database → FiniexTestingIDE
                                                                         ↓
                    BlackBox Framework ← Strategy Validation ← Multi-Tab Testing Engine
```

**Warum dieser Stack:**
- **MQL5**: Live-Tick-Daten von jedem Forex-Broker
- **Apache Arrow/Parquet**: Zero-Copy-Performance für große Datasets  
- **Python Multiprocessing**: Echte Parallelität (umgeht GIL)
- **Blackbox-Framework**: IP-Schutz + standardisierte Schnittstelle

---

## Aktueller Implementierungsstand

### Vollständig implementiert
- **MQL5 TickCollector v1.03** mit gestuftem Error-Tracking
- **JSON → Parquet Pipeline** mit Quality-Metadata-Integration
- **Quality-Aware Data Loader** mit Multi-Mode-Support
- **3-Level Error-Classification-System** (Negligible/Serious/Fatal)
- **Market-Authenticity-Detection** (echte vs. technische Anomalien)

### In Entwicklung  
- **Blackbox Base-Framework** mit Parameter-Schema-System
- **Multi-Process Test-Engine** mit Shared-Memory-Access
- **Basic Parameter-UI** für Development-Mode

### Geplant (MVP)
- **Multi-Tab Web-Interface** mit Real-time-Updates
- **Chart-System** mit Timeline-Scrubber und Debug-Overlays
- **Standard-Indikatoren-Library** (RSI, MACD, Bollinger)

---

## Quick Start

### Schritt 1: Datensammlung (Erledigt)

```bash
# 1. MQL5 TickCollector in MetaTrader 5 installieren
cp mql5/TickCollector.mq5 [MetaTrader]/MQL5/Experts/

# 2. Auf EURUSD Chart für 48 Stunden laufen lassen
# → Generiert JSON Tick-Daten in C:/FiniexData/

# 3. Erwarteter Output: 300-900MB rohe Tick-Daten
```

### Schritt 2: FiniexTestingIDE Setup

```bash
# 1. Environment einrichten
pip install -r requirements.txt

# 2. JSON zu Parquet konvertieren  
python python/tick_importer.py

# 3. Data Loading testen
python python/data_loader.py
```

### Schritt 3: Ihre erste Strategy erstellen

```python
from finiex.blackbox_framework import BlackboxBase, Signal

class MeineStrategy(BlackboxBase):
    def get_parameter_schema(self):
        return {
            'rsi_period': {'type': 'int', 'default': 14, 'description': 'RSI Periode'},
            'profit_target': {'type': 'float', 'default': 0.002, 'description': 'Gewinnziel %'}
        }
    
    def on_tick(self, tick):
        # Ihre geheime Trading-Logik hier
        rsi = self.indicators.rsi(self.price_history, self.parameters['rsi_period'])
        
        # Visual Debug (nur in Development-Mode)
        self.add_line_point("rsi", rsi, tick.timestamp)
        
        if rsi < 30:
            return Signal("BUY", price=tick.ask)
        elif rsi > 70:
            return Signal("SELL", price=tick.bid)
        
        return Signal("FLAT")
```

---

## MVP Roadmap & TODO-Liste

### Phase 1: Core Framework (4-6 Wochen) - **IN PROGRESS**

#### 1.1 Blackbox-Framework Grundlagen
- [ ] **BlackboxBase Klasse implementieren**
  - [ ] Parameter-Schema-System
  - [ ] Signal-Output-Format definieren  
  - [ ] Debug-Mode vs. Production-Mode
  - [ ] Basic Indicator-Library (RSI, MACD, SMA)
- [ ] **Parameter-Validierung**
  - [ ] Type-Checking (int, float, bool, string)
  - [ ] Range-Validierung (min/max)
  - [ ] Dependency-Rules zwischen Parametern
- [ ] **Testing-Framework Integration**
  - [ ] Blackbox-Instanziierung pro Test-Run
  - [ ] Parameter-Injection-System
  - [ ] Signal-Collection und -Verarbeitung

#### 1.2 Single-Process Test-Engine  
- [ ] **Basic Test-Runner**
  - [ ] Tick-by-Tick Strategy-Execution
  - [ ] Signal-zu-Trade-Konvertierung
  - [ ] P&L-Berechnung mit Spread/Slippage
  - [ ] Basic Performance-Metriken (Sharpe, MaxDD)
- [ ] **Data-Pipeline-Integration**
  - [ ] Quality-Aware Parquet-Loading
  - [ ] Multi-Mode Data-Access (Clean/Realistic/Raw)
  - [ ] Memory-efficient Tick-Streaming
- [ ] **Result-Storage**
  - [ ] Test-Run-Metadaten (Parameter, Timestamps)
  - [ ] Performance-Metriken als JSON
  - [ ] Trade-Liste als CSV Export

#### 1.3 Basic Web-Interface
- [ ] **Single-Tab Testing-UI**
  - [ ] Parameter-Input-Panel (automatisch generiert aus Schema)
  - [ ] Start/Stop Test-Controls
  - [ ] Basic Progress-Anzeige
  - [ ] Simple Results-Display
- [ ] **Backend-API (FastAPI)**
  - [ ] POST /api/test/start - Test starten
  - [ ] GET /api/test/{id}/status - Status abfragen  
  - [ ] GET /api/test/{id}/results - Ergebnisse abrufen
  - [ ] WebSocket für Live-Updates
- [ ] **Basic Chart-Rendering**
  - [ ] Candlestick-Chart mit Plotly.js
  - [ ] Signal-Marker (Buy/Sell-Arrows)
  - [ ] Basic Zoom/Pan-Funktionalität

### Phase 2: Multi-Tab IDE (6-8 Wochen)

#### 2.1 Multi-Process Test-Engine
- [ ] **Process-Pool-Management**
  - [ ] Worker-Process-Spawning
  - [ ] Shared-Memory für Tick-Daten (Arrow-Buffers)
  - [ ] Inter-Process-Communication für Results
  - [ ] Resource-Management (CPU/RAM-Limits)
- [ ] **Tab-Isolation-System**
  - [ ] Eine Blackbox-Instanz pro Tab
  - [ ] Separate Parameter-Sets pro Tab
  - [ ] Independent Resource-Allocation
  - [ ] Cross-Tab-Performance-Comparison

#### 2.2 Advanced Parameter-UI
- [ ] **Smart Parameter-Panel**
  - [ ] Parameter-Dependency-Visualization
  - [ ] Auto-Suggestion basierend auf Performance
  - [ ] Parameter-Synergy-Detection
  - [ ] Quick-Preset-Management
- [ ] **Multi-Tab-Interface**
  - [ ] Tab-Creation/Deletion-Management
  - [ ] Tab-Naming (basierend auf Parameter-Variation)
  - [ ] Tab-Status-Monitoring (Running/Completed/Failed)
  - [ ] Cross-Tab-Result-Comparison

#### 2.3 Real-time Performance-Monitoring
- [ ] **Live-Statistics-Dashboard**
  - [ ] Real-time P&L-Updates
  - [ ] Live Sharpe-Ratio-Berechnung
  - [ ] Risk-Monitoring (Drawdown-Alerts)
  - [ ] Trade-Count und Win-Rate-Tracking
- [ ] **Performance-Trend-Prediction**
  - [ ] Statistical Trend-Analysis
  - [ ] Early-Stop-Recommendations
  - [ ] Performance-Forecasting

### Phase 3: Advanced Features (4-6 Wochen)

#### 3.1 Timeline-Scrubber & Visual-Debug
- [ ] **Interactive Chart-Navigation**
  - [ ] Frame-by-Frame-Scrubbing
  - [ ] Jump-to-Timestamp-Funktion
  - [ ] Playback-Speed-Controls
  - [ ] Zoom-to-Signal-Events
- [ ] **Debug-Overlay-System**
  - [ ] Indicator-Value-Display per Tick
  - [ ] Decision-Logic-Visualization
  - [ ] Parameter-Impact-Highlighting
  - [ ] Strategy-State-Inspection

#### 3.2 Collection-Management-System  
- [ ] **Datenkollektion-Manager**
  - [ ] Timeline-basierte Situation-Selection
  - [ ] Preset-Market-Scenarios (News-Events, Sessions)
  - [ ] Quality-Score-Filtering
  - [ ] Batch-Situation-Creation
- [ ] **Results-Explorer**
  - [ ] Performance-Ranking (Best-to-Worst)
  - [ ] Multi-Criteria-Sorting
  - [ ] Failed-Run-Analysis
  - [ ] Export-Functionality (CSV/PDF-Reports)

#### 3.3 Production-Ready Features
- [ ] **Parameter-Einbrennung-System**
  - [ ] Development → Production Mode Conversion
  - [ ] Optimal-Parameter-Identification
  - [ ] Production-Blackbox-Generation
  - [ ] Abstract-Parameter-Layer für Live-Tuning


### ✅ Abgeschlossene Komponenten

#### Data Collection & Pipeline
- [x] **MQL5 TickCollector v1.03** - Live Tick-Daten von jedem Forex-Broker
  - [x] Gestuftes Error-Tracking-System
  - [x] JSON Export-Format mit Metadaten
  - [x] 48h+ Datensammlung (300-900MB Output)
- [x] **JSON → Parquet Pipeline** - Zero-Copy Performance für große Datasets
  - [x] Quality-Metadata-Integration
  - [x] 10:1 Daten-Kompression erreicht
- [x] **Quality-Aware Data Loader** - Multi-Mode Data-Access
  - [x] 3-Level Error-Classification (Negligible/Serious/Fatal)
  - [x] Market-Authenticity-Detection
  - [x] Clean/Realistic/Raw Data-Modi

---

## Performance-Ziele

| Metrik | Ziel | Status |
|--------|------|--------|
| Time-to-First-Backtest | < 30 min | 🔄 In Arbeit |
| Parallele Szenarien | 100+ | 📋 Geplant |
| Determinismus-Rate | ≥ 99% | 📋 Geplant |
| Daten-Kompression | 10:1 | ✅ Erreicht |
| UI-Response-Zeit | < 200ms | 📋 Geplant |

---

## Projektstruktur

```
docs/                    # Vollständige Dokumentation
├── finiex-complete-documentation.md
├── mql5-data-pipeline.md
├── finiex-tech-stack.md
└── finiex-ide-ux-concept.md

mql5/                    # MetaTrader 5 Data Collectors
├── TickCollector.mq5    # v1.03 mit Error-Tracking ✅
└── README.md

finiex/
├── testing_ide/         # Core FiniexTestingIDE
├── blackbox_framework/  # Blackbox-API & Framework
├── data_pipeline/       # Quality-Aware Data Processing ✅
└── core/               # Shared Components

data/                    # Tick-Daten-Storage (gitignored)
examples/
├── strategies/          # Beispiel-Strategien
└── testing_scenarios/   # Test-Szenarien

scripts/                 # Utility-Scripts
```

---

## Prioritäten für MVP

### Woche 1-2: Foundation
**Ziel:** Lauffähige Single-Tab-Testumgebung
1. Blackbox-Framework Core
2. Basic Test-Engine mit Data-Pipeline-Integration  
3. Simple Web-UI für einen Test-Run

### Woche 3-4: Multi-Tab-System
**Ziel:** Parallele Parameter-Tests
1. Multi-Process-Test-Engine
2. Tab-Management-System
3. Real-time Performance-Updates

### Woche 5-6: User-Experience  
**Ziel:** Professionelle IDE-Erfahrung
1. Timeline-Scrubber für Chart-Navigation
2. Advanced Parameter-UI mit Synergien
3. Collection-Management für Markt-Situationen

---

## Installation & Development

```bash
# Repository klonen
git clone https://github.com/dc-deal/FiniexTestingIDE.git
cd FiniexTestingIDE

# Python-Environment einrichten
pip install -r requirements.txt

# Development-Server starten
python -m finiex.testing_ide.server --dev
```

### Development-Guidelines
- Alle Strategien müssen mit dem Blackbox-Framework kompatibel sein
- Parameter-Schema-Standards einhalten
- Multi-Tab-Isolation beachten
- Quality-Aware Data-Loading verwenden

---

## Contributing

Wir begrüßen Beiträge zur **FiniexTestingIDE**! Bitte siehe [CONTRIBUTING.md](docs/contributing.md) für Guidelines.

---

## Lizenz

Dieses Projekt ist unter der MIT License lizenziert - siehe [LICENSE](LICENSE) für Details.

**Wichtig**: Alle **FiniexTestingIDE**-Marken bleiben ausschließliches Eigentum von Frank Krätzung.

---

## Kontakt & Support

**Projekt-Maintainer**: Frank Krätzung ([dc-deal](https://github.com/dc-deal))  
**Issues**: [GitHub Issues](https://github.com/dc-deal/FiniexTestingIDE/issues)  
**Erste Veröffentlichung**: 17. September 2025

---

**FiniexTestingIDE - Revolutionäre Trading-Strategy-Entwicklung**
