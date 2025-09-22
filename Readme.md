# FiniexTestingIDE

**© 2025 Frank Krätzung. Alle Rechte vorbehalten.**

---

## Trading-Strategy-Testing IDE - Proof of Concept

**Current Status:** Data Collection Phase - Building Foundation

Parameter-zentrierte Trading-Strategy-Testing-Platform mit fokus auf reproduzierbare Ergebnisse und IP-Schutz.

---

## Was aktuell läuft

### ✅ Live-Tick-Datensammlung (Produktiv)

**MQL5 TickCollector v1.03** sammelt seit mehreren Tagen kontinuierlich Live-Tick-Daten:
- 4 Märkte: EURUSD, AUDUSD, GBPUSD, EURCHF
- Hochwertige JSON-Ausgabe mit Error-Tracking
- Quality-Metrics und Session-Detection
- Beispiel-Output: [AUDUSD Sample](./data/samples/AUDUSD_20250916_223859_ticks.json)

**Data Quality Features:**
- 3-Level Error-Classification (Negligible/Serious/Fatal)
- Spread-Monitoring und Price-Jump-Detection
- Session-Awareness (Sydney/Tokyo/London/NY)
- Automatic Quality-Score-Calculation

```json
"quality_metrics": {
  "overall_quality_score": 1.000000,
  "data_integrity_score": 1.000000,
  "data_reliability_score": 1.000000
}
```

---

## In Entwicklung

### 🔄 Python Data Pipeline

**Nächster Schritt:** JSON → Parquet Conversion Pipeline
- Quality-Aware Processing basierend auf Metadata
- Apache Arrow für Zero-Copy Performance
- Multi-Symbol Data-Loading

### 🔄 Blackbox Framework

**Core-Komponente:** Parameter-Contract-System für Strategy-Testing
- IP-geschützte Strategy-Integration
- Parameter-Schema-Definition
- Signal-Output-Standardisierung

---

## Geplanter Workflow

```
Live Trading Data (MQL5) → JSON Export → Python Pipeline → Parquet Storage → Strategy Testing
```

**Vision:** Parameter-zentrierte IDE wo Strategien als Blackboxes gemountet werden und über verschiedene Market-Situationen getestet werden können.

---

## Quick Start - Datensammlung

### MQL5 Setup
```bash
# 1. TickCollector in MetaTrader 5 installieren
cp mql5/TickCollector.mq5 [MetaTrader]/MQL5/Experts/

# 2. Auf gewünschtem Chart starten
# → Generiert JSON-Files in C:/FiniexData/
```

### Sample Data Structure
Siehe [Beispiel-Output](./data/samples/AUDUSD_20250916_223859_ticks.json) für vollständige JSON-Struktur.

**Key Features der gesammelten Daten:**
- Millisekunden-Timestamps
- Bid/Ask/Spread-Tracking  
- Tick-Flags (BID/ASK/VOLUME)
- Session-Detection
- Real-Volume wenn verfügbar
- Comprehensive Error-Tracking

---

## Project Structure

```
FiniexTestingIDE/
├── mql5/
│   ├── TickCollector.mq5    # ✅ Live Data Collection
│   └── README.md
├── data/
│   └── samples/             # ✅ Example JSON Output
├── python/                  # 🔄 In Development
│   ├── data_pipeline/
│   └── blackbox_framework/
└── docs/                    # 📋 Architecture Documentation
```

---

## Development Roadmap

### Phase 1: Data Foundation (Current)
- [x] **MQL5 TickCollector** - Live data collection system
- [x] **Quality-Aware JSON Output** - Error tracking and metadata
- [ ] **Python Data Pipeline** - JSON → Parquet conversion
- [ ] **Basic Data Loader** - Quality-aware tick streaming

### Phase 2: Core Framework (Next 4-6 weeks)
- [ ] **Blackbox Base API** - Strategy integration interface
- [ ] **Parameter Contract System** - IP-protected parameter management
- [ ] **Single-Process Testing** - First strategy tests with real data
- [ ] **Console Interface** - Basic CLI for testing

### Phase 3: Multi-Processing (6-8 weeks)
- [ ] **Parallel Testing Engine** - Multiple strategy tests simultaneously
- [ ] **Performance Monitoring** - Real-time test progress and metrics
- [ ] **Results Management** - Performance comparison and ranking

---

## Current Data Collection Stats

**Running since:** September 16, 2025  
**Symbols tracked:** EURUSD, AUDUSD, GBPUSD, EURCHF  
**Data quality:** 95-100% (excellent)  
**Average ticks/minute:** 15-50 depending on session  
**Storage format:** JSON with comprehensive metadata

---

## Installation & Setup

```bash
# Clone repository
git clone https://github.com/dc-deal/FiniexTestingIDE.git
cd FiniexTestingIDE

# Setup MQL5 data collection
# See mql5/README.md for MetaTrader setup instructions

# Python environment (when ready)
pip install -r requirements.txt  # Coming soon
```

---

# docker setup

## Schritt 1: Container bauen

```sh
# In deinem Projektordner
docker-compose build

# Das dauert ein paar Minuten beim ersten Mal
```

## Schritt 2: Container starten

```sh
# Container im Hintergrund starten
docker-compose up -d

# In den Container "einsteigen" (-i = interaktiv)
docker-compose exec finiex-dev bash -i

# Sollte sowas zeigen: root@containerid:/app#
```
Im Container:

```sh
python --version
# Sollte zeigen: Python 3.12.x

pip list | grep pandas
# Sollte pandas anzeigen

# Python REPL testen, mit ein paar befehlen in der PYTHON REPL (-i = interaktiv)
python -i
>>> import pandas as pd
>>> import pyarrow as pa
>>> print("Setup erfolgreich!")
>>> exit()
```


# DB monitoring tools

Jupyter Notebooks - Das sind interaktive Web-basierte "Notizbücher" wo du Python-Code in Zellen ausführen kannst, mit sofortiger Ausgabe von Graphiken, Datenframes etc. Für Datenanalyse sind sie Industriestandard. Ja, definitiv vorrichten - bei Tick-Data-Exploration sind sie sehr wertvoll (visualisieren, experimentieren, schnelle Datenqualitäts-Checks).

## Contributing

This is currently a proof-of-concept in active development. The focus is on building a solid data foundation before expanding to the full IDE features.

**Current priorities:**
1. Reliable data collection pipeline
2. Quality-aware data processing
3. Basic strategy testing framework

---

## Why This Approach

**Problem:** Trading strategy development is code-centric, but 80% of time is spent on parameter tuning.

**Solution:** Parameter-centric IDE where strategies are blackboxes with exposed parameters that can be tested across multiple market situations.

**Foundation:** High-quality tick data with proper error classification and metadata - without this, no strategy testing platform can deliver reliable results.

---

## License

MIT License - see [LICENSE](LICENSE) for details.

**Trademarks:** All FiniexTestingIDE marks remain exclusive property of Frank Krätzung.

---

## Status & Contact

**Current Phase:** Data Collection & Pipeline Development  
**Maintainer:** Frank Krätzung ([dc-deal](https://github.com/dc-deal))  
**Issues:** [GitHub Issues](https://github.com/dc-deal/FiniexTestingIDE/issues)

---

*Building the foundation for parameter-centric trading strategy development.*
