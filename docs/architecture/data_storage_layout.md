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
| 1d | `runs/fixture_productions.jsonl` | RECORD | `entry_id` + `produced_at` — one line per production | none — one file, read whole | B · set |
| 1e | `logs/imports/` | RECORD | import id — the UTC start plus a random half, e.g. `20261010_090011_a3f9c2d1` | none — opened by the id its file is named after | A · document |
| 2 | `runs/ledger/` | RECORD | `(run_id, unit, period_no, currency)` — columns, never a path | `run_ledger_index.parquet` | B · set |
| 3 | `tests/*/reports/` | RECORD | family + version + date | `certificates_index.parquet` | A · document |
| 4 | `data/runtime/session_state/` | **CARRY-OVER** | `<bot_id>_<symbol>`, separator reserved | none — opened by key | A · document |
| 4b | `data/runtime/cold_start_state/` | **CARRY-OVER** | `<bot_id>_<symbol>`, separator reserved | `cold_start_state_index.parquet` | A · document |
| 5 | `data/processed/{broker}/ticks` | ARCHIVE | broker / symbol / file | `ticks_index.parquet` | C · bulk |
| 6 | `data/processed/{broker}/bars` | **DERIVED** ← ticks | broker / symbol / timeframe | `bars_index.parquet` | C · bulk |
| 7 | `data/processed/signals/` | ARCHIVE | type / symbol / day | `signals_index.parquet` | C · bulk |
| 8 | `data/processed/discovery_caches/` | DERIVED ← bars, ticks | family / broker_symbol | `discovery_caches_index.parquet` | A · document |
| 9 | `configs/generator_profiles/` | DERIVED ← discovery caches | mode / broker / symbol | none — its own fingerprints | A · document |
| 10 | `data/runtime/brokers/` | DERIVED ← a remote API | `broker_type` | none — one file per broker | A · document |
| 10b | `data/runtime/config_directory/` | DERIVED ← the configuration files (not a store) | path of the configuration file | `config_directory_index.parquet` | B · set |
| 10c | `data/runtime/import_timings.json` | DERIVED ← the imports' own durations (not a store) | none | none — one file | A · document |
| 11 | `data/finished/` | ARCHIVE | zip or file name | none — opened by name | A · document |
| 11b | `data/external/` | ARCHIVE | vendor / dataset / fetch folder | none — a fetch is opened by its folder, its `manifest.json` keyed on part SHA-256 and fetch time | A · document |
| 12 | `data/raw/` → `data/finished/` | **SPECIAL** | file name | none — conveyor | — |
| 13 | `logs/global.log` | **SPECIAL** | none | none — append stream | — |
| 14 | `user_configs/host_identity.json` | **SPECIAL** | none | none — one file, read at boot by its manager | — |
| 15 | `~/.finiex/activities/` — one per machine, configured | **SPECIAL** | `activity_id` | none — read by its own reader | — |

**Row 1d is one file, not a folder.** It is the fixture catalog's production record — which runs
each production of a catalog entry made, and whether they carried every property — and it sits
beside the run index so it dies with the run tree. The current fixture of an entry is derived from
it, never stored ([Fixture Catalog](fixture_catalog.md#the-production-record)).

**Ticks and bars are two stores, and bars are DERIVED.** Ticks are IMPORTED from the collector's
JSON; bars are GENERATED from those ticks, by a render that writes each symbol's files anew from
all of its ticks and replaces a file only once the new one reads back in full. Different origin,
different producer, different index. Registering bars as
ARCHIVE was the first defect this model found in itself — and the project's own store rules had
already written down the sentence the registration then contradicted.

### The data home — where every root lives

A deploy on a server is a restart into another code tree (#598), and the stores above are what
the new tree has to find again. So they do not live in the code tree: every root resolves under
one data home, the directory the environment variable `FINIEX_DATA_HOME` names, together with
the workspace (`user_configs/`) and the strategy directories. Unset, the working directory is
the home, and a development checkout keeps every path exactly as configured.

Three roots stay where they are, each on purpose: the certificates (row 3) and the generator
profiles (row 9) are committed and belong to the code tree of the release that made them, and
the activity list (row 15) is the machine's, at an absolute path every tree shares. An absolute
path in the configuration stands for itself. A home that does not exist refuses the start
instead of being created: an empty one holds no carry-over, and a session started on it would
begin as the bot's first run. Which home a process uses is named at its start — in the session
channel, beside the carry-over it read, and in the API server's boot lines. A store added later
needs no list: its root resolves under the home by construction, and a test walks the catalog to
prove it.

### The external history store

A venue's published history — data no instance of ours recorded — is kept here exactly as it was
downloaded (#561): `data/external/<vendor>/<dataset>/<fetch date>/`, holding the parts as the
vendor publishes them, the vendor's checksum list, and this side's `manifest.json`. The manifest is
keyed on each part's SHA-256 and the time it was fetched, never on the vendor's release label: a
vendor may re-upload a release under the same label, so the label alone does not pin the bytes.
Conversion records (`conversions/`) beside the manifest say which collector-shaped files each run
wrote into the import inbox. The store is never rewritten, the importer never reads it — the
converter does — and nothing in it ever enters a data package or the repository.

A fetch's parts may be removed once its conversion has been imported and verified. The converted
files then live in the finished archive (`data/finished/`, zipped there like the collector's own
files), and each of them names the parts it came from and their checksums. The store can therefore
be empty while the history it held is complete — which is why the catalog counts the finished
archive by its zips as well as by its loose files.

See it live, with entry counts and the stated reasons:

```bash
python python/cli/store_cli.py catalog          # add --sizes to also walk for bytes
python python/cli/store_cli.py rebuild --all    # every index a rebuild restores fully
```

Underneath the stores the catalog shows two advisories about dated claims the installation holds:
a release gate whose newest certificate has expired, and a broker fee structure frozen more than
ninety days ago. Both are decided in `validators/store_health_checks.py` and come back as findings
with their own check ids, so any other surface can show them the same way.

---

### The ledger's grain, and why it is four parts

A ledger row used to be one per `(run, currency)`. Since #537 a run books in **periods**, so the
grain is `(run_id, unit, period_no, currency)` — and each part of that key earns its place:

- **`unit`** because a run's scenarios cover DIFFERENT windows (measured: 40 scenarios, 40
  distinct ones), so "day 1 of the run" is not a thing and only "day 1 of this unit" is.
- **`period_no`** rather than a date, because a date cannot express two closes on one day and
  the industry books more than once a day in several places — perpetual funding every eight
  hours, an intraday margin call, an operator's period close.
- **`currency`** because P&L-denominated figures never mix currencies.

Both pipelines book: an AutoTrader session writes one row per trading day, a simulation scenario one
per trading day of ITS window. A run whose scenarios are shorter than a day writes one row each,
which is the shape the ledger had before — the grain widened, it did not change meaning.

**No aggregate row is written beside the periods.** The run's total is derivable from them —
`COLUMN_REDUCTION` beside `LEDGER_COLUMNS` states how every column combines — and a derivable
copy kept next to its source is the pair that drifts. Keeping both would also be wrong in
a way no reader could see: the deployment history SUMS rows and would count every month twice,
the sweep ranking SORTS them and would see one candidate four times. One reduction has a limit
the others do not: a STOCK (`final_equity`, `unrealized_pnl`, `open_position_count`) is the latest
reading of ONE account, so folded over the rows of several units — a backtest's scenarios — it
answers null rather than whichever account closed last.

The three levels this produces are ordinary double-entry bookkeeping, and
[accounting_periods.md](accounting_periods.md) names them: the trade records are the
**journal**, the ledger rows are the **ledger**, and everything over many periods — a
deployment's total, a Sharpe ratio — is the **closing**.


## An import's log and its timings

A data import (`data_index_cli.py import`) writes two things beside the archives it fills.

**Its own log, `logs/imports/import_<import_id>.log` (row 1e).** Every line the import produces —
the importers, the index rebuilds, each bar-render worker as its symbol finishes, the summary —
in the format of `global.log`, which still receives the same lines. The import id is the UTC
start plus a random half — the form run ids have, so two imports started in one second cannot
share a file — and the summary's last line names the file. It is a record of what happened, keyed by the
import, and nothing searches across imports yet, so it has no index. It has no lifetime either:
that is #535.

**The measured step durations, `data/runtime/import_timings.json` (row 10c).** One rate per step —
seconds per MB of JSON, per index file, per million ticks rendered at a given worker count, per
bar file for the caches — smoothed over the imports that produced it. The next import multiplies
each rate by what its scan found and shows the result as its estimate. It is written once, at the
end of a complete import into the production archive; an interrupted or scoped import writes
nothing, and a step that failed contributes no rate. Deleting it costs the next estimate and
nothing else, which is what makes it derived.

## The carry-over separator is reserved (#538)

A bot's persistent state is filed under `<bot_id>_<symbol>`, and until 2026-09-22 both halves
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

**The structural answer is `bot_id`, which every profile DECLARES** — mandatory since 2026-09-24,
so a profile without one is refused at boot. Composing an identity from what a profile is
CALLED means the identity moves when the name does — and a display name is exactly the thing an
operator improves: renaming `dotusd_production` to `dotusd_production_v2` would point a restarted bot at a new,
empty document while the venue still held its position. A declared id survives every rename of
everything else.

```json
{ "profile_name": "dotusd_production_v2", "bot_id": "dotlive01", "symbol": "DOTUSD" }
                                              ↑ the key stays dotlive01_dotusd through any rename
```

**What remains is uniqueness**: two profiles declaring the same `bot_id` for the same symbol would
still meet in one document. The startup validator is the answer — it compares every declared
identity across both profile trees and refuses a duplicate.

**And the name still decides whose document it is.** The envelope records the profile that wrote
it, and both stores read a document as this bot's only when that name is this profile's — so a
renamed profile finds its own document written by "another" one. The boot refuses to start over
such a document while it holds an open position or a session key
(`carry_over_continuity_validator`, #358), and the store will not write over it either; before
that, the first write replaced it and the position it described was left at the venue unknown.
The one deliberate exception is a declared continuation — a later stage naming the earlier in
`carry_over_from`, read in both directions, and held at boot to the conditions in
[autotrader_configuration.md](../autotrader/autotrader_configuration.md#a-declared-continuation--carry_over_from).

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
parity measurement could not name its own strategy identity at all, where the AutoTrader half has
carried `param_hash` and `profile_hash` since #497.

**The store owns its own bytes.** Registering FREEZES the normalised content under its id rather
than pointing at where the file was found:

```
run_configs/
  run_configs_index.parquet
  scenario_sets/<config_id>.json
  autotrader_profiles/<config_id>.json
  autotrader_rendered/<config_id>.json     what a session RAN with (#547)
  broker_configs/<config_id>.json          the broker configuration it traded with (#547)
```

That is not tidiness. A source may live in `user_algos/`, a separate repository, and an index
whose entries lived outside its own root could not die with its store. The per-run copy of the
config that each run directory once held was retired with #546: the frozen copy here is the
evidence, and a run names it by `config_id`. The configuration is one of several things a run
records about itself;
[A run is a record, and an old one stays true](../introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true)
maps the others.

**A source is what a run was GIVEN; the two rendered kinds are what it RAN with (#547).** A
profile file is the input to a cascade — the `app_config.autotrader` defaults lie under it, and
every parameter it leaves unset gets its schema default only when the factory builds the
component — and a month in which `app_config.json` or a default changes could prove the
configuration moved and never say what it was. So an AutoTrader session freezes the rendered
profile at its start, and every run — a session or a backtest — freezes the broker configuration
it trades with:

| kind | what it holds | named by |
|---|---|---|
| `autotrader_rendered` | the merged profile blocks, the strategy with every schema default filled (through the factories' own `resolve_parameters`), and the broker's `market_config.json` entry with its local override | the run header's `rendered_config_id`, written before the session can fail |
| `broker_config` | the broker configuration the run trades with, as it stands when the run starts: the symbol specifications — from the runtime cache for a dynamic broker's backtest or live-adapter session, from the seed file for a mock session or a static broker — and the seed's fee structure, its rates replaced by the venue's fee tier where a live-adapter session applies it | the run's broker section (`broker_config_id`, beside `config_hash`) and a log line at startup |

The broker configuration needs the log line because the run's broker section is written at the
END: a run killed before its report still says, in its own log (`autotrader_session.log` for a
session, `scenario_global_log.log` for a backtest), where its configuration was frozen. A backtest
has no rendered profile: what its scenario set and the application's `backtesting` defaults add up
to for the execution and simulator blocks is covered only by a hash on its ledger row
(`profile_hash`), not by a document.

Both are frozen from CONTENT, so a run that starts on an unchanged configuration names the
same id and writes nothing new. Their source columns stay empty on purpose — those mean "the file
a caller asks for", and a rendered document must never come back from `resolve()` or `history()`
as the newest version of the profile it was rendered from. And both are RECORDS, never schemas: a
later version of an algo may rename or drop a parameter, and an old document stays exactly as
true as it was — nothing reads it back into a factory. A private profile's documents are frozen
here too, like its source: the store is gitignored, nothing leaves the machine, and a
configuration has to stay readable without the foreign repository checked out at the right
commit — which a patch needs anyway, and a configuration must not.

**For the broker configuration, "unchanged" means byte for byte.** Its document carries each
symbol's fetch time and the file's own metadata, which `config_hash` leaves out, so a refresh that
reads the same values back keeps `config_hash` and still gives the next run a new
`broker_config_id`. `config_hash` covers only the symbols and the fee structure, not the account
terms in `broker_info` (leverage, margin-call and stop-out levels) a margin broker trades with.
Compare it to ask whether two runs traded under the same symbol specifications and fees — on a
margin broker, the broker section's `leverage` and levels beside it — and open `broker_config_id`
to read what one of them had ([Broker Configuration](../broker_config_guide.md#config-hash--seed-id)).

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

## The config directory reads, and writes nothing but itself (#554)

Every file that can start a run — scenario sets and AutoTrader profiles, under `configs/`,
`user_configs/` and the user algo directories — read into one row each: what it DECLARES, from its
raw JSON, never through the loader. The cache is one parquet file at the store's root, one row per
file keyed on its path, modification time and size, the row itself carried as its JSON:

```
data/runtime/config_directory/
  config_directory_index.parquet   path · source_mtime · source_size · status · row_json
```

- **A read writes nothing but this cache.** The scenario listing it replaced froze every file it
  saw into `run_configs/` — a RECORD that deletes nothing — so a half-finished file being edited
  became a permanent "version". The run-config store is not touched here at all.
- **Only a changed file is read.** A request walks the roots and compares each file's `stat` with
  its row; an unchanged file costs one `stat`, a new or changed one a parse, a deleted one drops
  out. A new `LOGIC_VERSION` reads everything again.
- **The walk is the cost** — one `stat` per directory entry, each a request across the bridged
  mount — so hidden and bytecode directories are pruned before they are entered (a strategy
  repository's `.git` is most of what lies under it), and a reading is served for `FRESHNESS_S`
  (30 s) from memory; `refresh` walks at once. Measured 2026-09-27 over 37 scenario sets and 31
  profiles: a refresh 0.3 s (0.2 s of it the walk), from memory under 0.1 ms.
- **A file that does not parse is a row**, `unreadable` with its reason — never an error, never
  cached as anything but what it is. JSON carrying neither marker (an analysis result beside a
  strategy) is cached as `not_a_config`, so it is not read again, and never served.
- **One name, one kind.** A file name is how a configuration is addressed everywhere a record
  names it, so a scenario set and an AutoTrader profile named alike is a conflict, not a
  precedence: the row is `unreadable` with that as its reason (judged at serve time, because it
  is a fact about another file), and both loaders refuse to start a run from the name —
  `validators/config_name_validator.py`, which only reads, so an AutoTrader start depends on
  nothing it has to write.
- **The run figures are not in it.** They are joined at serve time from the run index, matched on
  `config_snapshot` (the source file name, in both pipelines) and the run type, so a run that just
  started counts at once — and deleting this cache loses nothing. What the newest of those runs DID
  comes from the run-results ledger through the run list's own join, so the directory and the run
  list cannot disagree about a run.
- **`derived_from` is None on purpose:** its sources are hand-written configuration files, which
  are not a store.

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
patch="${FINIEX_DATA_HOME:-$PWD}/run_patches/<patch_hash>.patch"  # run from the project root
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

**Deletable and append-only describe the store, not what a run read from it.** A fetch overwrites
a symbol's entry in the runtime broker cache in place, a re-import with `--override` replaces tick
files, and every render replaces bar files. So a run never relies on what it read staying as it
was: it keeps a copy where it can — its broker configuration in `run_configs/`, the patch of
uncommitted code in `run_patches/` — and describes the rest
([A run is a record, and an old one stays true](../introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true)).

### A new thing joins an existing store before it becomes a new one

The store is the umbrella, and its kind says what the content is. So something new that has to
persist first looks for the store whose kind, source and lifecycle it shares, and becomes a family
or an entry there; a new store is created only where none fits, and its `note` says why. The spread
estimate is the example: an analysis computed from the tick archive, regenerable at any time, is a
family in `discovery_caches` (whose sources are therefore bars AND ticks), not a store of its own.

**"Discovery" names a content type, not a store kind**: a DERIVED analysis computed from the
archive, regenerable at any time and cached against its source — and against the version of its
method where the family records one, as the spread estimate does.
What the IDE produces itself — a run, a certificate, a carry-over — is RECORD or CARRY-OVER and
never a discovery: deleting it loses what happened.

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
save time, the bot's identity, `written_by_run_id`, and the writer's `carry_over_from`. The run id
is **provenance, never identity** — it records which session wrote the file so a restored state
can be traced back, and it is deliberately not part of the key. The writer's declaration is what
lets the EARLIER stage of a declared continuation recognise its successor's document as its own
bot's, rather than ignoring it and overwriting the successor's book.

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
- **The activity list** (`activities.directory`, `~/.finiex/activities/` by default, #603) holds
  one entry per activity running on the machine, each held by a kernel lock for its activity's
  life and removed at its end; its own reader clears what a killed process left behind. Nothing in
  it outlives its activity, so it is no record, no carry-over and no input, and it is built from no
  store. It is one per MACHINE and lies outside every code tree, because a machine holds several
  copies of the code and the list has to show the work of all of them. See
  [What runs on this machine](activity_registry.md).

**Not a store at all: a file the operator addresses for a reader outside the app.** The liveness
stamp file (`operator_alerts.liveness.liveness_stamp_file`, #235) holds one UTC instant, rewritten
with each liveness ping, at a path the operator chooses — and nothing in this app reads it. It has
no root the catalog could register, and deleting it loses nothing the app needs: the watcher it is
written for reads its absence as silence. See
[Unattended Operation](../autotrader/unattended_operation.md).

**Nor is a bot's lock file** beside its carry-over (`<bot_id>_<symbol>.lock`, #598). The lock is
the kernel's and the file only its handle: a session holds it while it runs, so a second start of
the same bot is refused, and its note names the holder for that refusal and is never read back
as data. Deleting it loses nothing; the kernel releases the lock with its process however that
ends.

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

Specs live in [`artifact_specs.py`](../../python/framework/reporting/io/artifact_specs.py); the
artifacts with a CSV surface and those with a row filter keep that real logic in
`report_csv_io.py` and `report_filters.py`.

A run's directory also holds its **order-event stream** (`io/order_events.jsonl`, #362) — written
while a live session runs, and with its report by a backtest. It is part of the run's record, not a
store of its own, and not an artifact: the run index lists it under `stream_files`, because an empty
artifact list is how a run that never reached its report is recognised. See [Order-Event
Stream](order_event_stream.md).

A live session writes two more records into `io/` as it runs: the warmup history it started from
(`warmup_history.json`, #588) and its tick stream's outages and refused trades
(`tick_stream_events.jsonl`, #609). Neither is a report artifact nor a served stream, so the run
index lists neither (`RUN_RECORD_FILENAMES`): an artifact would make a session that died before
its report read as reported, and a stream entry would promise a route that does not exist.

An AutoTrader session's directory also holds the **warmup history** it started from
(`io/warmup_history.json`, #588) — the bars and the instants that selected them, written at
injection and again once the seam at the first tick has closed. Part of the run's record like the
stream; a live session's warmup exists nowhere else. See [Warmup History](warmup_history.md).

---

## The index contract

An index is **derived**: it may be deleted or go stale without anything being lost, because
`rebuild()` reconstructs it from the store. The store is the truth; the index is the read path.

**One index breaks that today, and says so: `run_configs`.** Its frozen copies carry each
version's content, but which file a version came from, when it was first seen and how often it ran
were only ever written into the index. A rebuild therefore empties every configuration's history.
The store declares that loss on its descriptor (`rebuild_loses`), so `rebuild --all` leaves it out
and names it, a rebuild of that store alone needs `--accept-loss`, and the catalog does not advise
rebuilding it when it is stale. Making the index derivable again, so that it rejoins
`rebuild --all`, is #547.

- **ONE file, never a file per entry.** Measured here: 404 small parquet files cost 3.29 s
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
producer's own `schema_version`. What they inherit later is the `LOGIC_VERSION` field, under #583.

**Until then the tick index carries its own schema stamp**, and that is the stopgap rather than the
design. `needs_rebuild()` compares mtimes alone, so an index written before a column existed stays
"valid" and keeps serving rows without it until an import happens to touch a parquet — the trap that
makes adding a column unsafe to deploy. `index_version` was already being written by all three
managers and read by none; the tick index now reads it back and rebuilds when it differs. Bump
`INDEX_SCHEMA_VERSION` whenever a column is added or its meaning changes. The other two still write
a version nobody reads, which is the gap #583 closes properly.

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

0. Check first whether an existing store already holds things of its kind, from its source and
   with its lifecycle — then it is a family or an entry there, not a new store (see *A new thing
   joins an existing store before it becomes a new one* above).
1. Add a `StoreId` value in [`store_types.py`](../../python/framework/types/store_types.py).
2. Register it in [`store_registrations.py`](../../python/framework/store/store_registrations.py)
   with its kind, root, key, form and — unless it is SPECIAL — its index or a stated reason for
   having none. Two fields are for the reader of the catalog: `purpose`, one line saying what the
   store is FOR (printed on its row), and `doc`, the document that explains it, with a heading
   anchor where one section covers it (printed under the table as its help link).
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
written for EVERY bot — a bot whose algo remembers nothing still sends orders under a key,
and its successor still has to recognise them.

4b HAS an index, and by the rule above rather than against it: something does search across
bots — *which bot carries what, since when, from which run*, the question an operator asks
after a 03:00 restart, and the one a diagnostics layer asks across a fleet.

What it deliberately does NOT hold is history. A carry-over overwrites by definition, so what a
given boot adopted belongs to that run's RECORD (store 1), which is immutable and already
indexed. A reader wanting "what was adopted across thirty restarts" joins the two indexes;
bending a carry-over into a log would make it a different kind of store.

#### They share exactly one thing, and the rest can diverge

Both turn on the same restart, and both are keyed `<bot_id>_<symbol>` with the same
sanitisation — that identity is the ONLY connection. There is no shared write, no
transaction, no ordering guarantee. Anyone reasoning about restarts needs the differences:

| | 4 · `session_state` (#354) | 4b · `cold_start_state` (#355) |
|---|---|---|
| Writer | `AlgoStateStore` | `ColdStartStateStore` |
| **When** | every N ticks OR M seconds, plus shutdown | **boot + shutdown + on a STRUCTURAL book change, plus a tick cadence for drift** |
| **Gate** | the algo's own opt-in `uses_state_persistence()` | none — every bot |
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

The split is a measurement, not a preference. One carry-over write costs **11 ms** on this project's
tree, and the store's index rebuild another **26-40 ms** (`/tmp` says 2 ms; on this bridged mount
every file operation is a request across it, and that is the difference). A structural change
happens a handful of times a day and cannot be recovered, so it is written immediately. Drift moves
on nearly every tick of a trend — a trailing stop follows every new high — and is either re-derived
by the algo on its next pass or loses at most one interval of a running maximum, so it waits for
`cold_start.book_drift_interval_ticks`. Counted in TICKS because drift is *caused* by ticks: a quiet
market needs no writes, and a tick counter needs no clock (the first passes happen before the
canonical clock is injected). The index rebuild is left to the writes that BOUND a session; an index
is derived and reports itself stale until the next boot.

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
(#209) — and a dry run (like a mock session) restores nothing, because it never queried the venue,
and a rehearsal that closes remembered REAL positions with orders that are never placed reports a
book it does not have.

#### When an order of our shape cannot be placed — the causes, most likely first

The boot step reports an order that carries a client key of OUR shape from a session the
carry-over has no record of as an ERROR (`unknown_session`). It is rare, and every remaining
path to it runs through a human action — which is why it is reported and left standing rather
than handled automatically. If you ever meet it, this narrows it down:

| Cause | How likely | How to tell |
|---|---|---|
| Someone deleted, moved or restored `data/runtime/` (a "clean start", a machine move, a backup) | the usual one | the carry-over file is missing or its `saved_at_utc` predates the order |
| The session started in another data home — a deploy whose unit names no `FINIEX_DATA_HOME`, or the wrong one | right after a deploy | the boot named a home and a carry-over other than the last session's; a session trading real money refuses it rather than starting (#598) |
| The document was hand-edited or corrupted | rare | the boot log carries the "unreadable" or "payload rejected" warning |
| A schema version bump discarded it | only right after a deploy | the boot log names the version mismatch |
| Genuinely another client using the same key format | improbable | the key's session half matches no run id of ours |

Three paths are closed by construction and can be ruled out immediately: a **container rebuild**
cannot lose it (`./data` is a bind mount from the host), **eviction** cannot drop a key whose
order is still resting (eviction is by relevance before recency), and **two instances of the
same bot** cannot cross their writes — the second start is refused while the first holds the
bot's lock (#598).

⚠️ **A schema-version bump on a carry-over does not lose state quietly — it makes the session
trade beside its own forgotten orders.** The chain: envelope discarded → empty payload → the
predecessor's session keys are gone → its resting orders read as `unknown_session` → they are
NOT adopted. A session trading real money is therefore refused when it finds its carry-over in
another format, as when it finds none (#598): the way out is a declared first start
(`--first-start`), which starts over deliberately. A dry run and a mock session still start
fresh and report the discarded document as an ERROR in the session pot — they claim nothing about
the venue. That has to be known before the deploy rather than discovered at 03:00.
