"""
FiniexTestingIDE - The Security Study's Five Stages through the AutoTrader Pipeline (#358)

Runs the mock rehearsal profiles in order, each as a session of its own, and reads what each
leaves behind. What is held here is what the study cannot do without and what no unit sees: the
later stages START — the declared continuation passes the boot's identity and continuity checks —
the restart stage measures against the baseline the stage before it took, the refusals that cost
nothing are refused for the reason the guard gives, a phase that ends in the pass it began is
filed under its own name, and the session's end concludes the phases the hard stop overtook.

Outcomes that wait for a mock FILL are not asserted. A mock venue fills on a status read paced on
the machine's clock while the ticks replay at full speed (#494), so whether a fill lands inside a
phase's timeout depends on processing speed — the same reason the field study's capture test
leaves its outcomes alone. The mock profiles scale every duration past the replay's longest gap
between two ticks, and the rehearsal passes every phase on this machine; the suite holds only
what does not depend on it.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.acceptance_studies.security_cert_stages import (
    SECURITY_STAGES,
    stage_profile_path,
)
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.reporting.io.artifact_specs import SAFETY_ARTIFACT
from python.framework.types.acceptance_study_types.security_cert_types import SecurityStageId
from python.framework.types.log_layout_types import IO_SUBDIR, SECURITY_CERT_CAPTURE
from tests.shared.fixture_helpers import remove_run_dir

MOCK_DIR = Path('configs/autotrader_profiles/mock/security_cert')
_S = SecurityStageId


@pytest.fixture(scope='module')
def stages() -> Dict[SecurityStageId, Dict[str, Any]]:
    """
    Run the five mock stages in order, as the runner does.

    Returns:
        Stage → its capture lines (header dropped) and the breaker's own record
    """
    ran: Dict[SecurityStageId, Dict[str, Any]] = {}
    run_dirs: List[Path] = []
    for stage in SECURITY_STAGES:
        trader = AutotraderMain(load_autotrader_config(str(stage_profile_path(stage, MOCK_DIR))))
        trader.run()
        run_dir = Path(trader._run_dir)
        run_dirs.append(run_dir)
        capture = run_dir / SECURITY_CERT_CAPTURE
        safety = run_dir / IO_SUBDIR / SAFETY_ARTIFACT.filename
        ran[stage.stage_id] = {
            'lines': ([json.loads(line) for line in capture.read_text(encoding='utf-8')
                       .splitlines() if line.strip()][1:] if capture.exists() else []),
            'safety': (json.loads(safety.read_text(encoding='utf-8'))
                       if safety.exists() else {}),
        }
    yield ran
    for run_dir in run_dirs:
        remove_run_dir(run_dir)


def _result(lines: List[Dict[str, Any]], phase_id: str) -> Dict[str, Any]:
    """The phase's result line."""
    return next(line for line in lines
                if line['event_type'] == 'phase_result' and line['phase'] == phase_id)


class TestEveryStageStarts:

    @pytest.mark.parametrize('stage_id', list(_S), ids=lambda s: s.value)
    def test_it_writes_a_capture_that_reaches_its_end(self, stages, stage_id):
        lines = stages[stage_id]['lines']
        assert lines, f'{stage_id.value} wrote no capture — the boot refused it'
        assert lines[-1]['event_type'] == 'session_end'


class TestTheRefusalsThatCostNothing:

    @pytest.mark.parametrize('phase_id, reason', [
        ('g_invalid_lot_1', 'invalid_lot_size'),
        ('g_invalid_lot_2', 'invalid_lot_size'),
        ('g_funds_not_cooled', 'insufficient_funds'),
        ('g_funds_arms', 'insufficient_funds'),
        ('g_cooled_buy', 'rejection_cooldown'),
        ('g_short_not_cooled', 'insufficient_funds'),
    ])
    def test_each_is_refused_for_the_reason_the_guard_gives(self, stages, phase_id, reason):
        result = _result(stages[_S.GUARDS]['lines'], phase_id)
        assert (result['status'], result['extra']['reason']) == ('pass', f'denied ({reason})')


class TestAPhaseThatEndsWhereItBegan:

    def test_its_start_and_its_result_are_filed_under_its_own_name(self, stages):
        lines = stages[_S.SOFT]['lines']
        marks = [(line['event_type'], line['phase']) for line in lines
                 if line['event_type'] in ('phase_start', 'phase_result')]
        assert marks[:2] == [('phase_start', 's_start_flat'), ('phase_result', 's_start_flat')]


class TestTheRestartStageContinuesTheSoftStage:

    def test_it_measures_against_the_baseline_the_soft_stage_took(self, stages):
        soft, restart = stages[_S.SOFT]['safety'], stages[_S.RESTART]['safety']
        assert restart['baseline_restored'] is True
        assert restart['baseline']['origin'] == 'restored_carry_over'
        assert restart['baseline']['taken_at_utc'] == soft['baseline']['taken_at_utc']

    def test_and_holds_its_first_order_back(self, stages):
        result = _result(stages[_S.RESTART]['lines'], 'r_blocked_from_start')
        assert (result['status'], result['extra']['reason']) == ('pass', 'suppressed')


class TestTheSessionsEndConcludesTheHardStop:

    @pytest.mark.parametrize('stage_id, phase_id, outcome', [
        (_S.HARD_KEPT, 'h_await_stop', 'holdings_kept'),
        (_S.HARD_RESTART, 'hr_await_stop', 'complete'),
    ], ids=['kept', 'restart'])
    def test_the_wait_passes_on_the_stop_the_breaker_records(
            self, stages, stage_id, phase_id, outcome):
        result = _result(stages[stage_id]['lines'], phase_id)
        assert result['status'] == 'pass'
        assert result['extra']['reason'].startswith('ended by the safety layer: emergency')
        assert stages[stage_id]['safety']['flatten_outcome'] == outcome

    def test_the_restart_after_the_stop_measures_against_the_kept_stage_s_baseline(self, stages):
        kept, restart = stages[_S.HARD_KEPT]['safety'], stages[_S.HARD_RESTART]['safety']
        assert restart['baseline']['origin'] == 'restored_carry_over'
        assert restart['baseline']['taken_at_utc'] == kept['baseline']['taken_at_utc']
