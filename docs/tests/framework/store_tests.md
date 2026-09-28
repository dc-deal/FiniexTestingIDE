# Store Model Tests

`tests/framework/store/` — the store catalog and what it prints, the shared index base, the
generic form-A retrieval, and the carry-over envelope (#486).

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

**Catalog completeness.** Every `StoreId` has a registration — this is the assertion behind the
rule that a new store is entered in the catalog in the same change that creates it. Every descriptor carries a
kind, a form, a backend and a root, and says what it is FOR — one line that fits the catalog row
— with a help link whose file exists and whose anchor names a real heading, so renaming a heading
fails here rather than in a reader's browser. A `SPECIAL` store must state *why* it is special, so the kind
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

**Rebuild all.** `rebuild --all` covers every store that builds an index of this model, except the
ones that declare what their rebuild loses — and asks no store that has none. The run-config store
is declared lossy (its index alone knows which file each version came from and when it was first
seen), and rebuilding it without accepting the loss is refused before the index is touched. The
config directory is declared self-healing: its rebuild deletes the file and its next read writes
it again, so the catalog must not answer a rebuild with "rebuild before trusting it".

## `test_store_catalog_views.py`

What `store_cli.py catalog` and `rebuild` print, over hand-built rows, so no store on disk decides
the outcome. Each part of a row reaches the reader in its place: the purpose on the store's own
line, the key and the help link in the two blocks underneath, an absent root and an uncountable
store as such rather than as zero. The size column appears only when sizes were measured. A stale
index is listed as a task and one that refreshes itself as a note, never the other way round; a
stale index whose rebuild loses data is not sent to a rebuild at all, and `rebuild --all` names the
store it skipped and what its rebuild would lose. An
advisory appears under its check's title from the validation check catalog — one block per check
however many gates or brokers it names — and a catalog with no advisory prints none.

## `test_store_health_checks.py`

The two dated claims the catalog flags, as findings rather than printed lines: a release gate whose
NEWEST certificate has expired, and a broker fee structure frozen longer ago than ninety days. The
window's boundary is pinned (past it is flagged, reaching it is not), and so is the absence case —
a seed that records no freeze date is not an old one. Each finding is an advisory scoped to the
gate or the broker it concerns. One test runs both checks over this tree's own certificates and
broker configurations at a moment far ahead, which exercises the path from the stores to the
findings and holds every id they emit to the validation check catalog.

## `test_artifact_retrieval.py`

**The spec registry.** Every report artifact binds a `.json` name to a Pydantic model, and no two
share a file name — two specs on one name would silently overwrite each other inside a
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

Two bots must not share one carry-over document. The stores file one per BOT under
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

**Every profile MUST declare one** — required of every profile since 2026-09-24, one-off
included — and the tests pin the refusal: a profile without a `bot_id` is rejected at boot, a
`--one-off` session is no longer exempt (the route into a collision is copying a profile and
keeping its name, and that copy was exactly what the older continuous-only rule exempted), the
message carries a usable suggestion and shows the identity it would produce (a complaint the
operator cannot act on is one they work around), and a declared one passes.

**A declared `bot_id` takes precedence over the name**, and three tests pin why: it produces the
key, it survives a rename of everything else, and leaving it empty composes from the name exactly
as before — so no profile changes key by the field existing. Without it the identity moves when
the display name does, which is the one rename that silently orphans a bot's position book.

**Nothing is exempt, and one test exists to pin the correction that produced that rule.** The
first version of the check excluded mock profiles on the reasoning that they run no live
executor — which is wrong: `adapter_type: mock` selects the broker adapter (a
`MockBrokerAdapter`); the tick source is `tick_source.type`. Every AutoTrader
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
and an unknown name resolves to None. (`sync`, the bulk registration a scenario LISTING used to
do, is gone with that listing: a read must not write this RECORD store, #554.) And **the index
describes its store** — a missing frozen copy is reported, a `LOGIC_VERSION` bump invalidates, and
the rebuild finds every frozen copy while leaving `source_name` empty, because `first_seen` and
`source_path` were observations made at registration and exist nowhere else. The rebuild says so
by leaving them blank rather than inventing them.

## `test_run_patch_store.py`

The patch of every dirty tree a run ran from, keyed by the SHA256 of its bytes — not by the run
header's `diff_hash`, which digests the changed content (#551). The store makes one
promise — a run from uncommitted code can still be restored to the code that ran — and every
property it rests on fails silently when broken, which is why each is asserted.

**The key is the content.** A patch round-trips byte for byte; a patch filed under a hash it
does not have is refused and leaves nothing behind; equal diffs are one entry however many runs
ran them, and different diffs are two. An empty patch is an ordinary entry, not a special case.
The reference a header carries is the configured root plus the file name, so under the default
configuration it reads `run_patches/<hash>.patch` — and that file name is the key: its bytes hash
to it, which is the check the documented restore makes before `git apply`.

**Written once.** A second put of the same patch does not rewrite the file — asserted on the
inode, because an atomic write REPLACES the file and a rewrite with equal bytes is otherwise
invisible. No temporary file survives a write. A damaged entry is refused on read rather than
served (it would restore code that never ran) or answered with None (which would claim it was
never stored), and the next put of its patch repairs it.

**Reading.** An unknown hash and a store that was never written are both None. A key that is
not a SHA256 digest is refused, because the key becomes a file name.

**Restoration end to end**, against a throwaway git repository: a tracked change and an
untracked file are captured as a patch, stored, the tree reset, and the stored patch applied —
and both files come back as they were.

**A strategy repository keeps its own patches.** `RunPatchStore.inside_repository(<root>)` is
the store under `<root>/.finiex_run_patches/`. Asserted against a real repository: a dirty tree's
patch is kept there, the change is committed with `git add -A`, and the tree reads CLEAN with no
patch in the commit — the directory's own `.gitignore` (`*`) is what makes that hold, and a patch
git could see would refuse the next real-money start. A removed `.gitignore` comes back with the
next put, even one that writes no patch; an existing one is left as it is; this repository's store
writes none. And the suite never writes into a real strategy repository: the session fixture
redirects every foreign home, and one test asserts it for the operator's `user_algos/`.

**A release gate keeps its patch.** Only a session made of release-gate suites alone leaves the
operator's `run_patches/` in place, because its committed certificate names the patch. One daily
test in the same session is enough to keep the redirect, and an empty session is not a release
gate.

**Registration.** The store is a RECORD opened by id, has no index, and its note names #535 as
the owner of its lifetime question. The catalog counts only `.patch` files under the configured
root, never a temporary file. And the suite never writes into the operator's `run_patches/`: the
session fixture in `tests/conftest.py` redirects it, and one test asserts that it does — the
suite runs from a tree that is dirty whenever somebody is working on it.

## Related coverage elsewhere

| Suite | What it covers of this model |
|---|---|
| [Reporting Pipeline Tests](reporting_tests.md) | The artifacts themselves, and `ReportStore` against a real run tree |
| [API Endpoint Tests](api_endpoint_tests.md) | The report endpoints over the generic getter — response shapes unchanged by the collapse |
| [Algo State Persistence](../autotrader/state_persistence_tests.md) | The carry-over store's cadence, corrupt and staleness policies |
