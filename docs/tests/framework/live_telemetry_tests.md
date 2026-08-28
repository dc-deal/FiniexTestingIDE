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

---

## Signal Transport Panel

`tests/framework/live_telemetry/test_signal_transport_panel.py` — the signal-transport block in the
operator's CONNECTION panel (#141 Part 2a).

It exists because on an unattended multi-week run **a dead feed and a quiet market look identical on
screen**: the signal values keep displaying their last known state either way, so only the transport
can say whether anything still arrives. The distinction the block draws, and the reason it is not
merged into the existing feed line:

| | answers |
|---|---|
| `feed_stale` (#434) | is the signal **old**? |
| transport state | is anything still **arriving**? |

A healthy transport with a stale signal is a quiet producer. A dead transport with a fresh signal is
a session about to go blind without noticing.

**Total Tests:** 24

| Group | What it pins |
|---|---|
| mounted session | renders `mounted (no transport)` instead of an idle connection that was never meant to run; no session data renders nothing at all |
| live transport | position (`epoch`/`seq`) and envelope age; `awaiting first envelope` before the first arrival, never a bare `None` |
| the tape | newest first; hidden events are **counted**, not silently dropped |
| trouble | degraded producer, transport errors and **contract errors** are visible and counted apart — a transport error means nothing answered, a contract error means they answered and we could not read it, and the line used to be gated on the pull path's counters alone, which the stream never sets; the stream's terminal states (`cursor_ahead`, `misconfigured`) render red rather than dim, because both stop the feed and need a human; **a healthy transport shows no issue line** — noise in the quiet case is how a panel stops being read |
| journal identity | the producer's `journal_id` is shown with its name beside it, never the name alone; an unresolved name is **not** an alarm while an unidentified journal is; a mid-session change is marked; no probe renders no line at all |
| producer budget | a suspended producer is named with its reason; a healthy one adds no line at all |
| age rendering | the unit scales with the magnitude (`42s` / `2m` / `2.0h`) |

**Why it is tested rather than eyeballed:** the panel is read exactly when something is wrong, and a
panel that answers a question it was not asked is worse than none. The tape's own label is a case in
point — it originally rendered a pass's `trigger_reason` as a bare `breaking`, next to a worker
reporting `is_breaking: False` for the traded symbol. Both values were correct; the rendering
conflated a **pass property** with a **row verdict**. It now reads `seq N · breaking pass`.

The journal line is the same kind of distinction one level up. `Journal: 9c3fa4c80d95 (dev)` shows
the fingerprint of the producer's store *and* the label its own machine maps that fingerprint to.
Only the first binds: the label lives in a per-machine config on the producer side and can be
renamed, so a panel showing the name alone would show a claim. The tests pin that the id survives an
unresolved name, and that `⚠ unidentified` — the producer naming no journal at all — is rendered as
the different thing it is: not a probe that has not run, but a session nothing can certify.

