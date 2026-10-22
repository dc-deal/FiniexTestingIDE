# What Runs on This Machine — the Activity List

Several things run on one machine at once — a backtest, a parameter sweep, an AutoTrader session,
an import, a discovery cache build, the test suite, the API server — started from different
terminals, sometimes from different checkouts of the code. Nothing used to say which ones run.
When memory ran out, the kernel ended one of them, and afterwards nobody could tell which runs had
held what. Every long-running activity now enters itself into one list when it starts and leaves it
when it ends, and the list says what runs, who started it, from which code tree and commit, and how
much memory its processes hold.

Not here: the fields the API serves for the list (`docs/consumer/activities.md`, served at
`/api/v1/docs/activities`), the machine's own memory and processor load
(`utils/machine_resources_reader.py`), and what is still to come — progress, measured durations and
the time left, which follow in later slices of #603.

## One list per machine, outside every code tree

The list lives in one directory per machine, configured as `app_config.json::activities.directory`
and `~/.finiex/activities` by default. It is deliberately outside the code tree. A machine often
holds more than one copy of the code — a second checkout, a git worktree for parallel work, and on a
server one code tree per release tag — and a list kept inside a tree would split silently into one
list per tree: each would show only its own runs, and the memory the others hold would be invisible
to it. An absolute path stands for itself, so the list stays one per machine even where the rest of
an installation's data moves. The configuration refuses a relative path or an empty one: each
process would resolve it against its own working directory, and the one list would split.

`activities.session_env_var` names an environment variable whose value says which console session
an activity was started from, so two people or two parallel workers on one machine are told
apart. Empty, the default, records none.

## How an entry lives and dies

An entry is two files named by the activity's own id, `act_<UTC start>_<random half>`:

```
act_20261018_140211_3f9c1a7e.lock   taken first and held for the activity's life
act_20261018_140211_3f9c1a7e.json   written after the lock is held, in one atomic step
```

- **Running means the lock is held — nothing else.** The lock is a kernel lock (`flock`) on the open
  `.lock` file, held by the activity's process. The kernel releases it the moment the process ends,
  however it ends: a normal exit, an exception, `kill -9`. A file outlives a killed process and a
  process id is reused, so neither says whether an activity runs. Measured on 2026-10-14 (#598): the
  lock holds on this container's bridged disk as on its own, it takes 2.6 ms there against 0.02 ms,
  and it is free again at once after `kill -9`.
- **An entry is visible only while its lock is held.** The lock is taken before the entry is written,
  and at the end the entry goes first, then the lock file, then the lock.
- **A reader clears what a killed process left behind.** An entry whose lock is free belongs to an
  activity that ended without leaving. The reader removes it only when the lock is free AND the
  process the entry names — its id and its start time — is gone, so a mount or a container on which
  the lock cannot be seen does not empty the list. A lock file with no entry beside it is an activity
  in the moment between taking its lock and writing its entry, and the temporary file of an entry is
  one in the moment before its rename; both are removed only once they are older than a minute and
  free.
- **A reader touches only files that carry an activity id's prefix.** Every file of an entry begins
  with `act_`, and the reader takes nothing else — a directory that holds other files keeps them.
  The prefix is the whole rule: a file someone else names `act_….json` is read as an entry. An entry it cannot judge, because its lock cannot be asked about, is named as
  unreadable and left alone; a removal that fails is left for the next reading. Only a directory
  that cannot be listed fails a reading as a whole.
- **A forked child shares the lock.** A process the activity forks — a backtest's scenario
  workers — shares the open lock file, so there is one lock for all of them. When the activity
  ends, it releases the lock for every child at once. When it is killed, the lock stays held until
  its last child ends, and today a backtest's scenario workers do not end with a killed parent: the
  row stays listed, with no memory counted, until they are stopped. Making them end with their
  parent is scheduled for the next slice of #603. A program the activity starts as a new
  executable shares nothing.
- **Registering never ends an activity.** An entry that cannot be written costs a warning in the
  log the activity hands over — the global log where it hands none — and the activity runs on
  unlisted. That holds for a home directory that cannot be resolved too: the directory is resolved
  inside the guarded step. An observability fault must not stop a backtest, and must never end an
  unattended session with an exit code no supervisor restarts.

## Who enters itself

Each enters itself at the one place where its work begins and ends:

| Activity | Where | Also names |
|---|---|---|
| backtest | `BatchOrchestrator.run()` | its run id |
| sweep | `OptimizationRunner.run()` | its sweep id |
| AutoTrader session | `AutotraderMain.run()`, once its run id exists | its run id |
| import | `ImportCoordinator.run()` | — (its label is the import id its log is named by) |
| discovery cache build | `DiscoveryCacheManager.rebuild_all()` | — |
| test suite | the test runner's `run` command, and under it each suite it runs, named with its place in the run (`autotrader/integration (12/67)`) | — |
| API server | the API server's command, around the server | — |

An activity started inside another one in the same process — a sweep's combination, a cache build
an import runs — names its parent, and the list shows it under the parent. The API server is entered
by its command and never when the application object is built, because every test builds one.

Tests list what they start in a throwaway directory of their own, so a test run never appears in the
machine's list beyond the suite that started it.

## What an activity holds

Every process of the machine counts once, toward the nearest activity above it in the process tree:
a backtest's scenario workers toward the backtest, the test suite's pytest processes toward the
suite. Where several activities run in one process — a sweep and the backtest of its current
combination — the process belongs to the outermost of them; the inner one holds no process of its
own, and its memory is its parent's. So the rows add up without counting a process twice.

The figure is resident memory, and it is an upper bound: a forked child still shares pages with its
parent, and both count them. Measuring only what each process holds privately would sum correctly
but costs about fifteen times more per process — 0.49 s against 0.03 s over some 1,850 processes,
measured on 2026-10-19. One pass over the process table yields every process's parent and memory —
about 0.1 s with some 1,300 processes, measured the same day — and a reading never asks a process
twice. A process whose activity was killed and whose parent is gone is reparented, so it counts
toward no activity: the row of such an activity reads its memory as unknown.

## Reading the list

```
python python/cli/activity_cli.py list

  KIND                   WHAT                               SINCE (UTC) TREE             STARTED BY      MEMORY
  test suite             full                               14:35       .                cli             3.2 GB
  └ test suite           autotrader/integration (12/67)     14:52                        cli   (in test suite)
  sweep                  my_sweep                           14:02       release-v1.5.1   cli             7.1 GB
  └ backtest             my_set__sweep_20…200_ab12cd34_c003 14:31                        sweep      (in sweep)

  machine: 30.3 GB · 11.5 GB free (machine limit)
```

The newest activity stands first, each one nested under the activity it runs inside. `SINCE` is
the start in UTC, with its date when it lies on an earlier day; a label too long for its column
keeps its start and its end, where a sweep's combination names itself. `TREE` is `.` for the code
tree the command runs from and a directory name for any other.

`python python/cli/activity_cli.py check --min-free-gb 8` exits 0 when at least 8 GB are free, and
1 when not — and then prints who holds the memory, or why that could not be read. It exits 2 when
it gives no answer: a usage error, or a fault before the memory could be judged. It is the question
a wrapper script asks before it starts a run, so a list that cannot be read never changes the
answer. Free is the binding limit minus what is in use, the same reading the machine route serves;
a GB is 1024³ bytes, as everywhere here.

Over the API the same list is `GET /api/v1/activities`, behind the `machine` grant like
`/api/v1/machine-resources`: a row names scenario sets, profiles, run ids and command lines, which
nobody without a grant is shown. The route serves no path of the machine — a command line and a
tree are named relative to the code tree.

## Code

- `python/framework/utils/file_lock.py` — the kernel lock
- `python/framework/types/activity_registry_types.py` — the entry, its name rule and a reading
- `python/framework/activities/activity_registry.py` — entering and leaving
- `python/framework/activities/activity_registry_reader.py` — reading and clearing
- `python/framework/activities/activity_memory_attribution.py` — which process counts toward which
  activity
- `python/framework/activities/activity_snapshot.py` — one reading of the list and the machine, and
  the memory check
- `python/framework/utils/process_memory_reader.py` — one pass over the process table
- `python/framework/validators/free_memory_validator.py` — whether enough is free
- `python/framework/reporting/console/activity_list_summary.py` — the console list
- `python/cli/activity_cli.py` — `list` and `check`
