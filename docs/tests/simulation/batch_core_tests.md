# Batch Core Tests

Validates the `BatchOrchestrator` prepare/execute seam (#417): the validate → prepare_mount → execute
split is behavior-preserving, and the prepared `MountPackage` is reusable and deterministic.

**Location:** `tests/simulation/core/`
**Marks:** `simulation`
**Config:** `configs/scenario_sets/backtesting/backtesting_validation_test.json` (the small deterministic
validation set) — the integration tests run a real batch; the data-identity test is pure / data-independent.

System doc: [Process Execution & Subprocess Architecture](../../process_execution_guide.md) — the
*Mountable Preparation (#417)* section.

---

## Test Files

| File | What it proves |
|---|---|
| `test_mountable_prepare.py` | **split equivalence** (`run()` == validate + `prepare_mount()` + `execute()`) · **reuse / determinism** (one `MountPackage`, `execute()` twice → identical results, #368) · **data identity** (`DataIdentityKey` ignores `strategy_config`, changes with the data window) · **identity guard** (`execute()` raises `MountIdentityMismatchError` when fed scenarios whose data identity does not match the mount) |

---

## Why a dedicated suite

`tests/simulation/core/` is the home for batch-orchestrator-domain tests — the prepare/execute seam, the
determinism contract, and the data identity. The existing `tests/simulation/baseline/` end-to-end
orchestrator checks (tick count, P&L, warmup, latency determinism, …) are candidates to migrate here as a
separate refactor (#421).
