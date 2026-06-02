# Kraken Adapter — Nonce Tests

Offline unit tests for the Kraken adapter's private-call nonce handling.

## Background

The first live Field Study (#332) aborted with `EAPI:Invalid nonce`: the adapter
stamped a millisecond nonce (`int(time*1000)`) with no lock, so concurrent private
calls (the order-polling worker thread + the reconciler) collided on the same
millisecond or reached Kraken out of order. The fix:
- private calls are serialized under one lock (`_private_lock`), held through the POST;
- the nonce is strictly monotone: `max(int(time*1000), _last_nonce + 1)` — the time
  component keeps it above the previous session (across restarts), the counter
  guarantees a strict increase within the process (same-ms / concurrency).

## What it covers

| Test class | Asserts |
|---|---|
| `TestNonceMonotonicity` | 1000 rapid calls → nonces strictly increasing (same-ms → counter increments) |
| `TestNonceThreadSafety` | 8 threads × 100 calls under `_private_lock` → all 800 unique + strictly increasing |

Offline — `_sign_request` only signs + stamps the nonce (no network). The fixture
builds a `KrakenAdapter` from the real broker config with dummy credentials.

## Run

```bash
pytest tests/autotrader/kraken_adapter/ -v
```

Or launch.json: `🧩 Pytest: Kraken Adapter (#332 nonce)`.

See `python/framework/trading_env/adapters/kraken_adapter.py`
(`_do_fetch_private`, `_sign_request`).
