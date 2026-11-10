"""
The divergence report as text (#512) — printed by the command and kept as `report.txt`; an A/A of
two code stands the same way, kept as `aa_report.txt`.

Formatting only: every figure is a field of the report model, derived and judged before it gets
here; this unit loops and formats.
"""

from typing import List, Optional

from python.framework.types.api.divergence_report_types import (
    AaReport,
    AaStandRun,
    AaWindowResult,
    DayExclusionReason,
    DivergenceReport,
    MeasureVerdict,
    SegmentReport,
)

_RULE = '━' * 100


def render_divergence_report(report: DivergenceReport) -> str:
    """
    The report as the operator reads it.

    Args:
        report: The report

    Returns:
        The text, lines joined
    """
    dirty = ' (uncommitted changes)' if report.report_dirty else ''
    lines: List[str] = [
        _RULE,
        f'DIVERGENCE REPORT {report.report_id} · deployment {report.deployment_id} · '
        f'{report.symbol} · read as {report.reading.value}',
        _RULE,
        f'Window asked: until {_when(report.until_requested) or "the last sealed boundary"} · '
        f'decision timeframe {report.decision_timeframe}',
        f'Records: {report.records_root or "this machine"} · report code '
        f'{report.report_commit[:8] or "unknown"}{dirty}',
        'Venue ledger: ' + ('read' if not report.venue_ledger_unread
                            else f'not read — {report.venue_ledger_unread}'),
    ]
    for unread in report.unreadable:
        lines.append(f'  unreadable: {unread}')
    for named in report.live.named_sessions:
        lines.append(f'  session {named.run_id} covers no day: {named.reason}')
    for segment in report.segments:
        lines.extend(_segment(segment))
    lines.append(_RULE)
    return '\n'.join(lines)


def render_aa_report(report: AaReport) -> str:
    """
    An A/A as the operator reads it.

    Args:
        report: The A/A

    Returns:
        The text, lines joined
    """
    dirty = ' (uncommitted changes)' if report.current_dirty else ''
    lines: List[str] = [
        _RULE,
        f'A/A {report.report_id} · deployment {report.deployment_id} · {report.symbol} · '
        f'release {report.session_commit[:8] or "unknown"} against this tree '
        f'{report.current_commit[:8] or "unknown"}{dirty}',
        _RULE,
        f'Release tree: {report.release_tree} · data home: {report.data_home or "none"} · '
        f'records: {report.records_root or "this machine"}',
    ]
    if report.refused_because:
        lines.append(f'REFUSED: {report.refused_because}')
    for window in report.windows:
        lines.extend(_aa_window(window))
    lines.append(_RULE)
    return '\n'.join(lines)


def _aa_window(window: AaWindowResult) -> List[str]:
    """
    One A/A window's lines.

    Args:
        window: The window's result

    Returns:
        Its lines
    """
    lines = [
        '',
        f'WINDOW {window.index} · {_when(window.start) or "no start"} → '
        f'{_when(window.end) or "no end"} · {window.verdict.value.upper()}'
        + (f' — {window.not_checked_because}' if window.not_checked_because else ''),
    ]
    for run in (window.release, window.current):
        lines.append(f'  {_aa_run(run)}')
    if window.render_stamps_before:
        moved = window.render_stamps_before != window.render_stamps_after
        lines.append(f'  Bars: {len(window.render_stamps_before)} file(s) · '
                     + ('a render stamp moved between the runs' if moved else 'unchanged'))
    if window.one_sided_fields:
        lines.append('  Trade fields only one stand writes (not compared): '
                     + ', '.join(window.one_sided_fields))
    if window.difference_count:
        lines.append(f'  {window.difference_count} value(s) differ; the first:')
        for difference in window.differences:
            lines.append(f'    {difference.stream} #{difference.position} {difference.field}: '
                         f'release {difference.release_value} · this tree '
                         f'{difference.current_value}')
    return lines


def _aa_run(run: AaStandRun) -> str:
    """
    One stand's backtest in a line.

    Args:
        run: The stand's backtest

    Returns:
        The line
    """
    if run.failed_because:
        return f'{run.stand.value}: did not run — {run.failed_because}'
    dirty = ' (uncommitted)' if run.framework_dirty else ''
    return (f'{run.stand.value}: run {run.run_id} · code {run.framework_commit or "unknown"}{dirty}'
            f' · broker {run.broker_config_id or "none"} · {_count(run.order_events)} order '
            f'event(s), {_count(run.fills)} fill(s), {_count(run.trades)} trade(s)')


def _count(value: Optional[int]) -> str:
    """
    A count as the report prints it.

    Args:
        value: The count, or None where it was not read

    Returns:
        The number, or '—' for none
    """
    return str(value) if value is not None else '—'


def _segment(report: SegmentReport) -> List[str]:
    """
    One segment's lines.

    Args:
        report: The segment's report

    Returns:
        Its lines
    """
    segment = report.segment
    window = segment.window
    lines = [
        '',
        f'SEGMENT {segment.index} · sessions {", ".join(segment.run_ids)}',
        f'  Window: {_when(window.start)} → {_when(window.end)} ({window.end_cause.value})'
        + (f' — {window.end_detail}' if window.end_detail else ''),
    ]
    for change in segment.venue_account_changes or []:
        lines.append(f'  Account change at the venue: {_when(change.at)} {change.detail}')
    excluded = [day for day in segment.days if not day.included
                and DayExclusionReason.AFTER_WINDOW_END not in day.exclusions]
    for day in excluded:
        lines.append(f'  excluded {day.trading_day.isoformat()}: '
                     + ', '.join(cause.value for cause in day.exclusions))
    lines.append(f'  Backtest: {report.backtest_run_id or "none"} · scenario set '
                 f'{report.scenario_set_name}'
                 + (f' · warmup kept as {report.warmup_record_file}' if report.warmup_record_file
                    else ' · no warmup history kept'))
    if report.gate is not None:
        gate = report.gate
        lines.append(f'  Gate: {"comparable" if gate.comparable else "REFUSED"} · release '
                     f'{gate.release or "?"} · session {gate.session_commit[:8]} · backtest '
                     f'{gate.backtest_commit[:8]} · {len(gate.declared_files)} declared file(s)')
        for finding in gate.findings:
            lines.append(f'    {finding.severity} {finding.check}: {finding.message}')
    if report.not_compared_because:
        lines.append(f'  NOT COMPARED: {report.not_compared_because}')
        return lines
    verdict = report.verdict
    measures = report.measures
    if verdict is None or measures is None:
        return lines
    lines.append(f'  VERDICT: {verdict.state.value.upper()} — {verdict.reason}')
    lines.append(f'  Days: {verdict.compared_days} compared, {verdict.excluded_days} excluded '
                 f'(longest run {verdict.longest_excluded_run})'
                 + (f' · floor {verdict.day_floor}' if verdict.day_floor is not None else ''))
    chances = verdict.measures[0].chances if verdict.measures else None
    if chances is not None:
        lines.append(f'  Chances: {chances.compared_days} compared day(s) · '
                     f'{chances.days_with_an_order} with an order · {chances.orders} order(s) · '
                     f'{chances.fills} fill(s)')
    lines.append(f'  {"measure":<18} {"state":<12} {"zone":<7} {"k of N":<10} {"share":>7} '
                 f'{"run":>4}  reason')
    for measure in verdict.measures:
        lines.append(_measure_row(measure))
    for measure in verdict.measures:
        for item in measure.items:
            lines.append(f'    {measure.measure.value} {item.trading_day.isoformat()} '
                         f'{_when(item.at) or ""} {"(explained) " if item.explained else ""}'
                         f'{item.detail}')
    cross = verdict.cross_section
    lines.append('  Cross-section: ' + (
        f'agreement {cross.agreement * 100:.1f} % (weighted deviation '
        f'{cross.weighted_deviation * 100:.1f} %)' if cross.agreement is not None
        else 'not checked — nothing to compare in '
        + ', '.join(m.value for m in cross.not_checked)))
    table = measures.decision_table
    lines.append('  Decisions, live × backtest: ' + (', '.join(
        f'{cell.live_action.value} × {cell.backtest_action.value} {cell.days}'
        for cell in table.cells) or 'none')
        + f' · not checked {table.not_checked} · in a gap {table.in_gap}')
    daily = measures.equity.daily
    lines.append(f'  Measure 3, daily difference over {daily.day_count} day(s): mean '
                 f'{_pct(daily.mean_pct)}, tracking error {_pct(daily.tracking_error_pct)}, '
                 f'90 % interval [{_pct(daily.interval_low_pct)}, {_pct(daily.interval_high_pct)}]'
                 + (' — too narrow: the days depend on each other'
                    if daily.interval_too_narrow else ''))
    gaps = measures.gaps
    lines.append(f'  Gaps: {len(gaps.gaps)} · {len(gaps.excluded)} stretch(es) left out · '
                 f'{len(gaps.unconfirmed)} run(s) of missing ids unconfirmed · '
                 f'{len(gaps.refuted)} refuted')
    for unread in report.venue_unread + gaps.unreadable + measures.unreadable:
        lines.append(f'  unread: {unread}')
    for note in verdict.notes:
        lines.append(f'  Note: {note}')
    return lines


def _measure_row(measure: MeasureVerdict) -> str:
    """
    One measure's row in the table.

    Args:
        measure: The measure's verdict

    Returns:
        The row
    """
    share = f'{measure.share * 100:.1f} %' if measure.share is not None else '—'
    zone = measure.zone.value if measure.zone is not None else '—'
    return (f'  {measure.measure.value:<18} {measure.state.value:<12} {zone:<7} '
            f'{f"{measure.deviating} of {measure.compared}":<10} {share:>7} '
            f'{measure.longest_deviating_run:>4}  {measure.reason}')


def _when(instant) -> Optional[str]:
    """
    An instant as the report prints it.

    Args:
        instant: The instant, or None

    Returns:
        'YYYY-MM-DD HH:MM:SS' in UTC; None for none
    """
    return instant.strftime('%Y-%m-%d %H:%M:%S') if instant is not None else None


def _pct(value: Optional[float]) -> str:
    """
    A percentage figure as printed.

    Args:
        value: The figure in percent, or None

    Returns:
        '+0.0123 %', or '—' for none
    """
    return f'{value:+.4f} %' if value is not None else '—'
