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

## `test_store_catalog.py` (11 tests)

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

## `test_artifact_retrieval.py` (7 tests)

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

## `test_carry_over_envelope.py` (5 tests)

The envelope round-trips with its payload and its provenance. The writing run is optional, so a
writer without a run identity still produces a valid envelope. **The identity is the bot; the run
id is only recorded** — the distinction #355 turns on, because a restart mints a new run id and a
run-keyed carry-over could then only be found by guessing. An envelope missing its identity is
refused. An empty snapshot is the default rather than an error, since the store writes no file for
one.

---

## Related coverage elsewhere

| Suite | What it covers of this model |
|---|---|
| [Reporting Pipeline Tests](reporting_tests.md) | The artifacts themselves, and `ReportStore` against a real run tree |
| [API Endpoint Tests](api_endpoint_tests.md) | The 15 report endpoints over the generic getter — response shapes unchanged by the collapse |
| [Algo State Persistence](../autotrader/state_persistence_tests.md) | The carry-over store's cadence, corrupt and staleness policies |
