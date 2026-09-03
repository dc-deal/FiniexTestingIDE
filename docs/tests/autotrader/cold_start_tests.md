# Cold Start Tests Documentation

## Overview

The cold-start suite validates the boot step that rebuilds this bot's resting orders from
broker truth (#355 Phase 2), the position book it reads back from its own carry-over, and the
say the decision logic gets in the matter (#493).

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

And one row that is not about ownership at all:

| | Where it comes from | What happens |
|---|---|---|
| A spot POSITION | our own note, written by the session before | restored at boot, then held against the venue's balance |

At spot you have to remember your positions; at margin they sit in the market. That is the
whole reason the book exists — the venue cannot describe a holding as a position, so nobody
but us can testify to one.

---

## Test Structure

```
tests/autotrader/cold_start/
├── conftest.py                       ← real executor + mock adapter, carry-over store, RecordingLogger
├── test_cold_start_adoption.py       ← the boot step: what is adopted, what is refused
├── test_cold_start_state_store.py    ← the carry-over document and its index
├── test_position_book.py             ← the remembered positions: note, watcher, restore, cross-check
├── test_cold_start_hook.py           ← the algo's say: loosening only, and no case disappears
└── test_boot_wiring.py               ← what AutotraderMain writes, and when it refuses to
```

---

## What Each File Validates

| File | Focus |
|------|-------|
| `test_cold_start_adoption.py` | **Ownership:** an order from a session the carry-over knows is adopted and its internal id RECOVERED from the key's counter (not invented); a foreign client's key of the same shape, an order with no key, and a first-ever boot with no carry-over all leave the venue untouched. **Counter:** after adopting `pos_btcusd_1` the next minted id is `pos_btcusd_2`, and the carry-over alone lifts the counter when the predecessor's orders are already gone. **Refusals:** `operator_confirm` without a terminal refuses and stays flat (error pot, §35), `auto` adopts and says nobody confirmed, an unreachable venue stops the boot rather than starting blind. **Dry run:** reports what it would adopt and adopts nothing |
| `test_position_book.py` | **The note loses nothing:** every scalar survives the round trip, a PARTIALLY_CLOSED position does not return as untouched, the excursion extrema (#389) survive a constructor that would otherwise overwrite them, the incurred fee still counts toward the position and keeps its type, the entry executions are not dropped. **The watcher:** an unchanged book is not written again, a partial close / a moved stop / a new position each count as a change, and "nothing written yet" counts as one. **Restore:** a remembered position is back before the first tick, a margin session leaves the note alone and says so, a dry run restores nothing. **Cross-check:** more coins than the book claims is not a divergence, fewer is reported — and the book is NOT adjusted to fit |
| `test_cold_start_hook.py` | **Loosening only:** an accounted-for situation starts where the framework would have refused (and says so), a declining answer behaves exactly like no hook, and a declining answer cannot stop an automatic adoption. **Always told:** the hook is asked in `auto` mode too, and when only a stranger's order is resting, but not on an empty venue and not in a dry run; an answer of the wrong type is reported and read as "no". **The situation is complete:** the recovered order id, every skip reason separately, `is_clean()` in both directions, the resolved policy and whether anybody declared themselves present. **The contract:** a logic declaring a resting type must answer, a MARKET-only logic is not asked, a wrong arity is caught before the venue is queried |
| `test_boot_wiring.py` | **The tick-loop seam:** a structural change writes once and then stays quiet, a FAILED write is retried on the next pass (the watcher advances only on success), a moved stop waits for the tick cadence, and the cadence needs no clock — the first passes happen before one is injected. Nothing is written without permission (a refused or dry boot appends no key); the key, counter and protected key set all reach the store; a margin session passes `None` for the book (leaving a stored one alone) while a spot session with nothing open passes `[]` (a statement that overwrites); a failing store is logged into the session channel and swallowed |
| `test_cold_start_state_store.py` | Round-trip of the payload fields; keys ACCUMULATE across sessions (an order can rest across several restarts) and a repeated key moves rather than duplicates; the counter only ever rises; the key list is capped; provenance (`written_by_run_id`) is recorded and is NOT the key — the file is named after the BOT. Damage degrades rather than stops: unreadable JSON and another bot's document are reported and treated as absent. **Index:** describes every bot from one file, skips a damaged document instead of failing wholesale, goes stale when a bot is REMOVED (the row count catches what mtimes cannot), and is named `<store_id>_index.parquet` |

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

### The book is memory, not adoption

Restoring a position needs no tick and no clock: every field was known when the position was
opened, so the note is materialised straight back. The `__post_init__` of `Position` seeds the
excursion prices from the entry price unconditionally, which would silently discard restored
extrema — the projection assigns them after construction, and a test names that trap so a
future simplification cannot quietly undo it.

Restored fees are attached to the position but never re-counted into the new run's cost
tracking. The fee was charged to the run before, so the *trade's* net P&L carries it while
this run's fee total does not — an asymmetry the cold-start report block states out loud.

### The note covers Position, and a test says so

`Position` and `PositionCarryOver` describe the same thing and nothing in the language makes
them agree. The failure mode is silent: a field added to Position stops surviving restarts,
and the first symptom is a report that looks complete. So every Position field is either
carried or listed in `NOT_CARRIED` with a reason — adding one without deciding turns the
suite red. The reverse direction is asserted too: a note field with no counterpart would be
restored into nothing.

### The hook may only loosen

The framework's refusal is the floor. `accounted_for=True` lifts exactly one refusal — the
unattended `operator_confirm` case, the only place where the framework declines for lack of an
answer rather than for lack of knowledge. It never overrides an operator who answered in
person, and it can never make the framework refuse: an algo able to lock itself out of starting
would be a failure mode invisible from outside.

A yes also has to be SPECIFIC: it must name every adopted order and give a reason, or it is
not honoured — asserted in both directions (a yes naming nothing, a yes naming only some, a
yes without a reason, and a complete yes that is honoured).

The hook is asked on every boot that found anything, including `auto` mode where there is no
refusal to lift — because `auto` is what an unattended thirty-day run uses, and a hook that
only fires at a refusal would be silent in exactly the case it exists for. The answer is
recorded either way: the situation reaches the session channel and the run record whether the
algo accounted for it or not, so "the algo said it was fine" can never look like "nothing was
found".

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
