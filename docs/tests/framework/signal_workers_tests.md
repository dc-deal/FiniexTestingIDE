# Signal Worker Tests Documentation

## Overview

The signal-worker suite validates the SIGNAL worker type (#141): the pydantic envelope types, the
JSONL loader, the `SignalDataProvider` lookup, the `CORE/llm_sentiment` worker, the orchestrator
dispatch, and the didactic `CORE/hybrid_sentiment_reference` decision fusion. All run against mock
fixtures via direct provider injection — no batch, no tick loop.

**Test Location:** `tests/framework/signal_workers/`

**Components Covered:**
- Types: `AnalysisEnvelope` / `SentimentResult` / `SignalSnapshot` / `SignalSeries` / `RunError`
- `SignalDataProvider` + `signal_jsonl_loader`
- `CORE/llm_sentiment` worker (`AbstractSignalWorker`)
- `CORE/hybrid_sentiment_reference` decision logic
- `WorkerOrchestrator` SIGNAL dispatch

**Total Tests:** 31

---

## Test Files

### test_signal_provider.py
- Pydantic parse of the archived line — **int-ms `collected_msc`** normalization to UTC datetime,
  extra-tolerant metadata.
- JSONL loader — load + sort + `schema_version` gate + range trim + `status: error` (empty result).
- Provider — nearest `collected_msc ≤ tick` (gap → None, boundary inclusive, defensive HOLD on an
  empty/error snapshot).

### test_llm_sentiment_worker.py
- Worker contract — SIGNAL type, output schema, no warmup, factory registration.
- `compute_signal` mapping — gap → empty / confidence 0, snapshot field mapping, staleness flag.
- `should_refresh` — cold start, same window, new window.
- Orchestrator dispatch + snapshot recompute cadence (recompute on a new window, cache between).

### test_hybrid_sentiment_decision.py
- Fusion — RSI core + sentiment overlay (aligned boost, opposed block, stale ignored).
- #425 subscription — declared sentiment signals exist on the worker output schema.
- Factory registration.

---

## Fixtures

`tests/fixtures/signals/sentiment_sample.jsonl` — int-ms `collected_msc`, covering
success / no-news / partial / error (empty result) / breaking paths.

---

## Running

```bash
python -m pytest tests/framework/signal_workers/ -v
```
Or via launch.json: `🧩 Pytest: Signal Workers (All)`.
