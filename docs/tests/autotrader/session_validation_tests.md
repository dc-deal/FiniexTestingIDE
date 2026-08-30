# Session Validation Test Suite

Pins the AutoTrader's **post-run validation channel** — the live counterpart of the sim's
`PostRunValidator`.

## What this suite is for

A live session used to have no Tier-1 channel: `WarningsErrorsReport` could carry the log pot and
nothing else. The visible failure was the stress-test warning. The project rule states, without
naming a pipeline:

> Every active stress config surfaces as a Tier-1 warning, so a stressed run can never be
> mistaken for a clean one.

Two live profiles (`sentiment_outage_test.json`, `market_data_outage_test.json`) carry an active
`stale_data_stress` — and produced no such warning, because no live code could produce one. A
stressed session was indistinguishable from a clean one, in the console, in
`io/warnings_errors.json`, and over the API.

## Layout

| Class | What it pins |
|---|---|
| `TestTheStressWarningReachesALiveSession` | an active config is flagged, a disabled one is not; the message says `Session (1)`, never the sim's `Scenarios`; a real live session (no mock-replay settings at all) is not a finding |
| `TestSlowComponentsAreFlagged` | the sim threshold applied to the session's own worker / decision statistics; an empty session reports nothing |
| `TestTheChannelReachesTheReport` | a finding becomes a Tier-1 `WarningRow` carrying `check` / `domain` / `scope`; the log pot still arrives as Tier 2 with both empty; a clean session reports nothing |
| `TestTheSharedChecksProduceOneFormula` | sim and live differ only in the unit label, and the threshold is exclusive at the boundary |

The last class is the one worth knowing about. The two checks are **shared**
(`python/framework/validators/shared_advisory_checks.py`), not copied — a copy would drift
silently, and these tests are what makes the sharing observable from the live side. The sim side
pins the same formulas from the other end in
[Post-Run Validator tests](../framework/batch_validations_tests.md).

## The end-to-end half lives elsewhere

This suite builds results directly. That the validator is actually WIRED into a session is proven
by a real run, in
[`tests/autotrader/integration/test_market_data_outage.py`](integration_tests.md) —
`test_the_stress_config_reaches_the_session_validation_channel` asserts on the channel of a
session that really executed a stressed profile. A unit test cannot show that the call site
exists.

## Running

```bash
pytest tests/autotrader/session_validation/ -v --tb=short
```

VS Code: **"🧩 Pytest: Session Validation (All)"** launch configuration.

## Related

- The taxonomy this implements: [Warnings & Errors — Tier Taxonomy](../../architecture/warnings_errors_tiers.md)
- Where the rows are rendered and served: [Reporting Pipeline](../../architecture/reporting_pipeline.md)
- The live pipeline: [AutoTrader Architecture](../../autotrader/autotrader_architecture.md)
