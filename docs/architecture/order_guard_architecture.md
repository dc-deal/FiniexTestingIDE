# Order Guard Architecture

## Overview

The OrderGuard is the pre-trade guard inside `DecisionTradingApi`: it stops an order **before** it
reaches the executor. It is the universal gateway for all decision logics (CORE and
user-authored), covering both backtesting and every AutoTrader session.

It denies an order when one of its rules holds, checked in this order:

- **An undeclared level** (`ORDER_FEATURE_NOT_DECLARED`) — the order carries a stop-loss or
  take-profit level its decision logic did not declare (`refuse_undeclared_features()`).
- **Stale market data** (`STALE_MARKET_DATA`) — no new entry while the session's tick stream is
  stale. Only an AutoTrader session can be stale; a backtest's data is always fresh.
- **An unaccounted order** (`UNACCOUNTED_ORDER`) — no new entry while an order sent earlier was
  never named open or closed by the venue after its resolution ran out. A latch, not a cooldown:
  it ends when the order is accounted for.
- **Rejection Cooldown** (`REJECTION_COOLDOWN`) — blocking a direction after N consecutive broker
  rejections for a configurable period, preventing rejection spam (e.g. repeated
  INSUFFICIENT_MARGIN attempts). It is the one rule whose state the guard keeps itself, and most
  of this document describes it.

The guard is reached from `send_order()` only: closing or protecting an open position never passes
through it.

The OrderGuard lives in the intermediate layer between DecisionLogic and executor. It does not
enforce business rules, does not know about market types, and does not know about balances.
Structural validation (market type, balance, order type compatibility) belongs in the executor.

### Clock / Time Source

The guard is clock-agnostic — cooldown methods take an explicit `now: datetime` parameter supplied by `DecisionTradingApi` via `executor.get_current_time()`. This resolves to:

- **Backtesting:** the current tick's simulated timestamp. Cooldowns advance with simulated market time, which keeps them deterministic across runs and sim-correct under accelerated playback.
- **AutoTrader:** the tick timestamp — broker-delivered in a live-adapter session (effectively wall-clock real time), replayed from the archive in a mock session.

The guard itself never calls `datetime.now()`. This is the reason cooldowns behave identically for a backtest that compresses weeks of ticks into seconds and for a live-adapter session where ticks arrive in real time.

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
    ├── OrderGuard.refuse_undeclared_features()
    │   Only for an order carrying a stop-loss or take-profit
    │   level its logic did not declare → DENIED
    │   (ORDER_FEATURE_NOT_DECLARED)
    │
    ├── OrderGuard.validate()          ← PRE-VALIDATION (sync)
    │   Returns DENIED on stale market data, an unaccounted
    │   order or an active cooldown (STALE_MARKET_DATA,
    │   UNACCOUNTED_ORDER, REJECTION_COOLDOWN)
    │
    ├── executor.open_order()          ← ORDER SUBMISSION
    │   Returns PENDING (async) or DENIED (sync)
    │
    └── _on_order_outcome() callback   ← ASYNC OUTCOME
        Receives fill/rejection from executor
        Updates guard state (cooldown counter)
```

The guard sits at the top of the call chain. Blocked orders **never reach the executor** — they produce an `OrderResult(DENIED)` with a `guard_` prefixed order ID, recorded in the executor's order history via `record_guard_rejection()`.

---

## Async State Update Mechanism

Orders go through a latency pipeline (simulation) or broker polling (live execution stack). The actual fill or rejection happens asynchronously, ticks after the initial `open_order()` call returns PENDING.

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

### Listener Registration

`DecisionTradingApi.__init__()` registers `_on_order_outcome()` via
`executor.add_order_outcome_listener()`. The slot is **multi-listener**: registering does not
replace what is already there, and listeners are notified in registration order. Three consumers
register today:

| Registrant | Why |
|------------|-----|
| `decision_trading_api.py` | the OrderGuard path — cooldown state after a rejection |
| `decision_event_dispatcher.py` | strategy-facing order events |
| `live/drift_auditor.py` | read-only local-vs-broker telemetry (#327) |

The listener fires where an order reaches a terminal state after the PENDING return. A cancel does
not reach it, and neither does a denial, which `send_order()` returns directly.

| Location | Outcome | Callback |
|----------|---------|----------|
| `AbstractTradeExecutor._fill_open_order()` | INSUFFICIENT_MARGIN | `_notify_outcome(direction, rejection)` |
| `AbstractTradeExecutor._fill_open_order()` | INSUFFICIENT_FUNDS (LONG) | `_notify_outcome(direction, rejection)` |
| `AbstractTradeExecutor._fill_open_order()` | INSUFFICIENT_FUNDS (SHORT sell) | `_notify_outcome(direction, rejection)` |
| `AbstractTradeExecutor._fill_open_order()` | Successful fill | `_notify_outcome(direction, result)` |
| `LiveTradeExecutor._handle_broker_response()` | Broker REJECTED | `_notify_outcome(direction, rejection)` |
| `LiveTradeExecutor._record_async_rejection()` | Broker REJECTED in its answer to a submission, or UNDELIVERED (the venue never received the order) | `_notify_outcome(direction, rejection)` |
| `LiveTradeExecutor._book_order_given_up()` | UNACCOUNTED (given up: fill timeout, resolution ceiling or a session's stop) | `_notify_outcome(direction, unaccounted)` |
| `LiveTradeExecutor._handle_query_response()` | Broker REJECTED after it had accepted the order | `_notify_outcome(direction, rejection)` |

### Latency Window

Between `open_order() → PENDING` and the callback, N ticks may pass. During this window, additional
orders for the same direction can be submitted without the guard blocking them. This mirrors real
broker behavior — you can submit orders faster than the broker confirms them. The guard activates
once the rejection confirmation arrives.

---

## Two State Update Paths

Guard state (`record_rejection` / `record_success`) is updated through two paths:

1. **Synchronous** — refusals `open_order()` returns immediately (lot validation errors, funds at submission). The executor's own checks deny them; nothing the venue says comes back synchronously. Handled in `send_order()` before returning to the decision logic.

2. **Asynchronous** — outcomes after PENDING return (margin check at fill time, the venue's answer to a submission, broker polling results). Flow through `_notify_outcome()` → `_on_order_outcome()` callback.

Only these feed the cooldown counter:
- **Broker/account reasons:** `INSUFFICIENT_MARGIN`, `INSUFFICIENT_FUNDS`, `BROKER_ERROR`, `MARKET_CLOSED`
- **The endings `UNDELIVERED` and `UNACCOUNTED`**, whatever their reason: the venue could not be
  relied on, and sending more orders is what helps least

Local validation refusals (`INVALID_LOT_SIZE`, `INVALID_PRICE`, `SYMBOL_NOT_TRADEABLE`) are decision logic bugs and do **not** arm the cooldown.

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

The OrderGuard is configured through `OrderGuardDefaults`:

```python
class OrderGuardDefaults(StrictConfigModel):
    cooldown_seconds: float = 60.0           # Cooldown duration after threshold
    max_consecutive_rejections: int = 2       # Rejections before cooldown arms
    block_stale_market_data: bool = True      # Deny new entries while market data is stale
```

`block_stale_market_data` is the switch of the stale-market-data rule. It has no effect in a
backtest, where market data is always fresh. This block has no setting for the undeclared-level and
unaccounted-order rules.

### AutoTrader Pipeline

Configured in profile JSON (`configs/autotrader_profiles/.../*.json`), over the defaults in
`app_config.autotrader.order_guard`:

```json
"order_guard": {
    "cooldown_seconds": 60.0,
    "max_consecutive_rejections": 2,
    "block_stale_market_data": true
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

If omitted, an AutoTrader session uses `app_config.autotrader.order_guard`, which mirrors the model's
defaults, and a backtest uses `OrderGuardDefaults()` itself.

> **Note on `cooldown_seconds` in backtests:** the value is measured in **simulated tick time**, not
> wall-clock. A backtest that processes 10 hours of data in 3 seconds of CPU time will see 10 hours
> of cooldown-relevant time, not 3 seconds. Size this value to the simulated-time gap between
> consecutive retry attempts you want to suppress, not to the wall-clock execution speed.

---

## Guard Denials in Reporting

Guard denials are recorded in the executor's `_order_history` via `record_guard_rejection()`. They:

- Are counted in `execution_stats.orders_denied`, beside the executor's own denials, never as broker
  rejections
- Carry a `guard_` prefixed order ID for identification in logs
- Carry the `RejectionReason` of the rule that denied them (see [Overview](#overview))

---

## Key Files

| File | Role |
|------|------|
| `python/framework/trading_env/order_guard.py` | OrderGuard class — its rules (`validate()`, `refuse_undeclared_features()`) + the cooldown's state tracking |
| `python/framework/trading_env/decision_trading_api.py` | Integration point — guard in `send_order()`, async callback, side→direction resolution |
| `python/framework/trading_env/abstract_trade_executor.py` | Listener mechanism (`add_order_outcome_listener`, `_notify_outcome`), `resolve_order_side()` |
| `python/framework/types/config_types/autotrader_defaults_config_types.py` | `OrderGuardDefaults` Pydantic model — shared by both pipelines |
| `python/framework/types/trading_env_types/order_types.py` | `OrderSide`, `OrderDirection`, `RejectionReason` (the reason each rule denies with) |

---

## Two Independent Safety Layers

The project has two runtime protection mechanisms that operate independently at different granularities:

```
┌─────────────────────────────────────────────────────────────┐
│                    AutoTrader Tick Loop                      │
│                                                             │
│  1. check_safety()           ← ACCOUNT-LEVEL (per-tick)     │
│     "Is the account healthy?"                               │
│     Forces every decision FLAT while a threshold is hit     │
│     No order from the action, an action-driven close too    │
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
| **Trigger** | Consecutive broker rejections (same direction) | Account value below its floor (`min_equity` / `min_balance`), or a drawdown or daily-loss limit exceeded |
| **Effect** | Blocks one direction for cooldown period | Forces every decision FLAT — no new entry, and no action-driven close |
| **Recovery** | Automatic (cooldown expires or successful fill) | Automatic (the account value recovers above the threshold) |
| **Pipeline** | Both (backtesting + AutoTrader) | AutoTrader only |
| **Rationale** | Prevents retry spam after rejection | Prevents account blowup |
| **Config** | `order_guard` in profile / scenario JSON | `safety` in profile JSON |

The guard step in the diagram and the OrderGuard column describe the guard's rejection cooldown; its
other rules are listed under [Overview](#overview).

The layers are **fully independent** — neither knows about the other, neither can bypass the other.
When Safety blocks, the decision is overridden to FLAT *before* `send_order()` is called, so the
OrderGuard never sees the order. When the OrderGuard blocks, Safety is unaffected (it evaluates on
every tick regardless of order activity).

> Full Safety architecture: [safety_circuit_breaker_architecture.md](safety_circuit_breaker_architecture.md)
