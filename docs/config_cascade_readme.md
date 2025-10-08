# FiniexTestingIDE - Configuration Cascade System

**Complete documentation of the hierarchical parameter override system**

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Configuration Hierarchy](#configuration-hierarchy)
3. [The Cascade Chain](#the-cascade-chain)
4. [Cascading Parameters](#cascading-parameters)
5. [Non-Cascading Parameters](#non-cascading-parameters)
6. [Override Detection](#override-detection)
7. [Examples](#examples)
8. [Best Practices](#best-practices)

---

## 🎯 Overview

FiniexTestingIDE uses a **three-level configuration hierarchy** to manage parameters:

1. **App Config** (`app_config.json`) - Application-wide settings and flags
2. **Scenario Set Global** (`scenario_set.json → global`) - Base values for all scenarios
3. **Scenario Overrides** (`scenario_set.json → scenarios[i]`) - Per-scenario parameter changes

This system enables:
- ✅ **DRY Configuration** - Define once, inherit everywhere
- ✅ **Selective Overrides** - Change only what differs per scenario
- ✅ **Clear Visibility** - Automatic detection and logging of parameter changes
- ✅ **Parameter-Centric Testing** - Easy to test multiple parameter combinations

---

## 📊 Configuration Hierarchy

### Level 1: `app_config.json` (App-Wide Settings)

**Location:** `configs/app_config.json`

**Purpose:** Application-wide defaults and feature flags

**Scope:** Entire application, **NOT part of scenario cascade**

```json
{
  "version": "1.0",
  "execution": {
    "default_parallel_workers": true,
    "default_max_parallel_scenarios": 20,
    "default_worker_parallel_threshold_ms": 1.0
  },
  "logging": {
    "warn_on_parameter_override": true,
    "performance_tracking": true,
    "show_worker_details": true
  },
  "paths": {
    "scenario_sets": "configs/scenario_sets",
    "data_raw": "data/raw",
    "data_processed": "data/processed"
  }
}
```

**Key Points:**
- ❌ **NOT inherited by scenarios** - This is app-level configuration
- ✅ **Loaded at app boot** - Singleton pattern (AppConfigLoader)
- ✅ **Controls behavior** - Flags like `warn_on_parameter_override`
- ✅ **Default paths** - Where to find scenario sets, data, etc.

---

### Level 2: `scenario_set.json → global` (Base Values)

**Location:** `configs/scenario_sets/{name}.json → global`

**Purpose:** Base parameter values inherited by all scenarios in this set

**Scope:** All scenarios in this scenario set

```json
{
  "global": {
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
      "worker_parallel_threshold_ms": 1.0,
      "adaptive_parallelization": true,
      "log_performance_stats": true
    },
    "trade_simulator_config": {
      "broker_config_path": "./configs/brokers/mt5/ic_markets_demo.json",
      "initial_balance": 10000,
      "currency": "EUR"
    }
  }
}
```

**Key Points:**
- ✅ **Inherited by all scenarios** - Base values for this scenario set
- ✅ **Can be overridden** - Scenarios can change individual parameters
- ✅ **Extracted from first scenario** - When saving, global config comes from scenarios[0]

---

### Level 3: `scenario_set.json → scenarios[i]` (Overrides)

**Location:** `configs/scenario_sets/{name}.json → scenarios[i]`

**Purpose:** Per-scenario parameter overrides

**Scope:** Individual scenario only

```json
{
  "scenarios": [
    {
      "name": "EURUSD_window_02",
      "symbol": "EURUSD",
      "start_date": "2025-09-19",
      "end_date": "2025-09-21",
      "max_ticks": 4000,
      "strategy_config": {
        "workers": {
          "CORE/rsi": {
            "period": 5,
            "timeframe": "M1"
          }
        },
        "decision_logic_config": {
          "min_confidence": 0.8
        }
      },
      "execution_config": {
        "parallel_workers": false
      },
      "trade_simulator_config": {}
    }
  ]
}
```

**Key Points:**
- ✅ **Only overrides** - Empty sections (`{}`) mean "use global"
- ✅ **Per-parameter merge** - `workers.CORE/rsi.period: 5` overrides only period, not timeframe
- ✅ **Automatic detection** - Override warnings logged if `warn_on_parameter_override: true`

---

## 🔗 The Cascade Chain

### Boot Sequence

```
┌─────────────────────────────────────────────────────────┐
│ 1. APP BOOT                                             │
│    AppConfigLoader.__init__()                           │
│    ├─ Load app_config.json                              │
│    ├─ Initialize singleton                              │
│    └─ Provide flags: warn_on_parameter_override, etc.   │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ 2. SCENARIO LOADING                                     │
│    config_loader.load_config("eurusd_3_windows.json")  │
│    ├─ Load scenario_set.json                            │
│    ├─ Extract global configs                            │
│    │  ├─ global.strategy_config                         │
│    │  ├─ global.execution_config                        │
│    │  └─ global.trade_simulator_config                  │
│    │                                                     │
│    └─ For each scenario:                                │
│       ├─ Merge configs (global + scenario overrides)    │
│       │  ├─ strategy_config: Per-parameter merge        │
│       │  ├─ execution_config: Per-parameter merge       │
│       │  └─ trade_simulator_config: Per-parameter merge │
│       │                                                  │
│       ├─ Detect overrides (if warn_on_override)         │
│       │  └─ Log: ⚠️  Parameter overrides...             │
│       │                                                  │
│       └─ Create TestScenario with merged config         │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ 3. SCENARIO EXECUTION                                   │
│    Each scenario runs with its merged configuration     │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ 4. SCENARIO SAVING (Optional)                           │
│    config_loader.save_config(scenarios, "output.json")  │
│    ├─ Extract global from scenarios[0]                  │
│    ├─ For each scenario:                                │
│    │  └─ Extract only overrides (vs global)             │
│    └─ Write JSON with minimal redundancy                │
└─────────────────────────────────────────────────────────┘
```

---

## ✅ Cascading Parameters

These parameters support **per-parameter inheritance and overrides**:

### 1. `strategy_config.workers` (Per-Parameter Merge)

**Global:**
```json
"workers": {
  "CORE/rsi": {
    "period": 14,
    "timeframe": "M5"
  }
}
```

**Scenario Override:**
```json
"workers": {
  "CORE/rsi": {
    "period": 5
  }
}
```

**Result (Merged):**
```json
"workers": {
  "CORE/rsi": {
    "period": 5,        // ← FROM SCENARIO
    "timeframe": "M5"   // ← FROM GLOBAL
  }
}
```

**Override Log:**
```
⚠️  Parameter overrides in scenario 'EURUSD_window_02':
   └─ strategy_config.workers.CORE/rsi.period: 14 → 5
```

---

### 2. `strategy_config.decision_logic_config` (Per-Parameter Merge)

**Global:**
```json
"decision_logic_config": {
  "rsi_oversold": 30,
  "rsi_overbought": 70,
  "min_confidence": 0.6
}
```

**Scenario Override:**
```json
"decision_logic_config": {
  "min_confidence": 0.8
}
```

**Result (Merged):**
```json
"decision_logic_config": {
  "rsi_oversold": 30,     // ← FROM GLOBAL
  "rsi_overbought": 70,   // ← FROM GLOBAL
  "min_confidence": 0.8   // ← FROM SCENARIO
}
```

**Override Log:**
```
⚠️  Parameter overrides in scenario 'EURUSD_window_02':
   └─ strategy_config.decision_logic_config.min_confidence: 0.6 → 0.8
```

---

### 3. `execution_config` (Per-Parameter Merge)

**Global:**
```json
"execution_config": {
  "parallel_workers": true,
  "worker_parallel_threshold_ms": 1.0,
  "adaptive_parallelization": true,
  "log_performance_stats": true
}
```

**Scenario Override:**
```json
"execution_config": {
  "parallel_workers": false
}
```

**Result (Merged):**
```json
"execution_config": {
  "parallel_workers": false,           // ← FROM SCENARIO
  "worker_parallel_threshold_ms": 1.0, // ← FROM GLOBAL
  "adaptive_parallelization": true,    // ← FROM GLOBAL
  "log_performance_stats": true        // ← FROM GLOBAL
}
```

**Override Log:**
```
⚠️  Parameter overrides in scenario 'EURUSD_window_02':
   └─ execution_config.parallel_workers: true → false
```

---

### 4. `trade_simulator_config` (Per-Parameter Merge)

**Global:**
```json
"trade_simulator_config": {
  "broker_config_path": "./configs/brokers/mt5/ic_markets_demo.json",
  "initial_balance": 10000,
  "currency": "EUR"
}
```

**Scenario Override:**
```json
"trade_simulator_config": {
  "initial_balance": 5000
}
```

**Result (Merged):**
```json
"trade_simulator_config": {
  "broker_config_path": "./configs/brokers/mt5/ic_markets_demo.json", // ← FROM GLOBAL
  "initial_balance": 5000, // ← FROM SCENARIO
  "currency": "EUR"        // ← FROM GLOBAL
}
```

**Override Log:**
```
⚠️  Parameter overrides in scenario 'EURUSD_window_02':
   └─ trade_simulator_config.initial_balance: 10000 → 5000
```

---

## ❌ Non-Cascading Parameters

These parameters are **scenario-specific only** - no inheritance:

### Top-Level Scenario Properties

```json
{
  "name": "EURUSD_window_02",          // Scenario-specific
  "symbol": "EURUSD",                  // Scenario-specific
  "start_date": "2025-09-19",          // Scenario-specific
  "end_date": "2025-09-21",            // Scenario-specific
  "max_ticks": 4000,                   // Scenario-specific
  "data_mode": "realistic",            // Scenario-specific
  "enabled": true                      // Scenario-specific
}
```

**These are NOT overrides** - they define what the scenario tests!

---

### Strategy Config Top-Level

```json
"strategy_config": {
  "decision_logic_type": "CORE/simple_consensus",  // Global only
  "worker_types": ["CORE/rsi", "CORE/envelope"]    // Global only
}
```

**These cannot be overridden per scenario** - they define the strategy architecture!

---

### App Config Settings

```json
// app_config.json
{
  "logging": {
    "warn_on_parameter_override": true,  // App-wide flag
    "log_level": "INFO"                  // App-wide setting
  }
}
```

**These are NOT part of scenario cascade** - they control application behavior!

---

## 🔍 Override Detection

### Automatic Detection

When `app_config.json → logging.warn_on_parameter_override: true`:

```python
# In config_loader.py:
ParameterOverrideDetector.detect_and_log_overrides(
    scenario_name=scenario_data['name'],
    global_strategy=global_strategy,
    global_execution=global_execution,
    global_trade_simulator=global_trade_simulator,
    scenario_strategy=scenario_data.get('strategy_config', {}),
    scenario_execution=scenario_data.get('execution_config', {}),
    scenario_trade_simulator=scenario_data.get('trade_simulator_config', {}),
    logger=vLog,
    warn_on_override=True
)
```

**Output:**
```
📂 Loading scenarios from: configs/scenario_sets/eurusd_3_windows.json
⚠️  Parameter overrides in scenario 'EURUSD_window_02':
   └─ strategy_config.workers.CORE/rsi.period: 14 → 5
   └─ strategy_config.workers.CORE/rsi.timeframe: M5 → M1
   └─ strategy_config.decision_logic_config.min_confidence: 0.6 → 0.8
   └─ execution_config.parallel_workers: true → false
✅ Loaded 1 scenarios from eurusd_3_windows.json
```

---

### Manual Detection

```python
from python.framework.utils.parameter_override_detector import ParameterOverrideDetector

# Detect overrides
overrides = ParameterOverrideDetector.detect_overrides(
    global_config={'workers': {'CORE/rsi': {'period': 14}}},
    scenario_config={'workers': {'CORE/rsi': {'period': 5}}}
)

# Format for display
formatted = ParameterOverrideDetector.format_overrides_for_display(overrides)
# {'workers.CORE/rsi.period': '14 → 5'}
```

---

## 📚 Examples

### Example 1: Testing Different RSI Periods

**Use Case:** Test strategy with RSI periods 5, 14, 21

**Config:**
```json
{
  "global": {
    "strategy_config": {
      "workers": {
        "CORE/rsi": {"period": 14, "timeframe": "M5"}
      }
    }
  },
  "scenarios": [
    {
      "name": "RSI_5",
      "strategy_config": {
        "workers": {"CORE/rsi": {"period": 5}}
      }
    },
    {
      "name": "RSI_14",
      "strategy_config": {}  // Uses global (period: 14)
    },
    {
      "name": "RSI_21",
      "strategy_config": {
        "workers": {"CORE/rsi": {"period": 21}}
      }
    }
  ]
}
```

**Result:** 3 scenarios testing different RSI periods, timeframe inherited!

---

### Example 2: Sequential vs Parallel Testing

**Use Case:** Test same strategy with parallel ON and OFF

**Config:**
```json
{
  "global": {
    "execution_config": {
      "parallel_workers": true
    }
  },
  "scenarios": [
    {
      "name": "Parallel_ON",
      "execution_config": {}  // Uses global (parallel: true)
    },
    {
      "name": "Parallel_OFF",
      "execution_config": {
        "parallel_workers": false
      }
    }
  ]
}
```

**Result:** Compare performance with/without parallelization!

---

### Example 3: Multi-Balance Testing

**Use Case:** Test strategy performance across different account sizes

**Config:**
```json
{
  "global": {
    "trade_simulator_config": {
      "initial_balance": 10000,
      "currency": "EUR"
    }
  },
  "scenarios": [
    {
      "name": "Balance_1K",
      "trade_simulator_config": {"initial_balance": 1000}
    },
    {
      "name": "Balance_5K",
      "trade_simulator_config": {"initial_balance": 5000}
    },
    {
      "name": "Balance_10K",
      "trade_simulator_config": {}  // Uses global (10000)
    },
    {
      "name": "Balance_50K",
      "trade_simulator_config": {"initial_balance": 50000}
    }
  ]
}
```

**Result:** Test if strategy scales across account sizes, currency inherited!

---

## 💡 Best Practices

### 1. **Use Global for Common Settings**
```json
// ✅ GOOD - Define once
"global": {
  "strategy_config": {
    "workers": {
      "CORE/rsi": {"period": 14, "timeframe": "M5"}
    }
  }
}

// ❌ BAD - Repeat in every scenario
"scenarios": [
  {"name": "S1", "strategy_config": {"workers": {"CORE/rsi": {...}}}},
  {"name": "S2", "strategy_config": {"workers": {"CORE/rsi": {...}}}},
  {"name": "S3", "strategy_config": {"workers": {"CORE/rsi": {...}}}}
]
```

---

### 2. **Override Only What Changes**
```json
// ✅ GOOD - Only override period
"strategy_config": {
  "workers": {
    "CORE/rsi": {"period": 5}
  }
}

// ❌ BAD - Repeat entire config
"strategy_config": {
  "workers": {
    "CORE/rsi": {
      "period": 5,
      "timeframe": "M5"  // ← Unnecessary! Same as global
    }
  }
}
```

---

### 3. **Use Empty Objects for "Use Global"**
```json
// ✅ GOOD - Explicit "no overrides"
"execution_config": {}

// ❌ BAD - Omitting key (unclear if intentional)
// "execution_config" not present in JSON
```

---

### 4. **Enable Override Warnings**
```json
// app_config.json
"logging": {
  "warn_on_parameter_override": true  // ← See what changes!
}
```

**Benefit:** Catch unintentional overrides, understand parameter flow!

---

### 5. **Document Override Intent**
```json
{
  "name": "EURUSD_FastRSI",
  "strategy_config": {
    "workers": {
      "CORE/rsi": {
        "period": 5  // Testing faster RSI for scalping
      }
    }
  }
}
```

---

## 🎯 Summary

### The Three Levels

1. **`app_config.json`** - App flags, NOT part of cascade
2. **`scenario_set.json → global`** - Base values for all scenarios
3. **`scenario_set.json → scenarios[i]`** - Per-scenario overrides

### Cascading Rules

- ✅ **Per-parameter merge** - Only changed parameters override
- ✅ **Inheritance** - Unspecified parameters come from global
- ✅ **Automatic detection** - Override warnings show what changed
- ✅ **Minimal JSON** - Save only overrides, not full config

### Key Benefits

- 🎯 **DRY** - Define once, inherit everywhere
- 🔍 **Visibility** - Know exactly what changes per scenario
- ⚡ **Fast iteration** - Test parameter variations easily
- 📊 **Parameter-centric** - Focus on what matters: parameter tuning

---

**For implementation details, see:**
- `python/framework/utils/parameter_override_detector.py`
- `python/scenario/config_loader.py`
- `python/configuration/app_config_loader.py`
