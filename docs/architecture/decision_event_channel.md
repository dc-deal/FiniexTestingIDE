# Decision Event Channel (#348)

A typed, ordered, drain-guaranteed channel that lets a decision logic react to
order / lifecycle events **between ticks** — not only inside `compute(tick)`.

Without it, a decision logic learns about a fill only by polling
`get_open_positions()` / `has_pending_orders()` on the next tick. The channel
delivers the fill (and rejections, cancellations, partial closes, session end) as
a typed event to an overridable hook, in order, before the next tick runs.

The channel is **source-agnostic**: an event carries the same payload whether it
originated from the simulation latency path, live REST polling (#320), or a future
WebSocket push (#331). When push lands, only the *source* changes — the algo
contract does not.

## How a Decision Logic Uses It

1. Declare the events you want via the `get_subscribed_events()` classmethod
   (same pattern as `get_required_order_types()`). Default is no subscription →
   the channel is never even constructed (zero overhead).
2. Override the matching `on_*` hooks.

```python
class MyLogic(AbstractDecisionLogic):
    @classmethod
    def get_subscribed_events(cls):
        return {DecisionEventType.ORDER_FILLED, DecisionEventType.PARTIAL_CLOSE}

    def on_order_filled(self, event: OrderFilledEvent) -> None:
        # react to the fill here (e.g. arm a trailing stop)
        ...
```

## Interface Reference — Event → Payload → Hook

The single source of truth for the typed payloads is
[`decision_event_types.py`](../../python/framework/types/decision_event_types.py).
No guessing: each event delivers one typed payload to one hook.

| `DecisionEventType` | Payload (typed fields) | Hook |
|---|---|---|
| `ORDER_FILLED` | `OrderFilledEvent` — order_id, position_id, direction, fill_price, lots, result | `on_order_filled` |
| `ORDER_REJECTED` | `OrderRejectedEvent` — order_id, direction, reason, message, result | `on_order_rejected` |
| `ORDER_CANCELLED` | `OrderCancelledEvent` — order_id, direction | `on_order_cancelled` |
| `PARTIAL_CLOSE` | `PartialCloseEvent` — position_id, direction, closed_lots, remaining_lots, fill_price, result | `on_partial_close` |
| `SESSION_END` | `SessionEndEvent` — reason, severity | `on_session_end` |

Every payload also carries `tick_time` (sim time in backtests, wall-clock in live).

## Architecture

```
                       ┌──────────────────────────────────────────────┐
   ORDER_FILLED /      │  AbstractTradeExecutor                        │
   ORDER_REJECTED ─────┤   _notify_outcome  (existing fan-out, #319)   │──┐
                       │   _emit_order_cancelled / partial-close emit  │  │
   ORDER_CANCELLED /   │   → _decision_event_sink                      │──┤
   PARTIAL_CLOSE ──────┘                                                  │
                                                                          ▼
                                          ┌────────────────────────────────────┐
   SESSION_END (tick loop) ──────────────►│  DecisionEventDispatcher             │
                                          │   submit(event)  → filter + buffer   │
                                          │   drain()        → FIFO → on_* hooks │
                                          └────────────────────────────────────┘
                                                          ▲
                                                          │ drain() at the tick-loop boundary
                                          (after compute/execute, and in the idle heartbeat)
```

- `ORDER_FILLED` / `ORDER_REJECTED` ride the executor's existing order-outcome
  listener fan-out (#319). Close fills do **not** reach that fan-out, so they
  never produce spurious outcomes for OrderGuard / DriftAuditor.
- `ORDER_CANCELLED` / `PARTIAL_CLOSE` are emitted through a **dedicated sink**
  (`set_decision_event_sink`) — kept separate from `_notify_outcome` so existing
  outcome consumers are untouched.
- `SESSION_END` is built by the tick loop at session end (request, exhaustion,
  Ctrl+C, or safety halt).

### Drain Ordering & Re-Entrancy (the critical invariant)

`submit()` only **buffers**; hooks fire exclusively in `drain()`. `drain()` swaps
the buffer before invoking hooks, so an order submitted from inside a hook
resolves later and its event lands in the **next** drain — never re-entrantly
inside the current one. This both guarantees "events are fully processed before
the next tick" and prevents re-entrancy cascades. Events from a future WS thread
(#331) land in the same buffer and are drained on the main thread.

## Processing Model — Single-Consumer Queue (Industry Pattern)

The dispatcher **buffers, filters, orders, and delivers** events — it does not
*execute* them. The work runs in the decision logic's `on_*` hooks; the dispatcher
is the router. Think of it as an inbox with a sorting rule, emptied once per tick
boundary.

This is the established pattern for serious trading systems: events are produced
asynchronously on background threads (worker-thread HTTP today, the #331 WebSocket
later), marshaled onto **one ordered buffer**, and drained by a **single consumer
on the main thread**, in order, to completion. The same single-consumer-queue
principle underlies NautilusTrader's MessageBus, QuantConnect/Lean's single
algorithm thread, MetaTrader 5's serialized `OnTick` / `OnTradeTransaction` event
queue, FIX-based OMS (sequence-numbered execution reports), and the LMAX Disruptor.
It buys deterministic ordering, no re-entrancy, and back-pressure.

**Where we sit — tick-gated, not a continuous event loop.** We drain at the
tick-loop boundary (after compute/execute) and during the idle heartbeat (#320),
rather than the instant an event arrives. This keeps the model deterministic and
**symmetric between simulation and live** — in a backtest there is no continuous
loop, only ticks, so "drained before the next tick" *is* the backtest-determinism
property. The trade-off is up-to-one-heartbeat delivery latency in quiet live
periods; the #320 heartbeat bounds it and the #331 push path cuts production
latency to sub-second. MetaTrader 5 is the closest analog: `OnTick` (market) and
`OnTradeTransaction` (order/lifecycle) are two entry points of the same serialized
event queue — exactly our `on_tick` + drained `on_*` split.

A future move toward continuous event-loop trading would replace the tick-gated
drain with a dedicated consumer that drains on arrival — the dispatcher contract
(buffer → drain → re-entrancy swap) stays identical; only the *trigger* changes.
That is the same migration #331 (push instead of poll) already sets up.

## Sim / Live Emit Matrix

Each executor emits only the events it can produce truthfully. A logic that
subscribes to an event the current executor can't emit simply never receives it
(no error).

| Event | Simulation | AutoTrader |
|---|:--:|:--:|
| `ORDER_FILLED` / `ORDER_REJECTED` | ✓ | ✓ |
| `ORDER_CANCELLED` | ✓ | ✓ |
| `PARTIAL_CLOSE` | ✓ | ✓ |
| `SESSION_END` | ✓ (ticks exhausted / request) | ✓ (request / Ctrl+C / safety) |
| `PARTIAL_FILL` (broker multi-execution) | — (order-book topic #143) | from #342 |
| `RECONCILE_ALERT` | — (live-only) | from #151 Phase 2 |

## Session Control — `request_session_end`

`DecisionTradingApi.request_session_end(reason, severity)` lets a decision logic
end the session itself. The tick loop ends the session at the next boundary:
`SessionEndSeverity.NORMAL` runs the graceful shutdown (close remaining orders,
final stats, clean exit); `EMERGENCY` exits immediately. Works in both pipelines —
in backtesting it ends the scenario early; in live it shuts the AutoTrader down. A
`SESSION_END` event is delivered before teardown.

## Wiring & Drain Points

- Built in the runner mains via `DecisionEventDispatcher.create_if_subscribed(...)`
  — `autotrader_main.py` (live) and `process_main.py` (sim). Returns `None` when
  the logic subscribes to nothing.
- Drained at the tick-loop boundary: `autotrader_tick_loop.py` (after
  compute/execute and in the idle heartbeat) and `process_tick_loop.py` (after
  compute/execute). The final `SESSION_END` is drained once the loop ends.

## Relation to Other Components

- **#280 Event Tape** — the free-text `StrategyEvent` ring buffer for the live UI.
  Distinct from this channel (lossy, display-only). This channel is the typed,
  lossless, ordered delivery to algo hooks.
- **#233 Event-Stream CSV** — a trade-activity *export file*. Distinct from this
  in-process event bus.
- **#320 Polling Cadence** — the poll responses are one source feeding the channel.
- **#331 WebSocket Push** — will feed the same channel; polling demotes to fallback.
- **#151 Reconciliation** — `RECONCILE_ALERT` will flow through this channel.

## Tests

- Unit: `tests/autotrader/live_executor/test_decision_event_dispatcher.py` — filtering, FIFO, drain-to-completion, re-entrancy, outcome mapping.
- Dual-world parity: the `BacktestingEventProbe` decision logic runs through both pipelines and must record the identical event sequence:
  - Simulation: `tests/simulation/event_channel/test_event_channel_sim.py`
  - AutoTrader-mock: `tests/autotrader/integration/test_event_channel_live_pipeline.py`
