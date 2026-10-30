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
| Resting order, our key, session known from the carry-over | yes | **yes** | adopted, internal id recovered, filed by TYPE (limit world or stop world) |
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
├── test_boot_wiring.py               ← what AutotraderMain writes, and when it refuses to
├── test_carried_readings.py          ← the readings follow the kind of run: the pure rule (#626)
├── test_carry_over_configuration.py  ← an open position under a changed configuration (#626)
└── test_bot_instance_lock.py         ← one bot runs once: the lock beside the carry-over (#598)
```

---

## What Each File Validates

| File | Focus |
|------|-------|
| `test_cold_start_adoption.py` | **Ownership:** an order from a session the carry-over knows is adopted and its internal id RECOVERED from the key's counter (not invented); a foreign client's key of the same shape, an order with no key, and a first-ever boot with no carry-over all leave the venue untouched. **Counter:** after adopting `pos_btcusd_1` the next minted id is `pos_btcusd_2`, and the carry-over alone lifts the counter when the predecessor's orders are already gone. **The right WORLD (#500):** an adopted STOP lands in `_active_stop_orders` at its TRIGGER and a STOP_LIMIT carries its limit price beside it — every adopted order used to be filed as a limit regardless of type, which made it adoptable but not manageable: the stop-world cancel and modify paths could not find it while `modify_limit_order` could, and would have amended its trigger as a limit price. **Refusals:** `operator_confirm` without a terminal refuses and stays flat (error pot), `auto` adopts and says nobody confirmed, an unreachable venue stops the boot rather than starting blind. **Dry run:** reports what it would adopt and adopts nothing |
| `test_position_book.py` | **A refused boot claims nothing:** the situation is filed but marked `applied=False`, a boot that proceeds marks it True, and the report carries the flag plus its derived skip reasons. **A damaged note degrades:** a corrupt enum value in the carry-over is reported and the session starts without the book instead of dying on its own note. **The note loses nothing:** every scalar survives the round trip, a PARTIALLY_CLOSED position does not return as untouched, the excursion extrema (#389) survive a constructor that would otherwise overwrite them, the incurred fee still counts toward the position and keeps its type, the entry executions are not dropped. **The watcher:** an unchanged book is not written again, a partial close / a moved stop / a new position each count as a change, and "nothing written yet" counts as one. **Restore:** a remembered position is back before the first tick, a margin session leaves the note alone and says so, a dry run restores nothing. **Cross-check:** more coins than the book claims is not a divergence, fewer is reported — and the book is NOT adjusted to fit |
| `test_cold_start_hook.py` | **Loosening only:** an accounted-for situation starts where the framework would have refused (and says so), a declining answer behaves exactly like no hook, and a declining answer cannot stop an automatic adoption. **Always told:** the hook is asked in `auto` mode too, and when only a stranger's order is resting, but not on an empty venue and not in a dry run; an answer of the wrong type is reported and read as "no". **The situation is complete:** the recovered order id, every skip reason separately, `is_clean()` in both directions, the resolved policy and whether anybody declared themselves present. **The contract:** a logic declaring a resting type must answer, a MARKET-only logic is not asked, a wrong arity is caught before the venue is queried |
| `test_boot_wiring.py` | **The tick-loop seam:** a structural change writes once and then stays quiet, a FAILED write is retried on the next pass (the watcher advances only on success), a moved stop waits for the tick cadence, and the cadence needs no clock — the first passes happen before one is injected. Nothing is written without permission (a refused or dry boot appends no key); the key, counter and protected key set all reach the store; a margin session passes `None` for the book (leaving a stored one alone) while a spot session with nothing open passes `[]` (a statement that overwrites); a failing store is logged into the session channel and swallowed. **The reported drawdown (#497):** the curve is written beside the book on every one of those points — it has no cadence of its own, because a write plus its index rebuild has no business in a tick loop — and `persist_baseline=false` withholds it from the SUCCESSOR, at the read, since one switch governs both carried risk records — the writes carry it either way, as they carry the denominator. **Both boot reads honour a declared continuation (#358):** the early read that hands over the baseline and the cold-start step that reads the keys and the book accept the same predecessor document — one honoured without the other would adopt the book and miss the baseline. **The readings follow the kind of run (#626):** real money after a rehearsal starts fresh and its first write REPLACES the rehearsal's readings under its own stamp, in both account models; real money continues its own readings across a rehearsal, which writes none over them; a rehearsal continues a rehearsal; an unstamped carry-over is not continued; a new deployment starts fresh and is only said; the closing write follows the same rule, and a replacement still pending at the close carries the baseline so it is not erased. **The configuration rides with the venue claims:** a session placing real orders records what its claims were made under, a dry run leaves it alone |
| `test_cold_start_state_store.py` | Round-trip of the payload fields; keys ACCUMULATE across sessions (an order can rest across several restarts) and a repeated key moves rather than duplicates; the counter only ever rises; the key list is capped; provenance (`written_by_run_id`) is recorded and is NOT the key — the file is named after the BOT. Damage degrades rather than stops: unreadable JSON and another bot's document are reported and treated as absent. **A declared continuation (#358):** the later stage reads the earlier one's document, the earlier reads and KEEPS what the later left (its first write would otherwise replace the later stage's book), a third profile is still another bot, and the envelope records the writer's declaration. **Another profile's claims are never replaced:** a document written under another name that holds an open position — or only session keys — is not overwritten, and one that claims nothing (what a dry session writes) may be started over. **Risk baseline (#356):** the record reaches the next PROCESS field for field, keeps its original stamp, stays re-derivable as a spot record after JSON (`value == quote + base x mark_price`), survives a save that does not supply it (the store's `None` convention — the book is written far more often than the baseline moves, so a `None` that overwrote would erase it on the first position that opened), and is absent rather than zero-valued on a first boot. **Reported drawdown (#497):** the same five properties for the REPORT's curve — peak, deepest decline and the percentage reach the next process, the percentage is CARRIED rather than recomputed from the two floats (the quotient is the smaller number on every run that recovered), a save that does not supply it leaves it alone, a first boot has none, and the baseline and the curve stay TWO records because they answer different questions. **Deployment (#497):** the curve's START is not the handover stamp — `taken_at_utc` is re-written on every write and walks toward the present, `curve_started_utc` is minted once — and the deployment identity survives a write that does not supply it, which is what lets N sessions be joined into one history. Everything else about the baseline is provable in memory; if the WRITE drops the field, the successor re-anchors at the drawn-down value and every unit test stays green. **Index:** describes every bot from one file, skips a damaged document instead of failing wholesale, goes stale when a bot is REMOVED (the row count catches what mtimes cannot), and is named `<store_id>_index.parquet`. **The readings' kind (#626):** the stamp survives the disk, a write without readings leaves the stamp and the readings alone, a replacing write takes absent readings as absent, and a document from before the stamp has none |
| `test_carried_readings.py` | **The readings follow the kind of run (#626):** a session continues the carried drawdown curve and risk baseline only when a session whose orders went the same way wrote them — rehearsal after rehearsal and real money after real money continue, a change of kind in either direction does not; an unstamped carry-over (written before the kind was recorded) is not continued; `persist_baseline=false` comes first, nothing carried is nothing to continue, and a new deployment starts fresh whatever wrote them. **Who may write:** only a rehearsal over real money's readings writes none of its own, unless it begins a new deployment. **What is said:** the sentence names both kinds and the rule, and stays empty where nothing needs saying |
| `test_carry_over_configuration.py` | **The comparison (#626):** both rendered profiles projected onto the composite key under the definition the start runs with — the same configuration is one composite, a strategy parameter changes the strategy part, a safety limit the conditions part, the account currency the account part, and a neutral value nothing; a declared partner is exempt; claims without a recorded configuration, a record the run-config store does not hold, and a session that could not be rendered are not compared, and neither is a composite definition that cannot be loaded — it must not end a restart over an open position; nothing carried is nothing to compare; a field an update added exists on one side only, counts as a change and is marked so (case 3 of the rule). A layer that cannot be read leaves the origin unknown rather than the start undone. **Where a value comes from:** the layer that wins the merge is named — the profile, the workspace override or the shipped base layer of `app_config.json`, the broker's entry in `market_config.json` likewise, a code default, or a value the start resolves. **The refusal:** names the deployment and the run it continues, every changed value with before, after, part and origin, what is still open, and the three ways on — and, for a value only one side holds, that no file can restore it. **The decision at the boot, in both account models:** a changed strategy over a spot position and a changed condition over a margin resting order are refused with nothing applied; `--accept-composite-change <key>` with the key the refusal printed lets the start through with a notice and a `composite_change_accepted` finding that names the cost (two series), a key naming another composite is refused with a line saying so, and a flag left behind after an accepted change never accepts the next one; a change with nothing open begins a new stretch; the flag without a change is ignored with a note; an open position that could not be compared starts with a `carried_configuration_not_compared` finding; a declared partner continues across its changes; a dry run compares nothing. **The next start needs nothing:** the store keeps what the claims were made under, and a successor compares against the accepting start |
| `test_bot_instance_lock.py` | **One session per bot (#598):** a second start of the same bot is refused while the first holds the lock beside its carry-over, and the refusal names the bot, the symbol, the holder's process, its run once minted, and the lock file; another bot on the same machine starts; the lock is free once released and readable by its owner alone. **Another process:** a holder in a second process refuses the start while it lives and frees the lock the moment it is killed with `kill -9` — there is never a stale lock to clean up. **Two code trees:** two working directories on one data home share one lock, which is what refuses a deploy's new tree while the old session still runs; without a home each tree has a lock of its own, the limit the data home exists to close |

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

## Running the Tests

```bash
python -m pytest tests/autotrader/cold_start/ -v
```

Launch entry: `🧩 Pytest: Cold Start (#355)`.
