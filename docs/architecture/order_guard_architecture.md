# Order Guard Architecture

## Overview

The OrderGuard is a spam protection layer inside `DecisionTradingApi` that prevents rejection storms **before** orders reach the executor. It is the universal gateway for all decision logics (CORE and USER), covering both backtesting and live trading.

Its single responsibility is the **Rejection Cooldown** — blocking a direction after N consecutive broker rejections for a configurable period, preventing rejection spam (e.g. repeated INSUFFICIENT_MARGIN attempts).

The OrderGuard lives in the intermediate layer between DecisionLogic and executor. It does not enforce business rules, does not know about market types, and does not know about balances. Structural validation (market type, balance, order type compatibility) belongs in the executor.

### Clock / Time Source

The guard is clock-agnostic — cooldown methods take an explicit `now: datetime` parameter supplied by `DecisionTradingApi` via `executor.get_current_time()`. This resolves to:

- **Backtesting:** the current tick's simulated timestamp. Cooldowns advance with simulated market time, which keeps them deterministic across runs and sim-correct under accelerated playback.
- **AutoTrader:** the broker-delivered tick timestamp (effectively wall-clock real time).

The guard itself never calls `datetime.now()`. This is the reason cooldowns behave identically for a backtest that compresses weeks of ticks into seconds and for a live run where ticks arrive in real time.

> **Relation to Safety Circuit Breaker:** See [Two Independent Safety Layers](#two-independent-safety-layers) at the end of this document. Full Safety architecture: [safety_circuit_breaker_architecture.md](safety_circuit_breaker_architecture.md)

---

## Position in the Architecture

```
DecisionLogic
    │  calls send_order(side=OrderSide.BUY/SELL)
    ▼
DecisionTradingApi
    │  resolves side → direction via executor
    │
    ├── OrderGuard.validate()          ← PRE-VALIDATION (sync)
    │   Returns REJECTED if cooldown active
    │   (REJECTION_COOLDOWN)
    │
    ├── executor.open_order()          ← ORDER SUBMISSION
    │   Returns PENDING (async) or REJECTED (sync)
    │
    └── _on_order_outcome() callback   ← ASYNC OUTCOME
        Receives fill/rejection from executor
        Updates guard state (cooldown counter)
```

The guard sits at the top of the call chain. Blocked orders **never reach the executor** — they produce an `OrderResult(REJECTED)` with a `guard_` prefixed order ID, recorded in the executor's order history via `record_guard_rejection()`.

---

## Async State Update Mechanism

Orders go through a latency pipeline (simulation) or broker polling (live). The actual fill or rejection happens asynchronously, ticks after the initial `open_order()` call returns PENDING.

The OrderGuard needs to know about these async outcomes to maintain its rejection counter. This is solved with a **callback mechanism**:

```
send_order()
    │
    ▼
open_order() → PENDING              Guard sees: no block, let it through
    │                                (no state change — PENDING is not success)
    │  ... N ticks later (latency) ...
    ▼
_fill_open_order()
    ├── INSUFFICIENT_MARGIN?  → _notify_outcome(direction, rejection)
    │                                ▼
    │                         _on_order_outcome()
    │                                ▼
    │                         guard.record_rejection(LONG)  → counter=1
    │
    └── Success?              → _notify_outcome(direction, result)
                                     ▼
                              _on_order_outcome()
                                     ▼
                              guard.record_success(LONG)    → counter=0
```

### Callback Registration

`DecisionTradingApi.__init__()` registers `_on_order_outcome()` via `executor.set_order_outcome_callback()`. The callback fires at every terminal outcome point:

| Location | Outcome | Callback |
|----------|---------|----------|
| `AbstractTradeExecutor._fill_open_order()` | INSUFFICIENT_MARGIN | `_notify_outcome(direction, rejection)` |
| `AbstractTradeExecutor._fill_open_order()` | INSUFFICIENT_FUNDS (LONG) | `_notify_outcome(direction, rejection)` |
| `AbstractTradeExecutor._fill_open_order()` | INSUFFICIENT_FUNDS (SHORT sell) | `_notify_outcome(direction, rejection)` |
| `AbstractTradeExecutor._fill_open_order()` | Successful fill | `_notify_outcome(direction, result)` |
| `LiveTradeExecutor._handle_broker_response()` | Broker REJECTED | `_notify_outcome(direction, rejection)` |
| `LiveTradeExecutor._handle_timeout()` | Order timeout | `_notify_outcome(direction, rejection)` |
| `LiveTradeExecutor._process_active_orders()` | Terminal (cancelled/expired) | `_notify_outcome(direction, rejection)` |

### Latency Window

Between `open_order() → PENDING` and the callback, N ticks may pass. During this window, additional orders for the same direction can be submitted without the guard blocking them. This mirrors real broker behavior — you can submit orders faster than the broker confirms them. The guard activates once the rejection confirmation arrives.

---

## Two State Update Paths

Guard state (`record_rejection` / `record_success`) is updated through two paths:

1. **Synchronous** — direct rejections from `open_order()` that return immediately (lot validation errors, adapter exceptions, immediate broker rejection in live mode). Handled in `send_order()` before returning to the decision logic.

2. **Asynchronous** — outcomes after PENDING return (margin check at fill time, broker polling results). Flow through `_notify_outcome()` → `_on_order_outcome()` callback.

Only **broker/account rejections** feed the cooldown counter:
- `INSUFFICIENT_MARGIN`, `INSUFFICIENT_FUNDS`, `BROKER_ERROR`, `MARKET_CLOSED`

Local validation rejections (`INVALID_LOT_SIZE`, `INVALID_PRICE`, `SYMBOL_NOT_TRADEABLE`) are decision logic bugs and do **not** arm the cooldown.

---

## Cooldown Mechanics

The cooldown is **per-direction** — LONG and SHORT track independently.

```
record_rejection(LONG)  → counter[LONG] = 1
record_rejection(LONG)  → counter[LONG] = 2  → cooldown armed (if threshold=2)
                           cooldown_until[LONG] = now + cooldown_seconds

validate(LONG request)  → REJECTION_COOLDOWN (blocked)
validate(SHORT request) → None (passes — different direction)

... cooldown_seconds later ...

validate(LONG request)  → None (cooldown expired)
```

A successful fill resets the counter and clears any active cooldown for that direction:

```
record_success(LONG)    → counter[LONG] = 0, cooldown_until[LONG] cleared
```

---

## Configuration

The OrderGuard is configured through `OrderGuardConfig`:

```python
@dataclass
class OrderGuardConfig:
    cooldown_seconds: float = 60.0           # Cooldown duration after threshold
    max_consecutive_rejections: int = 2       # Rejections before cooldown arms
```

### AutoTrader Pipeline

Configured in profile JSON (`configs/autotrader_profiles/*.json`):

```json
"order_guard": {
    "cooldown_seconds": 60.0,
    "max_consecutive_rejections": 2
}
```

Loaded by `autotrader_config_loader.py` → `AutoTraderConfig.order_guard`.

### Backtesting Pipeline

Configured in scenario set JSON (`configs/scenario_sets/backtesting/*.json`):

```json
"global": {
    "order_guard": {
        "cooldown_seconds": 60.0,
        "max_consecutive_rejections": 1
    }
}
```

Supports 2-level cascade (`global` → per-`scenario`), same pattern as `stress_test_config`. Loaded by `scenario_config_loader.py` → `ProcessScenarioConfig.order_guard_config`.

If omitted, `OrderGuardConfig()` defaults apply in both pipelines.

> **Note on `cooldown_seconds` in backtests:** the value is measured in **simulated tick time**, not wall-clock. A backtest that processes 10 hours of data in 3 seconds of CPU time will see 10 hours of cooldown-relevant time, not 3 seconds. Size this value to the simulated-time gap between consecutive retry attempts you want to suppress, not to the wall-clock execution speed.

---

## Guard Rejections in Reporting

Guard rejections are recorded in the executor's `_order_history` via `record_guard_rejection()`. They:

- Increment `_orders_rejected` (same counter as broker rejections)
- Appear in `execution_stats.orders_rejected`
- Carry a `guard_` prefixed order ID for identification in logs
- Use `RejectionReason.REJECTION_COOLDOWN`

---

## Key Files

| File | Role |
|------|------|
| `python/framework/trading_env/order_guard.py` | OrderGuard class — cooldown validation + state tracking |
| `python/framework/trading_env/decision_trading_api.py` | Integration point — guard in `send_order()`, async callback, side→direction resolution |
| `python/framework/trading_env/abstract_trade_executor.py` | Callback mechanism (`set_order_outcome_callback`, `_notify_outcome`), `resolve_order_side()` |
| `python/framework/types/autotrader_types/autotrader_config_types.py` | `OrderGuardConfig` dataclass |
| `python/framework/types/trading_env_types/order_types.py` | `OrderSide`, `OrderDirection`, `REJECTION_COOLDOWN` enum value |

---

## Two Independent Safety Layers

The project has two runtime protection mechanisms that operate independently at different granularities:

```
┌─────────────────────────────────────────────────────────────┐
│                    AutoTrader Tick Loop                      │
│                                                             │
│  1. _check_safety()          ← ACCOUNT-LEVEL (per-tick)     │
│     "Is the account healthy?"                               │
│     Blocks ALL new orders if balance/drawdown threshold hit │
│     Overrides decision to FLAT — send_order() never called  │
│                                                             │
│  2. DecisionLogic._execute_decision_impl()                  │
│        │                                                    │
│        ▼                                                    │
│     DecisionTradingApi.send_order()                         │
│        │                                                    │
│        ├── OrderGuard.validate() ← ORDER-LEVEL (per-order)  │
│        │   "Should THIS specific order go through?"         │
│        │   Blocks per-direction after rejection spam        │
│        │                                                    │
│        └── executor.open_order()                            │
└─────────────────────────────────────────────────────────────┘
```

| Aspect | OrderGuard | Safety Circuit Breaker |
|--------|-----------|----------------------|
| **Granularity** | Per-order, per-direction | Per-account, session-wide |
| **Trigger** | Consecutive broker rejections (same direction) | Balance below threshold OR drawdown % exceeded |
| **Effect** | Blocks one direction for cooldown period | Blocks ALL new entries (decision overridden to FLAT) |
| **Recovery** | Automatic (cooldown expires or successful fill) | Automatic (balance recovers above threshold) |
| **Pipeline** | Both (backtesting + AutoTrader) | AutoTrader only |
| **Rationale** | Prevents retry spam after rejection | Prevents account blowup |
| **Config** | `order_guard` in profile / scenario JSON | `safety` in profile JSON |

The layers are **fully independent** — neither knows about the other, neither can bypass the other. When Safety blocks, the decision is overridden to FLAT *before* `send_order()` is called, so the OrderGuard never sees the order. When the OrderGuard blocks, Safety is unaffected (it evaluates on every tick regardless of order activity).

> Full Safety architecture: [safety_circuit_breaker_architecture.md](safety_circuit_breaker_architecture.md)
