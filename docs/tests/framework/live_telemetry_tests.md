# Live Telemetry Tests

`tests/framework/live_telemetry/test_live_frame_serialization.py` — the live-telemetry frame
serializer (`python/framework/utils/live_frame_serialization_utils.py`, `frame_to_json`). The
encoder turns a live-telemetry frame (the throttled per-run feed behind the live console
dashboards) into a JSON-safe dict so the same model can later feed a viewer push transport
(#379/#380). All frames are built from real framework types so a structural drift fails the test.

| Test | Description |
|------|-------------|
| `test_frame_to_json_is_json_dumpable` | the sim progress, sim status and AutoTrader session frames encode to a `json.dumps`-able dict |
| `test_sim_core_and_enums_serialized` | identity/balances live under the shared `core` (not top-level); `ScenarioStatus` / `AwarenessLevel` enums become their string values |
| `test_status_frame_carries_no_progress` | `LiveStatusFrame` is the lean three-field shape (index, name, status), never a progress frame |
| `test_live_session_nested_lists_serialized` | live frame: `core` + nested position list with enum direction encode cleanly |
| `test_import_progress_frame_serialized` | the import's frame (#175): step status, outcome and log level become their values, the start time its isoformat |

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


---

## Dashboard Render

`tests/framework/live_telemetry/test_autotrader_dashboard_render.py` — every panel of the AutoTrader
dashboard, rendered through `AutoTraderLiveDisplay.render` from a fully populated frame into a
recording console: no live terminal, no tick source, no thread (#586).

It exists because the trade-history panel once read a field its row never had and ended the
display at the first close a strategy made itself — the ordinary exit of most strategies. Nothing
rendered it: every mock profile switches the display off, and a test built on a MagicMock frame
cannot fail on a missing field, because a mock invents every attribute it is asked for. The frame
here is built from the real types (`conftest.py`), and a test holds it full: every field differs
from its default, so a field added to the frame joins the renders.

| Group | What it pins |
|---|---|
| the frame is full | every field of `AutoTraderDisplayStats` is set to something a panel renders |
| every panel renders | each layout — one, two and three columns — shows each of its panels, a margin account's portfolio too, and the frame before the first snapshot; ages and uptime are measured against the frame's own clock and tick time; the Audit line takes the width it is given; an order partly filled shows `└─ filled/requested · PARTIAL` |
| the Reason column | every `CloseReason` × `CloseType` × size at entry (larger, equal, unknown) gives its marker — `partial`, `remain` or the reason itself — and a trade filled in parts on each side gets its lines |
| words are text | a venue's reason, the strategy's awareness, events and outputs, the producer's journal name and budget reason reach the screen as written, brackets included — a closing tag with no opener builds fine and fails only when **printed** |
| a panel that fails | it shows as failed in its place, the other panels render, and the failure is reported once with its traceback |
| the loop | a frame that cannot be printed is replaced by a failure frame and the loop goes on — the frame is proven printable before the live display holds it, never in a refresh thread of rich's own; the failure frame still names the deployment |
| the exporter | `AutotraderDisplayExporter.trade_history_entry` carries the size at entry, and every field of the row comes from the record — the record differs from the row's default in each of them, so a forgotten copy cannot pass |

The Reason column and the partial-fill row are tested at the widest two-column terminal (159
columns): narrower, the trade table's fixed columns are cut, which is a layout matter of its own.

---

## Simulation Progress Render

`tests/framework/live_telemetry/test_live_progress_render.py` — the batch's progress screen, driven
the way a batch drives it: frames on its queue, its own thread, start and stop, into a recording
terminal. Every status a scenario can be in renders at two widths, a status-only frame changes the
status, and a frame that cannot be applied is reported and skipped — the frame after it still
arrives. Before #586 such a frame was swallowed without a word. The strategy's awareness text and
the scenario's name reach the screen as written, brackets included. A screen that cannot be
printed never reaches the live display: it is proven first and replaced, so the report of the
failure — which the global logger prints through the display — and the batch's own logging keep
working; held, it made every print in every thread raise.

---

## Render Failure Reporter

`tests/framework/live_telemetry/test_render_failure_reporter.py` — the shared reporter behind every
live screen. A screen redraws several times a second, so one failure repeats on every frame: each
distinct failure is reported once with its traceback, one place reports only its share and says
so on the last, no sink reports nothing, and a sink that fails itself does not raise.
