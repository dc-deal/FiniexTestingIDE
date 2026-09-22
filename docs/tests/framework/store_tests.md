# Store Model Tests

`tests/framework/store/` — the store catalog, the shared index base, the generic form-A
retrieval, and the carry-over envelope (#486).

Run: `python -m pytest tests/framework/store/ -v`
Launch entry: `🧩 Pytest: Store Model (All)`

Architecture: [Data Storage Layout](../../architecture/data_storage_layout.md)

---

## What this suite exists to catch

Three properties that a code review cannot check by reading, because in each case the broken
state and the correct state look identical from the outside:

| Property | Why reading cannot verify it |
|---|---|
| The catalog is **complete** | A store added without a registration looks exactly like a store that was never added |
| An index is **disposable** | A stale index looks exactly like a fresh one until something compares them |
| A **carry-over is keyed by the bot** | A run-keyed carry-over works perfectly until the first restart |

---

## `test_store_catalog.py`

**Catalog completeness.** Every `StoreId` has a registration — this is the assertion behind
CLAUDE.md §44's rule that a new store is entered in the same change. Every descriptor carries a
kind, a form, a backend and a root. A `SPECIAL` store must state *why* it is special, so the kind
is a declaration rather than a loophole. A managed store must carry an index or a note explaining
why it has none. Asking the catalog for an unregistered store is named as an error, never answered
with an empty result.

**Index base.** Uses a small in-test index over a directory of JSON files, so the base class is
exercised rather than one of its subclasses:

- A missing index reads as empty **with its columns**, not as a failure.
- The write leaves no `.tmp` behind — atomicity, verified by absence.
- **Deleting the index loses nothing:** rebuild reproduces the previous frame exactly. This is the
  property the whole store model rests on.
- The `LOGIC_VERSION` is stamped into the parquet's Arrow metadata and read back.
- **A bumped logic version invalidates the file** even though no source changed. This is the blind
  spot the field exists for: a staleness rule keyed on source mtime cannot see a change in the code
  that produced the content.
- An index written before the stamp existed reads as out of date rather than as current.

## `test_artifact_retrieval.py`

**The spec registry.** Seventeen report artifacts, each binding a `.json` name to a Pydantic model,
and no two sharing a file name — two specs on one name would silently overwrite each other inside a
run directory.

**Round trip.** `write_artifact` / `read_artifact` return the model the spec names.

**Store retrieval.** A missing artifact is `None` rather than an error; an unknown run is `None`;
a present artifact comes back decoded. An artifact that is present but does not match the current
model is **named** (`ReportArtifactUnreadableError`) rather than escaping as a bare validation
failure — a guard that used to exist for exactly one of the fifteen former getters, and became the
rule when they collapsed into one.

> The collapse had to preserve static typing: `get(run_id, BROKER_ARTIFACT)` is
> `Optional[BrokerReport]` and nothing looser. A runtime test cannot assert a static type, so what
> is asserted is the pair that makes the static claim true — every spec's model matches the
> artifact it names, and the round trip returns that model.

## `test_carry_over_envelope.py`

The envelope round-trips with its payload and its provenance. The writing run is optional, so a
writer without a run identity still produces a valid envelope. **The identity is the bot; the run
id is only recorded** — the distinction #355 turns on, because a restart mints a new run id and a
run-keyed carry-over could then only be found by guessing. An envelope missing its identity is
refused. An empty snapshot is the default rather than an error, since the store writes no file for
one.

## `test_run_completion_audit.py`

Which runs started and never reached their close. A run registers in the run index from its
header, written before anything can fail; its ledger row is the last step at close. A process
killed between the two exists in one store and not the other, and nothing said so.

The plain reverse set difference stays **out**. The ledger predates the run index, so it
legitimately holds rows for runs the index never saw — reporting that direction would bury the
one finding under ordinary history. Scoping to a `parent_id` is what lets a deployment name its
own missing sessions, since its session table is built from the ledger and a killed session is
absent from it by construction.

The scope takes a `parent_kind` beside the id, and the tests pin both directions of it: a
deployment asking for `x` must not collect a sweep that is also called `x`, and a row indexed
before the field existed is claimed by NEITHER kind. An unknown kind is not a claim — the same
shape as a missing monotonic stamp yielding no number rather than a wall-clock substitute.

A second, narrower question lives in the same file: a booking whose run directory is gone. It is
not the mirror of the first, and the tests pin exactly where the line runs — the run index must
still KNOW the run (otherwise it is ordinary history), and the row must carry no
`records_pruned_at` (otherwise a prune already accounted for it). Both currency rows of a
two-currency run are returned rather than one per run, which pins the collapse this project has
already measured elsewhere: keeping the first row per `run_id` silently drops the second currency.
The directories arrive as a mapping rather than being read off `RunInfo`, because that model is an
API type and carries no filesystem path.

One test pins a defect caught in review rather than a requirement: grouping by run group must
keep every run, where a dict comprehension keyed on the group silently keeps only the last.

## `test_carry_over_identity.py`

Two live bots must not share one carry-over document. The stores file one per BOT under
`<profile name>_<symbol>`, and both halves are free text nothing validates — so a collision is
invisible from inside either store: each asks whether a document belongs to THIS bot, and in a
collision it does, for both. The check therefore runs once across the profile tree at boot,
before anything reads or writes.

**The separator is RESERVED since #538, and the suite pins what that closed and what it did
not.** Both halves sanitise to `[a-z0-9-]`, so the `_` occurs exactly once and two DIFFERENT bots
can no longer collide by accident of where the underscores fall — `btc` + `USD_SPOT` and
`btc_usd` + `SPOT` used to be one file and are now `btc_usd-spot` and `btc-usd_spot`. That was
the dangerous one: the bot that started second read a position book it never wrote.

The sanitiser being lossy is still pinned as a **property**, not as a bug: a filename cannot
carry every character, so `dot live` and `dot-live` legitimately meet. That is one bot written
two ways rather than two bots merging, and it is the reason for a check rather than for a
stricter sanitiser.

**A declared `bot_id` takes precedence over the name**, and three tests pin why: it produces the
key, it survives a rename of everything else, and leaving it empty composes from the name exactly
as before — so no profile changes key by the field existing. Without it the identity moves when
the display name does, which is the one rename that silently orphans a live bot's position book.

**Nothing is exempt, and one test exists to pin the correction that produced that rule.** The
first version of the check excluded mock profiles on the reasoning that they run no live
executor — which is wrong: `adapter_type: mock` selects the tick SOURCE, and every AutoTrader
session runs a `LiveTradeExecutor` and builds both carry-over stores. Measured 2026-09-21, 15 of
the 16 documents in `data/runtime/cold_start_state/` belong to mock profiles, so the exclusion
would have skipped almost the entire population the check protects. An unknown adapter counts for
the same reason, which is why #209's MT5 will need no change here.

One thing IS skipped: an unreadable profile elsewhere in the tree. This check answers one
question and must not become a second config validator.

One test runs against the SHIPPED profiles rather than a fixture: a collision there would mean
two of the operator's own bots share a position book.

---

## `test_run_config_store.py`

Run configs as a store (#538). A configuration that starts a run used to be a file at a path, and
a path is not an identity — so nothing could say two runs used the same configuration, an edited
file left no trace, and a backtest could not name its own strategy identity at all.

Two properties carry the design, and the suite is organised around them.

**The identity is the CONTENT, normalised.** Registering the same bytes twice is one entry and one
frozen copy; reformatting the file is still the same configuration; changing a value mints a
second version beside the first, and that accumulation IS the history. `first_seen` on a known
version never moves, because it is the date the history reads. And the frozen copy hashes back to
its own file name — a record that cannot check itself is not a record.

**Three hashes separate four kinds of change**, which is the reason one hash is not enough:

| the change | `config_id` | `param_hash` | `scope_hash` |
|---|---|---|---|
| a scenario RENAMED | moves | holds | holds |
| a comment added | moves | holds | holds |
| a scenario ADDED | moves | holds | moves |
| a worker's period 14 → 21 | moves | moves | holds |

The first two rows are what the store exists to be able to SAY: different bytes, same meaning. A
profile carries no `scope_hash` at all, because it holds one symbol and no scenario list, and a
hash over nothing would be a claim rather than an absence.

Two more groups. **Resolution is a lookup and never the only way to find anything** — a registered
name resolves without a walk, a file that MOVED resolves to None rather than to a stale path, an
unknown name resolves to None, `sync` registers only what changed (zero writes in the steady
state), and one unparseable config does not make every other one unfindable. And **the index
describes its store** — a missing frozen copy is reported, a `LOGIC_VERSION` bump invalidates, and
the rebuild finds every frozen copy while leaving `source_name` empty, because `first_seen` and
`source_path` were observations made at registration and exist nowhere else. The rebuild says so
by leaving them blank rather than inventing them.

## Related coverage elsewhere

| Suite | What it covers of this model |
|---|---|
| [Reporting Pipeline Tests](reporting_tests.md) | The artifacts themselves, and `ReportStore` against a real run tree |
| [API Endpoint Tests](api_endpoint_tests.md) | The 15 report endpoints over the generic getter — response shapes unchanged by the collapse |
| [Algo State Persistence](../autotrader/state_persistence_tests.md) | The carry-over store's cadence, corrupt and staleness policies |
