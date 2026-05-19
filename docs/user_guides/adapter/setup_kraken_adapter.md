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

## 3. Broker Connection Settings

Connection settings for `kraken_spot` live in `configs/market_config.json` under the `kraken_spot` broker entry:

```json
{
  "broker_type": "kraken_spot",
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

| Field | Description | Default |
|-------|-------------|---------|
| `credentials_file` | Credentials filename (resolved via cascade) | `kraken_credentials.json` |
| `dry_run` | Validate orders without executing (`validate=true`) | `true` |
| `broker_transport.api_base_url` | Kraken REST API base URL | `https://api.kraken.com` |
| `broker_transport.rate_limit_interval_s` | Minimum seconds between private API calls | `1.0` |
| `broker_transport.request_timeout_s` | HTTP request timeout in seconds | `15` |
| `broker_transport.poll_interval_ms` | Minimum interval between per-order status polls (live LIMIT orders) | `5000` |

To override any field, create `user_configs/market_config.json` with only the changed values:

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

`user_configs/market_config.json` is gitignored — safe for real credentials references and live mode flags.

### Dry-Run Mode

When `dry_run: true`, the adapter sends `validate=true` to Kraken's AddOrder endpoint. Kraken validates the order completely (pair, volume, balance, permissions) but **does not execute it**. No money is moved, no txid is generated.

This is Kraken's native validation parameter — not a local simulation. It catches real API errors (bad permissions, insufficient balance, invalid pair) without risking funds.

**Kraken Spot has no testnet/sandbox.** Dry-run mode is the only way to test order flow without real execution.

`dry_run` is a **broker-level deployment decision** — not a per-session flag. It applies to all AutoTrader sessions that use `kraken_spot`. The committed default (`configs/market_config.json`) is always `true`. Switch to live trading by overriding in `user_configs/market_config.json`.

## 4. AutoTrader Profile

AutoTrader profiles contain only algorithm config — no broker connection fields needed. Example `configs/autotrader_profiles/ethusd_live.json`:

```json
{
  "name": "ethusd_live",
  "symbol": "ETHUSD",
  "broker_type": "kraken_spot",
  "adapter_type": "live",
  "strategy_config": { ... },
  "account": { "balances": { "USD": 0.0, "ETH": 0.0 }, "account_currency": "USD" },
  ...
}
```

`balances` with `0.0` values is intentional — the startup fetches real balances for all listed currencies from Kraken and overrides these values. `account_currency` is optional — if omitted, the quote currency (USD) is used by default. Set it explicitly to use the base currency (e.g., `"ETH"`) for P&L denomination.

## Config File Relationship

```
AutoTrader Profile (ethusd_live.json)
  "broker_type": "kraken_spot"
        |
        v
market_config.json → kraken_spot entry
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
**market_config.json** = broker-specific live config (API URL, dry_run, rate limit, credentials reference).
**Credentials** = only API keys.

## 5. First Run (Dry-Run)

```bash
python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/ethusd_live.json
```

Expected startup output:
```
Live broker config fetched for ETHUSD
Live balance: 0.006 ETH (profile default was 0.0)
Mode: DRY RUN (validate only)
```

If balance fetch succeeds, your API key and permissions are correct. Orders will be validated by Kraken but not executed.

## 6. Going Live

Set `dry_run: false` in `user_configs/market_config.json` (gitignored):

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

**This enables real order execution with real money.** Orders sent via `LiveRequestProcessor.submit_open_order` (composing the adapter's Tier-3 layers `_build_submit_payload` → `_do_request_submit` → `_parse_submit_response`) will be placed on the Kraken order book.

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
