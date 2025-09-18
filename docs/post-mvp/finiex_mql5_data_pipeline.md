# MQL5 → Testing IDE Daten-Pipeline

## Workflow-Übersicht

**MQL5 Expert Advisor → JSON-Files → Python Converter → Parquet-Database → Testing IDE**

Diese Pipeline implementiert **intelligente Datenqualitätssicherung** durch alle Stufen hinweg, um sowohl **technische Qualität** als auch **Markt-Realismus** zu gewährleisten.

---

## Datenqualitäts-Philosophie

### Das fundamentale Problem
Trading-Strategy-Testing steht vor einem Dilemma: **Wie testet man realistisch, ohne durch technische Artefakte verfälscht zu werden?**

### Die Lösung: Intelligente Fehlerklassifizierung

**Markt-authentische Anomalien (Qualitätsmerkmal):**
- Spread-Sprünge bei News-Events
- Liquiditätslücken zwischen Sessions  
- Volatilitäts-bedingte Preis-Anomalien
- **→ Für realistisches Testing behalten**

**System-bedingte Fehler (Qualitätsproblem):**
- Netzwerk-Unterbrechungen
- Feed-Korruption, Zeit-Regressionen
- PC-Performance-Probleme
- **→ Identifizieren und warnen/filtern**

---

## Phase 1: MQL5 Expert Advisor → JSON-Files

### TickCollector v1.03: Intelligente Echtzeit-Erfassung

#### Gestuftes Error-Tracking während Live-Sammlung

**NEGLIGIBLE (Vernachlässigbar)**
- Spread-Sprünge <50%, Session-Gaps 60-300s
- Fehlende Tick-Flags, negative Real-Volume
- **Aktion**: Loggen, Daten behalten (markt-authentisch)

**SERIOUS (Ernst)**
- Extreme Spreads >5%, Datenlücken >5min
- Preis-Sprünge >10%, Zeit-Regressionen (ms)
- **Aktion**: Warnen, Kontext prüfen

**FATAL (Fatal)**  
- Negative Preise, invertierte Spreads
- Zeitregressionen (Sekunden), unmögliche Zustände
- **Aktion**: Daten verwerfen oder Sammlung stoppen

#### Adaptive Symbol-Konfiguration
```
Major Pairs: MaxSpread 2%, MaxJump 8%
JPY Pairs:   MaxSpread 3%, MaxJump 15% 
Exotic Pairs: MaxSpread 10%, MaxJump 20%
```

#### Echtzeit-Qualitätsmetriken
- **Overall Quality Score**: 1.0 - (total_errors / total_ticks)
- **Data Integrity Score**: 1.0 - (fatal_errors / total_ticks)  
- **Data Reliability Score**: 1.0 - (serious+fatal / total_ticks)

#### JSON-Output mit Error-Tracking

**File-Rotation**: 50.000 Ticks pro JSON-File (nahtlos)

**Erwartete Dateigrößen:**
```
EURUSD: 18-25 MB/File (8-15 Files/Tag)
GBPUSD: 20-28 MB/File (6-12 Files/Tag)  
USDJPY: 15-22 MB/File (5-10 Files/Tag)
AUDUSD: 16-24 MB/File (4-8 Files/Tag)
```

**JSON-Struktur (vereinfacht):**
```json
{
  "metadata": {
    "symbol": "EURUSD",
    "collector_version": "1.03",
    "error_tracking": {
      "max_spread_percent": 5.00,
      "max_price_jump_percent": 10.00
    }
  },
  "ticks": [...],
  "errors": {
    "by_severity": {"negligible": 2, "serious": 0, "fatal": 0},
    "details": [...]
  },
  "summary": {
    "data_stream_status": "HEALTHY",
    "quality_metrics": {...},
    "recommendations": "..."
  }
}
```

---

## Phase 2: JSON-Files → Python Converter

### Pre-Import Health Assessment

#### Automatische Qualitätsbewertung vor Konvertierung

**Health-Check-Pipeline:**
1. Error-Rate-Analyse pro Schweregrad
2. Pattern-Recognition für System-vs-Markt-Anomalien  
3. Zeitkontext-basierte Klassifizierung
4. Multi-Symbol-Korrelations-Checks

**Import-Entscheidungslogik:**
```
Quality Score > 95%:  Automatic Import
Quality Score 85-95%: Import mit Warning  
Quality Score 70-85%: Manual Review empfohlen
Quality Score < 70%:  Import-Ablehnung vorgeschlagen
```

#### Error-Authentizitäts-Klassifizierung

**Market-Authentic Indicators:**
- Zeitkorrelation mit News-Events (14:30, 08:30 UTC)
- Multi-Symbol-Simultaneität
- Plausible Größenordnungen und Dauer
- Session-Übergangsmuster

**System-Suspected Indicators:**
- Isolierte, symbol-spezifische Anomalien
- Unplausible Häufigkeitsmuster  
- Korrelation mit Connection-Issues
- Anomalien außerhalb Marktzeiten

#### Erweiterte Datenvalidierung

**Automatische Checks:**
- Bid/Ask-Konsistenz, Spread-Plausibilität
- Zeitstempel-Kontinuität und -Monotonie  
- Volume-Validierung, Tick-Flag-Konsistenz
- Cross-Symbol-Korrelation bei paralleler Sammlung

**Quality-Enhancement:**
- Duplikat-Entfernung (keep latest)
- Zeitstempel-Normalisierung zu UTC
- Datentyp-Optimierung für Performance
- Missing-Value-Imputation wo sinnvoll

---

## Phase 3: Python Converter → Parquet-Database  

### Quality-Aware Parquet-Konvertierung

#### Optimierte Storage-Struktur
```
datasets/processed/
├── EURUSD/
│   ├── 2024Q1_ticks.parquet
│   └── quality_metadata.json
├── GBPUSD/
└── metadata/
    ├── schemas.json
    └── quality_indices.json
```

#### Erweiterte Parquet-Metadaten

**Jede Parquet-File enthält:**
```json
{
  "data_quality": {
    "overall_score": 0.947,
    "integrity_score": 1.000,
    "reliability_score": 0.998,
    "health_status": "HEALTHY",
    "error_breakdown": {
      "market_authentic_errors": 45,
      "system_suspected_errors": 3
    },
    "validation_timestamp": "2025-01-15T10:30:00Z",
    "recommendations": "Data suitable for realistic testing"
  },
  "source_info": {
    "original_files": ["EURUSD_20240101_120000_ticks.json"],
    "collection_period": "2024-01-01 to 2024-01-02",
    "broker": "Vantage International",
    "collector_version": "1.03"
  }
}
```

#### Kompression und Performance

**Erwartete Kompression:**
- JSON → Parquet: 8-12:1 Ratio
- 4 Symbole/Monat: ~2-4 GB (vs 18-34 GB JSON)
- Arrow-optimiert für Zero-Copy-Loading

**Column-Store-Optimierung:**
- Nur benötigte Spalten laden
- Memory-Mapping für Shared-Access
- Efficient Filtering auf Quality-Scores

---

## Phase 4: Parquet-Database → Testing IDE

### Quality-Aware Data Loading

#### Multi-Mode Data Loading

**Testing-Modi:**
```python
DataLoader.load(
  symbol="EURUSD",
  mode="realistic",        # realistic|clean|raw
  quality_threshold=0.90,  # minimum quality
  include_market_anomalies=True
)
```

**Clean Mode**: System-Errors gefiltert, minimale Anomalien
**Realistic Mode**: Markt-Anomalien behalten, System-Errors gefiltert  
**Raw Mode**: Alle Daten ungefiltert (Stress-Testing)

#### Adaptive Quality-Filtering

**Threshold-based Loading:**
- Automatisches Filtering basierend auf Test-Typ
- Quality-Score-Weighting für gemischte Datasets
- Real-time Quality-Feedback an Testing-Engine

**Memory-optimierte Pipeline:**
- Shared-Memory für parallele Worker-Prozesse
- Zero-Copy Arrow-Buffers zwischen Komponenten
- Lazy-Loading für große Datasets

#### Quality-Kontext für Strategy-Tests

**Jeder Strategy-Test enthält:**
```json
{
  "strategy_performance": {...},
  "data_context": {
    "quality_score": 0.94,
    "testing_mode": "realistic", 
    "market_anomalies_included": true,
    "reliability_disclaimer": "Results include realistic market conditions"
  }
}
```

---

## Phase 5: Testing IDE Integration

### Robustness-Testing Framework

#### Dual-Condition Strategy Validation

**Parallel-Testing für echte Robustheit:**

1. **Clean Conditions Test**
   - Quality Threshold: 99%
   - Mode: Clean (minimal anomalies)
   - **Zeigt**: Theoretisches Maximum

2. **Realistic Conditions Test**  
   - Quality Threshold: 85%
   - Mode: Realistic (market anomalies)
   - **Zeigt**: Erwartbare Live-Performance

**Robustness Score:**
```
Score = Realistic Performance / Clean Performance

>0.9: Sehr robust
0.7-0.9: Mäßig robust  
<0.7: Fragile Strategie
```

#### Quality-First Development Workflow

**Entwicklungsphasen mit passenden Modi:**
1. **Development**: Clean Mode (schnelle Iteration)
2. **Validation**: Realistic Mode (Robustness-Check)
3. **Stress-Test**: Raw Mode (Extreme-Conditions)
4. **Pre-Production**: Multi-Mode Final Validation

#### Developer-Experience Features

**Quality-Transparency:**
- Real-time Quality-Dashboard pro Symbol
- Historical Quality-Trends und -Patterns
- Quality-Impact-Analyse auf Strategy-Performance
- Automated Quality-Alerts und Recommendations

**Adaptive Testing:**
- Automatic Mode-Selection basierend auf Data-Quality
- Quality-Threshold-Management per Strategy-Typ
- Cross-Quality-Level Performance-Comparison
- Quality-Performance-Korrelations-Analytics

---

## Pipeline-Performance und Skalierung

### Speicher- und Performance-Erwartungen

**Tägliche Datenmengen (4 Symbole):**
```
Raw JSON: 640 MB - 1.2 GB
Parquet: 64-120 MB (10:1 compression)  
Monthly: ~2-4 GB komprimiert
```

**Typische Error-Verteilung:**
```
Negligible: 0.1-2% (normale Volatilität)
Serious: 0.01-0.1% (System-Issues)
Fatal: 0-0.001% (seltene Korruption)
Typical Quality Score: 94-99%
```

**Processing-Performance:**
- JSON→Parquet: ~1-2 min/100MB JSON
- Quality-Analysis: ~10-30s/File
- Parquet-Loading: <1s für 50k Ticks
- Multi-Worker-Skalierung: Linear bis CPU-Cores

### Monitoring und Alerting

**Pipeline-Health-Monitoring:**
- Real-time Quality-Score-Tracking
- Error-Pattern-Detection und -Alerting
- Cross-Symbol Quality-Correlation
- Historical Quality-Degradation-Detection

**Operational-Metrics:**
- Processing-Throughput pro Pipeline-Stage
- Quality-Distribution über Zeit
- Error-Classification-Accuracy
- Storage-Growth und Compression-Ratios

---

## Fazit: Revolutionäre Strategy-Validation

Diese Pipeline löst das fundamentale **Realismus-vs-Qualität-Dilemma** durch:

**Intelligente Klassifizierung** statt blinder Filterung  
**Workflow-integrierte Qualitätssicherung** in jeder Phase
**Transparente Quality-Scores** für fundierte Test-Entscheidungen
**Adaptive Testing-Modi** für verschiedene Entwicklungsphasen  
**Robustness-Validation** durch Dual-Condition-Testing

**Das Ergebnis:** Strategy-Entwickler können mit Vertrauen testen, wissend dass ihre Daten sowohl qualitativ hochwertig als auch marktrealistisch sind. Strategien, die in dieser Pipeline validiert werden, haben eine deutlich höhere Chance auf Live-Trading-Erfolg.
