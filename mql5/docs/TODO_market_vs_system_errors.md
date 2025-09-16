# Market Authenticity vs System Errors
## Importer Validation Framework

### Grundproblem

Nicht alle Errors in Tick-Daten sind gleich:
- **Markt-Authentizität**: Echte Marktanomalien sind Qualitätsmerkmal (realistische Testbedingungen)
- **System-Fehler**: Technische Issues mindern Datenqualität und sollten gefiltert/gewarnt werden

### Error-Klassifizierung für Importer

#### Markt-authentische Anomalien (BEHALTEN)
```
✓ spread_jump während News-Events (14:30 UTC, 08:30 UTC)
✓ price_jump bei Volatilität 
✓ Temporäre Spread-Erweiterungen
✓ Session-Gap-bedingte Sprünge
```
**Charakteristika:**
- Korrelieren mit bekannten Marktzeiten
- Betreffen mehrere Symbole gleichzeitig
- Kurze Dauer (Sekunden bis Minuten)
- Plausible Größenordnung

#### System-bedingte Fehler (WARNEN/FILTERN)
```
⚠ data_gap_major außerhalb Handelszeiten
⚠ time_regression (PC-Uhr-Probleme)
⚠ tick_unavailable (Connection-Issues)
⚠ Extreme Error-Häufungen (>10% in kurzer Zeit)
```
**Charakteristika:**
- Unregelmäßige Zeitpunkte
- Symbol-isoliert auftretend
- Lange Dauer oder wiederholend
- Technisch implausible Werte

### TODO: Importer-Algorithmus

#### 1. Pre-Import Health Check
```python
def validate_file_health(json_file):
    # Error-Rate-Analyse
    total_errors = sum(error_counts.values())
    error_rate = total_errors / total_ticks
    
    if error_rate > 0.05:  # >5% Error-Rate
        return "WARNING: High error rate detected"
    
    # System-Error-Detection
    system_errors = detect_system_errors(errors)
    if len(system_errors) > 0:
        return f"WARNING: {len(system_errors)} system-related errors"
    
    return "HEALTHY"
```

#### 2. Error-Pattern-Recognition
```python
def classify_error_origin(error, market_context):
    # Zeit-basierte Klassifizierung
    if is_news_time(error.timestamp):
        return "market_authentic"
    
    # Frequenz-basierte Klassifizierung  
    if error_frequency_suspicious(error.type):
        return "system_suspected"
        
    # Kontext-basierte Klassifizierung
    if has_concurrent_system_errors(error):
        return "system_suspected"
        
    return "market_authentic"
```

#### 3. Adaptive Import-Strategy
```python
def import_with_classification(json_file):
    health_status = validate_file_health(json_file)
    
    if health_status.startswith("WARNING"):
        log_warning(health_status)
        # Optional: User-Prompt für Import-Fortsetzung
    
    # Errors nach Authentizität klassifizieren
    for error in errors:
        error.origin = classify_error_origin(error, market_context)
        
    # Separate Behandlung im Framework
    market_errors = filter(lambda e: e.origin == "market_authentic", errors)
    system_errors = filter(lambda e: e.origin == "system_suspected", errors)
```

### Framework-Integration

#### Testing-Engine
```python
class BacktestEngine:
    def __init__(self, include_market_anomalies=True, filter_system_errors=True):
        self.include_market_anomalies = include_market_anomalies
        self.filter_system_errors = filter_system_errors
        
    def load_tick_data(self, files):
        for file in files:
            health = validate_file_health(file)
            if self.filter_system_errors:
                data = filter_system_errors(load_data(file))
            else:
                data = load_data(file)
```

#### Strategy-Testing
```python
# Test 1: Mit allen Markt-Realitäten
results_realistic = backtest(strategy, data, include_all_anomalies=True)

# Test 2: Nur mit system-gefilterten Daten  
results_clean = backtest(strategy, data, filter_system_errors=True)

# Vergleich zeigt Robustheit der Strategie
robustness_score = compare_results(results_realistic, results_clean)
```

### Implementierungs-Prioritäten

#### Phase 1: Basic Health Check
- [ ] Error-Rate-Schwellenwerte definieren
- [ ] Simple System-Error-Detection (data_gap, time_regression)
- [ ] Import-Warnings implementieren

#### Phase 2: Pattern Recognition
- [ ] News-Zeit-Datenbank für Kontext
- [ ] Error-Frequenz-Analyse
- [ ] Multi-Symbol-Korrelations-Check

#### Phase 3: Adaptive Framework
- [ ] Dynamische Error-Klassifizierung
- [ ] Strategy-Robustness-Testing
- [ ] Automated Data Quality Scoring

### Datenqualitäts-Metriken

```python
class DataQualityMetrics:
    market_authenticity_score: float  # Anteil echter Markt-Anomalien
    system_reliability_score: float   # 1 - (system_errors / total_ticks)
    testing_realism_score: float      # Balance zwischen Clean/Realistic
```

### Vorteile dieser Differenzierung

1. **Realistische Tests**: Markt-Anomalien bleiben für authentische Backtests
2. **Saubere Daten**: System-Fehler werden identifiziert und optional gefiltert
3. **Transparency**: User weiß was er testet (clean vs. realistic conditions)
4. **Robustness-Testing**: Strategien können gegen beide Szenarien getestet werden
5. **Quality Assurance**: Schlechte Datenqualität wird vor Import erkannt

---

*Dieses Dokument definiert die konzeptuelle Basis für intelligente Datenvalidierung im FiniexTestingIDE Importer.*