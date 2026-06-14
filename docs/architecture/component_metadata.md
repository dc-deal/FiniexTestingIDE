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

- **Version line at run start** (both pipelines): `surface_decision_logic_metadata`
  (`framework/validators/component_metadata_advisory.py`) logs
  `🧬 Algo: <name> v<version> — <doc_link>` at startup — sim subprocess (`process_main`) and
  AutoTrader session (`autotrader_main`).
- **Soft market-fit warning** (both pipelines): if a decision logic's `recommended_markets` /
  `recommended_instruments` are non-empty and the run's market type / symbol is not among
  them, a WARNING is emitted into the warnings channel (§35) — **never a block**. It is
  advisory: the HARD market-compatibility check (worker activity metric, see
  [market_capabilities.md](market_capabilities.md)) is what actually rejects incompatible
  combinations; this is the "this algo was not designed for here" nudge.

## Authoring

- Bump `version` when the component's LOGIC changes (not for param-only tuning — that is the
  fingerprint's job).
- Keep `doc_link` pointing at the component's main doc (relative path).
- Declare `recommended_markets` / `recommended_instruments` where the component is
  market/instrument-specific; leave empty for a generic component (empty = no warning).
- Workers typically carry `version` + `doc_link` only — a worker's market fit is its
  activity metric, so the recommended-market warning is driven by the decision logic.
