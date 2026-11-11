"""
A session and a backtest the divergence gate lets through (#512), for tests to vary one thing
of.

Every value is invented; the commits and digests only have to agree where the gate compares them.
"""

import copy
import dataclasses
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from python.framework.types.api.divergence_report_types import CompareWindow, WindowEndCause
from python.framework.types.api.report_types import DataWindow, RunResultRow
from python.framework.types.divergence_capture_types import (
    BacktestRunCapture,
    BacktestScenarioCapture,
    LiveSessionCapture,
)
from python.framework.types.run_origin_types import (
    CodeIdentity,
    ComponentIdentity,
    ComponentRole,
    RepositoryState,
)
from tests.shared.divergence_live_chain import FIRST_DAY, FIRST_TICK, days_from, session

RELEASE = '1.5.0'
SESSION_COMMIT = 'a1b2c3d4e5f6'
BACKTEST_COMMIT = 'f6e5d4c3b2a1'
STRATEGY_ROOT_LIVE = '/srv/bot/my_strategies'
STRATEGY_ROOT_HERE = '/work/user_algos/my_strategies'
STRATEGY_COMMIT = '0a1b2c3d4e5f'
DECISION_DIGEST = 'd' * 64
WORKER_DIGEST = 'e' * 64
BROKER_CONFIG = 'broker_1'
SCENARIO = 'my_recorded_window'
WINDOW_END = FIRST_TICK.replace(hour=0, minute=0) + timedelta(days=30)
# A strategy as the renderer writes it: every default filled in, each component named the way its
# configuration names it
RENDERED_STRATEGY: Dict[str, Any] = {
    'decision_logic_type': 'my_algo/decision.py',
    'decision_logic_config': {'decision_logic_type': 'my_algo/decision.py', 'threshold': 0.4,
                              'levels': [1, 2, 3]},
    'worker_instances': {'my_worker': 'my_algo/worker.py'},
    'workers': {'my_worker': {'worker_type': 'my_algo/worker.py', 'period': 20}},
}


def code_identity(framework_commit: str = SESSION_COMMIT, framework_dirty: bool = False,
                  strategy_root: str = STRATEGY_ROOT_LIVE,
                  strategy_commit: str = STRATEGY_COMMIT,
                  decision_digest: Optional[str] = DECISION_DIGEST) -> CodeIdentity:
    """A code identity of the framework, one strategy repository and two components."""
    return CodeIdentity(
        framework=RepositoryState(root='/framework', commit=framework_commit,
                                  dirty=framework_dirty,
                                  uncommitted_count=1 if framework_dirty else 0),
        repositories=[RepositoryState(root=strategy_root, commit=strategy_commit)],
        components=[
            ComponentIdentity(role=ComponentRole.DECISION, name='my_algo/decision.py',
                              type='my_algo/decision.py', repository=strategy_root,
                              package_digest=decision_digest),
            ComponentIdentity(role=ComponentRole.WORKER, name='my_worker',
                              type='my_algo/worker.py', repository=strategy_root,
                              package_digest=WORKER_DIGEST)])


def gate_session(run_id: str = 's1', release: Optional[str] = RELEASE,
                 identity: Optional[CodeIdentity] = None,
                 strategy: Optional[Dict[str, Any]] = None,
                 broker_config_id: str = BROKER_CONFIG) -> LiveSessionCapture:
    """A booked session of the window, with what the gate reads of it."""
    return dataclasses.replace(
        session(run_id, FIRST_TICK, days_from(FIRST_DAY, 3)),
        app_version=release, broker_config_id=broker_config_id,
        rendered_strategy_config=copy.deepcopy(strategy or RENDERED_STRATEGY),
        code_identity=identity if identity is not None else code_identity())


def backtest_scenario(name: str = SCENARIO, first_tick: Optional[datetime] = FIRST_TICK,
                      last_tick: Optional[datetime] = WINDOW_END - timedelta(seconds=1),
                      ends_on_tick_limit: bool = False, records_frames: bool = True,
                      strategy: Optional[Dict[str, Any]] = None) -> BacktestScenarioCapture:
    """A scenario that read the whole window, recorded its frames and ran the strategy."""
    return BacktestScenarioCapture(
        name=name,
        declared_window=DataWindow(
            unit_name=name, start_date=FIRST_TICK.isoformat(),
            end_date=None if ends_on_tick_limit else WINDOW_END.isoformat()),
        first_tick=first_tick, last_tick=last_tick, records_frames=records_frames,
        rendered_strategy_config=copy.deepcopy(strategy or RENDERED_STRATEGY))


def backtest_row(broker_config_id: str = BROKER_CONFIG,
                 features_beyond_venue: str = '') -> RunResultRow:
    """The backtest's ledger row."""
    return RunResultRow(
        param_hash='my_hash', run_id='b1', run_timestamp=FIRST_TICK.isoformat(),
        broker_config_id=broker_config_id, features_beyond_venue=features_beyond_venue)


def gate_backtest(identity: Optional[CodeIdentity] = None,
                  rows: Optional[List[RunResultRow]] = None,
                  scenarios: Optional[List[BacktestScenarioCapture]] = None) -> BacktestRunCapture:
    """The backtest of the window, run here on the backtest's commit."""
    return BacktestRunCapture(
        run_id='b1', app_version=RELEASE,
        code_identity=identity if identity is not None else code_identity(
            framework_commit=BACKTEST_COMMIT, strategy_root=STRATEGY_ROOT_HERE),
        rows=rows if rows is not None else [backtest_row()],
        scenarios=scenarios if scenarios is not None else [backtest_scenario()])


def gate_window(start: Optional[datetime] = FIRST_TICK,
                end: Optional[datetime] = WINDOW_END) -> CompareWindow:
    """The window the live side placed."""
    return CompareWindow(start=start, end=end, end_cause=WindowEndCause.UNTIL)


def empty_tree(_commit: str) -> Dict[str, str]:
    """A source tree with no file of the code path — the code path then refuses nothing."""
    return {}
