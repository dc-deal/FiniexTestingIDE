# Activity List Tests

`tests/framework/activities/` — the list of what runs on this machine (#603): the kernel lock it
rests on, entering and leaving it, clearing what a killed process left behind, counting each
process's memory once, the memory check a wrapper script asks, and the entry points that enter
themselves.

Run: `python -m pytest tests/framework/activities/ -v`
Launch entry: `🧩 Pytest: Activity List (All)`

Architecture: [What Runs on This Machine](../../architecture/activity_registry.md). The route that
serves the list is tested with the API: `tests/framework/api/test_activities_route.py`
([API Endpoint Tests](api_endpoint_tests.md)).

---

## What this suite exists to catch

The list is only worth reading if it is neither stale nor incomplete, and both failures look like
a correct list from the outside: an entry left by a killed process reads exactly like a running
activity, and an entry point that forgot to register leaves a list that is merely shorter.

Every test writes into a registry directory of its own (`registry_dir`), so no test sees another's
entries — the session-wide isolation in `tests/conftest.py` keeps them out of the machine's real
list on top of that.

---

## `test_file_lock.py`

The kernel lock: held while its holder lives, refused to a second taker, free after a release and a
second release harmless. Asking whether it is held never creates the file, and two readers asking
at once do not see each other as a holder. A lock the mount refuses (no locks on it) raises and
leaves no descriptor open. A holder in another process is seen, and `kill -9` frees the lock at
once.

## `test_activity_registry.py`

- **Listed while it runs:** an entry is listed with its lock held and every field it was given; the
  end of the block removes both files, after an exception too; two processes' activities are both
  listed; an activity started inside another names it as its parent.
- **Cleared when killed, and only then:** a holder killed with `kill -9` is gone at the next reading
  with its files; an entry whose lock is free but whose process lives is kept — a mount on which the
  lock cannot be seen must not empty the list; one whose process is gone is removed; a lock file
  without its entry stays while it is fresh and is removed once old and free, never while held; a
  held entry this code cannot read is named unreadable; a missing directory is an empty list.
- **A reading touches only its own files:** files the registry did not name survive a reading; an
  old temporary entry file is removed and a fresh one stays; a path that is no directory cannot be
  read.
- **One entry never fails the reading:** a held entry that is no object, or one without an offset,
  is named unreadable while the rest is listed; an entry whose lock cannot be asked about is named
  and kept; a removal that fails is left for the next reading.
- **Registering never ends the activity:** an unwritable registry costs one warning, and the work
  runs; so does a directory that cannot be resolved, which the reader refuses as unreadable; a stop
  between the lock and the entry leaves nothing behind; a lock that cannot be released costs a
  warning.
- **The configured directory stands for itself:** an empty or relative one is refused, an absolute
  one or one at the home directory is taken.
- **The snapshot:** the process holding a sweep counts toward the sweep, the backtest nested in it
  holds nothing of its own, and the machine's memory is read beside it.

## `test_activity_memory_attribution.py`

Over a synthetic process table: an activity holds its process and every descendant; a child
activity in its own process takes that process; an activity nested in its parent's process holds
nothing; an activity whose process is gone, or one of whose processes cannot be read, is `None` —
never zero.

## `test_free_memory_check.py`

The verdict — free is the limit minus what is used, never below zero, and exactly enough is enough
— and the command's exit codes: 0 when enough is free, answered without reading the list; 1 when
not, with the list of who holds the memory or why it could not be read, which never changes the
answer; 2 when there is no answer. A size that is no size is refused. The console list shows a
start on an earlier day with its date, and a long label keeps its end.

## `test_activity_entry_points.py`

A witness wraps the registry and reads the list at the moment an entry point registers. A real sweep
over the two-by-two mini grid lists the sweep and its backtests one under the other, each with its
origin; a real AutoTrader session refused at its start is listed under its run id, by the name every
report gives it, and leaves the list. The
import, the cache rebuild, the test suite and the API server are checked with their work replaced by
a stand-in, because the work itself is not under test here. After each, the list is empty again.
