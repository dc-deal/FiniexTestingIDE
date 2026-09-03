# Data Storage Layout — The Store Catalog

**The map of every place this application persists bytes.** Every store, classified by kind and
by how they are read. A store that is not in this table has no read path — adding one means
adding a row here *and* a registration in
[`store_registrations.py`](../../python/framework/store/store_registrations.py).

Built by **#486**. The logical layer above it — model · codec · store · present — is **#413**.

---

## The three levels

Only the top two are unified. That is the finding, not a compromise.

```
CATALOG   StoreCatalog          which stores exist, where, of what kind    ← ONE, a code registry
INDEX     AbstractStoreIndex    what lies in one store                     ← one per store, shared base
STORE     the bytes             per domain, deliberately different         ← untouched
```

Different stores have different access patterns. A carry-over file swapped atomically every 60 s and a
300 MB tick archive share nothing but the word "file"; forcing one shape on both makes one of them
worse. What they *can* share is how they describe themselves.

---

## The stores

| # | Store | Kind | Key | Index | Retrieval |
|---|---|---|---|---|---|
| 1 | `runs/` | RECORD | `run_id` | `runs_index.parquet`, from `header.json` | A · document |
| 2 | `runs/ledger/` | RECORD | `run_id` (a column) | `run_ledger_index.parquet` | B · set |
| 3 | `tests/*/reports/` | RECORD | family + version + date | `certificates_index.parquet` | A · document |
| 4 | `data/runtime/session_state/` | **CARRY-OVER** | `<profile>_<symbol>` | none — opened by key | A · document |
| 4b | `data/runtime/cold_start_state/` | **CARRY-OVER** | `<profile>_<symbol>` | `cold_start_state_index.parquet` | A · document |
| 5 | `data/processed/{broker}/ticks` | ARCHIVE | broker / symbol / file | `ticks_index.parquet` | C · bulk |
| 6 | `data/processed/{broker}/bars` | **DERIVED** ← ticks | broker / symbol / timeframe | `bars_index.parquet` | C · bulk |
| 7 | `data/processed/signals/` | ARCHIVE | type / symbol / day | `signals_index.parquet` | C · bulk |
| 8 | `data/processed/discovery_caches/` | DERIVED ← bars | family / broker_symbol | `discovery_caches_index.parquet` | A · document |
| 9 | `configs/generator_profiles/` | DERIVED ← discovery caches | mode / broker / symbol | none — its own fingerprints | A · document |
| 10 | `data/runtime/brokers/` | DERIVED ← a remote API | `broker_type` | none — one file per broker | A · document |
| 11 | `data/finished/` | ARCHIVE | file name | none — opened by name | A · document |
| 12 | `data/raw/` → `data/finished/` | **SPECIAL** | file name | none — conveyor | — |
| 13 | `logs/global.log` | **SPECIAL** | none | none — append stream | — |

**Ticks and bars are two stores, and bars are DERIVED.** Ticks are IMPORTED from the collector's
JSON; bars are GENERATED from those ticks, today only by a full re-render (`clean_mode` →
`_clean_bars`). Different origin, different producer, different index. Registering bars as
ARCHIVE was the first defect this model found in itself — and §44 had already written down the
sentence the registration then contradicted.

See it live, with entry counts and the stated reasons:

```bash
python python/cli/store_cli.py catalog          # add --sizes to also walk for bytes
python python/cli/store_cli.py rebuild --all    # every index this model owns
```

---

## Five kinds — a store is exactly one

| Kind | Key | Lifetime | Obligation |
|---|---|---|---|
| **RECORD** | the event it records | immutable | header · payload identity · index |
| **CARRY-OVER** | the identity of the OPERATION (the bot) | overwriting, atomic | header · provenance · index once N > 1 |
| **ARCHIVE** | source / symbol / time | append-only | index |
| **DERIVED** | mirrors its source | **deletable** | none — *if deleting it loses something, it is misfiled* |
| **SPECIAL** | — | — | catalogued with a stated reason, nothing else |

### RECORD and CARRY-OVER are never merged

```
runs/<type>/<name>/<run_id>/      the RECORD    — what this run did.        Key: run_id
data/runtime/session_state/…json  the CARRY-OVER — what reaches the NEXT run. Key: the bot
```

A restart mints a new `run_id` and a new directory. A carry-over written under a run id could only
be found by the next session **guessing** which directory was its predecessor — exactly the
directory archaeology run identity abolished. Different key, different lifetime.

The envelope every carry-over writes is
[`CarryOverEnvelope`](../../python/framework/types/persistence_types.py): schema version, store id,
save time, the bot's identity, and `written_by_run_id`. That last field is **provenance, never
identity** — it records which session wrote the file so a restored state can be traced back, and it
is deliberately not part of the key.

### Why the two SPECIAL stores stay special

- **`data/raw/` → `data/finished/`** is a **conveyor, not a store**: a file lies there in order to
  disappear. The importer reads it and MOVES it; it never rewrites the content. Giving it a header
  and an index would make it something it is not, and would blur that contract.
- **`logs/global.log`** is an append stream without identity. It gets bounding and rotation, never
  an index.

---

## Four retrieval forms — the reader belongs to the FORM

| Form | Signature | Where |
|---|---|---|
| **A · document by identity** | `id → model \| None` | `ReportStore.get`, `AlgoStateStore.load` |
| **B · set by predicate** | `filter → rows` | `RunResultsLedger.read`, `RunIndex.list_runs` |
| **C · bulk by range** | `path/window → frame` | `read_tick_parquet`, `load_signal_series_from_parquet` |
| **D · stream** | callback | the live signal transport — not a store retrieval |

**Form C stays outside the abstraction on purpose.** A tick frame is never routed through a generic
layer: the catalog hands out the path and steps aside. Three reasons, each checkable —

1. A and B move kilobytes; C moves hundreds of megabytes. Routing a frame through a generic layer
   means either copying it or passing it through: expensive, or pointless.
2. Form-C reads happen once per scenario at mount time, against tens of seconds of parquet I/O, and
   the index lookup they need already happened before this model existed. Only the origin of the
   address changed.
3. A layer for form C already exists and is not this one — the resident mount registry (#418) and
   the file cache (#21). This catalog will hold them as stores whose backend is RAM, rather than
   duplicating them.

That is what keeps the model **off the hot path**, and it is why the unification costs no throughput.

### One typed getter for form A

Eighteen hand-written readers used to differ in three tokens each. The spec carries those tokens,
and the static type survives the collapse:

```python
BROKER_ARTIFACT: ArtifactSpec[BrokerReport] = ArtifactSpec('broker.json', BrokerReport)

report = ReportStore().get(run_id, BROKER_ARTIFACT)   # statically Optional[BrokerReport]
```

Specs live in [`artifact_specs.py`](../../python/framework/reporting/io/artifact_specs.py); the two
artifacts with a CSV surface and the two with a row filter keep that real logic in
`report_csv_io.py` and `report_filters.py`.

---

## The index contract

An index is **derived**: it may be deleted or go stale without anything being lost, because
`rebuild()` reconstructs it from the store. The store is the truth; the index is the read path.

- **ONE file, never a fragment per entry.** Measured here: 404 small parquet fragments cost 3.29 s
  to open, the same rows as a single file 0.008 s — 420×, and 99.6 % of it is the file OPEN rather
  than the work.
- **`LOGIC_VERSION` is stamped into the parquet's Arrow metadata.** It closes a blind spot every
  pre-existing index family shares: validity keyed on the SOURCE mtime never sees a change in the
  CODE. Change a scan function and the index is still newer than its sources, so a staleness check
  says "current" and the old content keeps being served — while the tests go green, because they
  exercise the code and not the file. Bump it whenever the MEANING of a column changes.
- **Writes are atomic** (temp file + replace): a truncated parquet is unreadable, and an unreadable
  index is indistinguishable from a missing one only until something reads it.

**The three data-index managers are registered but not migrated.** `TickIndexManager`,
`BarsIndexManager` and `SignalIndexManager` are 1368 battle-tested lines carrying legacy-JSON
migration paths, and they are form C — the side this model deliberately does not mediate. They are
also less alike than their method names suggest: `data_format_version` lives in the tick index alone,
legitimately, because bars inherit it from the ticks they were rendered from and signals carry the
producer's own `schema_version`. What they inherit later is the `LOGIC_VERSION` field, under #175.

---

## Two rules the catalog enforces with tests

**`derived_from` is mandatory on every DERIVED store.** It is the EDGE, not the rule: WHICH store
is the source is catalog data; WHICH FILE a single entry watches stays with the family that
resolves it — the catalog cannot enumerate (broker, symbol) pairs without importing the very index
it would then have to keep fresh. `None` is allowed only with a stated reason: the broker runtime
cache derives from a remote API, which is not a store and cannot be named as one.

**Every index is named `<store_id>_index.parquet` — no dot.** Seven indexes were previously named
five ways: `index.parquet` twice in different folders, and three dot-prefixed, which is Unix
HIDING. A store index is not a hidden file — it is the read path, and it should be as visible as
the store it describes. `store_index_filename()` produces the name and a test asserts it for all
seven, the three legacy managers included: renaming their file is a constant, not the migration
#175 owns.

The same rule reached the discovery caches' **directory**: `data/processed/discovery_caches/`,
formerly dot-prefixed. Its name is now declared once as `DISCOVERY_CACHE_DIRNAME` — the three
cache families each carried their own literal and the catalog a fourth, which is how a rename
becomes a hunt.

> **The dot was never free.** Removing it broke 36 tests immediately, because a test helper
> excluded the index from its tick-file glob by testing for a leading dot rather than by naming
> it. An implicit convention fails silently the first time the thing it rests on changes. Every
> such exclusion now NAMES what it excludes. Production was unaffected only because its globs
> are depth-bound (`*/ticks/**/*.parquet`) while the indexes sit one level above — verified,
> not assumed.

## Why "stale" always says why

`staleness_reason()` returns one operator-readable clause instead of a boolean, and `is_valid()` is
defined as "no reason" so the two can never disagree. A bare flag sends the operator to rebuild the
wrong thing: rebuilding the discovery CACHES and rebuilding their INDEX are different commands, and
the first invalidates the second. That is now wired — a `cache rebuild-all` refreshes the index it
just invalidated — but the reason line is what makes the remaining cases actionable.

## Adding a store

1. Add a `StoreId` value in [`store_types.py`](../../python/framework/types/store_types.py).
2. Register it in [`store_registrations.py`](../../python/framework/store/store_registrations.py)
   with its kind, root, key, form and — unless it is SPECIAL — its index or a stated reason for
   having none.
3. Add its row to the table above.
4. `tests/framework/store/` asserts completeness, so a forgotten registration fails the suite
   rather than going unnoticed.

An index is due as soon as something **searches** the store's contents rather than opening a known
file. `data/runtime/brokers/` and the algo carry-over (4) are opened by key, so neither has one.

### Two carry-overs, and why they are two stores

Store 4 holds what the ALGO remembers (#354); store 4b holds what the FRAMEWORK remembers
(#355) — the session keys this bot has sent orders under, and how far its position counter had
run. They are separate for a structural reason rather than a tidy one: store 4 is only ever
constructed when the decision logic opts in (`uses_state_persistence()`), while 4b has to be
written for EVERY live bot — a bot whose algo remembers nothing still sends orders under a key,
and its successor still has to recognise them.

4b HAS an index, and by the rule above rather than against it: something does search across
bots — *which bot carries what, since when, from which run*, the question an operator asks
after a 03:00 restart, and the one a diagnostics layer asks across a fleet.

What it deliberately does NOT hold is history. A carry-over overwrites by definition, so what a
given boot adopted belongs to that run's RECORD (store 1), which is immutable and already
indexed. A reader wanting "what was adopted across thirty restarts" joins the two indexes;
bending a carry-over into a log would make it a different kind of store.

#### They share exactly one thing, and the rest can diverge

Both turn on the same restart, and both are keyed `<profile>_<symbol>` with the same
sanitisation — that identity is the ONLY connection. There is no shared write, no
transaction, no ordering guarantee. Anyone reasoning about restarts needs the differences:

| | 4 · `session_state` (#354) | 4b · `cold_start_state` (#355) |
|---|---|---|
| Writer | `AlgoStateStore` | `ColdStartStateStore` |
| **When** | every N ticks OR M seconds, plus shutdown | **boot + shutdown + on a STRUCTURAL book change, plus a tick cadence for drift** |
| **Gate** | the algo's own opt-in `uses_state_persistence()` | none — every live bot |
| Payload | the algo's opaque snapshot | session keys + position-counter high-water mark + the open position book |
| **Staleness** | `max_age_trading_days` + `on_stale` → **discards** | none |
| Index | none (opened by key) | yes (searched across bots) |

Three consequences worth knowing before debugging a restart:

1. **The gate splits them.** An algo that does not opt in has NO `session_state` and still
   has `cold_start_state`. That is today's normal case, which is why one of the two roots is
   usually absent on disk.
2. **The staleness asymmetry is deliberate.** After the configured age the algo's memory is
   discarded while the session keys are kept — an "already entered today" flag expires with
   time, a resting order does not. The two therefore disagree about what "too old" means, on
   purpose.
3. **They are not an atomic pair.** A crash between the two writes leaves half a state. Not
   critical — each is readable alone and each degrades to an empty payload with a warning —
   but there is no "both or neither".

#### Why the position book is in here at all

A spot position is not an object the venue holds. Kraken knows balances and orders; its
`OpenPositions` is margin-only and empty on spot. Everything that turns `0.014 BTC` into a
*position* — direction, entry price, fee, "still open" — is OUR record, derived from our own
fills. **At spot you have to remember your positions; at margin they sit in the market.**

So this is not adoption from broker truth (there is none to adopt) but memory plus a
cross-check:

```
write     STRUCTURAL change → at once      which positions exist, how much is left, status
          DRIFT             → tick cadence exit levels, excursion extrema
restore   at BOOT, no tick needed: every field was known when the position was opened
check     the restored book against the venue's balance — and only REPORT
```

The split is a measurement, not a preference. One carry-over write costs **11 ms** on this
project's tree, and the store's index rebuild another **26-40 ms** (§42 — `/tmp` says 2 ms;
the bridged mount is the difference). A structural change happens a handful of times a day
and cannot be recovered, so it is written immediately. Drift moves on nearly every tick of a
trend — a trailing stop follows every new high — and is either re-derived by the algo on its
next pass or loses at most one interval of a running maximum, so it waits for
`cold_start.book_drift_interval_ticks`. Counted in TICKS because drift is *caused* by ticks: a
quiet market needs no writes, and a tick counter needs no clock (the first passes happen
before the canonical clock is injected). The index rebuild is left to the writes that BOUND a
session; an index is derived and reports itself stale until the next boot.

**The shutdown write happens BEFORE the position cleanup, and the order is the point.**
`close_all_remaining_orders` closes open positions in OUR BOOK only — it fills a synthetic
close locally and nothing reaches the venue. A note written after that would say "this bot
holds nothing" while the asset is still at the broker, which erases exactly what the successor
needs. Whether a session END should flatten at the venue at all is a policy question and
belongs to #492; until it is answered, the note describes the VENUE.

The check is deliberately **one-sided**. The account is shared, so holding MORE than the book
claims is normal and says nothing (what a bot may *use* is declared capital, #489). Holding
LESS is not: the note then claims a position the account cannot cover, which is what happens
when something sold outside this bot. That is reported and the book is left as written —
shrinking it to fit would invent a number and hide the event.

The note is faithful rather than minimal, and the reason is silent failure. A sparse note does
not crash; it produces a closing trade record that looks complete and is not — excursion
extrema (#389) back at zero, the submission slippage audit (#340) blank, the entry executions
gone, a partially closed position returned as untouched. Fees are carried as `RestoredFee`
(settled cost, original type) and are NOT re-counted into the new run's cost tracking: the fee
belongs to the run that charged it, so the *trade's* net P&L carries it while this run's fee
total does not. The cold-start report block states that, because otherwise it reads as a
rounding error.

Margin is not restored — those positions come back from the venue, where they carry our tag
(#209) — and a dry run restores nothing, because it never queried the venue and a rehearsal
that closes remembered REAL positions with orders that never leave the process reports a book
it does not have.

#### When an order of our shape cannot be placed — the causes, most likely first

The boot step reports an order that carries a client key of OUR shape from a session the
carry-over has no record of as an ERROR (`unknown_session`). It is rare, and every remaining
path to it runs through a human action — which is why it is reported and left standing rather
than handled automatically. If you ever meet it, this narrows it down:

| Cause | How likely | How to tell |
|---|---|---|
| Someone deleted, moved or restored `data/runtime/` (a "clean start", a machine move, a backup) | the usual one | the carry-over file is missing or its `saved_at_utc` predates the order |
| Two instances of the same bot ran at once and their key writes crossed | rare — the write is read-modify-write, so it needs overlapping saves | two run ids in `runs_index.parquet` overlapping in time for one profile |
| The document was hand-edited or corrupted | rare | the boot log carries the "unreadable" or "payload rejected" warning |
| A schema version bump discarded it | only right after a deploy | the boot log names the version mismatch |
| Genuinely another client using the same key format | improbable | the key's session half matches no run id of ours |

Two paths are closed by construction and can be ruled out immediately: a **container rebuild**
cannot lose it (`./data` is a bind mount from the host), and **eviction** cannot drop a key
whose order is still resting (eviction is by relevance before recency).

⚠️ **A schema-version bump on a carry-over is not a state loss, it is a start refusal.** The
chain: envelope discarded → empty payload → the predecessor's session keys are gone → its
resting orders read as `unknown_session` → the start ban applies. That is defensible (trading
blind beside your own orders is worse), but it has to be known before the deploy rather than
discovered at 03:00.
