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

| Model | FeeType Enum | Adapter | Calculation | Charged |
|-------|--------------|---------|-------------|---------|
| `"spread"` | `FeeType.SPREAD` | MT5 | `spread_points * tick_value * lots` | **once**, at entry — the spread IS the round-trip price |
| `"maker_taker"` | `FeeType.MAKER_TAKER` | Kraken | `order_value * (rate / 100)` | **on every fill** — a round trip pays twice |

The `Charged` column is the part that decides money rather than magnitude (#506): booking an
exit fee on a spread broker would double-count its round-trip price, and NOT booking one on a
maker/taker venue makes every completed trade cost half of what it says.

### Fee Flow (both executors)

```
_fee_model()                       ← ONE lookup: fee_structure.model, default 'spread'
    │                                 read by both factories so the two legs cannot disagree
    ├── _create_entry_fee()   (from _fill_open_order)
    │       if MAKER_TAKER → create_maker_taker_fee()
    │       else           → create_spread_fee_from_tick()
    │
    └── _create_exit_fee()    (from _fill_close_order)
            if MAKER_TAKER → create_maker_taker_fee()   ← on close_lots, taker rate
            else           → None                       ← nothing to charge per side
```

Both factories call `adapter.get_maker_fee()` / `get_taker_fee()` **per fill**, and the Kraken
adapter reads them live from `broker_config` rather than caching them at construction (#337) —
so a rate written into the config during startup takes effect at the next fill instead of at
the next restart.

### Where a fee rate comes from — three readers, three answers (#337)

A volume-tiered venue prices per ACCOUNT, so a rate written into a file is a guess about which
tier that account sits in. Measured 2026-09-08: this project's Kraken account is charged taker
**0.8000 %** / maker **0.4000 %** while the seed declared 0.40 / 0.25 — half of both, in the
direction that makes every maker/taker backtest look cheaper than it is. The seed was
re-frozen to the measured rates on that date.

| Reader | Source | Why |
|---|---|---|
| **Backtest** | the git-tracked **seed**, always. Never asks | A run must be reproducible from a COMMIT. The rate is a written-down decision with a date, not a by-product of whenever someone last synced |
| **Live** | the seed as its baseline, **overridden by the venue's answer** when `auto_detect_fee_tier` is on | Correct by construction, and it starts from the same declared number the backtest uses so the two agree about what was expected |
| **Both** | a **WARNING** whenever the venue disagrees with the seed | The signal to re-freeze. It fires even with the switch OFF, because only a human can update the seed and the backtest keeps the old rate until they do |

```json
"brokers": [{
    "broker_type": "kraken_spot",
    "auto_detect_fee_tier": false,     ← opt-in, per broker, beside dry_run
    ...
}]
```

**Opt-in on purpose:** turning a declared number into a fetched one should be a decision. With
the switch off the divergence is still reported, so nothing is hidden either way.

The capability is **by override, not by flag**: `AbstractBrokerConfigFetcher.fetch_fee_tier()`
returns `None` by default, `KrakenConfigFetcher` overrides it with a `/0/private/TradeVolume`
call, and a spread broker never implements it. No `isinstance`, no branch at the call site.

**A note for anyone writing another tiered adapter:** Kraken keys its answer by the venue's
CANONICAL pair name, not by the symbol asked about — `pair=ETHUSD` comes back under `XETHZUSD`.
Indexing the response by the symbol raises `KeyError`. Measured 2026-09-08.

### Re-freezing a rate — the recurring act, and why it stays manual

The warning above says the account has moved to another tier. Acting on it is one edit to one
file, and the seed records WHEN and from WHAT:

```json
"_fee_structure_frozen": {
    "date": "2026-09-08",
    "source": "/0/private/TradeVolume, pair=ETHUSD -> XETHZUSD",
    "measured": "maker 0.4000 % / taker 0.8000 %; next tier 0.30/0.60 at 30-day volume 2500"
},
"fee_structure": { "model": "maker_taker", "maker_fee": 0.40, "taker_fee": 0.80, ... }
```

The provenance block sits OUTSIDE `fee_structure` deliberately: `config_hash` is computed over
that block, so a note written inside it would move the reproducibility anchor without changing
a single price.

It stays a human act, and the reason is not caution — it is that **no fetch can answer the
question a backtest asks.** The tier depends on 30-day rolling volume, which the bot's own
trading moves: a strategy that trades more prices itself into a cheaper tier. So the "correct"
rate for a run that has not happened yet does not exist to be looked up. The declared rate is an
ASSUMPTION, and the framework's job is to make it visible, dated and part of the run's identity
— not to keep it "true".

Two consequences worth stating plainly:

- **When in doubt, declare the WORSE rate.** A backtest that is too expensive under-promises; one
  that is too cheap manufactures profit that the live account will not produce. The state
  corrected on 2026-09-08 erred in the second direction for an unknown length of time.
- **A rate the verdict depends on is not a rate, it is a risk.** If a strategy is profitable at
  0.40 % and unprofitable at 0.80 %, the honest report is that the cost assumption decides the
  outcome — which is a sweep over the rate, not a better guess at it.

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
- Pip size derivation (`pip_mode`): how a per-symbol pip price unit is derived (see below)

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

Crypto uses larger blocks (72h vs 24h) and a higher ATR percentile threshold (P15 vs P10) because
24/7 markets have less pronounced volatility minima than session-based Forex markets. These defaults
override `generator_config.json` when present. The `split_algorithm` (always `atr_minima`) remains
global in `generator_config.json`.

### Pip Size Derivation (`pip_mode`)

A decision logic configured in pip-denominated parameters (stop distance, SL, TP) needs an
authoritative per-symbol **pip size** so one bot config runs across instruments without manual
overrides. The single source is `tick_size` + `digits` from `symbols`, interpreted by the
market's `pip_mode` (a required field on each `market_rules` entry):

```json
"market_rules": {
    "forex":  { "pip_mode": "fractional_pip" },
    "crypto": { "pip_mode": "tick" }
}
```

| `pip_mode` | Rule | Examples |
|---|---|---|
| `fractional_pip` | Forex pip convention. A fractional-pip ("pipette") broker quotes one extra digit (5-digit, or 3-digit JPY) → `pip = tick_size * 10`; a whole-pip broker (4-/2-digit) → `pip = tick_size`. | EURUSD `0.0001`, USDJPY `0.01` |
| `tick` | No pip concept (crypto / others) — the broker tick **is** the price unit. | BTCUSD `0.1`, ETHUSD `0.01`, ADAUSD `0.000001` |

Why market-aware: a "pip" is a Forex convention (the 4th decimal, 2nd for JPY); crypto has no pip,
so its tick is the natural unit. A digits-only or uniform `tick * 10` shortcut silently mis-scales
crypto by 10× (and `tick * 10` is float-dirty for tiny ticks). The derivation lives once in
`framework/utils/trading_math/pip_math.py` (`derive_pip_size`); the adapter exposes it as
`get_pip_size(symbol)`.

**How a decision logic accesses it:** the adapter's `get_pip_size(symbol)` fills
`TradingContext.pip_size` at scenario startup (both pipelines). A decision logic reads
`self.trading_context.pip_size` when its own `pip_size` parameter is left unset; an explicit
`pip_size` in the config always wins (non-standard instruments). The same authoritative value is
stamped on each `TradeRecord` at the source, so the run report shows MAE/MFE in the correct unit
(`pip` on Forex, `tick` on crypto) without any renderer re-deriving it.

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
  → FEE STRUCTURE for every run, live and backtest alike
  → Symbol specs for static-mode brokers
  → Never auto-overwritten

data/runtime/brokers/kraken_spot/kraken_spot_broker_config.json  ← gitignored hot cache
  → SYMBOL SPECS for dynamic-mode brokers, live and backtest alike
  → Auto-refreshed from Kraken API (weekly)
```

The static seed is committed with the codebase. The runtime cache is gitignored and machine-local.

**The split is by CONCERN, not by pipeline (#337).** Symbol specifications come from whichever
source the broker's `config_mode` selects — they are expensive to fetch and they genuinely change
at the venue. The **fee structure always comes from the seed**, in both pipelines, and
`BrokerDataPreparator._load_dynamic_broker_config` overwrites the cache's block with it.

The reason is reproducibility and it is not a preference: a fee rate moves realised P&L on every
trade, and the cache is gitignored and machine-local. A rate arriving through it would make two
backtests over identical data disagree, with one `broker_config_cli.py sync` between them and
nothing in either run able to say why. A number that moves the curve has to be a decision written
down in a commit.

*(Until 2026-09-08 this block described the intent and the code did something else: the dynamic
branch took the WHOLE config from the cache, fees included. It was harmless only because the
fetcher hardcodes the rates rather than fetching them, so the two happened to agree.)*

### Staleness Policy (dynamic mode)

| Cache age | Behavior |
|-----------|----------|
| < 7 days | Use cache silently — no API call (unless symbol is missing, see below) |
| 7–30 days | Try API refresh; on failure warn + use stale cache |
| > 30 days | Try API refresh; on failure strong warning (specs may be outdated) |
| No cache | Try API refresh; on failure hard error (first run, no fallback) |

On first run, an internet connection is required. After the first successful fetch, the session can run offline for up to 30 days before generating a strong staleness warning.

**Lazy symbol addition:** Symbols are added to the cache on demand. If a fresh cache exists but does
not contain the requested symbol (e.g., the cache was built during an ETHUSD session and you now
start a DOTUSD session), the missing symbol is fetched from the API and merged into the cache —
without affecting the age or status of existing symbols. Each symbol carries a `_last_fetched` field
indicating when it was last individually verified.

### Tombstone Symbols

Each symbol in the runtime cache has an `_active` field. Symbols fetched from the API are always written with `_active: true`. Symbols that are not part of a given API fetch are left unchanged — they are **not** tombstoned automatically. This means:

- Running ETHUSD and then DOTUSD results in a cache with both symbols marked `_active: true`
- Tombstoning (`_active: false`) is reserved for a future full-refresh path (a `broker-config sync` CLI that fetches all symbols at once and marks any missing ones as inactive)
- In the static seed, `_active` can be set manually to exclude a symbol from backtesting validation without deleting its spec

### Config Hash / Seed ID

Each loaded broker config is stamped with **two** 8-char SHA256 hashes, because they answer
different questions (#337):

- **`config_hash`** — the REPRODUCIBILITY anchor, over `symbols` **and** `fee_structure`. What
  `BrokerConfig.config_hash` returns, and what the run report's broker section carries — so it
  reaches the console, the report file and the API alike. A fee rate change moves it, because it
  changes what the run produces.
  **It does NOT reach the run ledger or any certificate yet** (#510): those are the artifacts
  that compare runs to each other, so until they carry it, two runs priced differently are still
  indistinguishable in the cross-run record.
- **`symbols_hash`** — the SYMBOL SET's identity, over `symbols` alone. What the config fetcher
  and `broker_config_cli.py` print to say whether a cache still describes the same instruments.

A config written before the split carries only `symbols_hash`, and the property falls back to it.

The examples below show the identity stamped onto a loaded config:

```
🗄  Broker config loaded: kraken_spot [a3f82c11]
    Source: data/runtime/brokers/kraken_spot/kraken_spot_broker_config.json
    Symbols: 9 active
```

The hash appears in:
- Startup log (global logger)
- Batch summary: `Config:  [a3f82c11]` line under broker info
- AutoTrader live header: `BTCUSD (kraken_spot) [a3f82c11] — DRY RUN`

Neither hash covers `_config_meta`, so a timestamp refresh does not move them.

### Syncing the Static Seed

When the runtime cache has been refreshed and you want to commit the new specs:

```bash
cp data/runtime/brokers/kraken_spot/kraken_spot_broker_config.json \
   configs/brokers/kraken/kraken_spot_broker_config.json
git diff configs/brokers/kraken/kraken_spot_broker_config.json
```

**Read the `fee_structure` half of that diff deliberately.** Symbol specs are the venue's facts
and copying them is routine; the fee rates are the number every backtest will price its trades
with from the next commit onwards. Committing them is the act of FREEZING a rate — give it the
same attention as a threshold, and expect `config_hash` to move for every run afterwards.

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
