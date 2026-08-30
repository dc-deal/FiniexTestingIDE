# Component Metadata

`ComponentMetadata` (`python/framework/types/component_metadata_types.py`) is an
author-declared metadata surface for workers and decision logics. It complements the
automatic `config_fingerprint` (a hash of the exact parameter set) with **semantic intent**:
a human-maintained version, a documentation pointer, and an advisory market/instrument fit.

> version = what the author means ("new state-machine logic, v0.2"); fingerprint = the exact
> params that produced a run. Together with git (the code commit) this is the standard quant
> provenance model (nautilus serializable config; MLflow run metadata).

## Interface

```python
@dataclass(frozen=True)
class ComponentMetadata:
    version: str = '0.0.0'
    doc_link: Optional[str] = None              # relative path to the component's main doc
    recommended_markets: tuple = ()             # advisory market types (forex, crypto, ...)
    recommended_instruments: tuple = ()         # advisory symbols (EURUSD, BTCUSD, ...)
```

`get_metadata() -> ComponentMetadata` is a classmethod hook on BOTH `AbstractWorker` and
`AbstractDecisionLogic`. The default is an empty `ComponentMetadata` (opt-in, no-op), but per
project convention it is **always maintained** for real components — see the project rules.

## Behavior

Two things happen with this metadata, and they are deliberately **separate functions** in
`framework/validators/component_metadata_advisory.py` — one is an observation, the other a
verdict, and merging them is what put a validator's judgement into the log pot for a while.

- **Version line — an OBSERVATION.** `surface_decision_logic_version` logs
  `🧬 Algo: <name> v<version> — <doc_link>` into the run's own log, at the place the logic is
  built: `process_startup_preparation` (sim subprocess) and `setup_pipeline` (AutoTrader).
  Nothing judges it; it puts a fact where a reader of that log needs it.
- **Market fit — a VERDICT.** `check_market_fit` RETURNS `ValidationFinding`s when
  `recommended_markets` / `recommended_instruments` are non-empty and the run's market type /
  symbol is not among them. It is **never a block** (severity WARNING, no scenario excluded):
  the HARD market-compatibility check (worker activity metric, see
  [market_capabilities.md](market_capabilities.md)) is what actually rejects incompatible
  combinations; this is the "this algo was not designed for here" nudge.

**Where the verdict is decided — before the run, in both pipelines.** Every input is static
config, and `get_metadata()` is a classmethod, so nothing needs to be instantiated and no
subprocess needs to start:

| | Decided in | Lands on |
|---|---|---|
| Simulation | `ScenarioValidator.validate_market_fit`, Phase 0 of the mount (after `BrokerDataPreparator` assigns `scenario.broker_type`) | `SingleScenario.validation_result` |
| AutoTrader | `AutotraderMain` at startup — a live operator must see it before the first trade | held, then `AutoTraderResult.session_validation_result` in `_collect_results` (the channel lives on the result) |

Both surface as **Tier-1** rows in the run report, carrying `check='market_fit'` and
`domain='algo'` — see [Warnings & Errors — Tier Taxonomy](warnings_errors_tiers.md). Live logs
the message at INFO as well, so it is visible at startup; deliberately not at WARNING, which
would put the same advisory in the report a second time as an unadjudicated pot line.

## Authoring

- Bump `version` when the component's LOGIC changes (not for param-only tuning — that is the
  fingerprint's job).
- Keep `doc_link` pointing at the component's main doc (relative path).
- Declare `recommended_markets` / `recommended_instruments` where the component is
  market/instrument-specific; leave empty for a generic component (empty = no warning).
- Workers typically carry `version` + `doc_link` only — a worker's market fit is its
  activity metric, so the recommended-market warning is driven by the decision logic.
