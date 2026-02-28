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
MT5Adapter / KrakenAdapter  → Broker-specific implementation
```

### Key Classes

| Class | Purpose |
|-------|---------|
| `AbstractAdapter` | Abstract base, `_validate_common_config()` for shared validation |
| `MT5Adapter` | MetaTrader 5 brokers (Forex, CFD) |
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

---

## Adding a New Broker

1. Create JSON config in `configs/brokers/<broker>/`
2. Add entry to `market_config.json` with `broker_type` and `market_type`
3. If new broker type: Create adapter extending `AbstractAdapter`
4. Implement required abstract methods: `_validate_config()`, `get_broker_type()`, `get_symbol_specification()`, etc.
