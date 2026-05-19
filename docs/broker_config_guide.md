# Broker Configuration Guide

## Overview

FiniexTestingIDE supports multiple broker types through a unified adapter architecture. Each broker has a JSON configuration file that defines trading rules, fees, and symbol specifications.

## Architecture

```
market_config.json          → MarketType (forex/crypto) + broker_type mapping
    ↓
broker_config.json          → Broker-specific settings (fees, leverage, symbols)
    ↓
AbstractAdapter                 → Abstract interface, common validation
    ↓
Mt5Adapter / KrakenAdapter  → Broker-specific implementation
```

### Key Classes

| Class | Purpose |
|-------|---------|
| `AbstractAdapter` | Abstract base, `_validate_common_config()` for shared validation |
| `Mt5Adapter` | MetaTrader 5 brokers (Forex, CFD) |
| `KrakenAdapter` | Kraken crypto exchange (Spot) |
| `FeeType` | Enum: `SPREAD`, `MAKER_TAKER`, `SWAP`, `COMMISSION` |
| `MarketType` | Enum: `FOREX`, `CRYPTO` |

### Selection Flow

1. `market_config.json` maps `broker_type` → `market_type` + config path
2. `BrokerConfigFactory` loads JSON and detects `broker_type`
3. Appropriate adapter is instantiated (MT5 or Kraken)
4. `AbstractAdapter._validate_common_config()` validates shared fields
5. Adapter-specific `_validate_config()` validates broker-specific fields

---

## Configuration Schema

### broker_info (Required)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `company` | string | Always | Broker name |
| `server` | string | Always | Server identifier |
| `trade_mode` | string | Always | `"demo"` or `"real"` |
| `leverage` | int | Always | Account leverage (1 = spot, no margin) |
| `hedging_allowed` | bool | Always | Allow opposite positions on same symbol |
| `margin_mode` | string | If leverage > 1 | `"retail_hedging"`, `"retail_netting"`, `"exchange"`, `"none"` |
| `margin_call_level` | float | If leverage > 1 | Margin call threshold (%) |
| `stopout_level` | float | If leverage > 1 | Stop out threshold (%) |

### fee_structure (Required)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model` | string | Always | `"spread"` (MT5) or `"maker_taker"` (Kraken) |
| `maker_fee` | float | If maker_taker | Maker fee percentage (e.g., 0.16) |
| `taker_fee` | float | If maker_taker | Taker fee percentage (e.g., 0.26) |
| `fee_currency` | string | Optional | Fee currency (`"quote"` default) |

### symbols (Required, min 1)

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `volume_min` | float | Always | Minimum lot size |
| `volume_max` | float | Always | Maximum lot size |
| `volume_step` | float | Always | Lot increment |
| `contract_size` | int | Always | Units per lot (100000 Forex, 1 Crypto) |
| `tick_size` | float | Always | Minimum price movement |
| `digits` | int | Always | Decimal places |
| `trade_allowed` | bool | Always | Symbol tradeable |
| `base_currency` | string | Kraken | Base currency (e.g., "BTC") |
| `quote_currency` | string | Kraken | Quote currency (e.g., "USD") |
| `kraken_pair_name` | string | Kraken | Kraken pair name for order API (e.g., "XBTUSD") |
| `_active` | bool | Kraken | `false` for delisted/tombstoned symbols (runtime cache only) |
| `_last_fetched` | string | Kraken | ISO-8601 UTC timestamp of last API fetch for this symbol (runtime cache only) |
| `swap_long` | float | MT5 | Overnight swap for long positions |
| `swap_short` | float | MT5 | Overnight swap for short positions |

---

## Example Configurations

### Kraken Spot (Crypto)

```json
{
  "broker_type": "kraken_spot",
  "broker_info": {
    "company": "Kraken",
    "server": "kraken_spot",
    "name": "kraken_public",
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
  "trading_permissions": { ... },
  "symbols": { ... }
}
```

### MT5 Forex

```json
{
  "broker_type": "mt5_forex",
  "broker_info": {
    "company": "IC Markets",
    "server": "ICMarkets-Demo",
    "trade_mode": "demo",
    "leverage": 500,
    "hedging_allowed": true,
    "margin_mode": "retail_hedging",
    "margin_call_level": 50.0,
    "stopout_level": 20.0
  },
  "fee_structure": {
    "model": "spread"
  },
  "trading_permissions": { ... },
  "symbols": { ... }
}
```

---

## Fee Model Selection

The `fee_structure.model` field determines which fee calculation is used:

| Model | FeeType Enum | Adapter | Calculation |
|-------|--------------|---------|-------------|
| `"spread"` | `FeeType.SPREAD` | MT5 | `spread_points * tick_value * lots` |
| `"maker_taker"` | `FeeType.MAKER_TAKER` | Kraken | `order_value * (rate / 100)` |

### TradeSimulator Flow

```
_check_and_open_order_in_portfolio()
    ↓
_create_entry_fee()
    ↓
fee_model = FeeType(config['fee_structure']['model'])
    ↓
if FeeType.MAKER_TAKER → create_maker_taker_fee()
if FeeType.SPREAD     → create_spread_fee_from_tick()
```

---

## Validation Rules

### Always Required
- `broker_info.company`, `broker_info.server`, `broker_info.trade_mode`
- `broker_info.leverage` (default: 1)
- `broker_info.hedging_allowed` (default: false)
- `fee_structure.model`
- At least one symbol with all required fields

### Conditional (leverage > 1)
- `broker_info.margin_mode`
- `broker_info.margin_call_level`
- `broker_info.stopout_level`

### Conditional (maker_taker model)
- `fee_structure.maker_fee`
- `fee_structure.taker_fee`

---

## MarketType Integration

The `market_config.json` maps `broker_type` to `MarketType`:

```json
{
  "brokers": [
    { "broker_type": "mt5", "market_type": "forex" },
    { "broker_type": "kraken_spot", "market_type": "crypto" }
  ]
}
```

MarketType controls:
- Weekend closure behavior (Forex: yes, Crypto: no)
- Activity metric (Forex: tick_count, Crypto: trade_volume)
- Gap detection rules
- Generator profile defaults (`generator_profile_defaults`): block size limits and ATR thresholds per market type

### Generator Profile Defaults

Each market type defines default parameters for the Generator Profile System:

```json
"market_rules": {
    "forex": {
        "generator_profile_defaults": {
            "min_block_hours": 2,
            "max_block_hours": 24,
            "atr_percentile_threshold": 10
        }
    },
    "crypto": {
        "generator_profile_defaults": {
            "min_block_hours": 4,
            "max_block_hours": 72,
            "atr_percentile_threshold": 15
        }
    }
}
```

Crypto uses larger blocks (72h vs 24h) and a higher ATR percentile threshold (P15 vs P10) because 24/7 markets have less pronounced volatility minima than session-based Forex markets. These defaults override `generator_config.json` when present. The `split_algorithm` (always `atr_minima`) remains global in `generator_config.json`.

---

## Static vs Dynamic Config Mode

### Overview

Every broker entry in `market_config.json` has an optional `config_mode` field:

```json
{ "broker_type": "kraken_spot", "config_mode": "dynamic" }
{ "broker_type": "mt5_forex" }
```

| Value | Meaning | Default |
|-------|---------|---------|
| `static` | Use the git-tracked JSON file (`broker_config_path`) directly | Yes |
| `dynamic` | Use a runtime cache; refresh from broker API weekly | No |

Currently only `kraken_spot` uses `dynamic`. MT5 and all future static brokers default to `static`.

### Broker Entry Schema (`market_config.json`)

Full schema for a dynamic broker entry (all connection fields are relevant to live AutoTrader sessions only):

```json
{
  "broker_type": "kraken_spot",
  "market_type": "crypto",
  "trading_model": "spot",
  "config_mode": "dynamic",
  "broker_config_path": "configs/brokers/kraken/kraken_spot_broker_config.json",
  "credentials_file": "kraken_credentials.json",
  "dry_run": true,
  "broker_transport": {
    "api_base_url": "https://api.kraken.com",
    "rate_limit_interval_s": 1.0,
    "request_timeout_s": 15,
    "poll_interval_ms": 5000
  }
}
```

| Field | Scope | Description |
|-------|-------|-------------|
| `broker_type` | All | Unique broker identifier |
| `market_type` | All | `"forex"` or `"crypto"` — controls market rules |
| `trading_model` | Optional | `"spot"` or `"margin"` — affects portfolio and display |
| `config_mode` | All | `"static"` (default) or `"dynamic"` |
| `broker_config_path` | All | Path to git-tracked broker config JSON (static seed) |
| `credentials_file` | Live only | Credentials filename, resolved via `user_configs/credentials/` cascade |
| `dry_run` | Live only | `true` = validate orders, no execution. Safe default. |
| `broker_transport.api_base_url` | Live only | Broker REST API base URL |
| `broker_transport.rate_limit_interval_s` | Live only | Minimum interval between private API calls (seconds) |
| `broker_transport.request_timeout_s` | Live only | HTTP request timeout (seconds) |
| `broker_transport.poll_interval_ms` | Live only | Minimum interval between per-order status polls (milliseconds, default 5000) |

To override any live setting (e.g., disable dry-run for production), create `user_configs/market_config.json`:

```json
{
  "brokers": [
    {
      "broker_type": "kraken_spot",
      "dry_run": false
    }
  ]
}
```

`user_configs/market_config.json` is gitignored. The committed default always has `dry_run: true`.

`dry_run` is a **broker-level deployment decision** — not a per-session flag. It applies to all AutoTrader sessions using that broker type, analogous to Alpaca's `paper_trading` environment variable or QuantConnect's brokerage model setting.

### Static Seed + Hot Cache Model

```
configs/brokers/kraken/kraken_spot_broker_config.json  ← git-tracked "seed"
  → Used by backtesting (stable, reproducible)
  → Never auto-overwritten

data/runtime/brokers/kraken_spot/kraken_spot_broker_config.json  ← gitignored hot cache
  → Used by AutoTrader live sessions (config_mode=dynamic)
  → Auto-refreshed from Kraken API (weekly)
```

The static seed is committed with the codebase. The runtime cache is gitignored and machine-local.

### Staleness Policy (dynamic mode)

| Cache age | Behavior |
|-----------|----------|
| < 7 days | Use cache silently — no API call (unless symbol is missing, see below) |
| 7–30 days | Try API refresh; on failure warn + use stale cache |
| > 30 days | Try API refresh; on failure strong warning (specs may be outdated) |
| No cache | Try API refresh; on failure hard error (first run, no fallback) |

On first run, an internet connection is required. After the first successful fetch, the session can run offline for up to 30 days before generating a strong staleness warning.

**Lazy symbol addition:** Symbols are added to the cache on demand. If a fresh cache exists but does not contain the requested symbol (e.g., the cache was built during an ETHUSD session and you now start a DOTUSD session), the missing symbol is fetched from the API and merged into the cache — without affecting the age or status of existing symbols. Each symbol carries a `_last_fetched` field indicating when it was last individually verified.

### Tombstone Symbols

Each symbol in the runtime cache has an `_active` field. Symbols fetched from the API are always written with `_active: true`. Symbols that are not part of a given API fetch are left unchanged — they are **not** tombstoned automatically. This means:

- Running ETHUSD and then DOTUSD results in a cache with both symbols marked `_active: true`
- Tombstoning (`_active: false`) is reserved for a future full-refresh path (a `broker-config sync` CLI that fetches all symbols at once and marks any missing ones as inactive)
- In the static seed, `_active` can be set manually to exclude a symbol from backtesting validation without deleting its spec

### Config Hash / Seed ID

Each loaded broker config is stamped with an 8-char SHA256 hash of its `symbols` block:

```
🗄  Broker config loaded: kraken_spot [a3f82c11]
    Source: data/runtime/brokers/kraken_spot/kraken_spot_broker_config.json
    Symbols: 9 active
```

The hash appears in:
- Startup log (global logger)
- Batch summary: `Config:  [a3f82c11]` line under broker info
- AutoTrader live header: `BTCUSD (kraken_spot) [a3f82c11] — DRY RUN`

The hash is computed from the `symbols` block only — `_config_meta` timestamp changes do not affect it. This makes it a stable identifier for the actual symbol specification in use.

### Syncing the Static Seed

When the runtime cache has been refreshed and you want to commit the new specs:

```bash
cp data/runtime/brokers/kraken_spot/kraken_spot_broker_config.json \
   configs/brokers/kraken/kraken_spot_broker_config.json
git diff configs/brokers/kraken/kraken_spot_broker_config.json
```

Review the diff carefully before committing:

| Diff entry | What to check |
|------------|---------------|
| `"_active": false` (new) | Expected delisting. Backtesting data for this symbol still works. |
| New symbol entry | Does bar index show data for it? (`bar_index_cli.py data-coverage`) |
| `volume_min` / `tick_size` changed | Any algos with hardcoded lot sizes? |
| `kraken_pair_name` changed | Rare — verify against Kraken API docs before committing. |
| `base_currency` / `quote_currency` changed | Should not happen — integrity check raises on next load. |
| `symbols_hash` changed | Actual spec change. Expected if any spec field changed above. |
| Only `last_fetched` changed, hash same | Pure timestamp update — safe to commit without deep review. |

### Pre-Populating the Runtime Cache

Before the first AutoTrader live session (or after deleting the cache), the runtime cache must exist. Use the sync CLI to populate it for all symbols in the tick index:

```bash
python python/cli/broker_config_cli.py sync
# or for a specific broker:
python python/cli/broker_config_cli.py sync --broker kraken_spot
```

The sync CLI:
1. Discovers all symbols in the tick index for each dynamic broker
2. Fetches symbol specs from the broker API for each symbol
3. Writes (or merges into) `data/runtime/brokers/<broker_type>/<broker_type>_broker_config.json`

After syncing, AutoTrader sessions can start offline. The weekly staleness check still applies — a session started more than 7 days after the last sync triggers a fresh API fetch.

**VS Code:** Use `🔧 Broker Config: Sync (all dynamic brokers)` in launch.json for a one-click sync.

### Symbol Integrity Validation

`BrokerConfigFactory` validates every loaded broker config (file or dict):

- `base_currency` and `quote_currency` must match the symbol key
- Example: `DASHUSD` must have `base_currency=DASH`, `quote_currency=USD`
- Error is raised at load time with file path, symbol name, and expected values

This catches copy-paste errors in static configs and schema drift in refreshed cache files before they propagate into a live session.

---

## Adding a New Broker

1. Create JSON config in `configs/brokers/<broker>/`
2. Add entry to `market_config.json` with `broker_type` and `market_type`
3. If new broker type: Create adapter extending `AbstractAdapter`
4. Implement required abstract methods: `_validate_config()`, `get_broker_type()`, `get_symbol_specification()`, etc.
