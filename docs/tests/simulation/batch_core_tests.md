# Batch Core Tests

Validates the `BatchOrchestrator` prepare/execute seam (#417): the validate → prepare_mount → execute
split is behavior-preserving, and the prepared `MountPackage` is reusable and deterministic. Plus the
batch **run-outcome contract** (#372) — the grading a supervisor reads as the process exit code.

**Location:** `tests/simulation/core/`
**Marks:** `simulation`
**Config:** `configs/scenario_sets/backtesting/backtesting_validation_test.json` (the small deterministic
validation set) — the integration tests run a real batch; the data-identity and exit-code tests are
pure / data-independent.

System doc: [Process Execution & Subprocess Architecture](../../process_execution_guide.md) — the
*Mountable Preparation (#417)* section.

---

## Test Files

### `test_ghost_pass_booking_boundary.py`

The trading-day boundary inside the simulation's ghost passes (#537 / #539 audit). A quiet
stretch between two data ticks is crossed by ghost passes, and they are the only thing that
advances the canonical clock there — so the boundary is checked at EACH ghost instant. Checked
once before them it ran on the clock the previous tick had already checked, which is no check at
all, and a fill resolved in a ghost pass after a rollover was booked into the day before it.

These pin the CALL, not the seal: whether a day flip seals is the recorder's own question and is
tested there. What only this level can show is WHEN the loop asks — one check per ghost pass,
each carrying that ghost's own instant, and BEFORE the resolutions of that instant, because the
period window is end-exclusive and the order of those two calls decides which day a fill lands
in. Plus the two cases where nothing is asked: a gap too short for a ghost, and a gap past the
#208 correctness threshold.


| File | What it proves |
|---|---|
| `test_mountable_prepare.py` | **split equivalence** (`run()` == validate + `prepare_mount()` + `execute()`) · **reuse / determinism** (one `MountPackage`, `execute()` twice → identical results, #368) · **data identity** (`DataIdentityKey` ignores `strategy_config`, changes with the data window) · **identity guard** (`execute()` raises `MountIdentityMismatchError` when fed scenarios whose data identity does not match the mount) |
| `test_batch_exit_code.py` | **the batch outcome reaches the process** (#372): all scenarios completed → `0` · a crashed scenario → `2` · only `LoggedErrors` failures → `3` · a crash outranks logged errors · an empty batch is not a success → `1` |
| `test_pipeline_drawdown_isolation.py` | **a backtest never inherits a live curve** (#497): the reported drawdown survives a restart in the AutoTrader through the cold-start carry-over, and the simulation shares the same `PortfolioManager` — so this pins that neither `restore_drawdown_state` nor the carry-over store is reachable from `framework/process/` or `framework/batch/`. A caller check rather than a behavioural one, because the failure is silent: two runs over identical data would simply report different drawdowns |
| `test_config_snapshot.py` | **every executing run carries its config** (#475 prework): a run started the way the benchmark harness starts one — `ScenarioSet` + orchestrator, without the CLI wrapper — gets `scenario_config.json`, and the snapshot is the config that actually ran. The call lives in `BatchOrchestrator.run()` because that is where every consumer passes; when only the CLI took it, the release-benchmark runs were the ones without one |

---

## Why a dedicated suite

`tests/simulation/core/` is the home for batch-orchestrator-domain tests — the prepare/execute seam, the
determinism contract, and the data identity. The existing `tests/simulation/baseline/` end-to-end
orchestrator checks (tick count, P&L, warmup, latency determinism, …) are candidates to migrate here as a
separate refactor (#421).
