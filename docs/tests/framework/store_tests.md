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

The sanitiser being lossy is pinned as a **property**, not as a bug: a filename cannot carry
every character, so `dot live` and `dot-live` legitimately meet. That is the reason for a check
rather than for a stricter sanitiser.

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

## Related coverage elsewhere

| Suite | What it covers of this model |
|---|---|
| [Reporting Pipeline Tests](reporting_tests.md) | The artifacts themselves, and `ReportStore` against a real run tree |
| [API Endpoint Tests](api_endpoint_tests.md) | The 15 report endpoints over the generic getter — response shapes unchanged by the collapse |
| [Algo State Persistence](../autotrader/state_persistence_tests.md) | The carry-over store's cadence, corrupt and staleness policies |
