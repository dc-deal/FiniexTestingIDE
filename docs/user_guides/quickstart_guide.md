# Quickstart Guide: Create Your Trading Bot

This guide shows you how to create a custom trading bot with FiniexTestingIDE.

---

## Overview

A trading bot consists of three parts:

```
┌─────────────┐     ┌─────────────────┐     ┌────────────┐
│   Workers   │ --> │  Decision Logic │ --> │   Config   │
│  (Signals)  │     │    (Trading)    │     │   (JSON)   │
└─────────────┘     └─────────────────┘     └────────────┘
     RSI              Buy/Sell/Flat         Scenarios
   Envelope           Position Mgmt         Parameters
     MACD                                   Timeframes
```

| Component | Responsibility | Base Class |
|-----------|----------------|------------|
| **Worker** | Compute indicators from bar data | `AbstractWorker` |
| **Decision Logic** | Make trading decisions from worker results | `AbstractDecisionLogic` |
| **Config** | Connect workers + decision + scenarios | JSON file |

---

## Step 1: Understand the Worker

Workers compute technical indicators. They receive bar history and return a `WorkerResult`.

### Example: RSI Worker (simplified)

```python
# Location: python/framework/workers/core/rsi_worker.py

class RsiWorker(AbstractWorker):
    """RSI computation from bar close prices"""
    
    def __init__(
        self, 
        name: str, 
        parameters: Dict, 
        logger: ScenarioLogger,
        trading_context: TradingContext = None,
    ):
        super().__init__(
            name=name, 
            parameters=parameters, 
            logger=logger,
            trading_context=trading_context, 
        )
        # periods auto-extracted by AbstractWorker for INDICATOR type
    
    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR
    
    def get_warmup_requirements(self) -> Dict[str, int]:
        # Tell system how many bars we need before trading starts
        return self.periods  # e.g., {"M5": 14}
    
    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """Called every tick - compute RSI value"""
        
        timeframe = list(self.periods.keys())[0]  # e.g., "M5"
        period = self.periods[timeframe]           # e.g., 14
        
        bars = bar_history.get(timeframe, [])
        close_prices = np.array([bar.close for bar in bars[-period:]])
        
        # RSI calculation
        deltas = np.diff(close_prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))
        
        return WorkerResult(
            outputs={
                'rsi_value': float(rsi),
                'avg_gain': float(avg_gain),
                'avg_loss': float(avg_loss),
                'bars_used': float(len(close_prices)),
            }
        )
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `get_worker_type()` | Return `WorkerType.INDICATOR` |
| `get_warmup_requirements()` | Bars needed before trading: `{"M5": 14}` |
| `compute()` | Calculate indicator, return `WorkerResult` |
| `get_output_schema()` | Declare typed output fields (optional) |

---

### Parameter Schema (Input)

Workers declare their configurable parameters with type, range, and defaults via `get_parameter_schema()`.
This prevents silent configuration errors (e.g., `deviation: 0.02` instead of `2.0`).

```python
from python.framework.types.parameter_types import InputParamDef, REQUIRED

class EnvelopeWorker(AbstractWorker):

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        return {
            'deviation': InputParamDef(
                param_type=float,
                default=2.0,
                min_val=0.5,
                max_val=5.0,
                description='Band deviation percentage'
            ),
        }
```

| Field | Purpose |
|-------|---------|
| `param_type` | Python type (`float`, `int`, `bool`, `str`) |
| `default` | Default value. Use `REQUIRED` when parameter must be provided |
| `min_val` / `max_val` | Numeric bounds (inclusive) |
| `choices` | Allowed values for enum-style parameters |
| `description` | Functional description |

### Output Schema

Workers declare their output fields via `get_output_schema()`. This enables the UI and decision logics to understand worker outputs without hardcoded assumptions.

```python
from python.framework.types.parameter_types import OutputParamDef

class RsiWorker(AbstractWorker):

    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        return {
            'rsi_value': OutputParamDef(
                param_type=float, min_val=0.0, max_val=100.0,
                description='RSI value', category='SIGNAL', display=True,
            ),
            'avg_gain': OutputParamDef(param_type=float, description='Average gain'),
            'avg_loss': OutputParamDef(param_type=float, description='Average loss'),
            'bars_used': OutputParamDef(param_type=float, description='Number of bars used'),
        }
```

| Field | Purpose |
|-------|---------|
| `category` | `'SIGNAL'` (trading-relevant) or `'INFO'` (diagnostic, default) |
| `display` | `True` to show in Live Console UI (default `False`) |
| `choices` | Allowed values for enum-style outputs (e.g., trend direction) |

Workers return results via `WorkerResult(outputs={...})`. Decision Logics access values with `result.get_signal('key')`.

---

**Validation behavior** is controlled by `strict_parameter_validation` in `execution_config`:
- `true` (default): Abort on boundary violations
- `false`: Warning only (experimental mode)

> **Note:** `periods` is validated separately by `validate_config()` (structural validation).
> `get_parameter_schema()` covers algorithm parameters only (deviation, thresholds, etc.).

---

### Volume vs Tick Count

⚠️ **Critical difference between market types:**

| Market | `bar.volume` | `bar.tick_count` |
|--------|--------------|------------------|
| **Crypto** | ✅ Real trade volume (BTC, ETH, etc.) | ✅ Number of trades |
| **Forex** | ⚠️ Always 0 (CFD has no real volume) | ✅ Number of price changes |

**For volume-based indicators (OBV, VWAP, etc.):**
- Crypto: Works correctly
- Forex: Will be constant (volume = 0)

**Example: Market-aware warning in worker:**

```python
class ObvWorker(AbstractWorker):
    def __init__(self, name, parameters, logger, trading_context=None):
        super().__init__(name, parameters, logger, trading_context=trading_context)
        
        # Warn if Forex (volume will be 0)
        if trading_context and trading_context.market_type == MarketType.FOREX:
```


### TradingContext (Optional)

Workers receive an optional `TradingContext` with market metadata:

```python
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.market_types.market_config_types import MarketType

@dataclass
class TradingContext:
    broker_type: str      # e.g., 'mt5', 'kraken_spot'
    market_type: MarketType  # FOREX or CRYPTO
    symbol: str           # e.g., 'EURUSD', 'BTCUSD'
```

**Use cases:**
- Conditional logic based on market type
- Volume-aware indicators (see below)
- Broker-specific behavior

---

## Step 2: Understand the Decision Logic

Decision Logic receives all worker results and decides: BUY, SELL, or FLAT.

### Example: Aggressive Trend (simplified)

```python
# Location: python/framework/decision_logic/core/aggressive_trend.py

class AggressiveTrend(AbstractDecisionLogic):
    """
    Aggressive trend-following strategy.
    BUY when RSI < 35 OR price below envelope
    SELL when RSI > 65 OR price above envelope
    """
    
    def __init__(self, name: str, logger: ScenarioLogger, config: Dict[str, Any],
                 trading_context: TradingContext = None):
        super().__init__(name, logger, config, trading_context=trading_context)
        
        # Config values (defaults defined in get_parameter_schema())
        self.rsi_buy = self.params.get("rsi_buy_threshold")
        self.rsi_sell = self.params.get("rsi_sell_threshold")
        self.lot_size = self.params.get("lot_size")
    
    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]  # Only market orders for this example
    
    def get_required_worker_instances(self) -> Dict[str, str]:
        """Declare which workers this logic needs"""
        return {
            "rsi_fast": "CORE/rsi",
            "envelope_main": "CORE/envelope"
        }
    
    @classmethod
    def get_output_schema(cls) -> Dict[str, OutputParamDef]:
        """Declare typed decision outputs."""
        return {
            'confidence': OutputParamDef(
                param_type=float, min_val=0.0, max_val=1.0,
                description='Signal confidence score',
                category='SIGNAL', display=True,
            ),
            'reason': OutputParamDef(
                param_type=str,
                description='Human-readable decision explanation',
                category='INFO',
            ),
            'price': OutputParamDef(
                param_type=float, min_val=0.0,
                description='Price at decision time',
                category='INFO',
            ),
        }

    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
    ) -> Decision:
        """Generate trading decision from worker results"""

        rsi_result = worker_results.get("rsi_fast")
        envelope_result = worker_results.get("envelope_main")

        rsi_value = rsi_result.get_signal('rsi_value')
        envelope_position = envelope_result.get_signal('position')

        # BUY signal
        if rsi_value < self.rsi_buy or envelope_position < 0.25:
            return Decision(
                action=DecisionLogicAction.BUY,
                outputs={
                    'confidence': 0.8,
                    'reason': f"RSI={rsi_value:.1f}",
                    'price': tick.mid,
                },
            )

        # SELL signal
        if rsi_value > self.rsi_sell or envelope_position > 0.75:
            return Decision(
                action=DecisionLogicAction.SELL,
                outputs={
                    'confidence': 0.8,
                    'reason': f"RSI={rsi_value:.1f}",
                    'price': tick.mid,
                },
            )

        # No signal
        return Decision(
            action=DecisionLogicAction.FLAT,
            outputs={
                'confidence': 0.5,
                'reason': 'No signal',
                'price': tick.mid,
            },
        )

    def _execute_decision_impl(
        self,
        decision: Decision,
        tick: TickData
    ) -> Optional[OrderResult]:
        """Execute the trading decision"""

        if decision.action == DecisionLogicAction.FLAT:
            return None

        # Check existing positions (one position only)
        open_positions = self.trading_api.get_open_positions()

        if len(open_positions) > 0:
            current = open_positions[0]
            new_direction = (OrderDirection.LONG
                           if decision.action == DecisionLogicAction.BUY
                           else OrderDirection.SHORT)

            # Same direction? Skip
            if current.direction == new_direction:
                return None

            # Opposite direction? Close first
            self.trading_api.close_position(current.position_id)
            return None

        # Open new position
        direction = (OrderDirection.LONG
                    if decision.action == DecisionLogicAction.BUY
                    else OrderDirection.SHORT)

        return self.trading_api.send_order(
            symbol=tick.symbol,
            order_type=OrderType.MARKET,
            direction=direction,
            lots=self.lot_size,
            comment=f"AggressiveTrend: {decision.get_signal('reason')}"
        )
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `get_required_worker_instances()` | Declare workers: `{"rsi_fast": "CORE/rsi"}` |
| `get_required_order_types()` | Return `[OrderType.MARKET]` |
| `get_output_schema()` | Declare typed output fields (optional) |
| `compute()` | Analyze workers, return `Decision(action=..., outputs={...})` |
| `_execute_decision_impl()` | Execute trades via `trading_api` |

### AwarenessChannel — Narrate Your Algo

The AwarenessChannel lets your decision logic narrate what it's thinking.
It appears as a single status line in both the AutoTrader Live Display
and the Backtesting Live Progress — no log persistence, purely visual.

```python
from python.framework.types.decision_logic_types import AwarenessLevel

# Inside compute():
self.notify_awareness(
    f"No edge — RSI {rsi_value:.1f}",
    AwarenessLevel.INFO,
    'no_edge'
)
```

Three levels control icon and color:

| Level | Icon | Color | Use for |
|-------|------|-------|---------|
| `INFO` | `i` | dim grey | Normal state narration |
| `NOTICE` | `!` | yellow | Filter blocks, weak signals |
| `ALERT` | `!!` | red bold | Unusual conditions |

The call is optional — if your algo never calls `notify_awareness()`,
no line is rendered and cost is zero. Place calls in `compute()`,
not in `_execute_decision_impl()` (execution-layer events go through
OrderGuard, not the awareness channel).

---

## Step 3: Create the Config

The JSON config connects everything together.

### Example: Scenario Set Config

```json
{
  "version": "1.0",
  "scenario_set_name": "my_strategy_test",
  "global": {
    "data_mode": "realistic",
    "strategy_config": {
      "decision_logic_type": "CORE/aggressive_trend",
      "worker_instances": {
        "rsi_fast": "CORE/rsi",
        "envelope_main": "CORE/envelope"
      },
      "workers": {
        "rsi_fast": {
          "periods": { "M5": 14 }
        },
        "envelope_main": {
          "periods": { "M30": 20 },
          "deviation": 2.0
        }
      },
      "decision_logic_config": {
        "rsi_buy_threshold": 35,
        "rsi_sell_threshold": 65,
        "lot_size": 0.1
      }
    },
    "trade_simulator_config": {
      "broker_config_path": "./configs/brokers/mt5/ic_markets_demo.json",
      "balances": { "USD": 100000 },
      "seeds": {
        "inbound_latency_seed": 42
      }
    }
  },
  "scenarios": [
    {
      "name": "GBPUSD_test_01",
      "symbol": "GBPUSD",
      "start_date": "2025-10-09T20:00:00+00:00",
      "end_date": "2025-10-09T23:59:00+00:00",
      "max_ticks": 5000,
      "enabled": true
    }
  ]
}
```

### Config Structure

| Section | Purpose |
|---------|---------|
| `decision_logic_type` | Which decision logic to use |
| `worker_instances` | Map instance names to worker types |
| `workers` | Parameters for each worker instance |
| `decision_logic_config` | Parameters for the decision logic |
| `trade_simulator_config` | Broker, balance, seeds, latency ranges |
| `scenarios` | Time windows to test |

> **Tip:** `app_config.json → default_trade_simulator_config` provides application-wide defaults (balance, currency, seeds, latency ranges). Scenario sets inherit these automatically — only override what differs. See [Config Cascade Guide](../config_cascade_guide.md) for details.

---

## Step 4: Deploy Your Bot

Create a directory under `user_algos/` for your strategy and place your files there.

### Create a Worker

1. Create `user_algos/my_strategy/my_indicator.py`
2. Define one class inheriting from `AbstractWorker` (any class name)
3. Implement `compute()`, `get_warmup_requirements()`, `get_parameter_schema()`

See `python/framework/workers/core/rsi_worker.py` as a reference implementation.

### Create a Decision Logic

1. Create `user_algos/my_strategy/my_decision.py`
2. Define one class inheriting from `AbstractDecisionLogic` (any class name)
3. Implement `compute()`, `_execute_decision_impl()`, `get_required_worker_instances()`

### Reference in Config

```json
{
    "decision_logic_type": "user_algos/my_strategy/my_decision.py",
    "worker_instances": {
        "custom_ind": "user_algos/my_strategy/my_indicator.py",
        "rsi_filter": "CORE/rsi"
    }
}
```

Paths are relative to the project root. CORE workers use the `CORE/name` shorthand.

Worker references in `get_required_worker_instances()` are relative to the decision logic file:

```python
def get_required_worker_instances(self) -> Dict[str, str]:
    return {
        'custom_ind': 'my_indicator.py',   # same directory as this file
        'rsi_filter': 'CORE/rsi',
    }
```

### External Algo Directories (optional)

Keep your strategies in a separate repo — add the root to `user_configs/app_config.json`:

```json
{
    "paths": {
        "user_algo_dirs": ["user_algos/", "/path/to/external/algos"]
    }
}
```

Scenario configs inside those directories are discovered automatically. Workers and decision logics are loaded from the explicit paths in those configs.

### Error Handling

File not found, syntax errors, or wrong class count → `ValueError` with a clear message. Fix the file and restart (or `rescan()` in REPL mode).

> See [user_modules_and_hot_reload_mechanics.md](user_modules_and_hot_reload_mechanics.md) for the full technical reference including hot-reload, sys.modules invalidation, and external directory mechanics.

---

## Step 5: Run Your Backtest

### Option A: VS Code Launch Config

Add to `.vscode/launch.json`:

```json
{
    "name": "🔬 Run (my_strategy)",
    "type": "debugpy",
    "request": "launch",
    "program": "${workspaceFolder}/python/cli/strategy_runner_cli.py",
    "args": ["run", "my_strategy_test.json"],
    "console": "integratedTerminal",
    "justMyCode": false
}
```

### Option B: CLI

```bash
python python/cli/strategy_runner_cli.py run my_strategy_test.json
```

---

## Trading Rules (Alpha)

Current limitations:

| Rule | Description |
|------|-------------|
| **Order Types** | MARKET, STOP, STOP_LIMIT supported. LIMIT pending. |
| **Full Close** | Close entire position (no partial fills yet) |
| **Margin Check** | Orders rejected if insufficient margin |
| **Pending Mgmt** | `cancel_stop_order`, `modify_position` (SL/TP) available |

> **Multiple Positions:** The system supports multiple simultaneous positions, but this is **untested**. All included bots use single-position logic. Use at your own risk.

> **Broker compatibility:** STOP orders are not supported by all brokers (e.g. Kraken requires STOP_LIMIT). Use `get_required_order_types()` to declare order needs; the framework validates this at startup. Set `use_stop_limit: true` in `decision_logic_config` for Kraken scenarios.

---

## Available Workers (CORE)

| Worker | Type | Description |
|--------|------|-------------|
| `CORE/rsi` | RSI | Relative Strength Index |
| `CORE/envelope` | Envelope | Bollinger-style bands (`deviation`: 0.5–5.0, default 2.0) |
| `CORE/macd` | MACD | Moving Average Convergence Divergence |
| `CORE/obv` | OBV | On-Balance Volume (⚠️ Forex: volume always 0, works best with Crypto) |
| `CORE/backtesting/heavy_rsi` | Heavy RSI | RSI with artificial delay (testing) |
| `CORE/backtesting/backtesting_sample_worker` | Test-only: Mandatory worker for Decision Logic "backtesting_deterministic" |

---

## Available Decision Logics (CORE)

| Logic | Description |
|-------|-------------|
| `CORE/aggressive_trend` | OR-logic: RSI or Envelope triggers trade (MARKET orders) |
| `CORE/simple_consensus` | AND-logic: Both indicators must agree (MARKET orders) |
| `CORE/cautious_macd` | MACD crossover + RSI filter, STOP/STOP_LIMIT entry, SL/TP, break-even |
| `CORE/backtesting/backtesting_deterministic` | Test-only: Trades at fixed ticks |

---

## Next Steps

1. **Profile your data** - `📊 VOLATILITY PROFILE`
2. **Generate scenarios** - `📊 Scenario Generator - Generate Blocks`
3. **Run backtests** - `🔬 Run Scenario`
4. **Review results** - Check trade history and P&L

→ See [CLI Tools Guide](../cli_tools_guide.md) for all commands.

---

## Example: Create a Simple SMA Crossover

```python
# 1. Worker: SMA (you could also just use bars directly in decision)

class SMAWorker(AbstractWorker):
    def compute(self, tick, bar_history, current_bars):
        bars = bar_history.get("M5", [])
        closes = [b.close for b in bars[-self.period:]]
        sma = np.mean(closes)
        return WorkerResult(outputs={'sma_value': float(sma)})

# 2. Decision: Crossover

class SMACrossover(AbstractDecisionLogic):
    def get_required_worker_instances(self):
        return {"sma_fast": "CORE/sma", "sma_slow": "CORE/sma"}

    def compute(self, tick, worker_results):
        fast = worker_results["sma_fast"].get_signal('sma_value')
        slow = worker_results["sma_slow"].get_signal('sma_value')

        if fast > slow:
            return Decision(action=DecisionLogicAction.BUY, outputs={'reason': 'SMA cross-up'})
        elif fast < slow:
            return Decision(action=DecisionLogicAction.SELL, outputs={'reason': 'SMA cross-down'})
        return Decision(action=DecisionLogicAction.FLAT)

# 3. Config

{
    "worker_instances": {
        "sma_fast": "CORE/sma",
        "sma_slow": "CORE/sma"
    },
    "workers": {
        "sma_fast": {"periods": {"M5": 10}},
        "sma_slow": {"periods": {"M5": 50}}
    }
}
```

---

*Happy backtesting!* 🚀
