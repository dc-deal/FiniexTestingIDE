# Cold Start Tests Documentation

## Overview

The cold-start suite validates the boot step that rebuilds this bot's resting orders from
broker truth (#355 Phase 2) and the framework carry-over that makes the recognition possible.

**Location:** `tests/autotrader/cold_start/`

All tests run offline. The executor is the **real** `LiveTradeExecutor` over a
`MockBrokerAdapter` — the adoption surface and the position counter are exactly what the boot
step writes to, and a stand-in would prove only that the stand-in works.

---

## The question the suite answers

Ownership, not existence, decides what may be adopted:

| | What the broker shows | Ownership decidable? | What happens |
|---|---|---|---|
| Resting order, our key, session known from the carry-over | yes | **yes** | adopted, internal id recovered |
| Resting order, our key SHAPE, unknown session | yes | no | left alone |
| Resting order, no key | yes | no | left alone |
| A balance | yes | **no** — a coin carries no owner tag | never adopted here; declared capital instead |

---

## Test Structure

```
tests/autotrader/cold_start/
├── conftest.py                       ← real executor + mock adapter, carry-over store, RecordingLogger
├── test_cold_start_adoption.py       ← the boot step: what is adopted, what is refused
└── test_cold_start_state_store.py    ← the carry-over document and its index
```

---

## What Each File Validates

| File | Focus |
|------|-------|
| `test_cold_start_adoption.py` | **Ownership:** an order from a session the carry-over knows is adopted and its internal id RECOVERED from the key's counter (not invented); a foreign client's key of the same shape, an order with no key, and a first-ever boot with no carry-over all leave the venue untouched. **Counter:** after adopting `pos_btcusd_1` the next minted id is `pos_btcusd_2`, and the carry-over alone lifts the counter when the predecessor's orders are already gone. **Refusals:** `operator_confirm` without a terminal refuses and stays flat (error pot, §35), `auto` adopts and says nobody confirmed, an unreachable venue stops the boot rather than starting blind. **Dry run:** reports what it would adopt and adopts nothing |
| `test_cold_start_state_store.py` | Round-trip of the two payload fields; keys ACCUMULATE across sessions (an order can rest across several restarts) and a repeated key moves rather than duplicates; the counter only ever rises; the key list is capped; provenance (`written_by_run_id`) is recorded and is NOT the key — the file is named after the BOT. Damage degrades rather than stops: unreadable JSON and another bot's document are reported and treated as absent. **Index:** describes every bot from one file, skips a damaged document instead of failing wholesale, goes stale when a bot is REMOVED (the row count catches what mtimes cannot), and is named `<store_id>_index.parquet` |

---

## Key Mechanisms Tested

### The recovered id

A wire key carries only the session discriminator and the counter (`p8b3f_47`) — Kraken allows
18 characters, so the readable internal id never goes on the wire. The id is rebuilt as
`pos_<symbol>_<counter>`, and the symbol comes from the profile: one profile is one symbol.

### Why the carry-over is required, not a convenience

Without it, "an order my predecessor placed" and "an order some other client placed on this
account" are the same observation — the venue's open-order list is account-wide. The first-boot
test pins that: with no carry-over, even an order carrying our exact format is left alone.

### The refusal is the feature

Unattended plus confirm-by-default would mean a bot that silently stops after a 03:00 restart.
It refuses instead and stays flat, which is a state an operator can find in the morning — and
the refusal reaches the session error pot, so the run cannot grade green.

---

## Running

```bash
python -m pytest tests/autotrader/cold_start/ -v
```

Launch entry: `🧩 Pytest: Cold Start (#355)`.
