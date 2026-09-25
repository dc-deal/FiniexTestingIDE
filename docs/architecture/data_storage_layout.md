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
| 1b | `run_configs/` | RECORD | `config_id` — SHA256 over the normalised content | `run_configs_index.parquet` | A · document |
| 1c | `run_patches/` — this repository's; a strategy repository keeps its own inside itself | RECORD | patch hash — SHA256 over the patch bytes | none — opened by id | A · document |
| 2 | `runs/ledger/` | RECORD | `(run_id, unit, segment_no, currency)` — columns, never a path | `run_ledger_index.parquet` | B · set |
| 3 | `tests/*/reports/` | RECORD | family + version + date | `certificates_index.parquet` | A · document |
| 4 | `data/runtime/session_state/` | **CARRY-OVER** | `<profile>_<symbol>`, separator reserved | none — opened by key | A · document |
| 4b | `data/runtime/cold_start_state/` | **CARRY-OVER** | `<profile>_<symbol>`, separator reserved | `cold_start_state_index.parquet` | A · document |
| 5 | `data/processed/{broker}/ticks` | ARCHIVE | broker / symbol / file | `ticks_index.parquet` | C · bulk |
| 6 | `data/processed/{broker}/bars` | **DERIVED** ← ticks | broker / symbol / timeframe | `bars_index.parquet` | C · bulk |
| 7 | `data/processed/signals/` | ARCHIVE | type / symbol / day | `signals_index.parquet` | C · bulk |
| 8 | `data/processed/discovery_caches/` | DERIVED ← bars | family / broker_symbol | `discovery_caches_index.parquet` | A · document |
| 9 | `configs/generator_profiles/` | DERIVED ← discovery caches | mode / broker / symbol | none — its own fingerprints | A · document |
| 10 | `data/runtime/brokers/` | DERIVED ← a remote API | `broker_type` | none — one file per broker | A · document |
| 11 | `data/finished/` | ARCHIVE | file name | none — opened by name | A · document |
| 12 | `data/raw/` → `data/finished/` | **SPECIAL** | file name | none — conveyor | — |
| 13 | `logs/global.log` | **SPECIAL** | none | none — append stream | — |
| 14 | `user_configs/host_identity.json` | **SPECIAL** | none | none — one file, read at boot by its manager | — |

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

### The ledger's grain, and why it is four parts

A ledger row used to be one per `(run, currency)`. Since #537 a run books in **periods**, so the
grain is `(run_id, unit, segment_no, currency)` — and each part of that key earns its place:

- **`unit`** because a run's scenarios cover DIFFERENT windows (measured: 40 scenarios, 40
  distinct ones), so "day 1 of the run" is not a thing and only "day 1 of this unit" is.
- **`segment_no`** rather than a date, because a date cannot express two closes on one day and
  the industry books more than once a day in several places — perpetual funding every eight
  hours, an intraday margin call, an operator's period close.
- **`currency`** because P&L-denominated figures never mix currencies.

Both pipelines book: a live session writes one row per trading day, a simulation scenario one
per trading day of ITS window. A run whose scenarios are shorter than a day writes one row each,
which is the shape the ledger had before — the grain widened, it did not change meaning.

**No aggregate row is written beside the periods.** The run's total is derivable from them —
`COLUMN_REDUCTION` beside `LEDGER_COLUMNS` states how every column combines — and a derivable
copy kept next to its source is the pair that drifts (§19). Keeping both would also be wrong in
a way no reader could see: the deployment history SUMS rows and would count every month twice,
the sweep ranking SORTS them and would see one candidate four times.

The three levels this produces are ordinary double-entry bookkeeping, and
[accounting_periods.md](accounting_periods.md) names them: the trade records are the
**Grundbuch**, the ledger rows are the **Hauptbuch**, and everything over many periods — a
deployment's total, a Sharpe ratio — is the **Abschluss**.


## The carry-over separator is reserved (#538)

A bot's persistent state is filed under `<profile>_<symbol>`, and until 2026-09-22 both halves
were sanitised to `[a-z0-9_]` — so the underscore occurred inside the halves as well as between
them, and two DIFFERENT bots could resolve to one document:

```
'btc'      + 'USD_SPOT'  ->  btc_usd_spot
'btc_usd'  + 'SPOT'      ->  btc_usd_spot     one file, two unrelated bots
```

The bot that started second opened the other's document and read a position book it never wrote —
and at spot that book is the only record there is, because a holding is a balance the venue cannot
describe as a position. The halves now sanitise to `[a-z0-9-]`, the `_` occurs exactly once, and
the two are `btc_usd-spot` and `btc-usd_spot`.

**The key is deliberately NOT the config id.** A content id changes when the config changes, which
is its job — and a restarted bot would then point at a new, empty document the moment somebody
raised a stop level, with its inherited position book gone from its own view while the venue still
held it. The key answers *which bot am I*, never *what does it currently look like*.

**The structural answer is `bot_id`, which a profile DECLARES**, and it is optional so that no
existing profile changes key by the field existing. Composing an identity from what a profile is
CALLED means the identity moves when the name does — and a display name is exactly the thing an
operator improves: renaming `dot_live` to `dotusd_live_v2` would point a restarted bot at a new,
empty document while the venue still held its position. A declared id survives every rename of
everything else.

```json
{ "name": "dotusd_live_v2", "bot_id": "dot-usd-live", "symbol": "DOTUSD" }
                                  ↑ the key stays dot-usd-live_dotusd through any rename
```

**What remains, for a profile that declares none**: two SPELLINGS of one name (`dot live` and
`dot-live`) still meet, as do two profiles using the same name for the same symbol. Those are one
bot written two ways rather than two bots merging, and the startup validator is the answer — it
compares the DECLARED identity where there is one.

The declared id also reaches the LEDGER (`bot_id`, IDENTITY reduction), so a report can say
which BOT a row belongs to rather than only what the profile was called at the time. And the
carry-over ENVELOPE records it, because a document that cannot say what it is filed under is a
document nothing can safely rename — the migration below is the first caller that would have got
that wrong.

Changing the rule ORPHANS every document on disk, so it shipped with the migration that renamed
them (`python/experiments/migrate_carry_over_keys/`). Any further change needs the same.

## Run configs are a store, and several rows per file are the point (#538)

A scenario set and an AutoTrader profile used to be files at a path, and a path is not an
identity. Three things followed. Nothing could say two runs used the SAME configuration —
measured on this tree: 53 per-run config snapshots holding **23 distinct contents**, one of them
eight times. A config edited yesterday left no trace that it changed. And the backtest half of a
parity measurement could not name its own strategy identity at all, where the live half has
carried `param_hash` and `profile_hash` since #497.

**The store owns its own bytes.** Registering FREEZES the normalised content under its id rather
than pointing at where the file was found:

```
run_configs/
  run_configs_index.parquet
  scenario_sets/<config_id>.json
  autotrader_profiles/<config_id>.json
```

That is not tidiness. A source may live in `user_algos/`, a separate repository, and an index
whose entries lived outside its own root could not die with its store. The per-run snapshot in each run directory stays: it is the evidence, and an id that
cannot be resolved back to bytes is not one.

**Several rows per source file are NORMAL here, unlike every other store.** Each row is one
version, and the accumulation IS the history — which is why validity is not a row count against a
file count. What `staleness_reason` checks is that every indexed version still has a frozen copy.

**Three hashes, because a change means three different things:**

| a change to | moves | example |
|---|---|---|
| the bytes | `config_id` | anything at all |
| what the algo DECIDES | `param_hash` | a worker's period 14 → 21 |
| WHICH DATA runs | `scope_hash` | a scenario added, a window moved |

A renamed scenario and an added comment move the first and neither of the others — so the history
can say *naming only, no decision, no scope*, which is the distinction a reader actually needs and
no single hash can make. `param_hash` is the same value the run ledger carries, so a config row
and a run row compare directly.

**The store is an accelerator for resolution, never the only way to find anything.** A name it
knows is answered from the index plus one `stat`; a name it does not know falls through to the
search that was always there, and a successful search registers what it found. Measured: 111 ms
of stats for 67 configs against 613 ms for a single recursive glob, and the scenario listing fell
from 19.5 s to 6.7 s — of which 5.7 s is Python startup, so the work itself went from 13.8 s to
0.95 s.

## Run patches keep the code a dirty tree ran (#551)

A run header names each repository's commit. On a dirty tree that commit is not what ran: the
working tree differs from it, so the header points at code that never executed — and the parity
backtest afterwards would be compared against code that no longer exists. This store keeps the
patch that separates the tree from its commit, and the header's `code_identity` names it:

```
run_patches/                        this repository's patches
  <patch_hash>.patch    one diff against the commit: tracked changes, deletions and every
                        untracked file as a creation — credential homes left out

<strategy repository>/              any OTHER repository a strategy came from, e.g. user_algos/
  .finiex_run_patches/
    .gitignore          `*` — hides the directory, itself included
    <patch_hash>.patch  the same entry, kept beside the code it describes
```

**A strategy repository keeps its patches inside itself.** A private strategy has a repository of
its own so that its code never enters this project's tree — and a patch is a full copy of the
uncommitted part of it, so filing it in `run_patches/` would undo exactly that separation. The
directory writes its own `.gitignore` (the `.pytest_cache` pattern): nobody edits the repository's
ignore rules, `git add -A` never picks a patch up, and keeping a patch never turns the tree dirty —
which would refuse the next real-money start from a freshly committed repository. An existing
`.gitignore` there is left as it is. These homes are not a catalog entry of their own: a strategy
can be loaded from any path, so the set of them is no configuration — the run headers that name
them are the list.

**Two digests, and only one of them is a key here.** The header's `diff_hash` is taken over the
CONTENT of the changed paths — path, executable bit and bytes — so the same delta has the same
identity on any machine, under any git configuration and any git version. The patch is one
RENDERING of that delta, and the store files it under the SHA256 of exactly its bytes. The header
names the entry in `patch_ref`, relative to the repository's `root` —
`run_patches/<patch_hash>.patch` under the default `app_config.json::paths.run_patches` for this
repository, `.finiex_run_patches/<patch_hash>.patch` for a strategy's; the file name IS the key, so
a moved root still resolves through the store by that name. `diff_hash` is never a file name.

Restoring the code that ran is the recorded commit, a check, and one `git apply`. The check is
the one the store's own read makes: the file must hash to its name — a damaged entry would
otherwise be applied without complaint. A worktree leaves the working tree you are in untouched,
and the patch path is absolute because `git -C` resolves a relative one against the repository,
not against the project root:

```bash
patch="$PWD/run_patches/<patch_hash>.patch"                   # run from the project root
# a strategy repository's: patch="<repository>/.finiex_run_patches/<patch_hash>.patch"
git -C <repository> worktree add /tmp/restored <commit>
echo "<patch_hash>  $patch" | sha256sum --check && git -C /tmp/restored apply "$patch"
```

`sha256sum` answers `<path>: OK` and only then is the patch applied; a mismatch prints `FAILED`
and exits non-zero.

`RunPatchStore.get(<patch_hash>)` is the same verified read in code: it refuses a damaged entry
rather than serving it.

- **Content-addressed, so it is immutable by construction.** Equal patches are one file however
  many runs ran them, and a second put of the same patch writes nothing. A put refuses a patch
  that does not hash to its key (`RunPatchHashMismatchError`); a read refuses an entry that no
  longer does (`RunPatchCorruptError`) rather than serving it, and rather than answering None,
  which would claim nothing was ever stored. A damaged entry is rewritten by the next put of its
  patch — the name fixes the content, so the verified bytes are the only right answer.
- **No index and no header, and neither is an exemption.** A patch is opened by the name a run
  header's `patch_ref` already carries — a lookup by identity, never a search, so the index
  obligation does not arise. The entry is the raw patch so that `git apply` reads it as it is;
  everything a header would say (which run, which repository, which commit) is in the run header
  that names it.
- **It never holds a credential.** A changed path under any directory named `credentials` is
  left out of the patch, and the header lists it in `patch_excluded`; the `diff_hash` records only
  THAT it changed. A real key pasted into a tracked placeholder would otherwise be copied here on
  every run start — before the credential guard ever sees it — and survive the operator's revert,
  because nothing in this store deletes anything.
- **Restorable is stated, not assumed.** A repository state says `restorable: true` only for a
  clean commit, or for a dirty tree whose complete patch was kept. A tree dirty through a nested
  repository is recorded but never claimed restorable — the patch cannot carry that repository's
  content. A tree whose only exclusions are credential homes still counts as restorable: what the
  patch leaves out is configuration a restore must not reproduce — a secret belongs in
  `user_configs/`, never in a record.
- **Gitignored, for two reasons.** It holds uncommitted code from `user_algos/`, which is
  private. And an untracked file is part of the diff: a store git could see would file itself
  into its own next patch.
- **What it weighs.** Measured 2026-09-24 on this tree: the `/app` patch 449,474 bytes over 77
  uncommitted changes deep into a multi-file build, the `user_algos/` patch 3,151 bytes — it grows
  with the uncommitted work, not with the tree. Producing a patch is the expensive half, and it is
  ONE pathspec-limited `git diff` over a temporary copy of the index: ~1.1 s for `/app` on top of
  the ~2.2 s `git status` the capture pays anyway, ~0.2 s for `user_algos/` — paid only for a
  dirty repository. Storing it is one file, a few milliseconds to write and to read back.
- **Its lifetime is an open question, owned by #535.** How long a patch has to outlive the runs
  that name it is not decided here; until it is, nothing in this store deletes anything.

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

### Why the SPECIAL stores stay special

- **`data/raw/` → `data/finished/`** is a **conveyor, not a store**: a file lies there in order to
  disappear. The importer reads it and MOVES it; it never rewrites the content. Giving it a header
  and an index would make it something it is not, and would blur that contract.
- **`logs/global.log`** is an append stream without identity. It gets bounding and rotation, never
  an index.
- **`user_configs/host_identity.json`** is the installation's minted identity (#551): one file,
  written once on the first start outside the tests and never rewritten. A broken file refuses the
  start instead of being minted again, because a silent re-mint is an identity change nobody
  notices. It is none of the five kinds — not a record of an event, not carried over by a bot, not
  an input — and deleting it is not harmless: the next start mints a different identity, and every
  run header after it names another host. It lives in the workspace and is never tracked, because
  a tracked id would give every clone the same one.

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

**Until then the tick index carries its own schema stamp**, and that is the stopgap rather than the
design. `needs_rebuild()` compares mtimes alone, so an index written before a column existed stays
"valid" and keeps serving rows without it until an import happens to touch a parquet — the trap that
makes adding a column unsafe to deploy. `index_version` was already being written by all three
managers and read by none; the tick index now reads it back and rebuilds when it differs. Bump
`INDEX_SCHEMA_VERSION` whenever a column is added or its meaning changes. The other two still write
a version nobody reads, which is the gap #175 closes properly.

**Its provenance columns** — `origin_instance_id`, `origin_class`, `origin_evidence` — are resolved
ONCE at import and read back from the stamp, never re-resolved from the registry. Full contract:
[`data_provenance.md`](data_provenance.md).

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

**The shutdown write happens BEFORE the order cleanup, and the note describes the VENUE.**
This was a workaround before #492: the cleanup closed open positions in our book only — it
filled a synthetic close locally and nothing reached the venue — so a note written afterwards
would have said "this bot holds nothing" while the asset was still at the broker, erasing
exactly what the successor needs. The cleanup no longer touches positions at all, so the
ordering is now simply the honest one rather than a defence against a defect. A session that
sells at the venue would have to write the note AFTER the sale; that path is not built, and
`session_end.positions = 'close'` refuses at startup until #487 makes it resolvable.

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

⚠️ **A schema-version bump on a carry-over does not lose state quietly — it makes the session
trade beside its own forgotten orders.** The chain: envelope discarded → empty payload → the
predecessor's session keys are gone → its resting orders read as `unknown_session` → they are
NOT adopted. And there is no start ban: the boot reports this as an ERROR in the session pot and
the session **starts anyway**, deliberately, because refusing forever would leave the operator no
way out and would abandon whatever this bot already holds at the venue. So the consequence is not
a bot that will not run; it is a bot that runs while its own resting orders are invisible to it.
That has to be known before the deploy rather than discovered at 03:00.
