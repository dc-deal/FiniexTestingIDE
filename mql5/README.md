# FiniexTestingIDE - MQL5 Data Collection

## TickCollector Enhanced v1.03

Professioneller Tick-Daten-Sammler für MetaTrader 5 mit gestuftem Error-Tracking und Qualitätssicherung.

### Neue Features v1.03

- **Gestuftes Error-System**: Atomare Fehlerklassifizierung in 3 Stufen
- **Datenqualitäts-Scoring**: Automatische Qualitätsbewertung
- **Intelligente Anomalie-Erkennung**: Erkennt Spread-Sprünge, Zeitlücken, Preisanomalien
- **Adaptive Validierung**: Konfigurierbare Schwellenwerte pro Symbol
- **Stream-Corruption-Detection**: Erkennt korrupte Datenströme
- **Detaillierte Error-Reports**: Vollständige Metadaten für jeden Fehler

### Error-Klassifizierung

#### NEGLIGIBLE (Vernachlässigbar)
- **Spread-Sprünge** < 50%
- **Kleine Datenlücken** 60-300 Sekunden
- **Fehlende Tick-Flags**
- **Negative Real-Volume**
- **Status**: Daten bleiben voll brauchbar

#### SERIOUS (Ernst)
- **Extreme Spreads** > 5%
- **Große Datenlücken** > 5 Minuten
- **Preis-Sprünge** > 10%
- **Millisekunden-Zeitregressionen**
- **Negative Tick-Volume**
- **Status**: Daten brauchbar mit Einschränkungen

#### FATAL (Fatal)
- **Bid/Ask ≤ 0**
- **Invertierter Spread** (Ask < Bid)
- **Spread ≤ 0**
- **Zeitregressionen** (Rückwärts-Zeit)
- **Status**: Daten womöglich unbrauchbar

### Installation

1. Kopiere `TickCollector.mq5` in deinen MetaTrader 5 Experts Ordner
2. Kompiliere in MetaEditor (F7)
3. Hänge an beliebigen Chart an (EURUSD, GBPUSD, USDJPY, AUDUSD empfohlen)
4. Konfiguriere Export-Pfad: `C:\FinexData\`

### Konfiguration

#### Basis-Parameter
```cpp
input string ExportPath = "";                    // Leer = MQL5-Standard-Ordner
input bool CollectTicks = true;                  // Sammlung ein/aus
input int MaxTicksPerFile = 50000;               // Ticks pro Datei
input bool IncludeRealVolume = true;             // Echtes Volumen sammeln
input bool IncludeTickFlags = true;              // Tick-Flags sammeln
```

#### Error-Tracking-Konfiguration
```cpp
input bool EnableErrorTracking = true;           // Error-System aktivieren
input int MaxErrorsPerFile = 1000;               // Max Errors pro Datei
input bool LogNegligibleErrors = true;           // Negligible Errors loggen
input bool LogSeriousErrors = true;              // Serious Errors loggen
input bool LogFatalErrors = true;                // Fatal Errors loggen
input bool StopOnFatalErrors = false;            // Bei Fatal Errors stoppen
```

#### Validierungs-Schwellenwerte
```cpp
// Standard-Werte (anpassbar je Symbol)
maxSpreadPercent = 5.0;        // Max 5% Spread
maxPriceJumpPercent = 10.0;    // Max 10% Preis-Sprung
maxDataGapSeconds = 300;       // Max 5 Min Datenlücke
warningDataGapSeconds = 60;    // Warning bei 1 Min Lücke
```

### JSON-Output-Struktur

#### Metadaten
```json
{
  "metadata": {
    "symbol": "EURUSD",
    "collector_version": "1.03",
    "data_format_version": "1.0.3",
    "error_tracking": {
      "enabled": true,
      "max_spread_percent": 5.00,
      "max_price_jump_percent": 10.00,
      "max_data_gap_seconds": 300
    }
  }
}
```

#### Error-Report
```json
{
  "errors": {
    "by_severity": {
      "negligible": 2,
      "serious": 0,
      "fatal": 0
    },
    "details": [
      {
        "severity": "negligible",
        "severity_level": 0,
        "type": "spread_jump",
        "description": "Spread jump: 0.00011 to 0.00018 (63.6% change)",
        "timestamp": "2025.09.16 22:39:09",
        "tick_context": 5,
        "affected_value": 0.00007000,
        "additional_data": "prev_spread=0.00011"
      }
    ]
  }
}
```

#### Qualitäts-Metriken
```json
{
  "summary": {
    "data_stream_status": "HEALTHY",
    "quality_metrics": {
      "overall_quality_score": 0.947368,
      "data_integrity_score": 1.000000,
      "data_reliability_score": 1.000000,
      "negligible_error_rate": 0.052632,
      "serious_error_rate": 0.000000,
      "fatal_error_rate": 0.000000
    },
    "recommendations": "Data quality is excellent - no specific recommendations."
  }
}
```

### Datenqualitäts-Scoring

- **Overall Quality Score**: 1.0 - (total_errors / total_ticks)
- **Data Integrity Score**: 1.0 - (fatal_errors / total_ticks)
- **Data Reliability Score**: 1.0 - ((serious + fatal_errors) / total_ticks)

### Stream-Status-Klassifizierung

- **HEALTHY**: Keine fatalen Errors, normale Datenqualität
- **COMPROMISED**: Fatale Errors vorhanden, Datenintegrität beeinträchtigt
- **CORRUPTED**: Stream-Korruption erkannt, Sammlung möglicherweise gestoppt

### File-Rotation-System

Das System rotiert Dateien **tick-basiert**, nicht größenbasiert:

```cpp
input int MaxTicksPerFile = 50000;  // Standard: 50.000 Ticks pro Datei
```

**Rotation-Ablauf (nahtlos):**
1. Tick 49.999: Normale Verarbeitung
2. Tick 50.000: In aktuelle Datei schreiben
3. **CloseCurrentFile()**: JSON abschließen, Error-Summary anhängen
4. **CreateNewExportFile()**: Neue Datei mit Metadaten erstellen
5. Tick 50.001: In neue Datei schreiben

**Kein Datenverlust** - der Übergang erfolgt zwischen OnTick()-Aufrufen.

### Erwartete Output-Größen (Schätzungen)

**Dateien pro Tag (24h Sammlung):**
- **EURUSD**: 8-15 Dateien/Tag
- **GBPUSD**: 6-12 Dateien/Tag
- **USDJPY**: 5-10 Dateien/Tag
- **AUDUSD**: 4-8 Dateien/Tag

*Variiert stark je nach Marktvolatilität und Handelszeiten*

**Dateigrößen pro File (50.000 Ticks):**
- **EURUSD**: 18-25 MB (bis 30 MB bei vielen Errors)
- **GBPUSD**: 20-28 MB (bis 35 MB bei Spread-Anomalien)
- **USDJPY**: 15-22 MB (3-Digit = kompakter)
- **AUDUSD**: 16-24 MB

**Speicherplatzbedarf (Schätzungen):**
- **Einzelsymbol**: 160-300 MB/Tag
- **Vier Symbole parallel**: 640 MB - 1.2 GB/Tag
- **Wöchentlich**: 4.5-8.4 GB
- **Monatlich**: 18-34 GB
- **Mit Parquet-Kompression**: ~2-4 GB/Monat (Faktor 8-12)

**Faktoren für größere Dateien:**
- News-Events: Bis zu 5x mehr Ticks
- London/NY Overlap: 2-3x höhere Aktivität  
- Error-Rate >5%: +20-30% Dateigröße
- Volatile Marktphasen: Einzeldateien bis 40-50 MB

**Error-Verteilung (typisch):**
- Negligible: 0.1-2% der Ticks
- Serious: 0.01-0.1% der Ticks  
- Fatal: 0-0.001% der Ticks

*Alle Angaben sind Schätzungen basierend auf typischen Forex-Marktbedingungen*

### Empfohlene Symbol-Konfigurationen

#### Major Pairs (EURUSD, GBPUSD, USDJPY)
```cpp
maxSpreadPercent = 2.0;        // Engere Toleranz
maxPriceJumpPercent = 8.0;     // Standard
```

#### JPY-Pairs (USDJPY, EURJPY, GBPJPY)
```cpp
maxPriceJumpPercent = 15.0;    // Höhere Toleranz
```

#### Exotic Pairs
```cpp
maxSpreadPercent = 10.0;       // Weitere Toleranz
maxPriceJumpPercent = 20.0;    // Höhere Volatilität erwartet
```

### Troubleshooting

#### Keine Dateien erstellt?
- Prüfe Expert Advisor Logs im Terminal
- Verifiziere dass Export-Pfad existiert
- Stelle sicher dass AutoTrading aktiviert ist
- Prüfe ob `CollectTicks = true` gesetzt ist

#### Hohe Fehlerraten?
- **> 5% Negligible**: Broker-Feed-Qualität prüfen
- **> 1% Serious**: Netzwerkstabilität und Server-Performance prüfen
- **> 0.1% Fatal**: Broker-Verbindung prüfen, möglicherweise Neustart erforderlich

#### Große Dateigrößen?
- Reduziere `MaxTicksPerFile` Parameter (z.B. auf 25.000)
- Aktiviere Session-Filtering für spezifische Handelszeiten
- Nutze `MaxErrorsPerFile` um Error-Log-Größe zu begrenzen

#### Performance-Optimierung
- Deaktiviere `LogNegligibleErrors` bei stabilen Feeds
- Erhöhe Validierungs-Schwellenwerte für weniger kritische Symbole
- Nutze `StopOnFatalErrors = true` um korrupte Streams zu stoppen

### Error-Code-Referenz

| Error Type | Severity | Beschreibung | Aktion |
|------------|----------|--------------|--------|
| `tick_unavailable` | SERIOUS | SymbolInfoTick() failed | Broker-Verbindung prüfen |
| `invalid_price_zero` | FATAL | Bid/Ask ≤ 0 | Daten verwerfen |
| `invalid_spread_zero` | FATAL | Spread ≤ 0 | Daten verwerfen |
| `spread_extreme` | SERIOUS | Spread > Schwellenwert | Marktvolatilität beachten |
| `spread_jump` | NEGLIGIBLE | Spread-Sprung > 50% | Normal bei Volatilität |
| `data_gap_major` | SERIOUS | Datenlücke > 5 Min | Verbindung prüfen |
| `data_gap_minor` | NEGLIGIBLE | Datenlücke 1-5 Min | Normal außerhalb Handelszeiten |
| `time_regression` | FATAL | Rückwärts-Zeitsprung | Server-Zeit prüfen |
| `price_jump_bid/ask` | SERIOUS | Preis-Sprung > Schwellenwert | Marktvolatilität |

### Support & Weiterentwicklung

**FiniexTestingIDE GitHub**: [github.com/dc-deal/FiniexTestingIDE](https://github.com/dc-deal/FiniexTestingIDE)

Für Issues, Feature-Requests und Contributions nutze bitte GitHub Issues.

### Changelog v1.03

- **NEW**: Gestuftes Error-System mit 3 Severity-Leveln
- **NEW**: Datenqualitäts-Scoring und automatische Empfehlungen
- **NEW**: Stream-Corruption-Detection
- **NEW**: Konfigurierbare Validierungs-Schwellenwerte
- **NEW**: Detaillierte Error-Metadaten mit Kontext
- **IMPROVED**: JSON-Struktur mit Error-Kategorisierung
- **IMPROVED**: Performance-optimierte Validierung
- **IMPROVED**: Erweiterte Session-Classification
