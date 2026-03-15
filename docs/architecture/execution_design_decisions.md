# Execution Layer — Design Decisions Log

Historical reasoning behind key architectural decisions in the execution layer. These document *why* things are built the way they are — useful for future maintainers and for evaluating alternative approaches.

> **Core architecture:** see [architecture_execution_layer.md](architecture_execution_layer.md)

---

## Why Abstract Class, Not Hooks?

We considered a hooks pattern: TradeSimulator stays monolithic, with hook functions (`on_before_fill`, `on_after_submit`) that live mode overrides. This was rejected because:

1. **Too many variation points**: 5+ methods with completely different implementations (submit, close, process_pending, has_pending, is_pending_close). Hooks work for 1-2 customization points, not for swapping half the class.
2. **Unclear ownership**: With hooks, it's ambiguous whether the base or the hook "owns" the fill. With abstract, the inheritance hierarchy is explicit.
3. **Testing**: Abstract classes can be tested via concrete subclasses. Hook-based systems require mocking the hooks, which tests the framework more than the logic.

## Why Pseudo-Positions Were Eliminated

The previous design added pending orders to `get_open_positions()` as Position objects with `pending=True`. This created a **behavior contract that couldn't survive the sim→live transition**:

- In simulation, TradeSimulator could construct pseudo-positions because it controlled the latency queue
- In live trading, there's no local pseudo-position — the broker hasn't confirmed anything yet
- Strategies written against the simulation API would break in live (different position list contents)

The replacement (`has_pending_orders` + `is_pending_close`) provides the same information without contaminating the position list.

## Why Fill Logic Lives in the Base Class

The initial refactoring extracted TradeSimulator into an abstract, but left fill logic in the subclass. This meant LiveTradeExecutor was hollow — `close_position()` raised NotImplementedError, but live trading *needs* the portfolio update logic.

Moving fills to the base was the realization that **fill processing is not simulation-specific**. It's the shared business logic that both modes need. The subclass only decides *when* to call it (after latency delay vs after broker confirmation).

## Why Fill Price Is a Parameter, Not Internal

Originally, `_fill_open_order()` determined the entry price internally from the current tick (ask for LONG, bid for SHORT). This works for simulation but not for live:

- In simulation, the system IS the market — current tick bid/ask is the "broker's" fill price
- In live, the broker returns the actual execution price, which may differ (slippage)

Making `fill_price` an optional parameter keeps backward compatibility (simulation passes nothing, gets tick-based price) while enabling live trading (passes broker's actual price). The portfolio always records the real execution price.

## Why PendingOrderManager Was Extracted

OrderLatencySimulator originally handled both storage/query AND delay simulation. This coupling meant LiveTradeExecutor would need its own separate storage — duplicating the dict, query methods, has_pending logic, etc.

Extracting AbstractPendingOrderManager provides:
- **Shared storage** — both modes use the same dict-based tracking
- **Shared queries** — `has_pending_orders()`, `is_pending_close()` are identical
- **DRY** — no duplicate implementations between simulation and live
- **Testable** — storage/query logic tested once, covers both modes

The split: AbstractPendingOrderManager owns the "what" (storage, query). Subclasses own the "when" (tick-based fill detection vs broker-response detection).

## Why AbstractAdapter Was Extended, Not Split

We considered a separate `OrderExecutionAdapter` interface for live execution methods. This was rejected because:

1. **Existing pattern works**: AbstractAdapter already has optional methods with `NotImplementedError` defaults (Tier 2: `create_stop_order`, `create_iceberg_order`). Same pattern for Tier 3 execution methods.
2. **No interface pollution**: Backtesting code never calls `execute_order()` — TradeSimulator uses its own latency queue. The methods exist but are never invoked.
3. **One inheritance chain**: `MockBrokerAdapter extends AbstractAdapter` — one class provides data, validation, AND execution. No diamond inheritance, no adapter composition.
4. **File organization solves complexity**: As adapters grow, execution logic moves to utility files (`kraken_order_execution.py`) while the adapter class remains the entry point.
