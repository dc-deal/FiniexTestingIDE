# Reconciliation Tests Documentation

## Overview

The reconciliation test suite validates the live reconciliation layer (#151,
Phase 1–2): the broker truth-pull (orders / balances / positions) and the
`Reconciler` running in **ALERT_ONLY** — detect + report divergence between the
local shadow state and broker truth, no mutation, no halt.

**Location:** `tests/autotrader/reconciliation/`

All tests run offline. Broker truth comes from `MockBrokerAdapter` (seeded state
+ `MockDivergenceMode`). The real Kraken pulls are validated separately by the
live-adapter tests and the Field Study (#332).

---

## Test Structure

```
tests/autotrader/reconciliation/
├── conftest.py                      ← builders (make_pending / make_broker_order / ...) + factory fixtures
├── test_broker_truth_pull.py        ← Phase 1: mock pulls + divergence modes
├── test_kraken_pull_parsers.py      ← Phase 1: Kraken _parse_* layers (pure, offline)
├── test_reconciler_diff.py          ← Phase 2: SPOT order diff (ghost/orphan/stale + in-flight grace)
├── test_reconciler_margin.py        ← Phase 2: MARGIN position diff; SPOT gate
├── test_cadence_and_alert_only.py   ← Phase 2: is_due hybrid + non-mutation + counters
├── test_flat_preflight.py           ← Phase 2: is_account_flat (orders + balances)
└── test_reconciliation_config.py    ← ReconciliationDefaults loader wiring
```

---

## What Each File Validates

| File | Focus |
|------|-------|
| `test_broker_truth_pull.py` | `MockBrokerAdapter.get_broker_orders/balances/positions` return seeded state; `MockDivergenceMode` (`DROP_ORDERS`, `DROP_BALANCE`, `PHANTOM_POSITION`, `STALE_PRICE`) perturbs as documented |
| `test_kraken_pull_parsers.py` | `KrakenAdapter._parse_openorders/balance/openpositions_response` map raw Kraken payloads → typed objects; reverse pair→symbol (XETHZUSD → ETHUSD); buy/sell → LONG/SHORT; dry-run sentinel → empty; zero-balance dropped |
| `test_reconciler_diff.py` | SPOT order reconciliation: clean match, ghost (broker extra), orphan (local extra), stale (price mismatch), `DROP_ORDERS` → all orphans, partial-fill bucket. **In-flight grace:** local orders with `broker_ref=None` or `DRYRUN-*` are excluded (no false orphans) |
| `test_reconciler_margin.py` | Position diff is `MARGIN`-gated: on SPOT a synthesized local position does NOT read as orphan; on MARGIN ghost/orphan/stale positions are detected |
| `test_cadence_and_alert_only.py` | `is_due` hybrid (every N ticks OR M seconds); `reconcile()` resets the tick window; ALERT_ONLY does not mutate local state; current-cycle vs cumulative divergence counters; **recovery** (a resolved divergence resets the current count + clean flag so the panel returns to ● ok); non-`alert_only` mode raises `NotImplementedError` (→ #349) |
| `test_flat_preflight.py` | `is_account_flat()` on SPOT: flat when no resting orders and only quote-currency balance; not flat with a resting order or a non-quote asset balance; dust balances ignored |
| `test_reconciliation_config.py` | `ReconciliationDefaults` defaults off; profile values applied via `load_autotrader_config`; unknown key → `ValueError` |

---

## Key Mechanisms Tested

### Divergence vocabulary

- **ghost** — broker has it, we lack it locally (`BrokerOrder` / `BrokerPosition`)
- **orphan** — we have it locally, broker lacks it (`PendingOrder` / `Position`)
- **stale** — matched by `broker_ref` but a field (price / lots) diverges

### TradingModel gate

The Reconciler reconciles **orders** in both worlds, but **positions only on
MARGIN**. On SPOT the broker has no position object (holdings are balances), so
the position diff is skipped — this is the regression guard against a permanent
false "orphan position" alert on every spot session. The MARGIN path is exercised
now via a MARGIN-configured Reconciler over a mock, proving the MT5 (#209) path
before MT5 exists.

### In-flight / dry-run grace

Local resting orders without a settled `broker_ref` (`None` while mid-roundtrip,
or `DRYRUN-*` in dry-run) are excluded from the diff, so the async submit
roundtrip and dry-run sessions never produce false orphans.

### ALERT_ONLY

`reconcile()` logs (`[RECONCILE]`) and counts divergences but never mutates the
portfolio or the order state. `AUTO_CORRECT` / `HALT_TRADING` land in #349 — the
Reconciler rejects any non-`alert_only` mode at construction.

---

## Fixtures

Code-level fixtures live in `conftest.py`:

| Fixture | Description |
|---------|-------------|
| `logger` | `GlobalLogger` for isolated tests |
| `mock_adapter` | Fresh `MockBrokerAdapter` (broker truth source) |
| `make_reconciler` | Factory building a `Reconciler` over a `FakeExecutor` with seeded local orders / positions, a `TradingModel`, and a config |

Builder functions (`make_pending`, `make_broker_order`, `make_position`,
`make_broker_position`) are imported from `conftest` by the test modules.
