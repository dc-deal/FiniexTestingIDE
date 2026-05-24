# Broker Trade Record Model — Order ↔ Executions Pairing

> See [Trade Execution Visibility](trade_execution_visibility.md) for how `BrokerTrade` records propagate through `Position.entry_trades` / `TradeRecord.entry_trades` / `exit_trades`, how the renderers surface them, and the long-format event-stream CSV.

## Concept

Every order placed at a broker eventually produces one or more **executions** (also called trades, fills, or deals). The order is the instruction; executions are the realizations. The relationship is always 1:N — one order, many executions — and is universal across institutional and retail brokers (FIX ExecutionReports, IBKR `execDetails`, Binance `myTrades`, Kraken `QueryTrades`, MT5 deals).

This project models that pairing via the `BrokerTrade` domain type. The data model is broker-agnostic; transport details live in each adapter's Tier-3 `_build_trades_query_payload` / `_do_request_trades_query` / `_parse_trades_query_response` triple.

```
ORDER (the instruction)
  │
  ├─── EXECUTION #1   vol=0.04 @ 100.10  fee=0.001
  ├─── EXECUTION #2   vol=0.03 @ 100.15  fee=0.0008
  └─── EXECUTION #3   vol=0.03 @ 100.20  fee=0.0008

cumulative_filled_lots = 0.10
cumulative_avg_price   = 100.146   (weighted)
cumulative_fee         = 0.0026
```

## Domain Type

[BrokerTrade](python/framework/types/trading_env_types/broker_trade_types.py):

| Field | Meaning |
|---|---|
| `trade_id` | Broker's execution ID (Kraken tradeid, MT5 deal ticket) |
| `parent_broker_ref` | Parent order's broker_ref (Kraken txid, MT5 order ticket) |
| `order_id` | OUR internal order_id — primary routing key |
| `volume` | Lots filled in this execution |
| `price` | Price of this execution |
| `fee` | Broker-reported fee for this execution |
| `fee_currency` | Fee currency (e.g. 'USD', 'EUR') |
| `timestamp` | UTC, timezone-aware execution time |
| `side` | LONG / SHORT |
| `is_maker` | True for LIMIT/maker fills, False for MARKET/taker |

`PendingOrder` (see [latency_simulator_types.py](python/framework/types/trading_env_types/latency_simulator_types.py)) carries the executions:

```python
trades: List[BrokerTrade]
cumulative_filled_lots: float
cumulative_fee: float
cumulative_avg_price: float
```

The helper `pending.append_trade(trade)` mutates `trades` and recomputes the cumulative aggregates in lock-step. Consumers read the cumulative fields directly without recomputing on every access.

## Adapter Tier-3 Contract

Three methods on `AbstractAdapter` — every live-capable adapter implements them:

```python
def _build_trades_query_payload(broker_ref: str) -> Dict[str, Any]
def _do_request_trades_query(payload: Dict[str, Any]) -> Dict[str, Any]
def _parse_trades_query_response(raw, broker_ref, order_id) -> List[BrokerTrade]
```

Pure / transport / pure layering, identical to the submit/query/cancel/modify triples established by #319.

The `OrderCapabilities.trade_level_reporting` flag (default True) declares whether the broker exposes per-execution detail. All real broker integrations (Kraken, MT5, IBKR, Binance) support it. Adapters that lack it can declare False and fall back to aggregated reporting — the data model still works (one synthetic record per fill).

### Kraken Implementation

Kraken's REST API requires a two-call pattern:

1. `POST /0/private/QueryOrders` with `trades=true` → returns the order detail plus a `trades: [tradeid, ...]` list
2. `POST /0/private/QueryTrades` with comma-separated `txid=` → returns full per-trade detail

The Kraken adapter encapsulates both calls inside `_do_request_trades_query`. Dry-run orders (DRYRUN-* refs) bypass the broker and return an empty list — a documented limitation, since dry-run orders never produce real executions.

### Mock Implementation

The `MockBrokerAdapter` records synthetic trade records at fill time (in `_do_request_submit` INSTANT_FILL and `_do_request_query` DELAYED_FILL paths) via the `_record_mock_trades` helper. The constructor parameter `trades_per_fill: int = 1` controls how many records are produced per fill:

- `1` (default): single full-volume trade — typical real-world fill
- `N > 1`: split into N records with even volume and small price offsets — exercises partial-fill code paths in regression tests

Each synthesized trade gets a `MOCK-TRADE-XXXXXX` ID and a quote-currency fee computed from the broker config's maker_taker rates.

## Drain-Layer Distribution (Post-Drain Anchor)

Reference: ISSUE_326 §8.

```
worker thread: HTTP / RPC trades_query → parse → push TradesQueryResponse
                                                          │
                                                          ▼
drain_inbox      ←──── routes to _trades_response_hook
                                                          │
                                                          ▼
LiveTradeExecutor._handle_trades_response(response):
  ├─ stale broker_ref check                     (defensive guard)
  ├─ for trade in response.trades:
  │     pending.append_trade(trade)             (mutates pending.trades + cumulative_*)
  │
  ├─ remove from _active_limit_orders
  └─ _fill_open_order(pending, cumulative_avg_price, ...)
        │
        ▼  (inherited from AbstractTradeExecutor — existing code, unchanged)
        ├─ portfolio.open_position(...)                  (with cumulative truth)
        ├─ self._order_history.append(EXECUTED)
        └─ self._notify_outcome(...)                     (multi-listener fan-out, #319)
              │
              ├─ OrderGuard._on_order_outcome
              ├─ DriftAuditor._on_order_outcome           (#327 — reads pending.trades)
              └─ Reconciler._on_order_outcome             (#151 Phase 4)
```

## Sim vs. Live — Same Algo-Facing Contract

| | Sim (TradeSimulator) | Live (LiveTradeExecutor) |
|---|---|---|
| Emission trigger | `_fill_open_order` / `_fill_close_order` (shared) | `_fill_open_order` / `_fill_close_order` (shared) |
| Data source | Synthesized from local price + fee model | Synthesized from polling response in V1 |
| Future: real broker trades | n/a — sim is its own truth | Async `submit_trades_query_async` populates per-execution truth |

Both pipelines route through `AbstractTradeExecutor._synthesize_pending_trade` when `pending.trades` is empty at fill time. Consumers (`portfolio`, `order_history`, listeners) see the same shape in both pipelines.

## V1 Limitation — Synthetic Fee

The polling-path synthesis in V1 uses the locally-computed entry fee (`entry_fee.cost`) as the BrokerTrade.fee value — not the broker-reported fee. Drift Audit (#327) consumes `pending.cumulative_fee` to compare against `KrakenFeeModel.compute_fee` and surface divergence. As long as V1 polling drives `pending.trades`, that comparison is tautologically zero. Real divergence detection requires async trades_query against the live broker, which #327 may trigger independently as a post-outcome consumer.

The async path (`submit_trades_query_async` → drain → `_handle_trades_response`) is fully wired and tested. It is not invoked from the V1 polling code path by default; that activation is deferred to:

- **#327 Drift Audit** — opt-in trades_query after each FILLED outcome, populates real broker fee
- **#320 Polling Cadence Management** — future migration of all polling to async via worker

## Out of Scope

- **Push-based trade streams** (Kraken WS `ownTrades`, MT5 EA `OnTradeTransaction`) — the data model supports it (push events arrive via inbox, drain routes the same way), but the wiring is a future enhancement.
- **Position-level trade aggregation across multiple orders** — `pending.trades` is per-order. Cross-order position-level trade summaries are a V1.4+ concern.
- **Historical backfill** — fetching old trades after session restart is reconciliation territory (#151).

## Tests

| File | Scope |
|---|---|
| [tests/autotrader/live_executor/test_broker_trade_records.py](tests/autotrader/live_executor/test_broker_trade_records.py) | append_trade aggregation, async path roundtrip via processor, stale-response guard, multi-trade mock |
| [tests/simulation/trade_emission/test_trade_emission.py](tests/simulation/trade_emission/test_trade_emission.py) | Sim `_fill_open_order` / `_fill_close_order` synthesis on the shared abstract path |
| [tests/parity/test_trade_records_parity.py](tests/parity/test_trade_records_parity.py) | Sim/live agreement on post-fill state (position, history, synthesis shape) |

## Related Issues

- **#319 LiveRequestProcessor Core Refactor** — provides the Tier-3 layer pattern, async worker dispatch, multi-listener fan-out
- **#318 Async Modify/Cancel** — same async pattern adapted to modify/cancel/position-modify
- **#327 Local-vs-Broker Drift Audit** — direct consumer of `pending.trades` and `cumulative_fee`
- **#320 Polling Cadence Management** — partial-fill bookkeeping delegated to this issue's data model
- **#151 Reconciliation Layer** — uses trade-level granularity for state reconciliation
- **#209 MT5 Live Adapter** — implements the same Tier-3 trades_query triple against `HISTORY_DEALS_GET`
