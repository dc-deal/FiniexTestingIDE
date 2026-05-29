# Decision Event Channel Tests (#348)

Validates the [Decision Event Channel](../../architecture/decision_event_channel.md)
end-to-end. Two layers: an isolated dispatcher unit test, and a **dual-world
parity** proof that the channel delivers the identical event sequence through the
simulation and AutoTrader-mock pipelines.

## What Is Tested

### Dispatcher unit (isolated)

`tests/autotrader/live_executor/test_decision_event_dispatcher.py` — with a minimal
fake executor + recording logic (no broker, no worker thread):

- `create_if_subscribed` returns `None` when the logic subscribes to nothing (zero overhead)
- EXECUTED outcome → `ORDER_FILLED`; REJECTED outcome → `ORDER_REJECTED`
- Unsubscribed event types are filtered out
- `drain()` delivers buffered events in FIFO order
- Re-entrancy: an event emitted from inside a hook lands in the **next** drain

### Dual-world parity (full pipeline)

A dedicated decision logic, `BacktestingEventProbe`
(`CORE/backtesting/backtesting_event_probe`), runs the same deterministic plan in
both pipelines: open a MARKET position → partial-close it → `request_session_end`.
It subscribes to every event and records the ordered sequence it receives. Both
worlds must produce:

```
['order_filled', 'partial_close', 'session_end']
```

| Pipeline | Test | Event log source |
|---|---|---|
| Simulation | `tests/simulation/event_channel/test_event_channel_sim.py` | `BacktestingMetadata.received_events` (cross-process) |
| AutoTrader-mock | `tests/autotrader/integration/test_event_channel_live_pipeline.py` | `decision_logic.get_received_event_log()` (in-process) |

The AutoTrader-mock test also exercises `request_session_end` end-to-end — the bot
ends the session itself (no operator Ctrl+C), and `SESSION_END` is the last event
delivered before teardown.

## Fixtures

- Simulation scenario set: `configs/scenario_sets/backtesting/event_channel_test.json`
- AutoTrader-mock profile: `configs/autotrader_profiles/backtesting/event_channel_lifecycle.json`

Both reuse the USDJPY mt5 tick data of the partial-close suites; only the decision
logic and its plan differ.

## Running

```
# Dual-world (sim)
pytest tests/simulation/event_channel/ -v

# Dual-world (AutoTrader-mock)
pytest tests/autotrader/integration/test_event_channel_live_pipeline.py -v

# Dispatcher unit
pytest tests/autotrader/live_executor/test_decision_event_dispatcher.py -v
```

VS Code launch entries: `🧩 Pytest: Event Channel (Sim)`,
`🧩 Pytest: Decision Event Channel (#348)`, and the `🧪 Simulation: Event Channel`
/ `🧪 AutoTrader: Event Channel` runners for full-log inspection.
