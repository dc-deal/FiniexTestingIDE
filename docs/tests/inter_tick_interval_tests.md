# Inter-Tick Interval Tests

Tests for the inter-tick interval profiling system — measures market-side time gaps between consecutive ticks.

## Run

```bash
pytest tests/inter_tick_interval/ -v
```

## What It Tests

| Area | Description |
|------|-------------|
| Stats computation | Known interval distributions produce correct min/max/mean/median/P5/P95 |
| Gap filtering | Session/weekend gaps exceeding threshold are excluded from stats |
| Edge cases | Empty intervals, single tick, all-gaps-filtered scenarios return `None` |
| `from_dicts()` integration | Interval data flows correctly through `ProfilingData.from_dicts()` |
| Timestamp collection | Known tick timestamps produce expected interval values in ms |
| Backward compatibility | `from_dicts()` without interval args works unchanged |

## Dependencies

None — pure mock-based, no data files or external services required.

## Key Types

- `InterTickIntervalStats` — distribution stats dataclass (min/max/mean/median/P5/P95 + gap info)
- `ProfilingData._compute_interval_stats()` — static method that filters gaps and computes stats via numpy

## Configuration

Gap filtering threshold is configured per market type in `configs/market_config.json`:

```json
"market_rules": {
    "forex": { "inter_tick_gap_threshold_s": 300 },
    "crypto": { "inter_tick_gap_threshold_s": 600 }
}
```
