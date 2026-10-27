# Rendered Configuration Tests

`tests/framework/rendered_config/` — what a run records it RAN with, held to what it actually ran
with, in both pipelines (#547).

Run: `python -m pytest tests/framework/rendered_config/ -v`
Launch entry: `🧩 Pytest: Rendered Configuration (All)`

Architecture: [Data Storage Layout](../../architecture/data_storage_layout.md) — the frozen kinds of
the run-config store.

---

## What this suite exists to catch

A run freezes its rendered configuration at its start: a session its rendered profile, a backtest
its rendered scenario set. Nothing reads the document back into a run, so a document that drifted
from what the run was built with would go unnoticed — and the comparison of a live session with
its backtest would compare a record with something else.

| Failure | What it costs |
|---|---|
| A block the backtest's subprocess reads differs from the document — a default written twice, a fallback of its own | The record states a value the run never used |
| A strategy component given as a file is recorded as the absolute path one machine resolved | One strategy reads differently on every machine and in every release tree, so a session never matches its backtest |
| A session and a backtest given one strategy render different strategy sections | The two pipelines cannot be compared at all |
| Rendering or freezing fails and takes the run with it | A record keeper breaks the thing it records |

Not here: the session's own document and its start-time check, which
`tests/autotrader/config/test_rendered_profile.py` covers; the run-config store itself, which
`tests/framework/store/` covers.

## The tests

`test_strategy_config_renderer.py` — the one strategy renderer both pipelines use:

- a file logic keeps its configured relative path, a `CORE/` worker its name
- the factory still injects the path it resolved, which performance tracking reads
- the same strategy renders alike from another working directory, with a copy of its file there
- the session's start-time check passes for a file logic built with its resolved path, because it
  projects the built side the way the renderer does

`test_rendered_scenario_set.py` — the backtest's document:

- **Every layer is in it:** a default no file sets, the broker's market entry and market rules, the
  run-level `backtesting` settings and history limits; the same set renders the same document.
- **The record is what the run reads**, for every shipped scenario set the loader accepts: each
  scenario's execution, simulator, order-guard and stress blocks, its market model and its history
  limit equal what `ProcessScenarioConfig.from_scenario` gives its subprocess, and the strategy
  matches the components the factories build. This is the backtest's counterpart of the session's
  start-time check, held as a test: both sides use one helper per block, so a difference can only
  be a defect, and it shows when the code changes.
- **The header names it:** a run freezes its document and names it beside `config_id`; a sweep
  combination with a changed parameter names a document of its own holding that value; a mount
  build renders nothing.
- **The run never fails for it:** a document that cannot be rendered, or a store that cannot be
  written, leaves `rendered_config_id` empty and the run goes on.
- **One strategy reads alike in both pipelines:** a mock profile's strategy, run as a backtest,
  records the same strategy section as the session does — the precondition for matching a live
  session with its backtest.

Every run the suite builds lands in the suite's isolated run tree and run-config store.
