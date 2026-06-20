# Live Telemetry Tests

`tests/framework/live_telemetry/test_live_frame_serialization.py` — the live-telemetry frame
serializer (`python/framework/utils/live_frame_serialization_utils.py`, `frame_to_json`). The
encoder turns a live-telemetry frame (the throttled per-run feed behind the live console
dashboards) into a JSON-safe dict so the same model can later feed a viewer push transport
(#379/#380). All frames are built from real framework types so a structural drift fails the test.

**Total Tests:** 4

| Test | Description |
|------|-------------|
| `test_frame_to_json_is_json_dumpable` | all three frame kinds (sim progress, sim status, live session) encode to a `json.dumps`-able dict |
| `test_sim_core_and_enums_serialized` | identity/balances live under the shared `core` (not top-level); `ScenarioStatus` / `AwarenessLevel` enums become their string values |
| `test_status_frame_carries_no_progress` | `LiveStatusFrame` is the lean three-field shape (index, name, status), never a progress frame |
| `test_live_session_nested_lists_serialized` | live frame: `core` + nested position list with enum direction encode cleanly |
