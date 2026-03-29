# Kraken Adapter Setup Guide

How to configure the KrakenAdapter for live trading (dry-run and production).

## Prerequisites

- **Kraken Spot account** at [kraken.com](https://www.kraken.com) (NOT Kraken Futures)
- Funded account (at least minimum order size for your pair)
- API key with correct permissions

## 1. Create API Key

Go to **kraken.com/u/security/api** and create a new key:

**Required permissions:**
- Query Funds
- Query Open Orders & Trades
- Create & Modify Orders

**Do NOT enable:** Withdraw Funds

Copy the **API Key** and **Private Key** (shown once at creation).

## 2. Credentials File

Create `user_configs/credentials/kraken_credentials.json`:

```json
{
  "api_key": "YOUR_API_KEY_HERE",
  "api_secret": "YOUR_PRIVATE_KEY_HERE"
}
```

This file is gitignored. The tracked default at `configs/credentials/kraken_credentials.json` contains placeholder values.

**Cascade:** `user_configs/credentials/` takes priority over `configs/credentials/`.

## 3. Broker Settings

Create or edit `user_configs/broker_settings/kraken_spot.json`:

```json
{
  "credentials_file": "kraken_credentials.json",
  "api_base_url": "https://api.kraken.com",
  "dry_run": true,
  "rate_limit_interval_s": 1.0,
  "request_timeout_s": 15,
  "symbol_to_kraken_pair": {
    "BTCUSD": "XBTUSD",
    "BTCEUR": "XBTEUR",
    "ETHUSD": "ETHUSD",
    "ETHEUR": "ETHEUR",
    "SOLUSD": "SOLUSD",
    "ADAUSD": "ADAUSD",
    "XRPUSD": "XRPUSD",
    "LTCUSD": "LTCUSD",
    "DASHUSD": "DASHUSD"
  }
}
```

| Field | Description | Default |
|-------|-------------|---------|
| `credentials_file` | Credentials filename (resolved via cascade) | `kraken_credentials.json` |
| `api_base_url` | Kraken REST API base URL | `https://api.kraken.com` |
| `dry_run` | Validate orders without executing (`validate=true`) | `true` |
| `rate_limit_interval_s` | Minimum seconds between private API calls | `1.0` |
| `request_timeout_s` | HTTP request timeout in seconds | `15` |
| `symbol_to_kraken_pair` | Standard symbol → Kraken pair name mapping for order API | See config |

**Cascade:** `user_configs/broker_settings/` takes priority over `configs/broker_settings/`.

### Dry-Run Mode

When `dry_run: true`, the adapter sends `validate=true` to Kraken's AddOrder endpoint. Kraken validates the order completely (pair, volume, balance, permissions) but **does not execute it**. No money is moved, no txid is generated.

This is Kraken's native validation parameter — not a local simulation. It catches real API errors (bad permissions, insufficient balance, invalid pair) without risking funds.

**Kraken Spot has no testnet/sandbox.** Dry-run mode is the only way to test order flow without real execution.

## 4. AutoTrader Profile

Point your profile to the broker settings file. Example `configs/autotrader_profiles/btcusd_live.json`:

```json
{
  "name": "btcusd_live",
  "symbol": "BTCUSD",
  "broker_type": "kraken_spot",
  "broker_config_path": "configs/brokers/kraken/kraken_spot_broker_config.json",
  "adapter_type": "live",
  "broker_settings": "kraken_spot.json",
  "strategy_config": { ... },
  "account": { "initial_balance": 0.0, "currency": "USD" },
  ...
}
```

`initial_balance: 0.0` is intentional — the startup fetches the real balance from Kraken and overrides this value.

## Config File Relationship

```
AutoTrader Profile (btcusd_live.json)
  "broker_settings": "kraken_spot.json"
        |
        v
Broker Settings (user_configs/broker_settings/kraken_spot.json)
  "credentials_file": "kraken_credentials.json"
  "dry_run": true
  "api_base_url": "https://api.kraken.com"
        |
        v
Credentials (user_configs/credentials/kraken_credentials.json)
  "api_key": "..."
  "api_secret": "..."
```

**Profile** = algorithm config (strategy, symbol, workers).
**Broker Settings** = broker-specific live config (API URL, dry_run, rate limit, credentials reference).
**Credentials** = only API keys.

## 5. First Run (Dry-Run)

```bash
python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/btcusd_live.json
```

Expected startup output:
```
Broker settings loaded: kraken_spot.json (dry_run=True)
Live broker config fetched for BTCUSD
Live balance: 1234.56 USD (profile default was 0.0)
Mode: DRY RUN (validate only)
```

If balance fetch succeeds, your API key and permissions are correct. Orders will be validated by Kraken but not executed.

## 6. Going Live

Set `dry_run: false` in your broker settings file:

```json
{
  "dry_run": false
}
```

**This enables real order execution with real money.** Orders sent via `execute_order()` will be placed on the Kraken order book.

Ensure:
- Your account has sufficient balance for the configured `lot_size`
- You understand the minimum order sizes for your trading pair (e.g., BTCUSD minimum ~0.0001 BTC)
- You have tested the full pipeline in dry-run mode first

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `EAPI:Invalid key` | Wrong API key, wrong portal (Futures vs Spot), expired key | Verify at kraken.com/u/security/api — must be Spot, not Futures |
| Balance fetch failed | API key missing "Query Funds" permission | Edit API key permissions at kraken.com |
| Symbol not found | Wrong pair name format | Use `BTCUSD`, not `BTC/USD` or `XBT/USD` |
| `Kraken API error: ['EOrder:Insufficient funds']` | Account balance too low for order size | Reduce `lot_size` in strategy config or fund account |
| Credentials file not found | File not in expected cascade path | Check `user_configs/credentials/` and `configs/credentials/` |
| `ConnectionError` / timeout | Network issue or Kraken API outage | Check [status.kraken.com](https://status.kraken.com), retry |
