# Data Storage Layout — The Store Catalog

**The map of every place this application persists bytes.** Thirteen stores, classified by kind and
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

Seven stores have seven access patterns. A carry-over file swapped atomically every 60 s and a
300 MB tick archive share nothing but the word "file"; forcing one shape on both makes one of them
worse. What they *can* share is how they describe themselves.

---

## The thirteen stores

| # | Store | Kind | Key | Index | Retrieval |
|---|---|---|---|---|---|
| 1 | `runs/` | RECORD | `run_id` | `runs_index.parquet`, from `header.json` | A · document |
| 2 | `runs/ledger/` | RECORD | `run_id` (a column) | `run_ledger_index.parquet` | B · set |
| 3 | `tests/*/reports/` | RECORD | family + version + date | `certificates_index.parquet` | A · document |
| 4 | `data/runtime/session_state/` | **CARRY-OVER** | `<profile>_<symbol>` | none — opened by key | A · document |
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
file. `data/runtime/brokers/` and the carry-over store are opened by key, so neither has one.
