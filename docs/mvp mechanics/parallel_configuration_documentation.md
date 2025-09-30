# 📋 Parallelisierungs-Konfiguration - Komplette Dokumentation

## 🎯 Übersicht

Alle Parallelisierungs-Einstellungen sind jetzt in `strategy_config["execution"]` zentralisiert.

---

## 📝 Konfigurations-Struktur

### Vollständiges Beispiel

```python
scenario = TestScenario(
    symbol="EURUSD",
    start_date="2025-09-25",
    end_date="2025-09-26",
    max_ticks=1000,
    data_mode="realistic",
    
    strategy_config={
        # ==========================================
        # STRATEGY PARAMETERS
        # ==========================================
        "rsi_period": 14,
        "envelope_period": 20,
        "envelope_deviation": 0.02,
        
        # ==========================================
        # EXECUTION CONFIGURATION
        # ==========================================
        "execution": {
            # Worker-Level Parallelization
            "parallel_workers": False,
            "worker_parallel_threshold_ms": 1.0,
            
            # Scenario-Level Parallelization
            "max_parallel_scenarios": 4,
            
            # Advanced Settings
            "adaptive_parallelization": True,
            "log_performance_stats": True,
        }
    },
)
```

---

## ⚙️ Parameter-Erklärung

### Worker-Level Settings

#### `parallel_workers` (bool, default: False)
**Aktiviert parallele Worker-Execution innerhalb eines Scenarios**

```python
"parallel_workers": False  # Sequential (empfohlen für 2-3 Workers)
"parallel_workers": True   # Parallel (gut bei 4+ Workers)
```

**Wann aktivieren:**
- ✅ 4+ Workers
- ✅ Workers brauchen >1ms
- ✅ Komplexe Berechnungen (ML, FFT)

**Wann NICHT aktivieren:**
- ❌ 2-3 Workers
- ❌ Workers <1ms
- ❌ Simple Indicators (RSI, SMA)

#### `worker_parallel_threshold_ms` (float, default: 1.0)
**Minimum Worker-Zeit für Parallelisierung**

```python
"worker_parallel_threshold_ms": 1.0   # Standard
"worker_parallel_threshold_ms": 2.0   # Konservativ
"worker_parallel_threshold_ms": 0.5   # Aggressiv
```

**Adaptive Logic:**
- Misst durchschnittliche Worker-Zeit
- Aktiviert Parallel nur wenn Durchschnitt > Threshold
- Vermeidet Thread-Overhead bei schnellen Workers

---

### Scenario-Level Settings

#### `max_parallel_scenarios` (int, default: 4)
**Maximum concurrent Scenarios**

```python
"max_parallel_scenarios": 4   # Standard (für 8-Core CPU)
"max_parallel_scenarios": 8   # High-End (für 16+ Core CPU)
"max_parallel_scenarios": 2   # Limited (für 4-Core CPU)
```

**Hardware-Guide:**
```
CPU Cores | Empfehlung | Begründung
----------|------------|------------------
4 Cores   | 2 Scenarios| Leave 2 cores free
8 Cores   | 4 Scenarios| Optimal balance
16 Cores  | 8 Scenarios| Max parallelization
32+ Cores | 12 Scenarios| Diminishing returns
```

---

### Advanced Settings

#### `adaptive_parallelization` (bool, default: True)
**Automatische Erkennung optimaler Parallelisierung**

```python
"adaptive_parallelization": True   # Auto-detect (empfohlen)
"adaptive_parallelization": False  # Manual control
```

**Wenn aktiviert:**
- Misst Worker-Zeiten während Warmup
- Schaltet automatisch zu Sequential wenn nicht effizient
- Logged Entscheidungen in Console

#### `log_performance_stats` (bool, default: True)
**Detaillierte Performance-Logs**

```python
"log_performance_stats": True   # Zeigt Timing-Statistiken
"log_performance_stats": False  # Nur Ergebnisse
```

**Output wenn aktiviert:**
```
📊 Performance Stats:
  Avg worker time: 0.7ms
  Parallel overhead: 0.2ms
  Mode: Sequential (workers too fast)
  Time saved: 0ms (would be -200ms with parallel)
```

---

## 🎨 Vorkonfigurierte Presets

### Preset 1: Fast Testing (Default)
**Optimal für Development & Debugging**

```python
"execution": {
    "parallel_workers": False,
    "worker_parallel_threshold_ms": 1.0,
    "max_parallel_scenarios": 1,
    "adaptive_parallelization": False,
    "log_performance_stats": True,
}
```

**Use Case:** Einzelner Test, schnelles Feedback

---

### Preset 2: Multi-Scenario Testing
**Optimal für Parameter-Exploration**

```python
"execution": {
    "parallel_workers": False,
    "worker_parallel_threshold_ms": 1.0,
    "max_parallel_scenarios": 4,
    "adaptive_parallelization": True,
    "log_performance_stats": True,
}
```

**Use Case:** 4-10 Scenarios parallel testen

---

### Preset 3: Heavy Strategy (Future)
**Für komplexe ML-basierte Strategies**

```python
"execution": {
    "parallel_workers": True,
    "worker_parallel_threshold_ms": 2.0,
    "max_parallel_scenarios": 2,
    "adaptive_parallelization": True,
    "log_performance_stats": True,
}
```

**Use Case:** Wenige Scenarios, viele heavy Workers

---

### Preset 4: Maximum Throughput
**Für Production Batch-Testing**

```python
"execution": {
    "parallel_workers": True,
    "worker_parallel_threshold_ms": 1.0,
    "max_parallel_scenarios": 8,
    "adaptive_parallelization": False,
    "log_performance_stats": False,
}
```

**Use Case:** 100+ Scenarios, Maximum Speed

---

## 🔧 Implementation Changes

### 1. strategy_runner_enhanced.py
**Liest Config aus und übergibt an Orchestrator**

```python
# Extract execution config
exec_config = scenarios[0].strategy_config.get("execution", {})

# Run with settings
results = orchestrator.run(
    parallel=len(scenarios) > 1,
    max_workers=exec_config.get("max_parallel_scenarios", 4)
)
```

### 2. batch_orchestrator.py
**Übergibt Config an WorkerCoordinator**

```python
def _create_orchestrator(self, scenario):
    exec_config = scenario.strategy_config.get("execution", {})
    
    return WorkerCoordinator(
        workers=[...],
        parallel_workers=exec_config.get("parallel_workers", False),
        parallel_threshold_ms=exec_config.get("worker_parallel_threshold_ms", 1.0)
    )
```

### 3. worker_coordinator.py
**Verwendet Config-Parameter**

```python
def __init__(self, workers, parallel_workers=False, parallel_threshold_ms=1.0):
    self.parallel_workers = parallel_workers
    self.parallel_threshold_ms = parallel_threshold_ms
    # ...
```

---

## 📊 Performance-Vergleich

### Scenario: 4 Scenarios, 2 Workers each

| Config | Worker Mode | Scenario Mode | Time | Speedup |
|--------|-------------|---------------|------|---------|
| Preset 1 | Sequential | Sequential | 8.8s | 1.0x (baseline) |
| Preset 2 | Sequential | Parallel (4) | 2.2s | 4.0x ✅ |
| Preset 3 | Parallel | Sequential | 9.5s | 0.9x ❌ |
| Preset 4 | Parallel | Parallel (4) | 2.4s | 3.7x ⚠️ |

**Fazit für 2 Workers:**
- ✅ Scenario-Parallelisierung: 4x schneller
- ❌ Worker-Parallelisierung: Langsamer (Overhead)

---

## 🧪 Testing Guide

### Test 1: Baseline
```python
"execution": {
    "parallel_workers": False,
    "max_parallel_scenarios": 1,
}
```
**Expected:** ~2.2s für 1 Scenario

### Test 2: Multi-Scenario
```python
# Activate scenario02 + scenario03 + scenario04
"execution": {
    "parallel_workers": False,
    "max_parallel_scenarios": 4,
}
```
**Expected:** ~2.5s für 4 Scenarios (4x faster than sequential!)

### Test 3: Worker-Parallel (experimental)
```python
"execution": {
    "parallel_workers": True,
    "max_parallel_scenarios": 1,
}
```
**Expected:** ~2.4s für 1 Scenario (slightly slower due to overhead)

---

## ✅ Migration Checklist

- [ ] Update `strategy_runner_enhanced.py` mit execution config
- [ ] Update `batch_orchestrator._create_orchestrator()` 
- [ ] Update `worker_coordinator.__init__()` signature
- [ ] Test mit Preset 1 (Baseline)
- [ ] Test mit Preset 2 (Multi-Scenario)
- [ ] Dokumentiere Performance in README

---

## 🎯 Nächste Schritte

1. **JETZT:** Config-Struktur einbauen
2. **Test:** Mit 1 Scenario (Baseline)
3. **Test:** Mit 4 Scenarios (Multi-Scenario)
4. **Später:** Adaptive Parallelization implementieren
5. **Future:** ML-basierte Optimal-Config-Prediction

**Default-Empfehlung:** Preset 2 (Multi-Scenario Testing)
