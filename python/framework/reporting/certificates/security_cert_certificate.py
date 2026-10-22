"""
FiniexTestingIDE - Security Acceptance Certificate (#358)

Post-run analyzer: reads the five stage runs of one security study and emits a PASS/FAIL
certificate, under the identity every release certificate carries (`release_version` /
`git_commit` / `timestamp` / `valid_until` / `overall_status`), written to
`tests/security_cert/reports/` and validated by a CI-friendly test.

What each rule rests on is declared in the rule catalog; this module computes the checks the
catalog's live rules name, from two sources per stage:
- the capture — the study's phases and the order lines copied from the session's own order-event
  stream. A submit phase's verdict and its order line come from the same order result, so the line
  CONFIRMS the verdict rather than deriving it a second time: one derivation, two transports. What
  the line catches is a verdict the stream does not carry — a denial the phase read with a reason
  the stream does not hold, a suppressed submit that reached the venue after all.
- the breaker's own record in the run's report (`io/safety.json`): its block count and the reason
  it gave, the baseline it measured against and where that came from, how its hard stop ended.
  The breaker writes it from its own state, not from anything the study observed.

PASS criteria (hard):
- all five stages ran, in order, from ONE commit, each with its orders sent to the venue
- every phase passed or was skipped; a phase that waits for the hard stop has no result, because
  the stop ended its session — a result there means the stop never came
- every check a live rule names passed
- nothing rests at the venue when the last stage ends

Informational: the fees each stage booked, from the run's own report, and what the venue's account
moved between the first stage's start and the last stage's end.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.acceptance_studies.security_cert_stages import (
    SECURITY_CERT_PROFILE_DIR,
    SECURITY_STAGES,
    stage_unit_name,
)
from python.framework.reporting.certificates.security_cert_rule_catalog import (
    SECURITY_RULE_CATALOG,
)
from python.framework.reporting.certificates.study_certificate_writer import (
    write_study_certificate,
)
from python.framework.reporting.io.artifact_specs import (
    RUN_SUMMARY_ARTIFACT,
    SAFETY_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import read_artifact
from python.framework.reporting.io.run_header_io import read_run_header
from python.framework.types.acceptance_study_types.security_cert_types import (
    ExpectedOutcome,
    RuleCertification,
    SecurityPhaseType,
    SecurityStageId,
)
from python.framework.types.acceptance_study_types.study_capture_types import (
    PREFLIGHT_PHASE,
    SESSION_END_PHASE,
    CaptureLineType,
)
from python.framework.types.acceptance_study_types.study_phase_types import PhaseOutcome
from python.framework.types.api.report_types import OrdersTo, RunHeader, SafetyReport
from python.framework.types.autotrader_types.safety_session_types import FlattenOutcome
from python.framework.types.log_layout_types import (
    IO_SUBDIR,
    RUN_HEADER_ARTIFACT,
    SECURITY_CERT_CAPTURE,
)
from python.framework.types.trading_env_types.order_event_types import OrderEventType

# Default home for committed certificates — sibling of tests/live_field_study/reports
DEFAULT_REPORTS_DIR = 'tests/security_cert/reports'
REPORT_PREFIX = 'security_cert_report'

# Phase outcomes that do not fail the certificate — a force close finds nothing to close
_PASSING_OUTCOMES = frozenset({PhaseOutcome.PASS, PhaseOutcome.SKIPPED})
# The order lines of the capture — what a submit phase's verdict is confirmed against
_ORDER_LINES = frozenset({
    CaptureLineType.ORDER_FILLED, CaptureLineType.PARTIAL_CLOSE, CaptureLineType.ORDER_REJECTED,
    CaptureLineType.ORDER_CANCELLED, CaptureLineType.ORDER_UNACCOUNTED})

# The limits the soft stage sets, by the name the breaker's reason gives each one
_SESSION_LIMITS = ('max_drawdown_pct', 'max_drawdown_abs')
_DAILY_LIMITS = ('max_daily_loss_pct', 'max_daily_loss_abs')


class _StageRun:
    """
    One stage's run as the certificate reads it: its capture, its header and its safety record.

    Args:
        stage_id: Which stage
        capture_path: Its capture file
    """

    def __init__(self, stage_id: SecurityStageId, capture_path: Path):
        self.stage_id = stage_id
        self.capture_path = capture_path
        lines = [ln for ln in capture_path.read_text(encoding='utf-8').splitlines() if ln.strip()]
        self.header: Dict[str, Any] = json.loads(lines[0])
        self.events: List[Dict[str, Any]] = [json.loads(ln) for ln in lines[1:]]
        run_dir = capture_path.parent
        self.run_header: Optional[RunHeader] = _read_or_none(
            lambda: read_run_header(run_dir / RUN_HEADER_ARTIFACT))
        self.safety: Optional[SafetyReport] = _read_or_none(
            lambda: read_artifact(run_dir / IO_SUBDIR / SAFETY_ARTIFACT.filename, SAFETY_ARTIFACT))
        self.run_dir = run_dir

    def results(self) -> Dict[str, Dict[str, Any]]:
        """The phase results, by phase id."""
        return {e['phase']: e for e in self.events
                if e.get('event_type') == CaptureLineType.PHASE_RESULT}

    def order_lines(self, phase_id: str) -> List[Dict[str, Any]]:
        """The order lines filed under a phase."""
        return [e for e in self.events
                if e.get('event_type') in _ORDER_LINES and e.get('phase') == phase_id]

    def end_snapshot(self) -> Optional[Dict[str, Any]]:
        """The venue read at the session's end, selected by its phase — never by position."""
        return next((e for e in reversed(self.events)
                     if e.get('event_type') == CaptureLineType.BROKER_SNAPSHOT
                     and e.get('phase') == SESSION_END_PHASE), None)

    def start_snapshot(self) -> Optional[Dict[str, Any]]:
        """The venue read before trading."""
        return next((e for e in self.events
                     if e.get('event_type') == CaptureLineType.BROKER_SNAPSHOT
                     and e.get('phase') == PREFLIGHT_PHASE), None)

    def ended_utc(self) -> Optional[str]:
        """When the capture was closed."""
        end = next((e for e in reversed(self.events)
                    if e.get('event_type') == CaptureLineType.SESSION_END), None)
        return end.get('ts_utc') if end else None


class SecurityCertCertificate:
    """Reads one security study's stage runs and produces / writes a PASS/FAIL certificate."""

    @staticmethod
    def find_latest_captures(
        search_root: Optional[str] = None,
        profile_dir: Path = SECURITY_CERT_PROFILE_DIR,
    ) -> Dict[SecurityStageId, Path]:
        """
        Find the newest capture of every stage under the AutoTrader run root.

        Args:
            search_root: The run root; None resolves run_logs.autotrader from config
            profile_dir: Where the stage profiles are — their names are the units the runs are
                filed under

        Returns:
            Stage → its newest capture; a stage that never ran is absent
        """
        root = Path(search_root) if search_root else Path(
            AppConfigManager().get_file_logging_config_object().run_logs.autotrader)
        found: Dict[SecurityStageId, Path] = {}
        for stage in SECURITY_STAGES:
            unit_dir = root / stage_unit_name(stage, profile_dir)
            if not unit_dir.exists():
                continue
            candidates = list(unit_dir.rglob(SECURITY_CERT_CAPTURE))
            if candidates:
                found[stage.stage_id] = max(candidates, key=lambda p: p.stat().st_mtime)
        return found

    @staticmethod
    def stage_failures(stage_id: SecurityStageId, capture_path: Path) -> List[str]:
        """
        What failed in ONE stage's phases — the check the study runner makes after each stage.

        Args:
            stage_id: The stage
            capture_path: Its capture file

        Returns:
            One line per failed phase check; empty when the stage passed
        """
        checks = _phase_checks(_StageRun(stage_id, capture_path))
        return [f"{name}: {check['detail']}" for name, check in checks.items()
                if not check['passed']]

    @staticmethod
    def analyze(captures: Dict[SecurityStageId, Path]) -> Dict[str, Any]:
        """
        Analyze one study's stage runs into a result dict (no release metadata).

        Args:
            captures: Stage → its capture file

        Returns:
            Analysis dict: overall status, every check, every rule with how it is certified, the
            fees and the account's movement
        """
        runs = {stage_id: _StageRun(stage_id, path) for stage_id, path in captures.items()}
        checks: Dict[str, Dict[str, Any]] = {}
        checks.update(_study_checks(runs))
        for run in runs.values():
            checks.update(_phase_checks(run))
        checks.update(_safety_checks(runs))

        rules = _rules(checks)
        failed_checks = sorted(name for name, check in checks.items() if not check['passed'])
        failed_rules = [r['rule'] for r in rules
                        if r['certification'] == RuleCertification.LIVE and r['status'] != 'passed']
        overall = 'PASSED' if not failed_checks and not failed_rules else 'FAILED'

        return {
            'overall_status': overall,
            'stages': {stage_id.value: str(run.capture_path) for stage_id, run in runs.items()},
            'failed_checks': failed_checks,
            'checks': checks,
            'rules': rules,
            'fees_charged': {stage_id.value: _fees_charged(run) for stage_id, run in runs.items()},
            'account_delta': _account_delta(runs),
            'warnings': [],
        }

    @staticmethod
    def generate(
        captures: Dict[SecurityStageId, Path],
        release_version: str = 'dev',
        comment: str = '',
        reports_dir: str = DEFAULT_REPORTS_DIR,
    ) -> Path:
        """
        Analyze one study and write its PASS/FAIL certificate to the reports directory.

        Args:
            captures: Stage → its capture file
            release_version: Version this study certifies (or 'dev')
            comment: Optional free-text note
            reports_dir: Target directory for the certificate

        Returns:
            Path to the written certificate
        """
        analysis = SecurityCertCertificate.analyze(captures)
        out_path, certificate = write_study_certificate(
            analysis=analysis,
            sources={'source_captures': {s.value: str(p) for s, p in captures.items()}},
            release_version=release_version,
            comment=comment,
            reports_dir=reports_dir,
            report_prefix=REPORT_PREFIX,
        )
        _print_summary(out_path, certificate)
        return out_path


# ============================================
# Checks
# ============================================

def _check(passed: bool, detail: str) -> Dict[str, Any]:
    """One check's result."""
    return {'passed': bool(passed), 'detail': detail}


def _study_checks(runs: Dict[SecurityStageId, _StageRun]) -> Dict[str, Dict[str, Any]]:
    """
    What holds across the stages: all present, in order, one commit, real orders, flat at the end.

    Args:
        runs: Stage → its run

    Returns:
        Check name → result
    """
    checks: Dict[str, Dict[str, Any]] = {}
    missing = [s.stage_id.value for s in SECURITY_STAGES if s.stage_id not in runs]
    checks['study:all_stages'] = _check(
        not missing, 'every stage ran' if not missing else f"missing: {', '.join(missing)}")

    ordered = [runs[s.stage_id] for s in SECURITY_STAGES if s.stage_id in runs]
    out_of_order = [
        f'{later.stage_id.value} started before {earlier.stage_id.value} ended'
        for earlier, later in zip(ordered, ordered[1:])
        if earlier.ended_utc() is None or later.header.get('started_utc', '') < earlier.ended_utc()]
    checks['study:in_order'] = _check(
        not out_of_order, 'each stage started after the one before ended'
        if not out_of_order else '; '.join(out_of_order))

    commits = {run.run_header.git_commit if run.run_header else None for run in ordered}
    dirty = [run.stage_id.value for run in ordered
             if run.run_header is None or run.run_header.code_identity is None
             or run.run_header.code_identity.is_dirty()]
    one_commit = len(commits) == 1 and None not in commits
    checks['study:one_commit'] = _check(
        one_commit and not dirty,
        (f'every stage from {next(iter(commits))}' if one_commit else
         f'the stages ran from {len(commits)} commits') +
        (f"; uncommitted or unreadable code: {', '.join(dirty)}" if dirty else ''))

    simulated = [run.stage_id.value for run in ordered
                 if run.run_header is None or run.run_header.orders_to != OrdersTo.VENUE]
    checks['study:real_orders'] = _check(
        not simulated, 'every stage sent its orders to the venue'
        if not simulated else f"not sent to the venue: {', '.join(simulated)}")

    last = ordered[-1] if ordered else None
    end = last.end_snapshot() if last else None
    order_count = (end.get('extra') or {}).get('order_count') if end else None
    checks['study:flat_at_end'] = _check(
        order_count == 0,
        'nothing rests at the venue after the last stage' if order_count == 0 else
        f'the last stage ended with {order_count} resting order(s)' if order_count else
        'the last stage has no readable end snapshot')
    return checks


def _phase_checks(run: _StageRun) -> Dict[str, Dict[str, Any]]:
    """
    Every phase of a stage: its verdict, and for a submit its order line agreeing.

    A phase that waits for the hard stop is concluded by the session's end, which the stop
    requests — so its verdict is held against the breaker's own record as well: a pass the
    breaker does not confirm is a session that ended for some other reason.

    Args:
        run: The stage's run

    Returns:
        'phase:<id>' → result
    """
    checks: Dict[str, Dict[str, Any]] = {}
    results = run.results()
    flatten_fired = bool(run.safety and run.safety.flatten_fired)
    for phase_id in run.header.get('phases', []):
        name = f'phase:{phase_id}'
        result = results.get(phase_id)
        if result is None:
            checks[name] = _check(False, 'phase not reached')
            continue
        outcome = result.get('status')
        extra = result.get('extra') or {}
        if outcome not in _PASSING_OUTCOMES:
            checks[name] = _check(False, f"{outcome}: {extra.get('reason', '')}")
            continue
        if extra.get('phase_type') == SecurityPhaseType.AWAIT_SESSION_END:
            checks[name] = _check(
                flatten_fired, extra.get('reason', '') if flatten_fired
                else 'the phase passed, but the breaker records no hard stop')
            continue
        disagreement = _stream_disagrees(run, phase_id, extra)
        checks[name] = _check(
            disagreement is None,
            extra.get('reason', '') if disagreement is None
            else f'the order stream disagrees: {disagreement}')
    return checks


def _stream_disagrees(run: _StageRun, phase_id: str, extra: Dict[str, Any]) -> Optional[str]:
    """
    Whether a submit phase's order line contradicts the outcome the phase passed on.

    Args:
        run: The stage's run
        phase_id: The phase
        extra: Its result line's extra — what it expected

    Returns:
        What disagrees, or None when the line confirms the verdict (or the phase sent nothing)
    """
    expect = extra.get('expect')
    if expect is None:
        return None
    lines = run.order_lines(phase_id)
    if expect == ExpectedOutcome.SUPPRESSED:
        return None if not lines else f'{len(lines)} order line(s) for a suppressed submit'
    if expect == ExpectedOutcome.FILLED:
        fills = [ln for ln in lines if ln.get('event_type') == CaptureLineType.ORDER_FILLED]
        return None if fills else 'no fill line'
    denials = [ln for ln in lines
               if ln.get('event_type') == CaptureLineType.ORDER_REJECTED
               and ln.get('status') == OrderEventType.DENIED.value
               and (ln.get('extra') or {}).get('reason') == extra.get('expect_reason')]
    return None if denials else f"no denial for {extra.get('expect_reason')}"


def _safety_checks(runs: Dict[SecurityStageId, _StageRun]) -> Dict[str, Dict[str, Any]]:
    """
    What the breaker's own record says, stage by stage — and across a restart, against the record
    the stage before it wrote.

    Args:
        runs: Stage → its run

    Returns:
        'safety:<name>' → result
    """
    checks: Dict[str, Dict[str, Any]] = {}
    soft = _safety_of(runs, SecurityStageId.SOFT)
    restart = _safety_of(runs, SecurityStageId.RESTART)
    hard_kept = _safety_of(runs, SecurityStageId.HARD_KEPT)
    hard_restart = _safety_of(runs, SecurityStageId.HARD_RESTART)

    if soft is not None:
        checks['safety:soft_blocked'] = _check(
            soft.enabled and soft.block_count >= 1 and soft.blocked_at_end
            and not soft.baseline_restored,
            f'engaged {soft.block_count} time(s), blocked at the end: {soft.blocked_at_end}, '
            f'baseline restored: {soft.baseline_restored}')
        named = [limit for limit in _SESSION_LIMITS + _DAILY_LIMITS
                 if f'{limit} (' in soft.reason_at_end]
        checks['safety:soft_reasons'] = _check(
            len(named) == len(_SESSION_LIMITS + _DAILY_LIMITS),
            f'named: {", ".join(named) or "none"} — "{soft.reason_at_end}"')

    if restart is not None:
        same = _same_baseline(soft, restart)
        checks['safety:restart_baseline_restored'] = _check(
            restart.baseline_restored and same,
            f'restored: {restart.baseline_restored}, the soft stage\'s baseline: {same}')
        session_named = all(f'{limit} (' in restart.reason_at_end for limit in _SESSION_LIMITS)
        daily_named = [limit for limit in _DAILY_LIMITS if f'{limit} (' in restart.reason_at_end]
        checks['safety:restart_reasons'] = _check(
            session_named and not daily_named,
            f'session limits named: {session_named}, daily named: '
            f'{", ".join(daily_named) or "none"} — "{restart.reason_at_end}"')

    if hard_kept is not None:
        checks['safety:hard_kept'] = _check(
            hard_kept.flatten_fired
            and hard_kept.flatten_outcome == FlattenOutcome.HOLDINGS_KEPT
            and bool(hard_kept.flatten_kept),
            f'fired: {hard_kept.flatten_fired}, outcome: {_outcome(hard_kept)}, '
            f'kept: {", ".join(hard_kept.flatten_kept) or "none"}')

    if hard_restart is not None:
        same = _same_baseline(hard_kept, hard_restart)
        checks['safety:hard_restart_fired'] = _check(
            hard_restart.flatten_fired and hard_restart.baseline_restored and same,
            f'fired: {hard_restart.flatten_fired}, baseline restored: '
            f'{hard_restart.baseline_restored}, the hard stage\'s baseline: {same}')
        run = runs[SecurityStageId.HARD_RESTART]
        closes = [e for e in run.events if e.get('event_type') == CaptureLineType.ORDER_FILLED
                  and (e.get('extra') or {}).get('action') == 'close']
        checks['safety:hard_restart_complete'] = _check(
            hard_restart.flatten_outcome == FlattenOutcome.COMPLETE and bool(closes),
            f'outcome: {_outcome(hard_restart)}, close fills: {len(closes)}')
    return checks


def _rules(checks: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Every rule of the catalog with how it is certified and, for a live one, whether it held.

    Args:
        checks: The computed checks

    Returns:
        One row per rule, in the catalog's order
    """
    rows = []
    for rule, entry in SECURITY_RULE_CATALOG.items():
        if entry.certification == RuleCertification.LIVE:
            check = checks.get(entry.evidence)
            status = 'not_measured' if check is None else (
                'passed' if check['passed'] else 'failed')
        else:
            status = entry.certification.value
        rows.append({
            'rule': rule.value,
            'title': entry.title,
            'certification': entry.certification.value,
            'evidence': entry.evidence,
            'stage': entry.stage.value if entry.stage else None,
            'status': status,
        })
    return rows


# ============================================
# Readers and figures
# ============================================

def _safety_of(runs: Dict[SecurityStageId, _StageRun], stage_id: SecurityStageId
               ) -> Optional[SafetyReport]:
    """A stage's safety record, or None when the stage or its record is missing."""
    run = runs.get(stage_id)
    return run.safety if run is not None else None


def _same_baseline(before: Optional[SafetyReport], after: SafetyReport) -> bool:
    """
    Whether a restarted stage measured against the baseline the stage before it wrote.

    Args:
        before: The writing stage's record
        after: The reading stage's record

    Returns:
        True when both name one baseline — same value, same stamp
    """
    if before is None or before.baseline is None or after.baseline is None:
        return False
    return (before.baseline.taken_at_utc == after.baseline.taken_at_utc
            and before.baseline.value == after.baseline.value)


def _outcome(report: SafetyReport) -> str:
    """The hard stop's outcome as text."""
    return report.flatten_outcome.value if report.flatten_outcome else 'none'


def _fees_charged(run: _StageRun) -> Dict[str, Any]:
    """
    What a stage booked in fees, from the run's own report — or that it is not available.

    Args:
        run: The stage's run

    Returns:
        {'value', 'currency', 'source'} or {'source': 'not_available'}
    """
    summary = _read_or_none(lambda: read_artifact(
        run.run_dir / IO_SUBDIR / RUN_SUMMARY_ARTIFACT.filename, RUN_SUMMARY_ARTIFACT))
    if summary is None:
        return {'source': 'not_available'}
    charged = [row for row in summary.currencies if row.fees_charged is not None]
    if len(charged) != 1:
        return {'source': 'not_available'}
    return {'value': charged[0].fees_charged, 'currency': charged[0].currency,
            'source': 'run_record'}


def _account_delta(runs: Dict[SecurityStageId, _StageRun]) -> Dict[str, Any]:
    """
    What the venue's account moved from the first stage's start to the last stage's end.

    Args:
        runs: Stage → its run

    Returns:
        Per currency the change, or the reason it cannot be stated
    """
    ordered = [runs[s.stage_id] for s in SECURITY_STAGES if s.stage_id in runs]
    if not ordered:
        return {'status': 'no_stages'}
    start, end = ordered[0].start_snapshot(), ordered[-1].end_snapshot()
    before = (start.get('extra') or {}).get('balances') if start else None
    after = (end.get('extra') or {}).get('balances') if end else None
    if before is None or after is None:
        return {'status': 'balances_unreadable'}
    return {'status': 'ok', 'delta': {
        currency: after.get(currency, 0.0) - before.get(currency, 0.0)
        for currency in sorted(set(before) | set(after))}}


def _read_or_none(read) -> Any:
    """
    Read an artifact, or None when it is missing or does not parse — a check then says so.

    Args:
        read: The read to attempt

    Returns:
        What it read, or None
    """
    try:
        return read()
    except (OSError, ValueError):
        return None


def _print_summary(out_path: Path, certificate: Dict[str, Any]) -> None:
    """
    Print the certificate's verdict, its failed checks and its rules.

    Args:
        out_path: Where it was written
        certificate: The certificate
    """
    print(f"\n{'=' * 68}")
    print(f"  Security Study Certificate: {certificate['overall_status']}")
    print(f"{'=' * 68}")
    for name in certificate.get('failed_checks', []):
        print(f"  ❌ {name}: {certificate['checks'][name]['detail']}")
    for row in certificate.get('rules', []):
        mark = {'passed': '✅', 'failed': '❌', 'not_measured': '⚪'}.get(row['status'], '·')
        print(f"  {mark} [{row['certification']}] {row['rule']}")
    for warning in certificate.get('warnings', []):
        print(f'  ⚠️  {warning}')
    print(f'\n  → {out_path}\n')

