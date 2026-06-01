# Broker Adapter Development Guide

How to implement a new broker adapter from scratch.

**Reference implementations:**
- `python/framework/trading_env/adapters/kraken_adapter.py` — real HTTPS broker (Kraken Spot REST API)
- `python/framework/testing/mock_broker_adapter.py` — in-process mock; the cleanest end-to-end template

Read either alongside this guide.

---

## Overview

Every adapter lives in `python/framework/trading_env/adapters/` and extends `AbstractAdapter`. The interface has three tiers:

| Tier | When active | What it provides |
|------|-------------|------------------|
| **Tier 1** — Config & Symbol Specs | Always (backtesting + live) | `get_symbol_specification`, `get_broker_specification`, `validate_order` |
| **Tier 2** — Order Object Construction | Always | `create_market_order`, `create_limit_order`, `create_stop_limit_order`, `create_iceberg_order` |
| **Tier 3** — Live Execution | After `enable_live()` | Twelve methods: `_build_*_payload` / `_do_request_*` / `_parse_*_response` × four operations (submit/query/cancel/modify) |

Tier 1+2 are purely constructors and validators. Tier 3 talks to the real broker — but is decoupled into three pure layers so the orchestration (async dispatch, lifecycle management, dry-run handling) happens outside the adapter in `LiveRequestProcessor`.

---

## The Tier-3 Layer Contract

Per operation (`submit` / `query` / `cancel` / `modify`), every live-capable adapter must implement three layers:

```
  _build_<op>_payload    →    _do_request_<op>    →    _parse_<op>_response
  (pure, no I/O)              (transport)              (pure, raw → BrokerResponse)
```

| Layer | Allowed | Forbidden |
|---|---|---|
| `_build_<op>_payload` | parameter packing, broker-specific format conversion | I/O, state mutation, reading `self._dry_run` |
| `_do_request_<op>` | actual transport (HTTPS, ZeroMQ, RPC), state mutation (rate-limit counters, dry-run state), **must raise on transport error** | parsing raw to BrokerResponse, lifecycle management |
| `_parse_<op>_response` | raw dict → `BrokerResponse` conversion, status mapping, fill data extraction | I/O, state mutation, retry logic |

`LiveRequestProcessor.submit_open_order` (sync), `submit_open_order_async` (worker-thread), `query_order_sync`, `cancel_order_sync`, `modify_order_sync` compose these layers. The adapter author never needs to think about threading, queues, or main-thread safety — that's the processor's job.

### The Four Operations

| Op | Purpose | Aufrufer |
|---|---|---|
| `submit` | Place a new order at the broker | `open_order(MARKET)`, `open_order(LIMIT)`, `close_position` |
| `query` | Poll an existing order's status | `_process_pending_orders` Phase-1 / Phase-2 polling |
| `cancel` | Withdraw a pending order | `cancel_limit_order` |
| `modify` | Change a pending order's price / SL / TP | `modify_limit_order` |

### Transport-Neutral Naming

The transport layer is named `_do_request_*` (not `_do_http_*`) because adapters with non-HTTP transport satisfy the same contract:

- KrakenAdapter — HTTPS POST (`_fetch_private`)
- MockBrokerAdapter — in-memory state machine
- Future MT5Adapter — ZeroMQ REQ/REP to the EA bridge

Same contract, three different transports, zero changes to `LiveRequestProcessor`.

### Capability Declaration

`get_order_capabilities()` declares which order types the adapter actually supports. The executor's feature gate consults this — an algo that wants STOP_LIMIT will be rejected at the gate if the adapter doesn't declare it.

```python
def get_order_capabilities(self) -> OrderCapabilities:
    return OrderCapabilities(
        market_orders=True,
        limit_orders=True,
        stop_orders=False,             # Kraken uses StopLimit instead
        stop_limit_orders=True,
        trailing_stop=False,
        iceberg_orders=True,
        hedging_allowed=self._hedging_allowed,
        partial_fills_supported=True,
    )
```

If a Tier-3 operation is declared but not implementable for some order type, the `_build_<op>_payload` layer should raise — fail fast at build time rather than send a malformed payload.

---

## Files to Create

For a broker named `example`:

```
python/framework/trading_env/adapters/example_adapter.py     ← adapter class
configs/brokers/example/example_broker_config.json           ← static symbol/broker specs
configs/credentials/example_credentials.json                 ← placeholder
user_configs/credentials/example_credentials.json            ← real credentials (gitignored)
configs/broker_settings/example_spot.json                    ← live connection settings (credentials_file, dry_run, broker_transport.{api_base_url, rate_limit_interval_s, request_timeout_s, poll_interval_ms})
configs/autotrader_profiles/live/example_ethusd.json         ← AutoTrader profile
tests/live_adapters/test_example_adapter_order_lifecycle_dry.py
tests/live_adapters/test_example_adapter_order_lifecycle_live.py
tests/live_adapters/test_example_adapter_order_lifecycle_fill.py
docs/user_guides/adapter/setup_example_adapter.md
docs/tests/live_adapters/example_adapter_integration_tests.md
```

---

## AbstractAdapter — Required vs Optional

### Always required (`@abstractmethod`)

| Method | Purpose |
|--------|---------|
| `_validate_config()` | Validate broker-specific config fields after common validation |
| `get_broker_name()` | Return company name string |
| `get_broker_type()` | Return `BrokerType` enum value |
| `get_order_capabilities()` | Return `OrderCapabilities` declaring supported order types |
| `create_market_order(symbol, direction, lots, **kwargs)` | Build and return a `MarketOrder` |
| `create_limit_order(symbol, direction, lots, price, **kwargs)` | Build and return a `LimitOrder` |
| `validate_order(symbol, lots)` | Return `(is_valid, error_message)` — use `_validate_lot_size()` from base |
| `get_all_aviable_symbols()` | Return list of symbol strings |
| `get_symbol_specification(symbol)` | Return `SymbolSpecification` |
| `get_broker_specification()` | Return `BrokerSpecification` |

### Tier 2 — optional, override as needed

`create_stop_order`, `create_stop_limit_order`, `create_iceberg_order` — base raises `NotImplementedError`. Override only for supported types. Declare support in `get_order_capabilities()`.

### Tier 3 — required for live adapters

| Method | Purpose |
|--------|---------|
| `is_live_capable()` | Return `True` after `enable_live()` succeeded |
| `enable_live(credentials_file, dry_run, transport: BrokerTransportConfig)` | Load credentials, store config, set `_live_enabled = True` |
| `_build_submit_payload(symbol, direction, lots, order_type, **kwargs)` | Build broker-specific submit payload |
| `_do_request_submit(payload)` | Send submit request; **raises on error** |
| `_parse_submit_response(raw, timestamp)` | Raw → `BrokerResponse` |
| `_build_query_payload(broker_ref)` | Build query/status payload |
| `_do_request_query(payload)` | Send query request |
| `_parse_query_response(raw, broker_ref, timestamp)` | Raw → `BrokerResponse` |
| `_build_cancel_payload(broker_ref)` | Build cancel payload |
| `_do_request_cancel(payload)` | Send cancel request |
| `_parse_cancel_response(raw, broker_ref, timestamp)` | Raw → `BrokerResponse` |
| `_build_modify_payload(broker_ref, symbol, new_price, new_stop_loss, new_take_profit)` | Build modify payload |
| `_do_request_modify(payload)` | Send modify request |
| `_parse_modify_response(raw, original_broker_ref, timestamp)` | Raw → `BrokerResponse` (may carry NEW broker_ref — see below) |

`_parse_*_response` receives `timestamp` as a parameter — never call `datetime.now()` inside the parse layer. The processor passes a parse-stage timestamp so async-dispatched responses get a timestamp from the main-thread drain (not from the worker), which matters for ordering and event correlation.

---

## Broker Config JSON

Two config files per broker:

**`configs/brokers/<broker>/<broker>_broker_config.json`** — static specs, checked in:

```json
{
  "broker_info": {
    "company": "MyBroker",
    "server": "mybroker_spot",
    "trade_mode": "demo",
    "leverage": 1,
    "hedging_allowed": false
  },
  "fee_structure": {
    "model": "maker_taker",
    "maker_fee": 0.16,
    "taker_fee": 0.26,
    "fee_currency": "quote"
  },
  "symbols": {
    "ETHUSD": {
      "base_currency": "ETH",
      "quote_currency": "USD",
      "trade_allowed": true,
      "volume_min": 0.001,
      "volume_max": 10000,
      "volume_step": 1e-8,
      "contract_size": 1,
      "tick_size": 0.01,
      "digits": 2,
      "stops_level": 0,
      "freeze_level": 0
    }
  }
}
```

Required `broker_info` fields: `company`, `server`, `trade_mode`, `leverage`, `hedging_allowed`. When `leverage > 1`, also: `margin_mode`, `stopout_level`, `margin_call_level`.

Required per-symbol fields: `volume_min`, `volume_max`, `volume_step`, `contract_size`, `tick_size`, `digits`, `trade_allowed`, `base_currency`, `quote_currency`.

**`configs/broker_settings/<broker>.json`** — connection settings (used by tests + AutoTrader `enable_live`):

```json
{
  "credentials_file": "example_credentials.json",
  "dry_run": true,
  "broker_transport": {
    "api_base_url": "https://api.example.com",
    "rate_limit_interval_s": 1.0,
    "request_timeout_s": 15,
    "poll_interval_ms": 5000
  }
}
```

`credentials_file` and `dry_run` are passed flat to `enable_live`; the four `broker_transport` fields are bundled into a `BrokerTransportConfig` Pydantic object and passed as `transport=`. Construct explicitly:

```python
adapter.enable_live(
    credentials_file=broker_settings['credentials_file'],
    dry_run=broker_settings['dry_run'],
    transport=BrokerTransportConfig(**broker_settings['broker_transport']),
)
```

---

## Credentials Cascade

`_load_credentials(filename)` (implement in your adapter) follows this cascade:

```python
user_path = Path('user_configs/credentials') / filename     # gitignored, real keys
default_path = Path('configs/credentials') / filename       # checked in, placeholder
```

Prefer `user_path`. Never log credentials. Store only in short-lived `_api_key` / `_api_secret` fields.

---

## Tier 3 Implementation — Step by Step

### `enable_live()`

Called once before any live execution. Loads credentials, stores config, instantiates the dry-run simulator:

```python
def __init__(self, broker_config):
    super().__init__(broker_config)
    ...
    # Always instantiated so DRYRUN-* refs stay queryable even if dry_run toggles
    self._dry_run_simulator: DryRunOrderSimulator = DryRunOrderSimulator()

def enable_live(
    self,
    credentials_file: str,
    dry_run: bool,
    transport: BrokerTransportConfig,
) -> None:
    self._api_key, self._api_secret = self._load_credentials(credentials_file)
    self._api_base_url = transport.api_base_url
    self._dry_run = dry_run
    self._rate_limit_interval_s = transport.rate_limit_interval_s
    self._request_timeout_s = transport.request_timeout_s
    self._live_enabled = True

def is_live_capable(self) -> bool:
    return self._live_enabled
```

### Submit — three layers walked through

**Build payload** (pure, no I/O):

```python
def _build_submit_payload(self, symbol, direction, lots, order_type, **kwargs):
    return {
        'pair': self._resolve_broker_pair(symbol),
        'side': 'buy' if direction == OrderDirection.LONG else 'sell',
        'type': 'market' if order_type == OrderType.MARKET else 'limit',
        'volume': str(lots),
        **({'price': str(kwargs['price'])} if order_type == OrderType.LIMIT else {}),
    }
```

**Do request** (transport, raises on error):

```python
def _do_request_submit(self, payload):
    if self._dry_run:
        # validate=true: broker validates payload (pair, lot, cost, margin) without executing.
        # Sentinel-tagged dict tells _parse_submit_response to delegate to the simulator.
        self._fetch_private('/0/private/AddOrder', {**payload, 'validate': 'true'})
        return {
            self._DRY_RUN_SENTINEL: 'submit',
            'lots': float(payload['volume']),
            'price': float(payload['price']) if 'price' in payload else None,
        }
    return self._fetch_private('/0/private/AddOrder', payload)
```

**Parse response** (pure):

```python
def _parse_submit_response(self, raw, timestamp):
    if raw.get(self._DRY_RUN_SENTINEL) == 'submit':
        return self._dry_run_simulator.submit(
            lots=raw['lots'], price=raw['price'], timestamp=timestamp,
        )
    txid_list = raw.get('txid', [])
    return BrokerResponse(
        broker_ref=txid_list[0] if txid_list else '',
        status=BrokerOrderStatus.PENDING,
        timestamp=timestamp,
        raw_response=raw,
    )
```

The same pattern repeats for query / cancel / modify. The MockBrokerAdapter's implementation is identical in shape (just with mock-state mutation in place of HTTP).

### `modify_order` — broker_ref replacement semantics

Some brokers (Kraken `EditOrder`) replace the order on modify and return a **new** broker_ref; the old one is invalidated. `_parse_modify_response` must surface this via `BrokerResponse.broker_ref` set to the new ref. `LiveRequestProcessor.update_broker_ref(old, new)` handles the index swap downstream.

```python
def _parse_modify_response(self, raw, original_broker_ref, timestamp):
    if raw.get(self._DRY_RUN_SENTINEL) == 'modify':
        return self._dry_run_simulator.modify(
            broker_ref=raw['broker_ref'], new_price=raw['new_price'], timestamp=timestamp,
        )
    new_txid = raw.get('txid', original_broker_ref)
    return BrokerResponse(
        broker_ref=new_txid,                       # may differ from original
        status=BrokerOrderStatus.PENDING,
        timestamp=timestamp,
        raw_response=raw,
    )
```

If your broker keeps the same ref on modify, return it unchanged. Either works — the processor adapts.

### `modify` parameter contract

`_build_modify_payload(broker_ref, symbol, new_price, new_stop_loss, new_take_profit)` — the SL/TP parameters are part of the contract even if your broker doesn't support modifying them (Kraken `EditOrder` only changes price). In that case accept them and silently ignore — keeps the contract uniform across adapters.

`symbol` is required because some brokers need the trading pair alongside the broker_ref (Kraken: `EditOrder` requires `pair`). Resolve it to the broker's pair format inside the build layer.

### Rate limiting

Enforce in the transport layer, before each call:

```python
def _enforce_rate_limit(self) -> None:
    elapsed = time.monotonic() - self._last_request_time
    if elapsed < self._rate_limit_interval_s:
        time.sleep(self._rate_limit_interval_s - elapsed)
    self._last_request_time = time.monotonic()
```

The worker thread is single-threaded so simple time-based throttling is sufficient. No additional locking needed.

---

## DryRunOrderSimulator — Mandatory Integration

Every live-capable adapter must integrate the shared `DryRunOrderSimulator` (`python/framework/trading_env/adapters/dry_run_simulator.py`). It provides a counter-based PENDING → FILLED lifecycle so dry-run mode exercises the same pending pipeline as real-mode.

```python
class DryRunOrderSimulator:
    def submit(self, lots, price, timestamp) -> BrokerResponse:    # PENDING + DRYRUN-NNNNNN ref
    def query(self, broker_ref, timestamp) -> BrokerResponse:      # PENDING while remaining_polls > 0, then FILLED
    def cancel(self, broker_ref, timestamp) -> BrokerResponse:     # CANCELLED, idempotent
    def modify(self, broker_ref, new_price, timestamp) -> BrokerResponse  # NEW ref, mimics EditOrder
```

Default `polls_until_fill=2` matches the typical tick-loop cadence (one tick → poll → still pending → next tick → poll → fill). Configurable per adapter if needed.

The Kraken adapter's pattern (sentinel-tagged raw from `_do_request_*` recognized by `_parse_*_response` and delegated to the simulator) is the canonical integration shape. Replicate it.

**Why dry-run goes through the full lifecycle:** before this was introduced (#319 step 9), dry-run mode returned `FILLED` immediately on submit — bypassing pending tracking, OrderGuard cooldowns, timeout detection, etc. Any bug in those paths was undetectable in dry-run. With the simulator, dry-run is real-mode-equivalent in behavior; only the source of the fill differs.

---

## Symbol Mapping

If the broker uses different pair names than the standard symbols (e.g., Kraken: `BTCUSD` → `XBTUSD`), put the mapping in the broker config and resolve in the build layer:

```python
def _resolve_broker_pair(self, symbol: str) -> str:
    symbol_info = self.broker_config.get('symbols', {}).get(symbol, {})
    return symbol_info.get('broker_pair_name', symbol)
```

---

## Test Suite

Every adapter requires three test files following the `_dry` / `_live` / `_fill` pattern. See [kraken_adapter_integration_tests.md](../../tests/live_adapters/kraken_adapter_integration_tests.md) for the full reference.

```
tests/live_adapters/
├── test_<broker>_adapter_order_lifecycle_dry.py    # dry-run / validate mode, no funds
├── test_<broker>_adapter_order_lifecycle_live.py   # real orders, no fills (LIMIT far OTM)
└── test_<broker>_adapter_order_lifecycle_fill.py   # real MARKET fills, minimum lot
```

The tests drive the Tier-3 layers via `LiveRequestProcessor.submit_open_order` / `query_order_sync` / `cancel_order_sync` / `modify_order_sync` — they never call the adapter's Tier-3 methods directly. This validates the full contract including the orchestration boundary.

Key rules:
- `_live` fixture must explicitly set `dry_run=False` — never rely on the config file default
- `_live` test wraps the order lifecycle in `try/finally` to guarantee cancellation on assertion failure
- `_fill` test does MARKET buy → poll until FILLED → MARKET sell → poll until FILLED (net zero exposure)
- Check the broker's minimum order cost (not just `volume_min`) — Kraken rejects orders below ~$5 even if `volume_min` is satisfied
- The `live_adapter` mark and runner exclusion apply automatically via `tests/conftest.py`

**Launch.json:** add a `🧩 Pytest: Live Adapters (All)` entry. No `🧪` entries needed — existing live profiles serve manual inspection.

---

## MockBrokerAdapter — The Template Reference

`python/framework/testing/mock_broker_adapter.py` is the cleanest end-to-end reference for the Tier-3 layer pattern. It satisfies the full 12-method contract with **no network**, no credentials, no config files. Use it as the structural template when implementing a new adapter:

- `_build_*_payload`: pure parameter packing, dictionary-only
- `_do_request_*`: in-memory state mutation (mock-as-transport — counter, `_mock_pending`)
- `_parse_*_response`: pure status-string → enum mapping via `_STATUS_MAP`

The mock's `MockExecutionMode` (`INSTANT_FILL`, `DELAYED_FILL`, `REJECT_ALL`, `TIMEOUT`) exists to test the different broker behavior shapes that real adapters might exhibit. When writing your adapter's tests, leverage the equivalent shapes from the real broker (dry-run for instant-fill-like, real submit for delayed-fill-like, etc.).

---

## API Performance Monitoring (#351)

The API Performance Monitor (`ApiPerfMonitor`) and its `set_api_monitor()` hook
live in `AbstractAdapter` — broker-agnostic, shared by every adapter. Only the
**measurement point is per-adapter**, because the transport differs per broker.
Wrap each broker transport call in the broker-agnostic `AbstractAdapter._timed_call`:

```python
# Kraken funnels ALL private calls through one method → one wrap:
def _fetch_private(self, endpoint, data=None):
    return self._timed_call(endpoint, lambda: self._do_fetch_private(endpoint, data))

# An adapter with several distinct transport calls (e.g. MT5) wraps each:
def _send_order(self, request):
    return self._timed_call('order_send', lambda: mt5.order_send(request))
```

- `_timed_call(endpoint, fn)` times `fn()` (including any rate-limit throttle — the
  real call cost), records `(endpoint, ms, success/error)`, and re-raises on
  failure. With no monitor attached it just calls `fn()` (zero overhead).
- `endpoint` becomes the live-panel row label — use a stable, human-readable id
  (the REST path tail like `/0/private/OpenOrders`, or the bridge method name).
- You do NOT wire the monitor: `AutoTraderMain` builds + injects it for live
  adapters (`config.api_monitor.enabled`, mock auto-disabled). Just call
  `_timed_call` at your transport boundary and the panel + logging come for free.

---

## AutoTrader Wiring

AutoTrader profile references only the broker type and adapter type:

```json
{
  "broker_type": "example_spot",
  "adapter_type": "live",
  "symbol": "ETHUSD",
  ...
}
```

`market_config.json` resolves `broker_type` → `broker_config_path` (path to the symbol-specs JSON). Connection settings come from `configs/broker_settings/<broker>.json` via the AutoTrader CLI.

The `BrokerType` enum (`python/framework/types/trading_env_types/broker_types.py`) must include the new type. The adapter factory (`python/framework/factory/`) maps `BrokerType` → concrete adapter class.

---

## Related

- `python/framework/trading_env/adapters/abstract_adapter.py` — full interface
- `python/framework/trading_env/adapters/kraken_adapter.py` — real HTTPS reference
- `python/framework/testing/mock_broker_adapter.py` — in-process template (cleanest read)
- `python/framework/trading_env/adapters/dry_run_simulator.py` — shared dry-run lifecycle utility
- `python/framework/reporting/api_perf_monitor.py` — API performance monitor (#351), instrumented via `_timed_call`
- `python/framework/trading_env/live/live_request_processor.py` — Tier-3 composition (sync + async orchestrators, worker thread, drain_inbox)
- `tests/live_adapters/` — reference test suite
- `docs/tests/live_adapters/kraken_adapter_integration_tests.md` — test pattern reference
- `docs/user_guides/adapter/setup_kraken_adapter.md` — model for adapter-specific setup docs
