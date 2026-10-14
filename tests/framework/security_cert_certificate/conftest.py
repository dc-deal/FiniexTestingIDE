"""
FiniexTestingIDE - Security Study Certificate Fixtures (#358)

Builds the five stage runs of one security study on disk — capture, run header and the breaker's
own record per stage — the way a passing study leaves them. The phases come from the REAL stage
profiles, so a profile that changes in a way the analyzer cannot follow fails here first. A test
takes the passing study and changes exactly one thing.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from python.framework.acceptance_studies.security_cert_stages import (
    SECURITY_STAGES,
    stage_profile_path,
)
from python.framework.reporting.io.artifact_specs import SAFETY_ARTIFACT, SECURITY_CERT_CAPTURE
from python.framework.reporting.io.run_header_io import RUN_HEADER_ARTIFACT
from python.framework.types.acceptance_study_types.security_cert_types import (
    ExpectedOutcome,
    SecurityPhaseType,
    SecurityStageId,
)
from python.framework.types.api.report_types import OrdersTo, RunHeader, SafetyReport
from python.framework.types.autotrader_types.safety_session_types import FlattenOutcome
from python.framework.types.log_layout_types import IO_SUBDIR
from python.framework.types.persistence_types import BaselineKind, BaselineOrigin, RiskBaseline
from python.framework.types.run_origin_types import CodeIdentity, RepositoryState

_COMMIT = 'abc1234'
# One hour per stage, so each starts after the one before it ended
_STAGE_HOUR = {stage.stage_id: index for index, stage in enumerate(SECURITY_STAGES)}

_SOFT_BASELINE = RiskBaseline(kind=BaselineKind.SESSION_FIXED, value=23.72,
                              taken_at_utc='2026-10-14T11:00:01+00:00',
                              origin=BaselineOrigin.FIRST_TICK)
_HARD_BASELINE = RiskBaseline(kind=BaselineKind.SESSION_FIXED, value=23.60,
                              taken_at_utc='2026-10-14T13:00:01+00:00',
                              origin=BaselineOrigin.FIRST_TICK)
_SESSION_REASON = 'max_drawdown_pct (0.3% > 0.0%) + max_drawdown_abs (0.07 > 0.00)'
# What the session's end concludes for a phase that waits for the hard stop
_STOP_ENDS = {
    SecurityStageId.HARD_KEPT: 'ended by the safety layer: emergency stop, spot holdings kept '
                               '(hard drawdown 0.07 > 0.00)',
    SecurityStageId.HARD_RESTART: 'ended by the safety layer: emergency flatten complete '
                                  '(hard drawdown 0.07 > 0.00)',
}
_DAILY_REASON = 'max_daily_loss_abs (0.07 > 0.00) + max_daily_loss_pct (0.3% > 0.0%)'


@dataclass
class StageSpec:
    """
    How one stage's run is written — a passing stage unless a test changes it.

    Args:
        lines: Capture lines after the header, in order
        header: The run header
        safety: The breaker's record
        started: The capture header's start stamp
    """
    lines: List[Dict[str, Any]] = field(default_factory=list)
    header: Optional[RunHeader] = None
    safety: Optional[SafetyReport] = None
    started: str = ''


def _ts(stage_id: SecurityStageId, minute: int) -> str:
    """A stamp inside the stage's hour."""
    return f'2026-10-14T{10 + _STAGE_HOUR[stage_id]:02d}:{minute:02d}:00+00:00'


def _phases(stage_id: SecurityStageId) -> List[Dict[str, Any]]:
    """The stage's phases, as its real profile declares them."""
    stage = next(s for s in SECURITY_STAGES if s.stage_id == stage_id)
    profile = json.loads(stage_profile_path(stage).read_text(encoding='utf-8'))
    return profile['strategy_config']['decision_logic_config']['phase_sequence']


def _passing_lines(stage_id: SecurityStageId) -> List[Dict[str, Any]]:
    """The capture lines a passing stage writes after its header."""
    lines: List[Dict[str, Any]] = [{
        'event_type': 'broker_snapshot', 'phase': 'preflight', 'plane': 'broker_truth',
        'status': 'flat', 'extra': {'order_count': 0, 'balances': {'BTC': 0.0, 'USD': 23.72}}}]
    for phase in _phases(stage_id):
        pid, ptype = phase['phase_id'], phase['phase_type']
        lines.append({'event_type': 'phase_start', 'phase': pid, 'plane': 'bot',
                      'extra': {'phase_type': ptype}})
        extra: Dict[str, Any] = {'reason': 'ok', 'phase_type': ptype}
        if ptype == SecurityPhaseType.AWAIT_SESSION_END:
            extra['reason'] = _STOP_ENDS[stage_id]
        if ptype == SecurityPhaseType.SUBMIT_EXPECT:
            extra.update({'expect': phase['expect'], 'expect_reason': phase.get('expect_reason'),
                          'lot_rule': phase.get('lot_rule', 'min_lot'), 'lots': 6e-05})
            if phase['expect'] == ExpectedOutcome.DENIED:
                lines.append({'event_type': 'order_rejected', 'phase': pid, 'plane': 'bot',
                              'status': 'denied', 'extra': {'reason': phase['expect_reason']}})
            elif phase['expect'] == ExpectedOutcome.FILLED:
                lines.append({'event_type': 'order_filled', 'phase': pid, 'plane': 'bot',
                              'status': 'filled', 'extra': {'action': 'open'}})
        lines.append({'event_type': 'phase_result', 'phase': pid, 'plane': 'bot',
                      'status': 'pass', 'extra': extra})
    if stage_id == SecurityStageId.HARD_RESTART:
        lines.append({'event_type': 'order_filled', 'phase': 'hr_await_stop', 'plane': 'bot',
                      'status': 'filled', 'extra': {'action': 'close'}})
    lines.append({'event_type': 'broker_snapshot', 'phase': 'session_end',
                  'plane': 'broker_truth', 'status': 'flat',
                  'extra': {'order_count': 0, 'balances': {'BTC': 0.0, 'USD': 23.41}}})
    lines.append({'event_type': 'session_end', 'phase': 'session_end', 'plane': 'bot',
                  'status': 'session end', 'ts_utc': _ts(stage_id, 30)})
    return lines


def _passing_safety(stage_id: SecurityStageId) -> SafetyReport:
    """The breaker's record a passing stage leaves."""
    if stage_id == SecurityStageId.GUARDS:
        return SafetyReport(run_id=f'run_{stage_id.value}', enabled=False)
    if stage_id == SecurityStageId.SOFT:
        return SafetyReport(run_id=f'run_{stage_id.value}', enabled=True, baseline=_SOFT_BASELINE, block_count=1,
                            blocked_at_end=True,
                            reason_at_end=f'{_SESSION_REASON} + {_DAILY_REASON}')
    if stage_id == SecurityStageId.RESTART:
        restored = _SOFT_BASELINE.model_copy(update={'origin': BaselineOrigin.RESTORED_CARRY_OVER})
        return SafetyReport(run_id=f'run_{stage_id.value}', enabled=True, baseline=restored, baseline_restored=True,
                            block_count=1, blocked_at_end=True, reason_at_end=_SESSION_REASON)
    if stage_id == SecurityStageId.HARD_KEPT:
        return SafetyReport(run_id=f'run_{stage_id.value}', enabled=True, baseline=_HARD_BASELINE, flatten_fired=True,
                            flatten_outcome=FlattenOutcome.HOLDINGS_KEPT,
                            flatten_kept=['pos_btcusd_1'])
    restored = _HARD_BASELINE.model_copy(update={'origin': BaselineOrigin.RESTORED_CARRY_OVER})
    return SafetyReport(run_id=f'run_{stage_id.value}', enabled=True, baseline=restored, baseline_restored=True,
                        flatten_fired=True, flatten_outcome=FlattenOutcome.COMPLETE)


def _passing_header(stage_id: SecurityStageId) -> RunHeader:
    """A run header from one clean commit, its orders sent to the venue."""
    return RunHeader(
        run_id=f'run_{stage_id.value}', start_time=_ts(stage_id, 0), run_type='autotrader',
        run_name=stage_id.value, git_commit=_COMMIT, orders_to=OrdersTo.VENUE,
        code_identity=CodeIdentity(framework=RepositoryState(root='.', commit=_COMMIT)))


def write_stage(root: Path, stage_id: SecurityStageId, spec: StageSpec) -> Path:
    """
    Write one stage's run directory.

    Args:
        root: Where the run directories go
        stage_id: The stage
        spec: What its run holds

    Returns:
        The capture file
    """
    run_dir = root / stage_id.value / f'run_{stage_id.value}'
    (run_dir / IO_SUBDIR).mkdir(parents=True, exist_ok=True)
    header_line = {'record_kind': 'header', 'schema_version': '2.0', 'started_utc': spec.started,
                   'profile': stage_id.value, 'symbol': 'BTCUSD',
                   'phases': [p['phase_id'] for p in _phases(stage_id)]}
    lines = [header_line] + [{'ts_utc': spec.started, 'seq': i, 'phase_index': 0, **line}
                             for i, line in enumerate(spec.lines, start=1)]
    capture = run_dir / SECURITY_CERT_CAPTURE
    capture.write_text('\n'.join(json.dumps(line) for line in lines) + '\n', encoding='utf-8')
    if spec.header is not None:
        (run_dir / RUN_HEADER_ARTIFACT).write_text(spec.header.model_dump_json(), encoding='utf-8')
    if spec.safety is not None:
        (run_dir / IO_SUBDIR / SAFETY_ARTIFACT.filename).write_text(
            spec.safety.model_dump_json(), encoding='utf-8')
    return capture


@pytest.fixture
def passing_study() -> Dict[SecurityStageId, StageSpec]:
    """Every stage as a passing study leaves it — change one thing, then build."""
    return {stage.stage_id: StageSpec(lines=_passing_lines(stage.stage_id),
                                      header=_passing_header(stage.stage_id),
                                      safety=_passing_safety(stage.stage_id),
                                      started=_ts(stage.stage_id, 1))
            for stage in SECURITY_STAGES}


@pytest.fixture
def build(tmp_path) -> Callable[[Dict[SecurityStageId, StageSpec]], Dict[SecurityStageId, Path]]:
    """Write a study's stages and hand back their captures."""
    def _build(study: Dict[SecurityStageId, StageSpec]) -> Dict[SecurityStageId, Path]:
        return {stage_id: write_stage(tmp_path, stage_id, spec)
                for stage_id, spec in study.items()}
    return _build
