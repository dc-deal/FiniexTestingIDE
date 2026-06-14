# Diagnostics CSV Sink Tests

`tests/framework/test_diagnostics_csv_sink.py` — the generic strategy-owned diagnostics CSV
channel (`python/framework/reporting/diagnostics_csv_sink.py`) and its
`AbstractDecisionLogic` API. Runs under the synthetic `framework/_root` suite.

End-to-end pipeline coverage lives separately:
[`tests/autotrader/integration/test_diagnostics_sink_integration.py`](../autotrader/) proves
the AutoTrader pipeline flushes a real algo-declared sink to the run dir at session end.

**Total Tests:** 14

## TestDiagnosticsCsvSink (7 Tests) — file logistics

| Test | Description |
|------|-------------|
| `test_flush_writes_header_and_rows` | header = declared columns, one row per appended dict, in column order |
| `test_missing_key_renders_empty_cell` | a declared column missing from a row → empty cell |
| `test_extra_key_is_ignored` | keys not in the declared columns are dropped on flush |
| `test_scenario_suffix_in_filename` | `scenario_suffix` → `<name>_<suffix>.csv` |
| `test_noop_when_run_dir_none` | `run_dir=None` (file logging disabled) → no write, returns None |
| `test_noop_when_no_rows` | no appended rows → no file written |
| `test_get_name` | sink reports its name |

## TestDecisionLogicSinkApi (3 Tests) — AbstractDecisionLogic API

| Test | Description |
|------|-------------|
| `test_diagnostics_csv_get_or_create_same_instance` | same name → same sink instance |
| `test_distinct_names_distinct_sinks` | distinct names → distinct sinks, both listed |
| `test_no_sinks_by_default` | a logic that declares none has an empty sink list |

## TestFlushDecisionDiagnostics (4 Tests) — shared run-end flush helper

| Test | Description |
|------|-------------|
| `test_flushes_all_sinks_into_diagnostics_subdir` | every declared sink is written into the `diagnostics/` subfolder |
| `test_applies_scenario_suffix` | the sim per-scenario suffix is applied to each sink |
| `test_no_sinks_is_safe` | a logic with no sinks does not raise |
| `test_run_dir_none_is_safe` | disabled file logging does not raise, writes nothing |
