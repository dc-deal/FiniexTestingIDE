# Drift Audit (#327)

Read-only telemetry channel that compares **locally computed** fee/volume/price values against the **broker-reported truth** delivered via the #326 async trades-query pipeline. Surfaces fee-model bugs, partial-fill mismatches, and tier mismatches without mutating any state — correction is reserved for the future Reconciliation Layer (#151).

## Concept

Every fully-filled order has two views of itself once it leaves our pipeline:

| View | Source | Fee | Volume | Avg Price |
|---|---|---|---|---|
| **Local synthesis** | `_synthesize_pending_trade` from `MakerTakerFee.calculate_cost(...)` | locally computed | requested lots | broker fill_price (passed through) |
| **Broker truth** | `QueryTrades` response → `BrokerTrade` records | broker `fee` field | sum of execution volumes | volume-weighted mean of execution prices |

DriftAuditor consumes both, computes the relative delta per dimension, and counts events that exceed configurable thresholds. Strict read-only — no state mutation, no portfolio adjustment, no error gating. The detected drift is the operator's signal that something in the local model has diverged from broker reality.

## Architecture

DriftAuditor is a **reactive observer**, not a poller. It reacts to the `EXECUTED` outcome event, fires exactly one `submit_trades_query_async()` to obtain the broker's per-execution truth, consumes the response asynchronously, and logs the comparison.

```
Order fills (synthetic data populates pending.trades via _synthesize_pending_trade)
  ↓
_fill_open_order runs (portfolio.open_position, history append)
  ↓
_notify_outcome(EXECUTED, result, pending)   ← #319 multi-listener, signature
                                                 extended with Optional[PendingOrder]
  ↓
DriftAuditor._on_order_outcome:
  - snapshot synthetic state from pending.cumulative_*
  - store in self._pending_audits[order_id]
  - executor.submit_trades_query_async(order_id, broker_ref)
        ↓ (worker thread)
        TradesQueryJob → HTTP /0/private/QueryOrders + /0/private/QueryTrades
        TradesQueryResponse → _http_inbox

(later, on next drain_inbox)
TradesQueryResponse arrives → executor._handle_trades_response
  ↓ (executor's own logic: append to pending if still active, otherwise log+skip)
  ↓ NEW: fan-out to registered trades-response consumers (#327)
DriftAuditor._on_trades_response:
  - pop snapshot from self._pending_audits (always — no leak on failure)
  - compute real cumulative_* from response.trades (immutable)
  - compare snapshot.synthetic_* vs real_*
  - log [DRIFT] event if over threshold; increment counters
```

Critical: **two inbox drain cycles**. The trades-query response cannot arrive in the same drain that triggered it — it requires a separate HTTP roundtrip on the worker thread. This is async-correct by design (no tick-loop blocking). V1.3 Pilot Run baseline for fill-to-trades-query latency: ~2000 ms.

## ID Correlation Across the Async Roundtrip

Async dispatch + worker thread + drain consumer = multiple hops where the response must be routed back to the correct snapshot. Robust correlation relies on **two independent IDs at different layers** — our internal `order_id` carried through all hops as the routing key, and the broker's `broker_ref` (txid) used for the API call. A third per-execution `trade_id` identifies individual fills within an order.

| ID | Owner | Lifetime | Used for |
|---|---|---|---|
| `PendingOrder.pending_order_id` | **Us** — assigned at submission, e.g. `pos_ethusd_3` | Order lifetime | Primary routing key. DriftAuditor's `_pending_audits` dict key. Listener correlation. Log identifier. |
| `PendingOrder.broker_ref` | **Broker** — Kraken txid (e.g. `OPRSKJ-IAYTG-T5VB2M`), MT5 ticket | Order lifetime, **stable across modify** (Kraken `AmendOrder` is in-place; #320 stale-ref guard now defensive) | The handle the API call needs (`POST /0/private/QueryOrders` with `txid=<broker_ref>`). |
| `BrokerTrade.trade_id` | **Broker** — Kraken tradeid (e.g. `TKH2SE-M7IF5-CFI7LT`), MT5 deal ticket | Permanent | Per-execution receipt. Persists in trade history, never reused. |
| `BrokerTrade.parent_broker_ref` | **Broker** — copy of the parent order's `broker_ref` | Permanent | Trade → parent order link on the broker side. |
| `BrokerTrade.order_id` | **Us** — written by the adapter's `_parse_trades_query_response` | Permanent | Trade → internal order link. The bridge that makes drain-side routing possible without re-lookups. |

### How the Routing Flows End-to-End

```
submission                      worker (HTTP)                drain (main thread)
─────────────────────────       ─────────────────────────    ──────────────────────────
DriftAuditor._on_order_outcome
  snapshot = AuditContext(
    order_id = pending.pending_order_id,   ← our internal, primary key
    broker_ref = pending.broker_ref,       ← broker's, for the API
    ...
  )
  self._pending_audits[order_id] = snapshot

  executor.submit_trades_query_async(
    order_id   = pending.pending_order_id,    ──┐  both IDs ride in
    broker_ref = pending.broker_ref,          ──┘  the TradesQueryJob
  )

                              TradesQueryJob arrives on worker
                              adapter._build_trades_query_payload(broker_ref)
                                → broker API call uses broker_ref
                              adapter._do_request_trades_query(...)
                                → Kraken returns raw trades
                              adapter._parse_trades_query_response(
                                  raw, broker_ref, order_id
                              )
                                → builds List[BrokerTrade] with:
                                  - trade_id       = broker's per-execution ID
                                  - parent_broker_ref = broker_ref (input)
                                  - order_id       = our order_id (input!)  ←─┐
                              worker pushes TradesQueryResponse with:           │  the bridge
                                order_id   = our order_id          ─────────────┘  written here
                                broker_ref = broker_ref-at-query-time
                                trades     = List[BrokerTrade]

                                                            drain_inbox picks up response
                                                            ↓
                                                            executor._handle_trades_response(response):
                                                              stale-ref guard:
                                                                if pending.broker_ref != response.broker_ref:
                                                                    log "stale" + skip executor mutation
                                                                    (next throttle cycle re-queries)
                                                              fan-out to consumers:
                                                                DriftAuditor._on_trades_response(response):
                                                                  snapshot = self._pending_audits.pop(
                                                                      response.order_id          ← our internal,
                                                                  )                                routes back to
                                                                  compare snapshot vs.             the right snapshot
                                                                       sum(response.trades.fee/volume/price)
```

### Why two IDs, not one

The split is deliberate and non-removable:

- **`broker_ref` is broker-assigned, not ours.** We cannot choose it, and historically it could even change mid-life — the legacy EditOrder was cancel-replace and flipped the txid, which the #320 stale-ref guard was built to absorb (a query dispatched before the modify returned the OLD ref while the in-flight pending already held the NEW one). Since the switch to in-place `AmendOrder` the txid is stable across a modify, so the guard no longer fires in normal Kraken flow — it stays as a defensive net for brokers that do cancel-replace.
- **Our `order_id` alone cannot drive the broker API.** Kraken does not know our internal naming — it needs its own txid.
- **The bridge is written by the adapter** in `_parse_trades_query_response`. The adapter receives our `order_id` as input to the parse step (carried through the worker dispatch), embeds it in every `BrokerTrade` it produces, and threads it into the `TradesQueryResponse`. From that moment on, the response is fully routable on the drain side without re-looking-up anything.

### Stale-Ref Guard vs. DriftAuditor's Snapshot Pop

These are independent decisions:

- The **executor's `_handle_trades_response`** uses the broker_ref guard to decide whether to *mutate the executor's own state* (append trades to `pending`, finalize fill). A stale-ref response is logged-and-skipped from the executor's perspective.
- **DriftAuditor's `_on_trades_response`** runs in the fan-out, AFTER the executor's own decision. It always pops its snapshot by `response.order_id`. A stale-ref response still carries broker truth (the trades that ran on whichever broker_ref was active at query time) — the audit comparison is still meaningful for the order whose snapshot was captured.

The only response that DriftAuditor truly ignores is one where the snapshot was never created (no matching entry in `_pending_audits` — e.g., trades-query triggered by something other than us, or already-popped by an earlier response with the same order_id).

### MT5 Carryover

The same two-ID pattern carries to MT5 (#209) with broker-specific value spaces:

| Layer | Kraken | MT5 |
|---|---|---|
| Our internal `order_id` | `pos_ethusd_3` | `pos_ethusd_3` (same — adapter-agnostic) |
| Broker `broker_ref` | Kraken txid (`OPRSKJ-...`) | MT5 ticket (numeric, e.g. `123456789`) |
| Per-execution `trade_id` | Kraken tradeid (`TKH2SE-...`) | MT5 deal ticket (numeric) |

The adapter abstraction (`AbstractAdapter._build_trades_query_payload` / `_parse_trades_query_response`) hides the broker-specific shape — the drain-side code only sees our internal `order_id` for routing.

## Listener Signature Extension

The `add_order_outcome_listener` signature was extended to provide the `PendingOrder` reference at outcome time:

```python
# Before
listener: Callable[[OrderDirection, OrderResult], None]

# After
listener: Callable[[OrderDirection, OrderResult, Optional[PendingOrder]], None]
```

Without the `pending` reference DriftAuditor could not snapshot the synthetic state. `Optional` because pre-submit rejections (line 389 in `live_trade_executor.py`) have no PendingOrder yet — they pass `None` explicitly.

OrderGuard's listener takes `pending: Optional[PendingOrder] = None` and ignores it.

## Trades-Response Multi-Consumer

The processor's `_trades_response_hook` is single-slot (executor registers itself). To let DriftAuditor receive the response, the **executor** introduces a multi-consumer fan-out at the end of `_handle_trades_response`:

```python
def _handle_trades_response(self, response: TradesQueryResponse) -> None:
    try:
        # ... existing logic: find pending, append trades, fill if active ...
    finally:
        # Fan-out — runs on success AND failure paths so consumers can
        # clean up their own tracking state (Risk 4 — no leaks)
        for consumer in self._trades_response_consumers:
            try:
                consumer(response)
            except Exception as e:
                self.logger.error(f"trades_response consumer raised: {e}", exc_info=True)
```

**Consumer contract:** read from `response.trades` (immutable response object), NOT from `pending.trades` (mutation-order-sensitive across the executor's own logic).

Per-consumer try/except — one bad consumer cannot kill the chain (Risk 2 mitigation).

The processor's single-hook contract stays unchanged — multi-consumer logic lives at the executor level (where consumers logically live).

## Drift Types

| Type | What is compared | Default threshold |
|---|---|---|
| `FEE` | `pending.cumulative_fee` (local) vs. `sum(BrokerTrade.fee)` (broker) | 0.5 % |
| `VOLUME` | `pending.requested_lots` (local) vs. `sum(BrokerTrade.volume)` (broker) | 0.1 % |
| `PRICE` | `pending.cumulative_avg_price` (local) vs. volume-weighted mean of broker trades | 1.0 % |
| `SLIPPAGE` | `pending.submission.tick_mid_price` (local) vs. volume-weighted mean of broker trades | 0.5 % |

**PRICE channel scope.** The PRICE counter compares Kraken's QueryOrder summary price against Kraken's QueryTrades per-execution average — both come from the broker. It detects broker-internal-reporting inconsistencies (one-off rounding edge cases between the two Kraken endpoints), not market-reality cost. Useful as a sanity check; do not interpret a sustained PRICE count as an action signal.

**SLIPPAGE channel scope.** The SLIPPAGE counter compares the trade-channel tick mid-price captured at submission (`PendingOrder.submission.tick_mid_price`) against the volume-weighted mean of broker trades. Both sides come from *independent* sources — our tick feed and the broker's matching engine — so the delta is the **real cost the operator paid** (spread + intra-latency market drift + book-walking on larger orders). This is the empirical baseline that #244 (Crypto Spread Simulation) consumes for spread reconstruction.

## Slippage vs. Spread — Conceptual Clarity

Two distinct concepts that get conflated easily:

| Concept | Definition | Layer |
|---|---|---|
| **Spread** | Bid-Ask gap at a single moment | Quote / order-book property |
| **Slippage** | Expected (reference) price vs. actual fill price | Execution-event property — broader |

Slippage is the broader concept and **contains** the spread effect, **plus** any market movement during the submission-to-fill window, **plus** book-walking on larger orders. For Crypto trade-channel feeds (Kraken `bid == ask == last`) slippage is dominated by the spread component but the metric remains event-based and post-fill. MT5-style brokers with real bid/ask in the tick data still feed the same formula — `submission.tick_mid_price = (bid + ask) / 2` — and the audit value-add is the latency-window drift component on top of what `SpreadFee` already accounts for.

## Configuration

`DriftAuditConfig` (Pydantic, in `autotrader_defaults_config_types.py`):

```python
class DriftAuditConfig(BaseModel):
    enabled: bool = True
    fee_threshold_pct: float = 0.5       # bug-signal threshold for fee drift
    volume_threshold_pct: float = 0.1    # partial-fill signal
    price_threshold_pct: float = 1.0     # Kraken-intra-reporting consistency check
    slippage_threshold_pct: float = 0.5  # real submission-to-fill cost (market reality)
    log_all: bool = False                # if True, log every event (not just threshold breaches)
    sample_rate: float = 1.0             # reserved notausgang; V1.3 default = audit every fill
```

Wired through the autotrader cascade (`app_config.json` → profile override). `sample_rate` is reserved as a rate-limit escape hatch — at 1.0 every fill triggers a post-fill trades-query (+2 ops on Kraken Tier-3 budget per fill).

Backtesting pipeline does NOT wire the auditor (sim has no broker truth distinct from the local fee model — comparison would be tautological).

## Snapshot Lifecycle

The `_pending_audits` dict carries `AuditContext` snapshots between the outcome event and the trades-response arrival. Critical invariants:

- Stored on EXECUTED outcome (not on REJECTED, not on DRYRUN orders)
- **Always popped** on `_on_trades_response`, regardless of `response.success` — prevents leaks (Risk 4)
- On `shutdown()`: any unfinished entries are logged as a warning and the dict is cleared

The `AuditContext.submission_tick_mid_price` field is `None` for synthetic cleanup pendings (scenario-end force-close — see `architecture_execution_layer.md` *End-of-scenario cleanup*). The SLIPPAGE comparison branch checks `if not None` and skips automatically — a force-close liquidation has no algo-initiated submission moment, so there is no slippage to measure.

## Live Display Footer

`AutoTraderDisplayStats` carries the audit counter fields (populated from `DriftAuditor.get_display_counters()` in `_build_display_stats`):

```python
drift_enabled: bool = False
drift_audited: int = 0
drift_fee_events: int = 0
drift_volume_events: int = 0
drift_price_events: int = 0
drift_slippage_events: int = 0
drift_max_fee_pct: float = 0.0
drift_max_slippage_pct: float = 0.0
```

Renderer adds a conditional line to the SESSION panel, width-aware:

```
Audit:   ✓47 │ ⚠3 fee (max 4.8%) │ ⚠0 vol │ 47 price │ ◇47 slip (max 0.05%)   (wide, ≥120 cols)
Audit:   ✓47 │ ⚠3F │ ⚠0V │ 47P │ ◇47S                                          (compact, <120 cols)
```

Styling:
- `[green]` for ✓ healthy audit counter
- `[yellow]` for FEE / VOLUME drift (actionable bug signal)
- `[dim]` for PRICE counter (Kraken-intra-reporting consistency check)
- `[cyan]` for SLIPPAGE counter (real submission-to-fill cost — structural, market-reality measurement)

## Performance Budget

Drift audit overhead per filled order:
- Snapshot creation: a few dataclass field reads + dict insert
- Trades-query: +2 Kraken ops (`QueryOrders` + `QueryTrades` — counts against account rate limit)
- Comparison: three arithmetic deltas, a dict insert, conditional log

Target: <1 ms per filled order excluding the network roundtrip. Verified during the throughput benchmark when present in the run.

If rate-limit pressure ever becomes a concern (very-high-frequency strategies), `sample_rate < 1.0` reduces the audit cadence. V1.3 default is 1.0 (audit every fill — production trading frequencies make this trivially cheap).

## Files

- `python/framework/trading_env/live/drift_auditor.py` — DriftAuditor class
- `python/framework/types/live_types/drift_audit_types.py` — `DriftType`, `DriftRecord`, `AuditContext`, `DriftAuditSummary`
- `python/framework/types/config_types/autotrader_defaults_config_types.py` — `DriftAuditConfig`
- `python/framework/types/trading_env_types/submission_metadata_types.py` — `SubmissionMetadata` (#345): the submission-moment snapshot carried as one typed field
- `python/framework/types/trading_env_types/latency_simulator_types.py` — `PendingOrder.submission`
- `python/framework/types/trading_env_types/order_types.py` — `OrderResult.submission`
- `python/framework/types/portfolio_types/portfolio_types.py` — `Position.entry_submission`
- `python/framework/types/portfolio_types/portfolio_trade_record_types.py` — `TradeRecord.entry_submission` / `exit_submission`
- `python/framework/trading_env/portfolio_manager.py` — propagation through `open_position` / `close_position_portfolio` / `partial_close_position` / `_create_trade_record`
- `python/framework/trading_env/abstract_trade_executor.py` — listener signature extension; submission-tick capture in `_fill_open_order` / `_fill_close_order`
- `python/framework/trading_env/live/live_trade_executor.py` — `submit_trades_query_async()` delegating method, `add_trades_response_consumer()`, fan-out in `_handle_trades_response`; submission-tick capture at MARKET/LIMIT-open and close submission gates
- `python/framework/trading_env/live/live_request_processor.py` — `register_pending_open` / `register_pending_close` accept a `submission: SubmissionMetadata` parameter
- `python/framework/trading_env/simulation/order_latency_simulator.py` — submission-tick capture at sim open/close submission
- `python/framework/autotrader/autotrader_main.py` — DriftAuditor instantiation gated by config + isinstance(LiveTradeExecutor) + shutdown hook
- `python/framework/autotrader/autotrader_tick_loop.py` — display-stats population via `_drift_display_counters()`
- `python/system/ui/autotrader_live_display.py` — Audit footer in SESSION panel (4 counters incl. SLIPPAGE)
- `python/framework/reporting/event_stream_csv_writer.py` — `EVENT_FIELDS` extended with `submission_tick_mid_price` / `submission_tick_time_msc` columns on `ORDER_SUBMIT` / `CLOSE_SUBMIT` / `POSITION_OPEN` rows (CSV column names unchanged by #345)

## Related

- **#326 Broker Trade Record Model** — provides the `BrokerTrade` per-execution detail that DriftAuditor compares against. The async pipeline (`submit_trades_query_async` → drain → `_handle_trades_response`) was wired in #326; #327 is its first productive consumer.
- **#319 Multi-listener foundation** — DriftAuditor uses `add_order_outcome_listener` and mirrors the pattern with `add_trades_response_consumer`.
- **#320 Polling Cadence** — orthogonal. #320 manages order-status polling (every 5 s); the audit triggers a one-shot post-fill trades-query (per filled order, not periodic).
- **#244 Crypto Spread Simulation** — the direct beneficiary of the SLIPPAGE channel. Per-trade slippage records become the empirical calibration baseline for the volatility-factor spread reconstruction model.
- **#330 Multi-Fill Visibility** — the SLIPPAGE channel writes through the same `Position`/`TradeRecord` propagation path established for `entry_trades`/`exit_trades`. The event-stream CSV (`events.csv`) carries the `submission_tick_mid_price` / `submission_tick_time_msc` columns on `ORDER_SUBMIT` / `CLOSE_SUBMIT` / `POSITION_OPEN` rows.
- **#332 Live Field Study** — runs the full DriftAuditor pipeline end-to-end against a real broker, captures all four drift types (FEE / VOLUME / PRICE / SLIPPAGE) in the JSONL artifact. The `slippage` block is the first concrete production data for #244 calibration.
- **#337 Kraken Fee Tier Auto-Detection** — closes the loop on FEE drift root cause when DriftAuditor surfaces a tier mismatch.
- **#151 Reconciliation Layer (V1.4)** — drift audit is observability; reconciliation is correction. Audit feeds #151's design.

---

## Documentation Maintenance — Post-#151 Cleanup

Several passages in this document reference the Reconciliation Layer (#151) as a *future / deferred* capability — phrased in V1.x temporal terms ("Correction is reserved for the future Reconciliation Layer", "Correction is deferred to #151", etc.). Once #151 lands, those forward-references should be reframed from deferred-language to the established architecture split: **drift detection lives here, state correction lives in the Reconciliation Layer.** Grep target for the cleanup pass: `#151` in this file. The same pattern applies to other V1.x docs that mention "deferred to #151" — collect the cleanup as part of #151's documentation deliverable.
