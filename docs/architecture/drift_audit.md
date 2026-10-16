# Drift Audit (#327)

Read-only telemetry channel that compares what a live fill was **booked with** against the
**venue's own executions**, delivered via the #326 async trades-query pipeline — for every fill, the
entry and, since #508, the exit. Surfaces partial-fill mismatches and, where a venue reports no fee
per fill, fee-model bugs and tier mismatches — without mutating any state; correction is reserved
for the future Reconciliation Layer (#151).

Since #508 a live fill books the venue's own fee from the answer that reported it, wherever the
venue reports one there, so on Kraken the FEE channel compares two of the venue's figures: the
order's total fee from that answer against the sum of its executions' fees. It is what keeps
measuring, on every real fill, that the fee really is complete in the first answer that says the
order filled. Where a venue reports no fee per fill, the booked fee is the estimate and the channel
measures the model against the venue, as it always did. On Kraken a configured rate the account is
not charged no longer shows in this channel; the fee-tier check at session start (#337) is what
catches it there.

A protective order the venue holds is booked in steps while it works, and its executions describe
the whole order. So it is audited once, by the booking that closes its position, against everything
booked for it, a restarted session's predecessor included. One the venue ends after part of it
executed leaves its position open and is not audited. Its SLIPPAGE is not measured: the mid at its
placement measures the distance to the stop, not what the fill cost.

## Concept

Every fully-filled order has two views of itself once it leaves our pipeline:

| View | Source | Fee | Volume | Avg Price |
|---|---|---|---|---|
| **Booked** | `_synthesize_pending_trade` at the fill, carrying the booked fee | the venue's figure from its status answer (#508), the model's estimate otherwise | requested lots — booked lots for a close | broker fill_price (passed through) |
| **Broker truth** | `QueryTrades` response → `BrokerTrade` records | broker `fee` field | sum of execution volumes | volume-weighted mean of execution prices |

DriftAuditor consumes both, computes the relative delta per dimension, and counts events that exceed
configurable thresholds. Strict read-only — no state mutation, no portfolio adjustment, no error
gating. The detected drift is the operator's signal that something in the local model has diverged
from broker reality.

## Architecture

DriftAuditor is a **reactive observer**, not a poller. It reacts to a booked fill — an entry's
`EXECUTED` outcome, or a close through the executor's close-fill listeners (#508), because a close
deliberately reaches no order-outcome listener — fires exactly one `submit_trades_query_async()` to
obtain the venue's per-execution truth, consumes the response asynchronously, and logs the
comparison. It asks only where the venue declares `trade_level_reporting`; a venue that answers no
trades query has nothing to audit against.

```
Order fills (one execution synthesized via _synthesize_pending_trade, with the booked fee)
  ↓
_fill_open_order / _fill_close_order runs (portfolio, history append)
  ↓
entry: _notify_outcome(EXECUTED, result, pending)     close: _on_close_fill(pending, result)
  ↓                                                       ↓
DriftAuditor._on_order_outcome                         DriftAuditor._on_close_fill
  - snapshot what was booked from pending.cumulative_*
  - store in self._pending_audits[broker_ref]
  - executor.submit_trades_query_async(order_id, broker_ref)
        ↓ (worker thread)
        TradesQueryJob → HTTP /0/private/QueryOrders + /0/private/QueryTrades
        TradesQueryResponse → _http_inbox

(later, on next drain_inbox)
TradesQueryResponse arrives → executor._handle_trades_response
  ↓ fan-out to registered trades-response consumers — the executor books nothing from it (#508)
DriftAuditor._on_trades_response:
  - pop snapshot from self._pending_audits by broker_ref (always — no leak on failure)
  - compute real cumulative_* from response.trades (immutable)
  - compare snapshot.synthetic_* vs real_*
  - log [DRIFT] event if over threshold; increment counters
```

Critical: **two inbox drain cycles**. The trades-query response cannot arrive in the same drain that
triggered it — it requires a separate HTTP roundtrip on the worker thread. This is async-correct by
design (no tick-loop blocking). V1.3 Pilot Run baseline for fill-to-trades-query latency: ~2000 ms.

## ID Correlation Across the Async Roundtrip

Async dispatch + worker thread + drain consumer = multiple hops where the response must be routed
back to the correct snapshot. Robust correlation relies on
**two independent IDs at different layers** — our internal `order_id` carried through all hops as
the routing key, and the broker's `broker_ref` (txid) used for the API call. A third per-execution
`trade_id` identifies individual fills within an order.

| ID | Owner | Lifetime | Used for |
|---|---|---|---|
| `PendingOrder.pending_order_id` | **Us** — assigned at submission, e.g. `pos_ethusd_3` | Order lifetime | Primary routing key. Listener correlation. Log identifier. NOT unique across a position's entry and its closes, which share it |
| `PendingOrder.broker_ref` | **Broker** — Kraken txid (e.g. `OPRSKJ-IAYTG-T5VB2M`), MT5 ticket | Order lifetime, **stable across modify** (Kraken `AmendOrder` is in-place; #320 stale-ref guard now defensive) | The handle the API call needs (`POST /0/private/QueryOrders` with `txid=<broker_ref>`). DriftAuditor's `_pending_audits` dict key: unique per venue order, so an entry's audit and its position's close's cannot collide (#508). |
| `BrokerTrade.trade_id` | **Broker** on an execution the venue reports — Kraken tradeid (e.g. `TKH2SE-M7IF5-CFI7LT`), MT5 deal ticket; **Us** (`SYNTH-<order_id>-<n>`) on the one recorded on an order when its fill is booked | Permanent | Per-execution receipt, never reused. The trade history carries the recorded execution, so its ids are ours (#508). |
| `BrokerTrade.parent_broker_ref` | **Broker** — copy of the parent order's `broker_ref` | Permanent | Trade → parent order link on the broker side. |
| `BrokerTrade.order_id` | **Us** — written by the adapter's `parse_trades_query_response` | Permanent | Trade → internal order link. The bridge that makes drain-side routing possible without re-lookups. |

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
  self._pending_audits[broker_ref] = snapshot

  executor.submit_trades_query_async(
    order_id   = pending.pending_order_id,    ──┐  both IDs ride in
    broker_ref = pending.broker_ref,          ──┘  the TradesQueryJob
  )

                              TradesQueryJob arrives on worker
                              adapter.build_trades_query_payload(broker_ref)
                                → broker API call uses broker_ref
                              adapter.do_request_trades_query(...)
                                → Kraken returns raw trades
                              adapter.parse_trades_query_response(
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
                                                              fan-out to consumers (books nothing, #508):
                                                                DriftAuditor._on_trades_response(response):
                                                                  snapshot = self._pending_audits.pop(
                                                                      response.broker_ref        ← unique per
                                                                  )                                venue order
                                                                  compare snapshot vs.
                                                                       sum(response.trades.fee/volume/price)
```

### Why two IDs, not one

The split is deliberate and non-removable:

- **`broker_ref` is broker-assigned, not ours.** We cannot choose it, and historically it could even
  change mid-life — the legacy EditOrder was cancel-replace and flipped the txid, which the #320
  stale-ref guard was built to absorb (a query dispatched before the modify returned the OLD ref
  while the in-flight pending already held the NEW one). Since the switch to in-place `AmendOrder`
  the txid is stable across a modify, so the guard no longer fires in normal Kraken flow — it stays
  as a defensive net for brokers that do cancel-replace.
- **Our `order_id` alone cannot drive the broker API.** Kraken does not know our internal naming — it needs its own txid.
- **The bridge is written by the adapter** in `parse_trades_query_response`. The adapter receives
  our `order_id` as input to the parse step (carried through the worker dispatch), embeds it in
  every `BrokerTrade` it produces, and threads it into the `TradesQueryResponse`. From that moment
  on, the response is fully routable on the drain side without re-looking-up anything.

### Why the snapshot is keyed by the venue's reference

The executor's `_handle_trades_response` mutates nothing since #508 — it only fans the response
out — so the stale-reference guard it used to apply before appending executions to an order is gone
with the appending. **DriftAuditor's `_on_trades_response`** pops its snapshot by
`response.broker_ref`, the reference the query was made with. Keyed by `order_id` it could not tell
an entry from its position's close: both carry the position's id, and a close that fills within the
two seconds the entry's executions take would have replaced the entry's snapshot.

The only response that DriftAuditor truly ignores is one where the snapshot was never created (no
matching entry in `_pending_audits` — e.g., trades-query triggered by something other than us, or
already-popped by an earlier response for the same reference).

### The same pattern on MT5

The same two-ID pattern carries to MT5 (#209) with broker-specific value spaces:

| Layer | Kraken | MT5 |
|---|---|---|
| Our internal `order_id` | `pos_ethusd_3` | `pos_ethusd_3` (same — adapter-agnostic) |
| Broker `broker_ref` | Kraken txid (`OPRSKJ-...`) | MT5 ticket (numeric, e.g. `123456789`) |
| Per-execution `trade_id` | Kraken tradeid (`TKH2SE-...`) | MT5 deal ticket (numeric) |

The adapter abstraction (`AbstractAdapter.build_trades_query_payload` / `parse_trades_query_response`) hides the broker-specific shape — the drain-side code only sees our internal `order_id` for routing.

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

The processor's `_trades_response_hook` is single-slot (executor registers itself). To let DriftAuditor receive the response, the **executor**'s `_handle_trades_response` is a multi-consumer fan-out, and since #508 that is all it is — it books nothing and writes nothing onto the order:

```python
def _handle_trades_response(self, response: TradesQueryResponse) -> None:
    if not response.success:
        self.logger.warning(f"TradesQueryResponse error for {response.order_id}: ...")
    # Every response, failures included, so consumers can clean up their own
    # tracking state (Risk 4 — no leaks)
    for consumer in self._trades_response_consumers:
        try:
            consumer(response)
        except Exception as e:
            self.logger.error(f'trades_response consumer raised: {e}', exc_info=True)
```

**Consumer contract:** read from `response.trades` (the immutable response object), NOT from `pending.fills.trades` — those are the executions the executor synthesized for its own booking, not the venue's.

Per-consumer try/except — one bad consumer cannot kill the chain (Risk 2 mitigation).

The processor's single-hook contract stays unchanged — multi-consumer logic lives at the executor level (where consumers logically live).

## Drift Types

| Type | What is compared | Default threshold |
|---|---|---|
| `FEE` | `pending.cumulative_fee` — the fee BOOKED, the venue's own from its status answer where it reports one (#508) — vs. `sum(BrokerTrade.fee)` (broker) | 0.5 % |
| `VOLUME` | `pending.requested_lots` (local; a close's booked lots) vs. `sum(BrokerTrade.volume)` (broker) | 0.1 % |
| `PRICE` | `pending.cumulative_avg_price` (local) vs. volume-weighted mean of broker trades | 1.0 % |
| `SLIPPAGE` | `pending.submission.tick_mid_price` (local) vs. volume-weighted mean of broker trades | 0.5 % |

**PRICE channel scope.** The PRICE counter compares Kraken's QueryOrder summary price against
Kraken's QueryTrades per-execution average — both come from the broker. It detects
broker-internal-reporting inconsistencies (one-off rounding edge cases between the two Kraken
endpoints), not market-reality cost. Useful as a sanity check; do not interpret a sustained PRICE
count as an action signal.

**SLIPPAGE channel scope.** The SLIPPAGE counter compares the trade-channel tick mid-price captured
at submission (`PendingOrder.submission.tick_mid_price`) against the volume-weighted mean of broker
trades. Both sides come from *independent* sources — our tick feed and the broker's matching engine
— so the delta is the **real cost the operator paid** (spread + intra-latency market drift +
book-walking on larger orders). This is the empirical baseline #244 (Crypto Spread Simulation) is
meant to consume for spread reconstruction. The records do not outlive the session yet: #587 keeps
them as an artifact of the run, and until then #244 has nothing to read.

## Slippage vs. Spread — Conceptual Clarity

Two distinct concepts that get conflated easily:

| Concept | Definition | Layer |
|---|---|---|
| **Spread** | Bid-Ask gap at a single moment | Quote / order-book property |
| **Slippage** | Expected (reference) price vs. actual fill price | Execution-event property — broader |

Slippage is the broader concept and **contains** the spread effect, **plus** any market movement
during the submission-to-fill window, **plus** book-walking on larger orders. For Crypto
trade-channel feeds (Kraken `bid == ask == last`) slippage is dominated by the spread component but
the metric remains event-based and post-fill. MT5-style brokers with real bid/ask in the tick data
still feed the same formula — `submission.tick_mid_price = (bid + ask) / 2` — and the audit
value-add is the latency-window drift component on top of the half spread the fill price already
carries.

### What the literature calls this

The execution-cost literature names this measurement **arrival cost** (also: trading cost) — the
difference between the average execution price and the market price at the moment the order
entered the market. It is described there as the single most important metric for judging a
venue, a broker or an algorithm, which is what this channel is for.

One asymmetry is worth knowing before comparing a number here against one from that literature:
it uses "slippage" as a synonym for the WHOLE implementation shortfall, while the SLIPPAGE
channel measures only the arrival-cost component of it. The other two components are the delay
cost between decision and dispatch — structurally zero in this framework, because a bot decides
and submits within one tick — and the opportunity cost of an order that never filled, which
nothing here measures today. A reader who expects the two definitions to coincide will otherwise
find a gap that is not one.

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
```

Wired through the autotrader cascade (`app_config.json` → profile override). Every audited fill triggers a post-fill trades-query (+2 ops on Kraken Tier-3 budget per fill) — see Performance Budget.

Backtesting pipeline does NOT wire the auditor (sim has no broker truth distinct from the local fee model — comparison would be tautological).

## Snapshot Lifecycle

The `_pending_audits` dict carries `AuditContext` snapshots between the outcome event and the trades-response arrival. Critical invariants:

- Stored on an entry's EXECUTED outcome and on a booked close (#508) — not on REJECTED, not on
  DRYRUN orders, not where the venue declares no trade-level reporting; keyed by `broker_ref`
- **Always popped** on `_on_trades_response`, regardless of `response.success` — prevents leaks (Risk 4)
- On `shutdown()`: any unfinished entries are logged as a warning and the dict is cleared

The `AuditContext.submission_tick_mid_price` field is `None` wherever a pending carries no
algo-initiated submission moment — the SLIPPAGE comparison branch checks `if not None` and skips
automatically, so there is nothing to measure and nothing to guard. The end-of-run force-close that
used to produce such pendings was removed with #492 (see
[session_end_policy.md](session_end_policy.md)); the field's `None` case remains for every other
path that has no submission snapshot.

## Live Display Footer

`AutoTraderDisplayStats` carries the audit counter fields (populated from `DriftAuditor.get_display_counters()` by `AutotraderDisplayExporter`):

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
Audit:   ✓47 │ ⚠0 fee │ ⚠0 vol │ 0 price │ ◇0 slip (max 0.01%)   (wide, ≥120 cols)
Audit:   ✓47 │ ⚠0F │ ⚠0V │ 0P │ ◇0S                              (compact, <120 cols)
```

Styling:
- `[green]` for ✓ healthy audit counter
- `[yellow]` for FEE / VOLUME drift (actionable bug signal)
- `[dim]` for PRICE counter (Kraken-intra-reporting consistency check)
- `[cyan]` for SLIPPAGE counter (real submission-to-fill cost — structural, market-reality measurement)

## Performance Budget

Drift audit overhead per fill — an entry and, since #508, every close:
- Snapshot creation: a few dataclass field reads + dict insert
- Trades-query: +2 Kraken ops (`QueryOrders` + `QueryTrades` — counts against account rate limit),
  so a round trip costs four
- Comparison: three arithmetic deltas, a dict insert, conditional log

Target: <1 ms per filled order excluding the network roundtrip. Verified during the throughput benchmark when present in the run.

There is no knob that lowers the audit cadence: every fill is audited, entry and close, which
production trading frequencies make cheap. A very-high-frequency strategy would need one added.

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
- `python/framework/trading_env/live/live_trade_executor.py` — `submit_trades_query_async()`
  delegating method, `add_trades_response_consumer()`, fan-out in `_handle_trades_response`;
  submission-tick capture at MARKET/LIMIT-open and close submission gates
- `python/framework/trading_env/live/live_request_processor.py` — `register_pending_open` / `register_pending_close` accept a `submission: SubmissionMetadata` parameter
- `python/framework/trading_env/simulation/order_latency_simulator.py` — submission-tick capture at sim open/close submission
- `python/framework/autotrader/autotrader_main.py` — DriftAuditor instantiation gated by config + isinstance(LiveTradeExecutor) + shutdown hook
- `python/framework/autotrader/autotrader_display_exporter.py` — display-stats population via `_drift_display_counters()`
- `python/system/ui/autotrader_live_display.py` — Audit footer in SESSION panel (4 counters incl. SLIPPAGE)
- `python/framework/reporting/event_stream_csv_writer.py` — `EVENT_FIELDS` extended with `submission_tick_mid_price` / `submission_tick_time_msc` columns on `ORDER_SUBMIT` / `CLOSE_SUBMIT` / `POSITION_OPEN` rows (CSV column names unchanged by #345)

## Related

- **#326 Broker Trade Record Model** — provides the `BrokerTrade` per-execution detail that
  DriftAuditor compares against. The async pipeline (`submit_trades_query_async` → drain →
  `_handle_trades_response`) was wired in #326; #327 is its first productive consumer.
- **#319 Multi-listener foundation** — DriftAuditor uses `add_order_outcome_listener` and mirrors the pattern with `add_trades_response_consumer`.
- **#320 Polling Cadence** — orthogonal. #320 manages order-status polling (every 5 s); the audit triggers a one-shot post-fill trades-query (per filled order, not periodic).
- **#244 Crypto Spread Simulation** — the direct beneficiary of the SLIPPAGE channel. Per-trade slippage records become the empirical calibration baseline for the volatility-factor spread reconstruction model.
- **#330 Multi-Fill Visibility** — the SLIPPAGE channel writes through the same
  `Position`/`TradeRecord` propagation path established for `entry_trades`/`exit_trades`. The
  event-stream CSV (`events.csv`) carries the `submission_tick_mid_price` /
  `submission_tick_time_msc` columns on `ORDER_SUBMIT` / `CLOSE_SUBMIT` / `POSITION_OPEN` rows.
- **#332 Live Field Study** — a real-broker session, so the auditor runs in it as in any live
  session; its figures reach only the Audit footer and the session log. The study's JSONL artifact
  carries none of the four drift types: each fill line carries what its slippage is measured
  against, and the certificate computes its own `slippage` block from those lines. That block is
  the first concrete production data for #244 calibration.
- **#337 Kraken Fee Tier Auto-Detection** — the detector of a tier mismatch on Kraken, at session start. Since #508 the FEE channel compares the venue's figures with each other there, so it no longer sees one.
- **#151 Reconciliation Layer (V1.4)** — drift audit is observability; reconciliation is correction. Audit feeds #151's design.

---

## Documentation Maintenance — Post-#151 Cleanup

Several passages in this document reference the Reconciliation Layer (#151) as a *future / deferred*
capability — phrased in V1.x temporal terms ("Correction is reserved for the future Reconciliation
Layer", "Correction is deferred to #151", etc.). Once #151 lands, those forward-references should be
reframed from deferred-language to the established architecture split:
**drift detection lives here, state correction lives in the Reconciliation Layer.** Grep target for
the cleanup pass: `#151` in this file. The same pattern applies to other V1.x docs that mention
"deferred to #151" — collect the cleanup as part of #151's documentation deliverable.
