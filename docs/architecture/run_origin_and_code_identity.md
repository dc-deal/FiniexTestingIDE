# Run Origin and Code Identity

A run header used to record the commit of this repository and nothing else about the code that
ran. That is not enough, for two reasons. A user strategy lives in `user_algos/`, which is its own
git repository and is ignored by this one, so a threshold changed in the bot's own module — with
its declared version left at `1.0.0` — produced a live run and a backtest with identical
identities and different code. And the header could not say **who** or **what** started a run, or
**on which machine**: answerable from memory while there is one operator at one console, and not
at all once the viewer can start a run.

Since #551 every run header carries two more blocks, written at the START of the run in both
pipelines: an **origin** and, for a run that reports, a **code identity**. This document explains
what each one records, where it comes from, who reads it, what it costs, and the refusal it makes
possible: a real-money session does not start from uncommitted code.

**Not here:** how a token is bound to an account (see
[Credentials Layout](credentials_layout.md#tokens-and-accounts--a-client-and-the-one-it-acts-for)),
how the patch of a dirty tree is stored and restored (see
[Data Storage Layout](data_storage_layout.md#run-patches-keep-the-code-a-dirty-tree-ran-551)),
what the API serves and when its contract changed (see
[API Contract Log](api_contract_log.md)), and the run header's other fields (see
[Reporting Pipeline](reporting_pipeline.md) and
[API Server Architecture](api_server_architecture.md)).

## What a run header carries

The shape, with illustrative values — a session whose decision logic is loaded from `user_algos/`:

```json
"origin": {
  "channel": "cli",
  "client": "console",
  "person": "operator",
  "host": "h_7k2m9q",
  "allow_dirty": false
},
"code_identity": {
  "framework": { "root": "/app", "in_repository": true, "commit": "151c9889", "dirty": true,
                 "uncommitted_count": 42, "changes": [" M .gitignore", "…"],
                 "diff_hash": "9f2c…", "patch_ref": "run_patches/c83a….patch",
                 "patch_excluded": ["configs/credentials/inbound/consumer_tokens.json"],
                 "restorable": true },
  "repositories": [
    { "root": "/app/user_algos", "in_repository": true, "commit": "2f054f5", "dirty": true,
      "changes": ["?? my_bot/"], "diff_hash": "4e01…", "patch_ref": "run_patches/7d19….patch",
      "patch_excluded": [], "restorable": true }
  ],
  "components": [
    { "role": "decision", "name": "user_algos/my_bot/my_strategy.py",
      "type": "user_algos/my_bot/my_strategy.py", "version": "0.1.0",
      "source_path": "/app/user_algos/my_bot/my_strategy.py",
      "repository": "/app/user_algos", "package_digest": "b71c…" },
    { "role": "worker", "name": "rsi_fast", "type": "CORE/rsi", "version": "1.0.0",
      "source_path": "python/framework/workers/core/rsi_worker.py", "repository": "/app" }
  ]
}
```

A header written before these fields existed carries neither block. That reads as **unknown**,
never as a guess — there is no migration.

## The origin — who started the run, for whom, and where

| Field | Today | Where it comes from |
|---|---|---|
| `channel` | `cli` · `sweep` · `direct` | declared by the entry point (below) |
| `client` | `console` | the caller; an API consumer name once the `api` channel exists |
| `person` | `operator` | the principal the client acts for |
| `host` | `h_` plus six characters | minted once per installation |
| `allow_dirty` | `false` | `--allow-dirty` on the AutoTrader CLI |

**The channel is DECLARED by the entry point, never inferred.** Detecting pytest from the
environment would be a heuristic, and a heuristic in a provenance field is a guess recorded as a
fact:

| Entry point | Declares |
|---|---|
| `strategy_runner_cli.py run` | `cli` |
| `autotrader_cli.py run` | `cli` |
| the optimization runner — every combination of a sweep | `sweep` |
| anything constructing a `ScenarioSet` or an `AutotraderMain` without saying | `direct` |

The channel travels as a keyword argument: `ScenarioSet(channel=…)`,
`initialize_batch_and_run(channel=…)`, `AutotraderMain(channel=…)`. A new entry point states its
own.

**The console operator is a principal of its own, and never an API account.** Every channel that
exists today starts at a terminal, so `client` is `console` and `person` is `operator`. The `api`
channel — a consumer token naming its client, bound to exactly one account — arrives with the
first write route (#552). Until then nothing constructs a run from a request, so
`build_run_origin` reads no token.

**The host is minted, not read.** The container hostname changes on every rebuild, so it cannot
identify an installation. `HostIdentityManager` mints the id on the first start that asks for it
and writes it to `user_configs/host_identity.json`, on the bind mount that survives a rebuild:

- a **missing** file is minted, and the mint is logged as a warning naming the file;
- a file that is **present but cannot be trusted** refuses the start and is never re-minted — a
  silent re-mint is an identity change nobody notices. A live session builds its origin FIRST,
  before the code identity is captured and before its header is written, so the refusal costs no
  git work, stores no patch and leaves no header stating an identity nobody trusts. The
  AutoTrader CLI reports it as a refusal: exit code 2, the file named, no stack trace;
- under **config isolation** (every pytest run) the declared test id `test` is stated and nothing
  is read or written.

## The code identity — which code ran

Built by `code_identity_builder.build_code_identity`, wired by
`run_origin_builder.capture_code_identity`:

- **`framework`** — this repository, found from the code's own location rather than the process
  cwd (`git_info_utils.get_framework_root()`), so the header's commit, the ledger's dirty flag
  and this block all describe the checkout the code was imported from. Always present: where git
  cannot say, the state still names the checkout and records its state as unknown.
- **`repositories`** — every OTHER repository a component was loaded from, once each, in the same
  shape.
- **`components`** — one entry per decision logic and worker instance the run resolves, through
  the factories' own resolution, so the file recorded is the file that is loaded. A component
  loaded from a path carries a **`package_digest`**: SHA256 over every file of its package
  directory, path and content, with git's ignore rules deciding what is code (a `__pycache__`
  never is). Content rather than git's tree id on purpose — the same code yields the same digest
  whether or not it is committed, so two runs can be compared by it directly. A file directly in
  a repository's root is digested alone, because that repository as a whole is not its package.
  A component the factories cannot resolve — a module that raises at import, a failing
  `get_metadata()` — is recorded without a source and a warning goes to the global log; the
  pipeline then fails on it with its own message. If the pipeline loads it after all — the file
  was mid-edit at the capture and saved before the load — the startup guard counts it as code that
  moved.

**`in_repository` has three values, because "no" and "nobody knows" are different answers.**

| Value | Means | Can it be committed code? |
|---|---|---|
| `true` | a repository answered for it | yes, when `dirty` is false and `commit` is set |
| `false` | git answered: no repository — or one that **ignores** the directory | never: no commit can contain it |
| `null` | git did not run, or it **refused** a checkout that is there | unknown |

A directory the surrounding repository ignores is unversioned code, however clean `git status`
reports the rest of the tree: the ignored files are invisible to it. A refusal is recognised by a
`.git` at or above the directory when `rev-parse` gives no answer — typically "detected dubious
ownership" for a checkout another user owns, common for a mount in a container — and the checkout
holding that `.git` is recorded as the root, because that is what a `safe.directory` entry has to
name.

**What makes a repository dirty.** Everything `git status` reports, read so that no user
configuration can hide anything: untracked files listed one by one whatever
`status.showUntrackedFiles` says, submodules never ignored, and an index entry flagged
assume-unchanged or skip-worktree counted as a change, because git stops LOOKING at such a file,
which is the opposite of the file being unchanged. Every git call pins the settings that would
otherwise change the answer or the patch (`git_info_utils._PINNED_CONFIG`), treats every path as a
NAME (`--literal-pathspecs` — an untracked file called `configs/*` once widened a path list back
onto the excluded credential files), and drops the inherited environment variables that redirect
a read (`GIT_DIR`, `GIT_WORK_TREE`, set inside a git hook, point `-C <root>` at another
repository) or override the rendering (`GIT_DIFF_OPTS`) — `git_info_utils._DROPPED_GIT_ENV`.
A question git could not answer is never read as "no": a failed ignore listing or a package that
could not be read records its code as unknown.

**The diff hash and the patch are two things, and so are their keys.**

- **`diff_hash`** — SHA256 over the CONTENT of every changed path, in path order: the path, its
  executable bit and a SHA256 of its bytes, a symlink's target, or a deletion marker. Git's
  rendering of a diff plays no part, so the same delta has the same digest on any machine, under
  any configuration and any git version. It identifies the delta; two runs with equal hashes ran
  the same uncommitted code.
- **`patch_ref`** — where the patch restoring that delta was kept: `run_patches/` under the
  SHA256 of the patch BYTES, which is a different number from the `diff_hash`. The patch is one
  pathspec-limited `git diff` against HEAD over a temporary copy of the index, so tracked changes,
  deletions and new files come out in one call and the repository's own index is never touched.
  Its rendering is pinned (`_PATCH_FORMAT`) against every setting known to break it or move it,
  so it applies under any configuration; the bytes remain one rendering, which is why the identity
  is the `diff_hash` and never the patch.
- **`patch_excluded`** — changed paths under any directory named `credentials` are left out of
  the patch, and the diff hash records only that they changed. A real key pasted into a tracked
  placeholder would otherwise be copied into `run_patches/` at every run start, before the
  credential guard ever sees it. Credentials are not code, so leaving them out does not make the
  code unrestorable.
- **`restorable`** — whether this repository's code can be put back exactly: a clean commit, or a
  dirty tree whose complete patch was stored. False when the patch could not be kept, and false
  when the tree is dirty through a nested repository, whose content a patch of the outer one
  cannot carry.

**When it is captured.**

| Run | Code identity |
|---|---|
| a simulation commissioned to report (`reporting: expected`) — every CLI run and sweep combination | captured over EVERY scenario's strategy |
| a simulation commissioned not to report (`reporting: none`, the test path) | none — nothing will read it |
| a live or mock AutoTrader session | always, over the profile's strategy |
| a sweep's mount build | no header at all — it is not a run |

A live session captures it after its origin and BEFORE its header, and keeps it on the session,
because the startup guard below asks it and must not depend on a run directory having been
created. The git state per repository is cached per process; the package digests are read FRESH
at every capture. When a later capture in the same process — the next sweep combination — finds a
package moved since the previous one, it drops the cached git state and reads the repositories
again, so one header never pairs fresh component content with a stale "clean". A CORE component
carries no package digest, so an edit to THIS repository between two combinations of one sweep is
not seen until the next process; the framework's state is the one the process started from. The
startup guard's re-check of the packages (below) is the same fresh read.

**A capture that fails refuses the session, with a record.** The capture runs before the loggers,
so an exception there would escape as a stack trace before the session has a header. It is held
instead: the header is written without a code identity, and the first step of the startup
handling refuses the session with `CodeIdentityCaptureError` — `STARTUP FAILED`, exit code 2, the
cause in the message and the full stack trace in `autotrader_global.log`. Every session, dry run
and mock included: the header of such a run names no code at all. What can still fail is the
storage and filesystem around the capture — the component resolution degrades by itself.

**`CodeIdentity.is_dirty()`** answers one question: can the code that ran be reproduced from
commits alone? Any dirty repository, any component in no repository, and any repository git could
not read say it cannot. An unknown state counts as dirty on purpose: a guard reading "not dirty"
where git could not answer would pass exactly the run it exists to stop.

## Who reads it

**The ledger reads its provenance from the header** instead of deriving it a second time. The
ledger row is the LAST thing a run writes, so a session killed before its close used to lose
its component versions and its dirty flag entirely; the header is written first.

- `decision_version` and `worker_versions` come from the header's components, filtered to the
  strategy the row's `config_snapshot` records. A component the factories could not resolve is
  left out, as it always was.
- **`git_dirty` changed meaning.** It covered only this repository, so a run of an uncommitted
  strategy in `user_algos/` read as clean. It is now `code_identity.is_dirty()` — every
  repository — and a run whose header carries no code identity reads as dirty, because nothing
  says it was clean. The old default for "git could not answer" was `false`.
- `git_commit` and `git_branch` come from the header too: the capture reads the branch in the
  same breath as the commit. A live row is written at the END of its session, and a branch read
  then would pair the start's commit with whatever is checked out after thirty days. Only a run
  whose header carries no code identity falls back to the process's read — and a recorded identity
  whose commit git could not read keeps that commit MISSING rather than borrowing a later read.

Every run that reaches the ledger carries a code identity: both report coordinators write into the
run directory its header was written into, only a simulation commissioned to report is given a
coordinator, and a live session always captures — or refuses to start when it cannot. A missing
one therefore means an unreadable header, or a session refused because its capture failed —
logged as a warning, recorded as unknown, never a failed report phase.

**The run index flattens both blocks** into flat columns, identically on append and on rebuild:
`origin_channel` · `origin_person` · `host_id` · `framework_dirty` · `code_dirty`. None means
unknown. `framework_dirty` is this repository alone and unknown where git could not read it;
`code_dirty` is `is_dirty()`. The index columns are not served on the API's run list.

**What the API serves changed in two places**, both in contract 4: the new `caller` route, and the
ledger's `git_dirty` on `GET /api/v1/sweeps/{sweep_id}`, which kept its shape and changed its
meaning as described above — it covers every repository a component of the run came from, and
reads true where the code state could not be determined. A consumer that read it as "this
repository had uncommitted changes" now sees true for more runs, correctly. Details in the
[API Contract Log](api_contract_log.md#version-4--2026-09-24-551).

## What it costs

The cost is the filesystem, not git — on this bridged mount one `stat()` costs milliseconds — and
it is paid once per process: every git read is cached per repository root, and only a capture that
finds code moved since the last one reads a repository again.

Measured 2026-09-24 on this tree, `/app` dirty with 81–82 uncommitted entries while other work was
in progress, each read in a fresh process:

| Read | `/app` | `user_algos/` |
|---|---|---|
| `git status` (with the assume-unchanged / skip-worktree listing) | 2.1–2.2 s | 0.19–0.21 s |
| the diff hash — every changed path read and hashed | 0.6 s | — |
| the patch of a dirty tree | 1.2 s | 0.19–0.20 s |

The `git status` is not new: the ledger used to pay it at the END of every run and now reuses the
one the capture paid at the START (`get_git_info()` at the ledger fell from 2.7 s to 0.3 s). What
is new is the diff hash and the patch, and only on a dirty tree: about 1.8 s on `/app` in the
state measured above. A clean repository pays neither. The package re-check at the live startup
guard reads one package directory per path component — milliseconds.

## Real orders from uncommitted code

**A session that would send real orders refuses to start while the code it would run is not
exactly one known commit.** The thirty-day live run is a parity proof: afterwards a backtest over
the same period is run and the divergence measured, and that backtest needs the code that ran. A
run from a dirty tree can be tied to its code only through its stored patch; a run from a tree git
cannot read cannot be tied to anything. `--allow-dirty` is the one way through, and it is recorded.

### Which sessions it guards

What decides is the **effective** `dry_run` — the merged value `AutotraderMain._is_dry_run()`
resolves from the broker's setting and the profile — never the profile field. Several real-money
profiles live outside `production/`, and a private profile may live outside every profile root, so
neither the file nor its folder can say whether money moves.

| Session | Guarded |
|---|---|
| live, effective `dry_run` false | yes |
| live, effective `dry_run` true — by the broker's setting or the profile's | no |
| mock (`adapter_type: mock`) | no — a mock session is always a dry run |
| simulation | no — it never sends an order |

When #304 replaces `dry_run` with `mode: live|paper`, the guard follows `mode: live`.

### What counts as uncommitted

The question is `CodeIdentity.is_dirty()`, asked of every repository the capture recorded
(`RepositoryState.is_committed()` per repository). The refusal shows each one on its own line:

| Repository state | Shown as | The way forward the message offers |
|---|---|---|
| exactly one commit | `clean` | — |
| modified, deleted, untracked or hidden files | `2 changes · untracked: my_bot/` | commit the changes |
| in no repository, or in a directory its repository ignores | `not under version control` | put it under version control and commit it |
| git did not run, refused the checkout, or could not read it | `state unknown (git unavailable or refused)` | make git able to read it: is git installed? on "dubious ownership", `git config --global --add safe.directory <root>` |
| no code identity captured | `state unknown (code identity not captured)` | find why in `autotrader_global.log` — in practice the session has already been refused at the start (above) |

An unknown state refuses on purpose. An identity that cannot be determined is not a clean one, and
a guard reading "clean" where git could not answer would pass exactly the run it exists to stop.

### The refusal

The guard runs in `_validate_startup()`, after the carry-over identity checks and before the
swap-mode check. That is after `setup_pipeline`, so a live start has already fetched its broker
configuration and warmup bars — but no order can have been sent. It is raised as
`UncommittedCodeError` and ends the session through the ordinary `STARTUP FAILED` path, exit
code 2, with the message as the emergency cause in the summary:

```
❌ STARTUP FAILED
  This session would send REAL ORDERS (dry_run resolves to false), but the code it
  would run is not committed:
    framework   /app          clean
    algo        user_algos/   2 changes · untracked: my_bot/
  A run from uncommitted code cannot be traced back to the code that ran — the parity
  backtest afterwards would compare against code that no longer exists.
  What to do:
    • commit the changes in user_algos/, then start again        ← the normal path
    • a deliberate real-money test from this tree:
        python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/production/my_bot_live.json --allow-dirty
      the run then stores its diff hash and patch and reports a Tier-1 warning
```

The command carries the profile the session was started with, so it can be copied as it stands.
The last line promises a patch only where `restorable` holds for every blocking repository.
Anywhere else it says which code nothing can restore afterwards, and why — no repository to diff
against, git could not read it, the patch could not be kept, or it cannot cover a nested
repository:

```
      the run then reports a Tier-1 warning — but nothing can restore the code afterwards in
      user_algos/ (its patch could not be kept)
```

### Code that changes while the session starts

The capture reads the packages before `setup_pipeline` loads the components, and the pipeline
then reads their files from disk again. An edit in between would run code the header does not
describe. So the guard re-reads every path component's package — a fresh read, deliberately
outside the per-process cache (`verify_component_digests`) — and compares it with the digest the
capture recorded:

| Session | A package moved |
|---|---|
| effective `dry_run` false | refused with `CodeChangedDuringStartupError` — `--allow-dirty` included, because the patch it records describes the code before the edit |
| dry run or mock | starts, with one WARNING in the session log and on the console, so the run's own report says its header does not describe the code that was loaded |

The refusal names each moved file and one way forward:

```
❌ STARTUP FAILED
  This session would send REAL ORDERS (dry_run resolves to false), but the code it
  loaded changed while it was starting — after its code identity was captured:
    decision   /app/user_algos/my_bot/my_strategy.py
  The run header's digests and patch describe the code as it was at the capture, not
  the code that was loaded, so this session could not be traced back to what it ran.
  --allow-dirty does not help here: the patch it records would be the wrong one.
  What to do:
    • finish the edits — nothing may write to the strategy while a session starts —
      then start again
```

A dry run is not refused because no money moves on code nobody can name; its record is still
wrong, and the WARNING is what puts that into the record rather than leaving it unnoticed.

The re-check covers components loaded from a path — an edited, deleted or added file of the
package alike, because it lists the package afresh as well as reading it. One window stays open:
this repository's own modules, CORE components included, are imported when the process starts,
before the capture reads the tree, so an edit in that window is what the header records while the
process runs the module it had already imported.

### `--allow-dirty` — the development path, deliberately not silent

Typed on a real-money start from uncommitted code, the flag lets the session through and leaves a
trace in every place a reader looks:

- the header records it: `origin.allow_dirty: true`;
- the patch of every dirty repository git could diff is already in `run_patches/`, stored by the
  capture, and the header's `patch_ref` points at it — with credential homes left out, as above;
- before the first order, one line in the session log and on the console names what is
  uncommitted and where its patch is, or why there is none:

  ```
  ⚠️  REAL ORDERS FROM UNCOMMITTED CODE (--allow-dirty): algo user_algos/ 2 changes · untracked: my_bot/ (patch run_patches/7d19….patch)
  ```

- after the run, the post-run validation reports a Tier-1 warning, check `uncommitted_code`,
  domain `setup` — so the run can never be read as a clean one in the report, in
  `io/warnings_errors.json`, or over the API.

The session-log line is INFO, not WARNING, for the reason the market-fit advisory gives: the
verdict travels as the Tier-1 finding, and a WARNING would put it in the report a second time as
an unadjudicated log line. The verdict is decided ONCE, by the guard at startup, and handed to
`SessionPostRunValidator` rather than derived again there.

A flag typed on a clean tree or on a dry run overrides nothing: no line, no warning. The header's
`origin.allow_dirty` still records that it was typed. The flag never lets through code that
changed while the session started (above).

Guard and messages: `python/framework/validators/uncommitted_code_validator.py`. Tests:
[AutoTrader config tests](../tests/autotrader/config_tests.md).
