# Broker Adapter Development Guide

How to implement a new broker adapter from scratch. The Kraken adapter (`kraken_adapter.py`) is the reference implementation — read it alongside this guide.

---

## Overview

Every adapter lives in `python/framework/trading_env/adapters/` and extends `AbstractAdapter`. The interface has three tiers:

| Tier | When active | What it provides |
|------|-------------|------------------|
| **Tier 1** — Common Orders | Always (backtesting + live) | `create_market_order`, `create_limit_order` |
| **Tier 2** — Extended Orders | Always, broker-specific | `create_stop_limit_order`, `create_iceberg_order`, etc. |
| **Tier 3** — Live Execution | After `enable_live()` | `execute_order`, `check_order_status`, `cancel_order`, `modify_order` |

Tier 1+2 are purely constructors — they build typed order objects from parameters. No API calls. Tier 3 connects to the real broker REST API.

---

## Files to Create

For a broker named `example`:

```
python/framework/trading_env/adapters/example_adapter.py     ← adapter class
configs/brokers/example/example_broker_config.json           ← static symbol/broker specs
configs/broker_settings/example.json                         ← runtime settings (URL, rate limit)
configs/credentials/example_credentials.json                 ← placeholder (empty or test key)
user_configs/credentials/example_credentials.json            ← real credentials (gitignored)
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
| `get_order_capabilities()` | Return `OrderCapabilities` declaring what order types are supported |
| `create_market_order(symbol, direction, lots, **kwargs)` | Build and return a `MarketOrder` |
| `create_limit_order(symbol, direction, lots, price, **kwargs)` | Build and return a `LimitOrder` |
| `validate_order(symbol, lots)` | Return `(is_valid, error_message)` — use `_validate_lot_size()` from base |
| `get_all_aviable_symbols()` | Return list of symbol strings |
| `get_symbol_specification(symbol)` | Return `SymbolSpecification` dataclass |
| `get_broker_specification()` | Return `BrokerSpecification` dataclass |

### Tier 2 — optional, override as needed

`create_stop_order`, `create_stop_limit_order`, `create_iceberg_order` — base raises `NotImplementedError`. Override only for supported order types. Declare support in `get_order_capabilities()`.

### Tier 3 — optional, override for live adapters

`is_live_capable`, `enable_live`, `execute_order`, `check_order_status`, `cancel_order`, `modify_order` — base raises `NotImplementedError`. See below.

---

## Broker Config JSON

Two config files per broker:

**`configs/brokers/<broker>/<broker>_broker_config.json`** — static, checked in, exported from broker API or docs:

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

Required `broker_info` fields: `company`, `server`, `trade_mode`, `leverage`, `hedging_allowed`. When `leverage > 1`, also required: `margin_mode`, `stopout_level`, `margin_call_level`.

Required per-symbol fields: `volume_min`, `volume_max`, `volume_step`, `contract_size`, `tick_size`, `digits`, `trade_allowed`, `base_currency`, `quote_currency`.

**`configs/broker_settings/<broker>.json`** — runtime settings, user-editable:

```json
{
  "credentials_file": "example_credentials.json",
  "api_base_url": "https://api.example.com",
  "dry_run": true,
  "rate_limit_interval_s": 1.0,
  "request_timeout_s": 15,
  "symbol_to_broker_pair": {
    "BTCUSD": "BTC-USD"
  }
}
```

---

## Credentials Cascade

`_load_credentials()` (implement in your adapter) must follow the cascade:

```python
user_path = Path('user_configs/credentials') / credentials_filename   # gitignored, real keys
default_path = Path('configs/credentials') / credentials_filename      # checked in, placeholder
```

Prefer `user_path` if it exists, fall back to `default_path`. Never log credentials. Store them only in short-lived `_api_key` / `_api_secret` instance fields.

---

## Tier 3 Implementation

### `enable_live(broker_settings)`

Called by the AutoTrader pipeline before any live execution. Reads the broker settings dict, loads credentials, sets internal state.

```python
def enable_live(self, broker_settings: Dict[str, Any]) -> None:
    credentials_file = broker_settings.get('credentials_file', 'example_credentials.json')
    self._api_key, self._api_secret = self._load_credentials(credentials_file)
    self._api_base_url = broker_settings.get('api_base_url', 'https://api.example.com')
    self._dry_run = broker_settings.get('dry_run', True)
    self._rate_limit_interval_s = broker_settings.get('rate_limit_interval_s', 1.0)
    self._request_timeout_s = broker_settings.get('request_timeout_s', 15)
    self._live_enabled = True

def is_live_capable(self) -> bool:
    return self._live_enabled
```

### `execute_order()`

Returns `BrokerResponse` with `status=PENDING` and a real `broker_ref`. The caller (`LiveTradeExecutor`) will poll `check_order_status()` until FILLED.

In dry-run mode, use the broker's sandbox/validate flag — return a synthetic `DRYRUN-XXXXXX` ref. Return `PENDING` initially (not `FILLED`), so the pending lifecycle is exercised. Return `FILLED` on the next `check_order_status()` poll.

```
execute_order() → PENDING (DRYRUN-XXXXXX)
check_order_status() poll 1 → PENDING
check_order_status() poll 2 → FILLED (simulated)
```

### Rate limiting

Enforce rate limits BEFORE each API call, not after. Sleep the remaining interval since the last request:

```python
def _enforce_rate_limit(self) -> None:
    elapsed = time.time() - self._last_request_time
    if elapsed < self._rate_limit_interval_s:
        time.sleep(self._rate_limit_interval_s - elapsed)
    self._last_request_time = time.time()
```

### `modify_order(broker_ref, symbol, new_price, new_stop_loss, new_take_profit)`

**`symbol` is a required parameter.** Some broker APIs (including Kraken's `EditOrder`) require the trading pair alongside the order reference — the `broker_ref` alone is not enough. Always accept `symbol` and resolve the broker's internal pair name from it.

Some brokers replace the order on modification (new `broker_ref` returned). In that case, return the new ref in `BrokerResponse.broker_ref`. `LiveTradeExecutor` handles this correctly.

---

## Symbol Mapping

If the broker uses different pair names than the standard symbols in config (e.g., Kraken: `BTCUSD` → `XBTUSD`), add a `symbol_to_broker_pair` dict in broker settings and a resolver method:

```python
def _resolve_broker_pair(self, symbol: str) -> str:
    return self._symbol_to_broker_pair.get(symbol, symbol)
```

---

## Test Suite

Every adapter requires three test files following the `_dry` / `_live` / `_fill` pattern. See [kraken_adapter_integration_tests.md](kraken_adapter_integration_tests.md) for the full reference, including the pattern checklist and lessons learned.

```
tests/live_adapters/
├── test_<broker>_adapter_order_lifecycle_dry.py    # dry-run / validate mode, no funds
├── test_<broker>_adapter_order_lifecycle_live.py   # real orders, no fills (LIMIT far OTM)
└── test_<broker>_adapter_order_lifecycle_fill.py   # real MARKET fills, minimum lot
```

Key rules:
- `_live` fixture must explicitly set `dry_run=False` — never rely on the config file default
- `_live` test wraps the order lifecycle in `try/finally` to guarantee cancellation on assertion failure
- `_fill` test does MARKET buy → poll until FILLED → MARKET sell → poll until FILLED (net zero exposure)
- Check the broker's minimum order cost (not just `volume_min`) — some brokers reject orders below a cost floor (Kraken: ~$5)
- The `live_adapter` mark and runner exclusion apply automatically via `tests/conftest.py`

**Launch.json:** Add a `🧩 Pytest: Live Adapters (All)` entry if not already present. No `🧪` entry — existing live profiles serve manual inspection.

---

## AutoTrader Wiring

The AutoTrader profile references the broker config and settings:

```json
{
  "broker": {
    "broker_type": "example_spot",
    "broker_config_path": "configs/brokers/example/example_broker_config.json",
    "broker_settings_path": "configs/broker_settings/example.json"
  }
}
```

The `BrokerType` enum (`python/framework/types/trading_env_types/broker_types.py`) must include the new broker type. The adapter factory (`python/framework/factory/`) must map the new `BrokerType` to the concrete adapter class.

---

## Related

- `python/framework/trading_env/adapters/abstract_adapter.py` — full interface
- `python/framework/trading_env/adapters/kraken_adapter.py` — reference implementation
- `tests/live_adapters/` — reference test suite
- `docs/tests/live_adapters/kraken_adapter_integration_tests.md` — test pattern reference
- `docs/user_guides/adapter/setup_kraken_adapter.md` — user-facing credential setup (model for new adapter setup docs)
