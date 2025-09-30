# Scenario Config System - Komplette Dokumentation

## JSON Config Format

### Basis-Struktur

```json
{
  "version": "1.0",
  "created": "2025-09-30T15:45:00",
  
  "global": {
    "data_mode": "realistic",
    "strategy_config": {
      "rsi_period": 14,
      "envelope_period": 20,
      "envelope_deviation": 0.02,
      "execution": {
        "parallel_workers": true,
        "artificial_load_ms": 5.0,
        "max_parallel_scenarios": 4
      }
    }
  },
  
  "scenarios": [
    {
      "name": "EURUSD_window_01",
      "symbol": "EURUSD",
      "start_date": "2025-09-20",
      "end_date": "2025-09-22",
      "max_ticks": 1000
    },
    {
      "name": "EURUSD_window_02",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-25",
      "max_ticks": 1000
    }
  ]
}
```

### Override-Mechanismus

Scenario-spezifische Settings überschreiben global:

```json
{
  "global": {
    "data_mode": "realistic",
    "strategy_config": {
      "execution": {
        "artificial_load_ms": 5.0
      }
    }
  },
  
  "scenarios": [
    {
      "name": "light_test",
      "symbol": "EURUSD",
      "start_date": "2025-09-25",
      "end_date": "2025-09-26",
      "max_ticks": 100,
      "strategy_config": {
        "execution": {
          "artificial_load_ms": 2.0  // Override: lighter load
        }
      }
    },
    {
      "name": "heavy_test",
      "symbol": "GBPUSD",
      "start_date": "2025-09-25",
      "end_date": "2025-09-26",
      "max_ticks": 1000
      // No override: uses global 5.0ms
    }
  ]
}
```

---

## Scenario Generator - Strategien

### 1. Time Windows (Standard)

Teilt Daten in gleich große Zeitfenster:

```python
generator = ScenarioGenerator(loader)

scenarios = generator.generate_from_symbol(
    symbol="EURUSD",
    strategy="time_windows",
    num_windows=5,        # 5 Zeitfenster
    window_days=2,        # Je 2 Tage
    ticks_per_window=1000 # 1000 Ticks pro Fenster
)
```

**Output:**
```
EURUSD_window_01: 2025-09-17 to 2025-09-19
EURUSD_window_02: 2025-09-20 to 2025-09-22
EURUSD_window_03: 2025-09-23 to 2025-09-25
EURUSD_window_04: 2025-09-26 to 2025-09-28
EURUSD_window_05: 2025-09-29 to 2025-10-01
```

### 2. Volatility-Based (Advanced)

Findet High/Low Volatility Perioden:

```python
scenarios = generator.generate_from_symbol(
    symbol="EURUSD",
    strategy="volatility",
    high_vol_threshold=0.02,  # 2% moves
    max_scenarios=10
)
```

**Output:**
```
EURUSD_high_vol_1: 2025-09-18 (NFP release)
EURUSD_high_vol_2: 2025-09-22 (ECB meeting)
EURUSD_low_vol_1: 2025-09-20 (quiet period)
```

### 3. Multi-Symbol Batch

Generiert für alle verfügbaren Symbole:

```python
scenarios = generator.generate_multi_symbol(
    symbols=None,  # All available
    scenarios_per_symbol=3,
    num_windows=3,
    window_days=2
)
```

**Output:**
```
24 scenarios total (8 symbols × 3 scenarios each):
EURUSD_window_01, EURUSD_window_02, EURUSD_window_03
GBPUSD_window_01, GBPUSD_window_02, GBPUSD_window_03
USDJPY_window_01, USDJPY_window_02, USDJPY_window_03
...
```

---

## Integration in strategy_runner_enhanced.py

### Vorher (Hardcoded):

```python
scenario01 = TestScenario(
    symbol="EURUSD",
    start_date="2025-09-25",
    end_date="2025-09-26",
    max_ticks=1000,
    data_mode="realistic",
    strategy_config={...},
    name="EURUSD_01_test",
)

orchestrator = BatchOrchestrator([scenario01], loader)
```

### Nachher (Config-Based):

```python
from python.framework.scenario_config import ScenarioConfigLoader

# Load scenarios from JSON
config_loader = ScenarioConfigLoader()
scenarios = config_loader.load_config("eurusd_3_windows.json")

# Run
orchestrator = BatchOrchestrator(scenarios, loader)
results = orchestrator.run(parallel=True, max_workers=4)
```

---

## CLI Integration (Future)

```bash
# Generate scenarios
python -m python.framework.scenario_generator \
  --symbol EURUSD \
  --strategy time_windows \
  --num-windows 5 \
  --output configs/scenarios/eurusd_5_windows.json

# Run from config
python python/strategy_runner_enhanced.py \
  --config configs/scenarios/eurusd_5_windows.json \
  --parallel

# Generate + Run in one go
python python/strategy_runner_enhanced.py \
  --generate-scenarios \
  --symbols EURUSD,GBPUSD,USDJPY \
  --windows 3 \
  --parallel
```

---

## Beispiel-Configs

### configs/scenarios/quick_test.json
```json
{
  "version": "1.0",
  "global": {
    "data_mode": "realistic",
    "max_ticks": 100,
    "strategy_config": {
      "execution": {
        "parallel_workers": false,
        "artificial_load_ms": 2.0
      }
    }
  },
  "scenarios": [
    {
      "name": "quick_eurusd",
      "symbol": "EURUSD",
      "start_date": "2025-09-25",
      "end_date": "2025-09-26"
    }
  ]
}
```

### configs/scenarios/heavy_batch.json
```json
{
  "version": "1.0",
  "global": {
    "data_mode": "realistic",
    "max_ticks": 1000,
    "strategy_config": {
      "execution": {
        "parallel_workers": true,
        "artificial_load_ms": 5.0,
        "max_parallel_scenarios": 8
      }
    }
  },
  "scenarios": [
    {"name": "EURUSD_w1", "symbol": "EURUSD", "start_date": "2025-09-17", "end_date": "2025-09-19"},
    {"name": "EURUSD_w2", "symbol": "EURUSD", "start_date": "2025-09-20", "end_date": "2025-09-22"},
    {"name": "GBPUSD_w1", "symbol": "GBPUSD", "start_date": "2025-09-17", "end_date": "2025-09-19"},
    {"name": "GBPUSD_w2", "symbol": "GBPUSD", "start_date": "2025-09-20", "end_date": "2025-09-22"},
    {"name": "USDJPY_w1", "symbol": "USDJPY", "start_date": "2025-09-17", "end_date": "2025-09-19"},
    {"name": "USDJPY_w2", "symbol": "USDJPY", "start_date": "2025-09-20", "end_date": "2025-09-22"},
    {"name": "AUDUSD_w1", "symbol": "AUDUSD", "start_date": "2025-09-17", "end_date": "2025-09-19"},
    {"name": "AUDUSD_w2", "symbol": "AUDUSD", "start_date": "2025-09-20", "end_date": "2025-09-22"}
  ]
}
```

---

## File Structure

```
configs/
└── scenarios/
    ├── quick_test.json              # Schneller Test (1 scenario)
    ├── heavy_batch.json             # 8 scenarios parallel
    ├── eurusd_5_windows.json        # Generated: 5 time windows
    ├── all_symbols_batch.json       # Generated: All symbols
    └── volatility_periods.json      # Generated: High vol periods
```

---

## Implementation Steps

### 1. Create scenario_config.py
```bash
touch python/framework/scenario_config.py
# Copy code from artifact
```

### 2. Create configs directory
```bash
mkdir -p configs/scenarios
```

### 3. Generate first config
```python
from python.framework.scenario_config import ScenarioGenerator, ScenarioConfigLoader
from python.data_worker.data_loader.core import TickDataLoader

loader = TickDataLoader("./data/processed/")
generator = ScenarioGenerator(loader)

# Generate 3 windows for EURUSD
scenarios = generator.generate_from_symbol(
    symbol="EURUSD",
    strategy="time_windows",
    num_windows=3,
    window_days=2
)

# Save
config_loader = ScenarioConfigLoader()
config_loader.save_config(scenarios, "eurusd_3_windows.json")
```

### 4. Update strategy_runner_enhanced.py
```python
# Add at top
from python.framework.scenario_config import ScenarioConfigLoader

def run_strategy_test(config_file: str = None):
    loader = TickDataLoader("./data/processed/")
    
    if config_file:
        # Load from config
        config_loader = ScenarioConfigLoader()
        scenarios = config_loader.load_config(config_file)
    else:
        # Fallback to hardcoded (backwards compatible)
        scenarios = [create_default_scenario()]
    
    orchestrator = BatchOrchestrator(scenarios, loader)
    return orchestrator.run(parallel=True, max_workers=4)
```

---

## Testing

### Test 1: Generate Config
```python
python -c "
from python.framework.scenario_config import *
from python.data_worker.data_loader.core import TickDataLoader

loader = TickDataLoader('./data/processed/')
gen = ScenarioGenerator(loader)
scenarios = gen.generate_from_symbol('EURUSD', num_windows=3)

cfg = ScenarioConfigLoader()
cfg.save_config(scenarios, 'test.json')
print(f'Generated: {len(scenarios)} scenarios')
"
```

### Test 2: Load & Run
```python
from python.framework.scenario_config import ScenarioConfigLoader
from python.framework.batch_orchestrator import BatchOrchestrator
from python.data_worker.data_loader.core import TickDataLoader

loader = TickDataLoader("./data/processed/")
config_loader = ScenarioConfigLoader()

scenarios = config_loader.load_config("eurusd_3_windows.json")
orchestrator = BatchOrchestrator(scenarios, loader)

results = orchestrator.run(parallel=True, max_workers=4)
print(f"Completed {len(results['results'])} scenarios")
```

---

## Benefits

1. **Separation of Concerns**: Config getrennt von Code
2. **Reproducibility**: Exakte Test-Scenarios versionierbar
3. **Automation**: Generator erstellt Configs automatisch
4. **Flexibility**: Einfaches Testen verschiedener Setups
5. **Scalability**: Von 1 bis 1000 Scenarios ohne Code-Änderung
