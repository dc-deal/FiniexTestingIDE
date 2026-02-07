# Worker Naming & Requirements System

## Overview

This document explains how the framework handles worker instances and their integration with DecisionLogic strategies. The system uses a **direct contract model**: your DecisionLogic explicitly declares which worker instances it needs (with exact names and types), and the configuration must fulfill this contract exactly.

**TL;DR**: DecisionLogic declares exact worker instances it needs. Config must match exactly. No aliases, no mapping, direct contract.

The key benefit of this approach is clarity and type safety - there's no hidden mapping layer, and you always know exactly which worker instance your logic is using. This is especially important when you need multiple instances of the same worker type with different parameters (e.g., a fast and slow RSI).

---

## Namespace System

The framework organizes workers and DecisionLogics using a namespace system. Each component has a type identifier that consists of a namespace prefix and a component name, separated by a forward slash (`/`).

Workers and DecisionLogics use namespaces with `/` separator:

| Namespace | Location | Description |
|-----------|----------|-------------|
| `CORE/` | `python/framework/workers/core/`<br>`python/framework/decision_logic/core/` | Framework-provided |
| `USER/` | `python/workers/user/`<br>`python/decision_logic/user/` | Your custom implementations |
| `BLACKBOX/` | *Feature-gated, not available yet* | Encrypted/compiled workers |

**Example**: `CORE/rsi`, `USER/my_custom_macd`, `CORE/aggressive_trend`

This separation allows you to extend the framework with your own implementations while keeping them organized and avoiding naming conflicts with core components. The factory system automatically loads components from the correct directories based on their namespace.

---

## How It Works: The Contract Model

The worker requirement system follows a three-step contract model that ensures type safety and clear dependencies. Let's walk through each step to understand how DecisionLogic and configuration work together.

### 1. DecisionLogic Declares Requirements

```python
# python/decision_logic/user/my_strategy.py
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic

class MyStrategy(AbstractDecisionLogic):
    
    def get_required_worker_instances(self) -> Dict[str, str]:
        """Define exact worker instances needed"""
        return {
            "rsi_fast": "CORE/rsi",
            "rsi_slow": "CORE/rsi",
            "envelope_main": "CORE/envelope"
        }
    
    def compute(self, worker_results, tick, bar_history, current_bars):
        # Use instance names directly
        rsi_fast = worker_results.get("rsi_fast")
        rsi_slow = worker_results.get("rsi_slow")
        envelope = worker_results.get("envelope_main")
        
        # Your logic here...
```

**Key points:**
- Instance names use `snake_case` for consistency and readability
- Multiple instances of same type allowed (e.g., two RSI with different settings) - this is a powerful feature that lets you compare different parameter combinations
- Names are arbitrary but must be consistent with config - you choose the names that make sense for your strategy

Notice how we declare both the instance name and the required worker type. This creates an explicit contract: "I need a worker instance called `rsi_fast` and it must be of type `CORE/rsi`." The framework validates this contract during initialization, catching configuration errors early before any trading logic runs.

---

### 2. Config Must Match Contract

Now that the DecisionLogic has declared its requirements, the configuration file must fulfill this contract. Every instance name declared in `get_required_worker_instances()` must appear in the `worker_instances` section with the correct type, and you must provide the necessary parameters for each instance in the `workers` section.

```json
{
  "decision_logic_type": "USER/my_strategy",
  "worker_instances": {
    "rsi_fast": "CORE/rsi",
    "rsi_slow": "CORE/rsi",
    "envelope_main": "CORE/envelope"
  },
  "workers": {
    "rsi_fast": {
      "period": 14,
      "timeframe": "M5"
    },
    "rsi_slow": {
      "period": 30,
      "timeframe": "M15"
    },
    "envelope_main": {
      "period": 20,
      "deviation": 2.0
    }
  }
}
```

**Validation rules:**
- ✅ All instance names from `get_required_worker_instances()` must exist in `worker_instances`
- ✅ Worker types must match exactly - no type override allowed (if DecisionLogic says `CORE/rsi`, you cannot substitute `CORE/macd`)
- ✅ Parameters are validated by WorkerFactory based on each worker's contract (required vs. optional parameters)

The validation happens during system initialization, before any market data is processed. If there's a mismatch between what your DecisionLogic requires and what the config provides, you'll get a clear error message explaining exactly what's wrong.

Notice how we can have two RSI workers (`rsi_fast` and `rsi_slow`) with completely different parameters. This is the power of instance-based configuration - you're not limited to one configuration per worker type.

---

## Creating Custom Workers

If the framework's built-in workers don't meet your needs, you can create custom workers. Custom workers live in `python/workers/user/` and are automatically discovered by the factory when you use the `USER/` namespace.

Here's the minimal structure for a custom worker:

```python
# python/workers/user/my_indicator.py
from python.framework.workers.abstract_blackbox_worker import AbstractBlackboxWorker
from python.framework.types.global_types import WorkerContract, WorkerType

class MyIndicatorWorker(AbstractBlackboxWorker):
    
    def __init__(
        self, 
        name: str, 
        parameters: Dict = None,
        logger: ScenarioLogger = None,
        trading_context: TradingContext = None, 
        **kwargs
    ):
        super().__init__(
            name=name, 
            parameters=parameters, 
            logger=logger,
            trading_context=trading_context,
            **kwargs
        )
        
        # Access market info if needed
        self._trading_context = trading_context

        params = parameters or {}
        self.my_param = params.get('my_param')
    
    @classmethod
    def get_parameter_schema(cls) -> Dict[str, ParameterDef]:
        """Declare parameters with type safety and validation ranges."""
        from python.framework.types.parameter_types import ParameterDef, REQUIRED
        return {
            'my_param': ParameterDef(
                param_type=int,
                default=REQUIRED,
                min_val=1,
                max_val=200,
                description="My custom parameter"
            ),
        }

    def should_recompute(self, tick, bar_updated):
        return bar_updated
    
    def compute(self, tick, bar_history, current_bars):
        # Your computation here
        pass
```

**Register in Factory** (for USER namespace):
- Factory auto-loads from `python/workers/user/` when you reference `USER/my_indicator`
- Class name convention: `MyIndicatorWorker` for `my_indicator` (camelCase class name, snake_case type name)
- The worker becomes available immediately - no manual registration needed

The parameter schema system is key here: by declaring `get_parameter_schema()` with `ParameterDef` entries, you tell the factory what configuration your worker needs — including types, ranges, and defaults. The factory validates this statically in Phase 3 and again at instantiation in Phase 6, so configuration errors are caught before any data is loaded or any tick is processed.

Once your worker is created, you can use it in any DecisionLogic by referencing it as `USER/my_indicator` in the `get_required_worker_instances()` method.

---

## Common Issues & Solutions

Even with validation, there are a few common mistakes that users encounter. Here are the most frequent issues and how to fix them quickly.

### ❌ Type Mismatch
```python
# DecisionLogic requires:
{"rsi_fast": "CORE/rsi"}

# Config has:
{"rsi_fast": "CORE/macd"}  # ❌ Type override not allowed!
```
**Fix**: Match the type exactly or change DecisionLogic requirements.

This error means your DecisionLogic has a strict contract that expects a specific worker type, but your configuration is trying to substitute a different type. The system prevents this to avoid unexpected behavior - if your logic is written to process RSI values, receiving MACD data instead would break the strategy.

---

### ❌ Missing Instance
```python
# DecisionLogic requires:
{"rsi_fast": "CORE/rsi"}

# Config missing:
{"rsi_slow": "CORE/rsi"}  # ❌ Wrong name!
```
**Fix**: Use exact instance name from `get_required_worker_instances()`.

Instance names must match exactly - the system doesn't try to "guess" what you meant. This strictness prevents subtle bugs where you think you're using one worker configuration but are actually using another. If your DecisionLogic declares `rsi_fast`, your config must have `rsi_fast` with that exact spelling.

---

### ❌ Unknown Worker Type
```json
{"my_worker": "CORE/nonexistent"}  // ❌ Worker doesn't exist
```
**Fix**: Check available workers in Factory registry or implement custom worker.

This happens when you reference a worker type that doesn't exist. Either you have a typo in the namespace/name (e.g., `CORE/rsi` vs `CORE/RSI`), or the worker hasn't been implemented yet. For custom workers, make sure the file exists in the correct directory and the class name follows the naming convention.

---

### ❌ Missing Required Parameters
```json
{
  "worker_instances": {"macd_main": "CORE/macd"},
  "workers": {
    "macd_main": {}
  }
}
```
**Fix**: Check worker's `get_parameter_schema()` for `REQUIRED` parameters and provide them.

Each worker declares its parameters via `get_parameter_schema()`. Parameters with `default=REQUIRED` must be provided in config. The factory validates this in Phase 3 (static) and Phase 6 (instantiation). Example: MACDWorker requires `fast_period`, `slow_period`, and `signal_period`.


### ❌ Parameter Out of Range
```json
{
  "workers": {
    "envelope_main": {
      "periods": { "M5": 20 },
      "deviation": 2.0
    }
  }
}
```
**Fix**: `deviation=2.0` is below minimum `0.5`. Likely meant `2.0`. Check `get_parameter_schema()` for valid ranges. With `strict_parameter_validation: true` (default), this aborts. Set to `false` in `execution_config` for warning-only mode.
