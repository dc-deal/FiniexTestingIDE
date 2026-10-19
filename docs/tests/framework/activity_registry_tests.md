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
at once do not see each other as a holder. A holder in another process is seen, and `kill -9` frees
the lock at once.

## `test_activity_registry.py`

- **Listed while it runs:** an entry is listed with its lock held and every field it was given; the
  end of the block removes both files, after an exception too; two processes' activities are both
  listed; an activity started inside another names it as its parent.
- **Cleared when killed, and only then:** a holder killed with `kill -9` is gone at the next reading
  with its files; an entry whose lock is free but whose process lives is kept — a mount on which the
  lock cannot be seen must not empty the list; one whose process is gone is removed; a lock file
  without its entry stays while it is fresh and is removed once old and free, never while held; a
  held entry this code cannot read is named unreadable; a missing directory is an empty list.
- **Registering never ends the activity:** an unwritable registry costs one warning, and the work
  runs.
- **The snapshot:** the process holding a sweep counts toward the sweep, the backtest nested in it
  holds nothing of its own, and the machine's memory is read beside it.

## `test_activity_memory_attribution.py`

Over a synthetic process table: an activity holds its process and every descendant; a child
activity in its own process takes that process; an activity nested in its parent's process holds
nothing; an activity whose process is gone, or one of whose processes cannot be read, is `None` —
never zero.

## `test_free_memory_check.py`

The verdict — free is the limit minus what is used, exactly enough is enough — and the command's
exit codes: 0 when enough is free, 1 when not, with the list of who holds the memory.

## `test_activity_entry_points.py`

A witness wraps the registry and reads the list at the moment an entry point registers. A real
two-combination sweep lists the sweep and its backtests one under the other, each with its origin; a
real AutoTrader session refused at its start is listed under its run id and leaves the list. The
import, the cache rebuild, the test suite and the API server are checked with their work replaced by
a stand-in, because the work itself is not under test here. After each, the list is empty again.
