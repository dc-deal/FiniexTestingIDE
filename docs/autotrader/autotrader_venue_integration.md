# AutoTrader Venue Integration

A live session talks to a venue we do not control, over a network that fails, using a contract
their documentation describes and their servers implement — and the two are not always the same
thing. Every answer this document gives was either measured against the venue or is marked as
unmeasured.

This document is the outward-facing half: where a broker's configuration comes from at boot, what
the Kraken adapter's execution tier maps to, and the one rule that decides what a failed external
call means.

**Not here:** how to build an adapter — `docs/user_guides/adapter/adapter_development_guide.md`.
The connection policy itself — `docs/architecture/external_connection_policy.md`. What a fill does
to the books — `autotrader_capital_and_safety.md`.

## Live Broker Config Acquisition (#230)

For `adapter_type='live'`, AutoTrader fetches broker config and account balance from the Kraken REST API at startup instead of relying solely on static JSON.

### Startup Flow (Live Mode)

```
create_broker_config(config, logger)   (autotrader_broker_config_setup.py)
  → config_mode=DYNAMIC (from market_config.json)
  → entry = MarketConfigManager().get_broker_entry(broker_type)
  → KrakenConfigFetcher(entry.credentials_file, entry.broker_transport.api_base_url)
  → fetch_broker_config_with_cache(symbol, broker_type)
       cache < 7 days old  → use silently, no API call
       cache 7–30 days     → try GET /0/public/AssetPairs; on failure: warn + use cache
       cache > 30 days     → try GET /0/public/AssetPairs; on failure: strong stale warning + use cache
       no cache at all     → GET /0/public/AssetPairs; on failure: hard error (first run)
  → fee_structure ← the git-tracked seed, replacing whatever the cache carried (#337)
  → POST /0/private/TradeVolume → the account's real fee tier
       auto_detect_fee_tier=true   → applied for this session
       auto_detect_fee_tier=false  → NOT applied; a divergence still WARNS, naming both rates
       fetcher cannot answer        → nothing happens, the declared rates stand
  → POST /0/private/Balance → account balance (live: the profile declares none; fetched for the symbol's base/quote)
  → BrokerConfigFactory.from_serialized_dict(config_dict)
  → adapter.enable_live(credentials_file, dry_run, transport)  ← Tier 3 activation
  → return BrokerConfig with live-enabled KrakenAdapter
```

**Cache location:** `data/runtime/brokers/<broker_type>/` (gitignored, auto-refreshed weekly).  
**Static seed:** `configs/brokers/kraken/kraken_spot_broker_config.json` — git-tracked, never
auto-overwritten. Used by `config_mode=static` brokers, and its `fee_structure` is the declared rate
for EVERY reader: a backtest takes it whole, and a dynamic live session starts from it before the
venue is asked (#337).
**Balance fetch failure** is **fatal** — a 0.0 balance in live mode is dangerous.

**Mock mode**: Completely unchanged. No API calls, no credentials needed, `enable_live()` never called.

### Account Currency & Balance Semantics

For a **mock** session, `scenario_settings.balances` sets the starting capital (a scenario replay
needs real balances, like the sim) and determines how P&L is denominated internally. For a **live**
session there is no profile balances block — the broker's real balances are fetched at startup for
the symbol's base/quote currencies (resolved authoritatively from the symbol spec, #265). The
account currency is derived at startup from the balances keys matched against the symbol's
base/quote currencies (quote currency preferred). An optional `scenario_settings.account_currency`
override allows explicit control.

**Rules:**
- At least one key in `scenario_settings.balances` must match either the **base** or **quote** currency of the traded symbol (mock).
- Live balances come from the broker for the symbol's base/quote currencies — the profile declares none.
- Cross-currency accounts (e.g., `balances: {"EUR": 100}` with `SOLUSD`) are not supported and raise a `NotImplementedError` at startup.

**Account currency derivation (in order):**
1. Explicit `scenario_settings.account_currency` if set → used as-is
2. Quote currency of symbol if present in balances → e.g., USD for ETHUSD
3. Base currency of symbol if present in balances → e.g., ETH for ETHUSD
4. First key in balances (fallback)

**Supported configurations for Spot trading:**

| `scenario_settings.balances` | `account_currency` | Symbol | Meaning |
|---|---|---|---|
| `{"USD": 100}` | (omitted) | `SOLUSD` | P&L in USD — recommended for multi-pair setups |
| `{"SOL": 0, "USD": 100}` | (omitted) | `SOLUSD` | Dual-balance, P&L in USD (quote, default) |
| `{"ETH": 0, "USD": 50}` | `"ETH"` | `ETHUSD` | Dual-balance, P&L in ETH (explicit override) |

**Recommendation:** Use `"USD"` as account currency for all spot pairs. USD is the quote currency
across all Kraken USD pairs — one balance covers all symbols, P&L is always in USD (consistent with
backtesting), and no per-symbol currency management is needed. Use `account_currency` override only
when explicitly needed (e.g., testing P&L in base currency).

**What happens after trades:** If a BUY fills, the base asset increases and quote decreases (and
vice versa for SELL). The AutoTrader only tracks the configured currency — the other side
accumulates silently on the Kraken account. This is expected Spot behavior. The Reconciliation Layer
(#151) will address cross-session position awareness.

### Broker Connection Settings

Broker-specific live settings are stored in `market_config.json` alongside the broker entry — not in the AutoTrader profile:

```
Profile (production/ethusd_live.json)           ← Algorithm config (strategy, workers, symbol)
  "broker_type": "kraken_spot"
        |
market_config.json → kraken_spot     ← Broker connection config
  "credentials_file", "dry_run", "broker_transport.{api_base_url, rate_limit_interval_s, ...}"
        |
Credentials (kraken_credentials.json) ← Only API keys
```

To override connection settings (e.g., `dry_run: false` for live trading), create
`user_configs/market_config.json` with the changed fields. See
[Kraken Adapter Setup Guide](../user_guides/adapter/setup_kraken_adapter.md) for full configuration
details.

### Credentials Cascade

Credentials follow the project-wide `configs/` → `user_configs/` override pattern:

1. `user_configs/credentials/kraken_credentials.json` — user override (gitignored, real keys)
2. `configs/credentials/kraken_credentials.json` — tracked default (mock values)

The `credentials_file` in broker settings is just the filename (e.g., `"kraken_credentials.json"`). The cascade is resolved automatically.

### API Authentication

Private Kraken endpoints use HMAC-SHA512 signing: `API-Sign = base64(HMAC-SHA512(url_path + SHA256(nonce + post_data), base64_decode(api_secret)))`. The `nonce` is an increasing integer (millisecond timestamp).

### Fee Handling

The declared rate lives in ONE place — the broker's git-tracked seed — and every other reader points
at it (#337). A backtest reads it and nothing else, so a run stays reproducible from a commit; a
live session starts from the same number, then asks the venue.

Asking is `POST /0/private/TradeVolume`, and what happens with the answer is split in two on purpose:

| | `auto_detect_fee_tier: false` (default) | `auto_detect_fee_tier: true` |
|---|---|---|
| the session prices with | the declared rate | the venue's rate |
| a divergence from the seed | WARNS | WARNS |

The warning fires either way, because the failure this replaces was silent: measured 2026-09-08 the
declared 0.25/0.40 were exactly HALF what the account was charged (0.40/0.80 on `XETHZUSD`), and no
run said so. A static default is not automatically the safe one — this one was optimistic, which is
the dangerous direction for a backtest.

Note that the tier depends on 30-day rolling volume, which the bot's own trading moves. So no fetch
can give the "right" rate for a run that has not happened yet: the declared rate is an ASSUMPTION
the record has to pin, which is why `config_hash` covers `fee_structure`. Re-freezing the seed is a
deliberate, dated act — see `docs/broker_config_guide.md`.

## KrakenAdapter Tier 3 — Live Order Execution (#133 Step 3)

Tier 3 adds real Kraken REST API order execution to `KrakenAdapter`. Methods are activated by
calling `enable_live(credentials_file, dry_run, transport)` — without it, the adapter works in Tier
1+2 mode (backtesting only). `transport` is a `BrokerTransportConfig` (api_base_url,
rate_limit_interval_s, request_timeout_s, poll_interval_ms).

### Adapter Tiers

| Tier | Scope | Requires Credentials | Used By |
|------|-------|---------------------|---------|
| 1 | Config validation, broker/symbol specs | No | Backtesting + AutoTrader |
| 2 | Order creation (MarketOrder, LimitOrder, etc.) | No | Backtesting + AutoTrader |
| 3 | Live execution (AddOrder, QueryOrders, CancelOrder, AmendOrder) | Yes | AutoTrader (live mode) |

### Tier 3 API Mapping

| Method | Kraken Endpoint | Key Parameters |
|--------|----------------|----------------|
| `execute_order()` | `POST /0/private/AddOrder` | pair, type, ordertype, volume, price, validate |
| `check_order_status()` | `POST /0/private/QueryOrders` | txid |
| `cancel_order()` | `POST /0/private/CancelOrder` | txid |
| `modify_order()` | `POST /0/private/AmendOrder` | txid, limit_price |

### Dry-Run Mode

Kraken Spot has no testnet/sandbox. Dry-run uses Kraken's native `validate=true` parameter on AddOrder — Kraken validates the order (pair, volume, balance, permissions) but **does not execute it**.

Controlled by `dry_run` in `market_config.json` for the broker type (default: `true` — safe by default). Override in `user_configs/market_config.json` to go live. Console shows `Mode: DRY RUN (validate only)` or `Mode: LIVE TRADING` at startup.

Dry-run behavior:
- `execute_order()`: sends `validate=true`, returns synthetic `DRYRUN-NNNNNN` broker_ref
- `check_order_status()` / `cancel_order()` / `modify_order()`: return synthetic responses (order doesn't exist at broker)

### AmendOrder — In-Place Modify

Kraken's `AmendOrder` amends the order **in place** — the txid (and any client order id) stay the
same, so there is no cancel-replace and no broker_ref swap. `parse_modify_response` returns the
unchanged `broker_ref`; the response carries an `amend_id` for auditing. The
`update_broker_ref(old, new)` swap path remains as a defensive net for brokers that *do* return a
new ref on modify, but it is not exercised by Kraken.

### Rate Limiting

Configurable via `broker_transport.rate_limit_interval_s` in broker settings (default: 1.0s). Simple time-based throttle — minimum interval between private API calls. Conservative but safe for personal use.

Enforced inside the adapter's `_enforce_rate_limit()` (called from every private HTTP call). Because
all broker I/O is funneled through a single worker thread, this gate also serializes async polling
against submits/edits/cancels — no risk of two private API calls landing under the rate window.

### Polling Cadence (#320)

Active LIMIT orders are polled asynchronously through the same worker-thread pattern as
submit/edit/cancel/trades_query. `LiveTradeExecutor._process_active_orders` is a non-blocking
scheduler: for each `_active_limit_orders` entry it either skips (no broker_ref yet, in-flight
query, or inside throttle window) or enqueues a `QueryJob` to the worker. The response is consumed
on the main thread via `drain_inbox` → `_handle_query_response`.

The scheduler runs on the tick path (`on_tick`) **and** on the idle heartbeat (`heartbeat()`, #360)
— so the fill/cancel-confirm query fires during a quiet stretch too, not only when a real tick
arrives. The per-order throttle (`poll_interval_ms`) still gates the actual broker I/O, so a faster
heartbeat does not multiply API calls.

Three gates on the scheduler, all silent skips:

| Gate | Reason |
|------|--------|
| `broker_ref is None` | Submit still in flight at the broker — wait for `_handle_limit_submit_response` |
| `pending.in_flight_query is True` | A previous QueryJob has not returned yet |
| `now_ms - pending.last_polled_at_ms < poll_interval_ms` | Inside the per-order throttle window |

Pathological "stuck in-flight" cases (worker dead, network hung) are caught by the existing `check_timeouts()` mechanism — when `pending.timeout_at` passes, the order is rejected via `_handle_timeout`.

`_handle_query_response` ALWAYS clears `pending.in_flight_query` (the query is resolved either way),
then applies a stale-broker_ref guard before any state mutation. The guard was built for the legacy
EditOrder flip (a QueryJob dispatched before the swap returned the OLD ref while
`pending.broker_ref` already held the NEW one). With in-place `AmendOrder` the txid is stable across
a modify, so the guard no longer fires in normal Kraken flow; it stays as a defensive net (e.g.
brokers that cancel-replace). State mutations are skipped on stale; the next throttle cycle fires a
fresh QueryJob against the current ref.

`poll_interval_ms` is per-broker via `BrokerTransportConfig` (default 5000 ms). Tuning guidance:
5000 ms (default — Kraken-friendly), 1000 ms (scalping), 500 ms (only with rate-limit headroom
verified). MARKET-order polling in `_process_pending_orders` stays sync — low frequency, no rate
pressure.

### Drift Audit (#327)

After every EXECUTED outcome the `DriftAuditor` (wired in `autotrader_main.py` when
`drift_audit.enabled=True`) captures a snapshot of the synthetic state (`pending.cumulative_fee` /
`cumulative_avg_price` / `cumulative_filled_lots`) and fires a one-shot
`submit_trades_query_async()` against the broker. When the per-execution `TradesQueryResponse`
arrives via `drain_inbox`, the executor's `_handle_trades_response` fan-outs to all registered
`_trades_response_consumers` — including DriftAuditor — which then compares snapshot vs. broker
truth across FEE / VOLUME / PRICE dimensions, logs drift events above their thresholds, and surfaces
counters in the SESSION panel `Audit:` line.

Strict read-only — no state mutation, no portfolio adjustment. Correction is deferred to the future Reconciliation Layer (#151). Detailed architecture: [architecture/drift_audit.md](../architecture/drift_audit.md).

The listener signature `add_order_outcome_listener(callback)` was extended to
`Callable[[OrderDirection, OrderResult, Optional[PendingOrder]], None]` to give consumers the
pending reference at outcome time. OrderGuard's adapter accepts the new arg and ignores it.
Pre-submit rejections (no PendingOrder yet) pass `pending=None` explicitly at the single relevant
call site (`_record_async_rejection` in `live_trade_executor.py`).

### Symbol Mapping

Standard symbols (e.g., `BTCUSD`) are mapped to Kraken pair names (e.g., `XBTUSD`) for order API calls via the `kraken_pair_name` field in the broker config JSON (static seed or runtime cache). If the field is absent, the symbol key is used as-is.

## External Connections — one ladder, one give-up rule (#473)

A live session holds seven connections to things outside its own process. They now share
one classification and one vocabulary; the full policy is
[architecture/external_connection_policy.md](../architecture/external_connection_policy.md).
Two consequences visible in this pipeline:

**The tick loop cannot die of a broker fault.** The reconcile truth pull and the order
re-poll are already cadenced, so on a transient failure the cycle is **skipped** rather
than retried inline — no sleep in the loop, no shifted heartbeat, and the cadence is the
ladder. Before this, one 502 from a public venue propagated to `autotrader_main` and ended
the session in an emergency shutdown.

```
14:32:07  🔍 reconcile #47: SKIPPED — broker truth unreachable (HTTP 502) · next attempt in 30s
14:32:37  🔍 reconcile #48: clean — broker_orders=1 local_orders=1
```

A skipped cycle is **not clean** (nothing was compared) and is counted separately
(`reconcile_skipped` on the SESSION panel) — a reconcile count climbing against a dead
venue would be "gave up" wearing the face of "still checking".

**Boot reads differ on purpose.** The signal producer registry **degrades** (the staleness
contracts describe the reduced state and the boot bridge mounts the archive slice, so the
session starts STALE rather than blind); warmup bars and the account balance **refuse to
start**, because neither has a contract to degrade into.

---
