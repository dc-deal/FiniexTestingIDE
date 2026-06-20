"""
Tests for the live-telemetry frame serializer (#400).

Verifies frame_to_json produces a JSON-safe dict (enums -> values, datetime ->
isoformat, nested dataclasses -> dicts) for all three frame kinds, built from
REAL framework types (no stand-ins) so a structural drift fails the test.
"""

import json
from datetime import datetime, timezone

from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    PositionSnapshot,
)
from python.framework.types.decision_logic_types import (
    AwarenessLevel,
    DecisionAwareness,
    DecisionLogicAction,
)
from python.framework.types.live_types.live_core_snapshot_types import LiveCoreSnapshot
from python.framework.types.live_types.live_scenario_stats_types import (
    LiveScenarioStats,
    LiveStatusFrame,
)
from python.framework.types.live_types.live_stats_config_types import ScenarioStatus
from python.framework.types.trading_env_types.order_types import OrderDirection
from python.framework.utils.live_frame_serialization_utils import frame_to_json


def _sim_progress_frame() -> LiveScenarioStats:
    return LiveScenarioStats(
        core=LiveCoreSnapshot(
            symbol='EURUSD',
            ticks_processed=120,
            balance=10100.0,
            initial_balance=10000.0,
            total_trades=3,
            winning_trades=2,
            losing_trades=1,
            last_awareness=DecisionAwareness(
                message='tunnel armed', level=AwarenessLevel.NOTICE),
        ),
        scenario_name='s0',
        scenario_index=0,
        total_ticks=500,
        progress_percent=24.0,
        status=ScenarioStatus.RUNNING,
        current_tick_time=datetime.now(timezone.utc).isoformat(),
    )


def _live_session_frame() -> AutoTraderDisplayStats:
    return AutoTraderDisplayStats(
        core=LiveCoreSnapshot(symbol='BTCUSD', balance=250.0, initial_balance=250.0),
        session_start=datetime.now(timezone.utc),
        dry_run=True,
        broker_type='kraken_spot',
        last_decision_action=DecisionLogicAction.BUY,
        last_tick_time=datetime.now(timezone.utc),
        open_positions=[PositionSnapshot(
            position_id='p1', symbol='BTCUSD', direction=OrderDirection.LONG,
            lots=0.001, entry_price=60000.0, unrealized_pnl=1.5)],
    )


def test_frame_to_json_is_json_dumpable():
    """All three frame kinds encode to a json.dumps-able dict."""
    frames = [
        _sim_progress_frame(),
        LiveStatusFrame(scenario_index=0, scenario_name='s0',
                        status=ScenarioStatus.WARMUP_DATA_TICKS),
        _live_session_frame(),
    ]
    for frame in frames:
        # Must not raise — proves enums/datetimes/nested dataclasses are leaves
        json.dumps(frame_to_json(frame))


def test_sim_core_and_enums_serialized():
    """Shared core is a nested dict; enums become their string values."""
    encoded = frame_to_json(_sim_progress_frame())
    # Identity + balances live under the shared core, not at the top level
    assert 'symbol' not in encoded
    assert encoded['core']['symbol'] == 'EURUSD'
    assert encoded['core']['balance'] == 10100.0
    assert encoded['status'] == 'running'                            # ScenarioStatus -> value
    assert encoded['core']['last_awareness']['level'] == 'notice'    # AwarenessLevel -> value
    assert encoded['core']['last_awareness']['message'] == 'tunnel armed'


def test_status_frame_carries_no_progress():
    """A status frame is the lean three-field shape, not a progress frame."""
    encoded = frame_to_json(LiveStatusFrame(
        scenario_index=2, scenario_name='s2', status=ScenarioStatus.WARMUP_TRADER))
    assert encoded == {
        'scenario_index': 2,
        'scenario_name': 's2',
        'status': 'warmup_trader',
    }


def test_live_session_nested_lists_serialized():
    """Live frame: core + nested position list with enum direction encode cleanly."""
    encoded = frame_to_json(_live_session_frame())
    assert encoded['core']['symbol'] == 'BTCUSD'
    assert encoded['last_decision_action'] == 'BUY'              # DecisionLogicAction -> value
    assert encoded['open_positions'][0]['direction'] == 'long'  # OrderDirection -> value
    json.dumps(encoded)
