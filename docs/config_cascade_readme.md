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
      "decision_logic_type": "CORE/aggressive_trend",
      "worker_instances": {
        "rsi_fast": "CORE/rsi",
        "envelope_main": "CORE/envelope"
      },
      "workers": {
        "rsi_fast": {
          "period": 14,
          "timeframe": "M5"
        },
        "envelope_main": {
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
- ✅ **worker_instances** - Defines which worker instances exist (instance_name → worker_type)
- ✅ **workers** - Parameters for each worker instance (indexed by instance_name)
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
          "rsi_fast": {
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
- ✅ **Per-parameter merge** - `workers.rsi_fast.period: 5` overrides only period, not timeframe
- ✅ **worker_instances NOT overridable** - Worker architecture is global only
- ✅ **Automatic detection** - Override warnings logged if `warn_on_parameter_override: true`

---

## 🔗 The Cascade Chain

### Boot Sequence

```
┌──────────────────────────────────────────────────────────┐
│ 1. APP BOOT                                             │
│    AppConfigLoader.__init__()                           │
│    ├─ Load app_config.json                              │
│    ├─ Initialize singleton                              │
│    └─ Provide flags: warn_on_parameter_override, etc.   │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│ 2. SCENARIO LOADING                                     │
│    config_loader.load_config("eurusd_3_windows.json")  │
│    ├─ Load scenario_set.json                            │
│    ├─ Extract global configs                            │
│    │  ├─ global.strategy_config                         │
│    │  │  ├─ worker_instances (architecture)             │
│    │  │  └─ workers (parameters)                        │
│    │  ├─ global.execution_config                        │
│    │  └─ global.trade_simulator_config                  │
│    │                                                     │
│    └─ For each scenario:                                │
│       ├─ Merge configs (global + scenario overrides)    │
│       │  ├─ strategy_config.workers: Per-param merge    │
│       │  ├─ execution_config: Per-parameter merge       │
│       │  └─ trade_simulator_config: Per-parameter merge │
│       │                                                  │
│       ├─ Detect overrides (if warn_on_override)         │
│       │  └─ Log: ⚠️  Parameter overrides...             │
│       │                                                  │
│       └─ Create TestScenario with merged config         │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│ 3. SCENARIO EXECUTION                                   │
│    Each scenario runs with its merged configuration     │
└──────────────────────────────────────────────────────────┘
                          ↓
┌──────────────────────────────────────────────────────────┐
│ 4. SCENARIO SAVING (Optional)                           │
│    config_loader.save_config(scenarios, "output.json")  │
│    ├─ Extract global from scenarios[0]                  │
│    ├─ For each scenario:                                │
│    │  └─ Extract only overrides (vs global)             │
│    └─ Write JSON with minimal redundancy                │
└──────────────────────────────────────────────────────────┘
```

---

## ✅ Cascading Parameters

These parameters support **per-parameter inheritance and overrides**:

### 1. `strategy_config.workers` (Per-Parameter Merge)

Worker parameters cascade **per worker instance, per parameter**. Each worker instance's parameters merge independently.

**Global:**
```json
"worker_instances": {
  "rsi_fast": "CORE/rsi",
  "envelope_main": "CORE/envelope"
},
"workers": {
  "rsi_fast": {
    "period": 14,
    "timeframe": "M5"
  },
  "envelope_main": {
    "period": 20,
    "deviation": 0.02
  }
}
```

**Scenario Override:**
```json
"workers": {
  "rsi_fast": {
    "period": 5
  }
}
```

**Result (Merged):**
```json
"workers": {
  "rsi_fast": {
    "period": 5,        // ← FROM SCENARIO
    "timeframe": "M5"   // ← FROM GLOBAL
  },
  "envelope_main": {
    "period": 20,       // ← FROM GLOBAL (unchanged)
    "deviation": 0.02   // ← FROM GLOBAL (unchanged)
  }
}
```

**Override Log:**
```
⚠️  Parameter overrides in scenario 'EURUSD_window_02':
   └─ strategy_config.workers.rsi_fast.period: 14 → 5
```

**Important:** You cannot add new worker instances in scenarios. The `worker_instances` dict defines the architecture globally and cannot be overridden per scenario. You can only change parameters of existing instances.

---

### 2. `strategy_config.decision_logic_config` (Per-Parameter Merge)

DecisionLogic configuration parameters cascade individually. This allows fine-tuning strategy behavior per scenario.

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

Execution settings cascade individually, allowing performance testing with different configurations.

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

Trading simulator settings cascade individually, enabling testing across different account configurations.

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

### Strategy Config Architecture

```json
"strategy_config": {
  "decision_logic_type": "CORE/aggressive_trend",  // Global only
  "worker_instances": {                            // Global only
    "rsi_fast": "CORE/rsi",
    "envelope_main": "CORE/envelope"
  }
}
```

**These cannot be overridden per scenario** - they define the strategy architecture! The `worker_instances` dict determines which workers exist and their types. Scenarios can only modify parameters of these instances via the `workers` section, not change the architecture itself.

**Why?** Because the DecisionLogic's `get_required_worker_instances()` method declares a fixed contract. Changing which workers exist would break this contract.

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
   └─ strategy_config.workers.rsi_fast.period: 14 → 5
   └─ strategy_config.workers.rsi_fast.timeframe: M5 → M1
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
    global_config={'workers': {'rsi_fast': {'period': 14}}},
    scenario_config={'workers': {'rsi_fast': {'period': 5}}}
)

# Format for display
formatted = ParameterOverrideDetector.format_overrides_for_display(overrides)
# {'workers.rsi_fast.period': '14 → 5'}
```

---

## 📚 Examples

### Example 1: Testing Different RSI Periods

**Use Case:** Test strategy with fast RSI (period 5) vs standard RSI (period 14)

**Config:**
```json
{
  "global": {
    "strategy_config": {
      "decision_logic_type": "CORE/aggressive_trend",
      "worker_instances": {
        "rsi_fast": "CORE/rsi",
        "envelope_main": "CORE/envelope"
      },
      "workers": {
        "rsi_fast": {
          "period": 14,
          "timeframe": "M5"
        },
        "envelope_main": {
          "period": 20,
          "deviation": 0.02
        }
      }
    }
  },
  "scenarios": [
    {
      "name": "RSI_Fast_Period5",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "strategy_config": {
        "workers": {
          "rsi_fast": {
            "period": 5
          }
        }
      }
    },
    {
      "name": "RSI_Standard_Period14",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "strategy_config": {}  // Uses global (period: 14)
    },
    {
      "name": "RSI_Slow_Period21",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "strategy_config": {
        "workers": {
          "rsi_fast": {
            "period": 21
          }
        }
      }
    }
  ]
}
```

**Result:** 3 scenarios testing different RSI periods. All other parameters (timeframe, envelope settings) are inherited from global!

---

### Example 2: Multiple Worker Instances with Different Timeframes

**Use Case:** Strategy uses both fast RSI (M1) and slow RSI (M5) for trend confirmation

**Config:**
```json
{
  "global": {
    "strategy_config": {
      "decision_logic_type": "USER/dual_rsi_strategy",
      "worker_instances": {
        "rsi_fast": "CORE/rsi",
        "rsi_slow": "CORE/rsi",
        "envelope_main": "CORE/envelope"
      },
      "workers": {
        "rsi_fast": {
          "period": 14,
          "timeframe": "M1"
        },
        "rsi_slow": {
          "period": 14,
          "timeframe": "M5"
        },
        "envelope_main": {
          "period": 20,
          "deviation": 0.02
        }
      }
    }
  },
  "scenarios": [
    {
      "name": "Standard_M1_M5",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "strategy_config": {}  // Uses global config
    },
    {
      "name": "Aggressive_M1_M1",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "strategy_config": {
        "workers": {
          "rsi_slow": {
            "timeframe": "M1"  // Both on M1 now
          }
        }
      }
    },
    {
      "name": "Conservative_M5_M15",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "strategy_config": {
        "workers": {
          "rsi_fast": {
            "timeframe": "M5"
          },
          "rsi_slow": {
            "timeframe": "M15"
          }
        }
      }
    }
  ]
}
```

**Result:** Compare different timeframe combinations. Worker periods remain at 14, only timeframes change per scenario!

---

### Example 3: Sequential vs Parallel Testing

**Use Case:** Test same strategy with parallel ON and OFF to measure performance impact

**Config:**
```json
{
  "global": {
    "execution_config": {
      "parallel_workers": true,
      "worker_parallel_threshold_ms": 1.0,
      "adaptive_parallelization": true,
      "log_performance_stats": true
    }
  },
  "scenarios": [
    {
      "name": "Parallel_Enabled",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "execution_config": {}  // Uses global (parallel: true)
    },
    {
      "name": "Sequential_Only",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "execution_config": {
        "parallel_workers": false
      }
    }
  ]
}
```

**Result:** Compare performance with/without parallelization. All other execution settings (thresholds, logging) inherited!

---

### Example 4: Multi-Balance Testing

**Use Case:** Test strategy performance across different account sizes

**Config:**
```json
{
  "global": {
    "trade_simulator_config": {
      "broker_config_path": "./configs/brokers/mt5/ic_markets_demo.json",
      "initial_balance": 10000,
      "currency": "EUR"
    }
  },
  "scenarios": [
    {
      "name": "Micro_Account_1K",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "trade_simulator_config": {
        "initial_balance": 1000
      }
    },
    {
      "name": "Small_Account_5K",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "trade_simulator_config": {
        "initial_balance": 5000
      }
    },
    {
      "name": "Standard_Account_10K",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "trade_simulator_config": {}  // Uses global (10000)
    },
    {
      "name": "Large_Account_50K",
      "symbol": "EURUSD",
      "start_date": "2025-09-23",
      "end_date": "2025-09-24",
      "trade_simulator_config": {
        "initial_balance": 50000
      }
    }
  ]
}
```

**Result:** Test if strategy scales across account sizes. Broker config and currency are inherited!

---

## 💡 Best Practices

### 1. **Use Global for Common Settings**
```json
// ✅ GOOD - Define once
"global": {
  "strategy_config": {
    "worker_instances": {
      "rsi_fast": "CORE/rsi"
    },
    "workers": {
      "rsi_fast": {
        "period": 14,
        "timeframe": "M5"
      }
    }
  }
}

// ❌ BAD - Repeat in every scenario
"scenarios": [
  {"name": "S1", "strategy_config": {"workers": {"rsi_fast": {...}}}},
  {"name": "S2", "strategy_config": {"workers": {"rsi_fast": {...}}}},
  {"name": "S3", "strategy_config": {"workers": {"rsi_fast": {...}}}}
]
```

---

### 2. **Override Only What Changes**
```json
// ✅ GOOD - Only override period
"strategy_config": {
  "workers": {
    "rsi_fast": {
      "period": 5
    }
  }
}

// ❌ BAD - Repeat entire config
"strategy_config": {
  "workers": {
    "rsi_fast": {
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

### 5. **Use Descriptive Instance Names**
```json
// ✅ GOOD - Self-documenting names
"worker_instances": {
  "rsi_fast": "CORE/rsi",      // Period 5, M1
  "rsi_slow": "CORE/rsi",      // Period 21, M5
  "envelope_tight": "CORE/envelope",  // deviation 0.01
  "envelope_wide": "CORE/envelope"    // deviation 0.05
}

// ❌ BAD - Generic names
"worker_instances": {
  "rsi1": "CORE/rsi",
  "rsi2": "CORE/rsi",
  "env1": "CORE/envelope",
  "env2": "CORE/envelope"
}
```

---

### 6. **Document Override Intent**
```json
{
  "name": "EURUSD_ScalpingTest",
  "strategy_config": {
    "workers": {
      "rsi_fast": {
        "period": 5,      // Faster RSI for scalping
        "timeframe": "M1" // Tick-level sensitivity
      }
    }
  }
}
```

---

### 7. **Keep Architecture Global**
```json
// ✅ GOOD - Architecture in global
"global": {
  "strategy_config": {
    "decision_logic_type": "CORE/aggressive_trend",
    "worker_instances": {
      "rsi_fast": "CORE/rsi",
      "envelope_main": "CORE/envelope"
    }
  }
}

// ❌ BAD - Cannot override architecture per scenario
"scenarios": [
  {
    "strategy_config": {
      "worker_instances": {  // ❌ This won't work!
        "macd": "CORE/macd"  // ❌ Cannot add new workers
      }
    }
  }
]
```

**Why?** DecisionLogic declares required workers via `get_required_worker_instances()`. This contract is fixed and cannot change per scenario.

---

## 🎯 Summary

### The Three Levels

1. **`app_config.json`** - App flags, NOT part of cascade
2. **`scenario_set.json → global`** - Base values for all scenarios
3. **`scenario_set.json → scenarios[i]`** - Per-scenario overrides

### Cascading Rules

- ✅ **Per-parameter merge** - Only changed parameters override
- ✅ **Inheritance** - Unspecified parameters come from global
- ✅ **Worker instances** - Architecture is global, parameters cascade
- ✅ **Automatic detection** - Override warnings show what changed
- ✅ **Minimal JSON** - Save only overrides, not full config

### Worker System

- 🏗️ **worker_instances** - Defines architecture (global only, cannot override)
- ⚙️ **workers** - Parameters cascade per instance (can override individual params)
- 📝 **Instance names** - Use descriptive names (rsi_fast, rsi_slow, envelope_tight)
- 🔗 **Contract-driven** - DecisionLogic's `get_required_worker_instances()` defines requirements

### Key Benefits

- 🎯 **DRY** - Define once, inherit everywhere
- 🔍 **Visibility** - Know exactly what changes per scenario
- ⚡ **Fast iteration** - Test parameter variations easily
- 📊 **Parameter-centric** - Focus on what matters: parameter tuning
- 🏗️ **Type-safe** - Worker architecture validated against DecisionLogic contract

---

**For implementation details, see:**
- `python/framework/utils/parameter_override_detector.py`
- `python/scenario/config_loader.py`
- `python/configuration/app_config_loader.py`
- `python/framework/workers/worker_factory.py`
- `python/framework/decision_logic/abstract_decision_logic.py`