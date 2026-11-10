"""
The A/A of a deployment's code (#512): its windows backtested twice with the same inputs — on the
release the sessions ran and on the current tree — and held against each other event by event.

The verdict's backtests run on the current tree, not on the release the sessions ran, so the verdict
rules ask for proof that the two compute the same from the same inputs; otherwise the comparison
would measure the instrument instead of the parity. Both backtests read only what both code stands
understand — the strategy and the account the sessions recorded, the archive's warmup, the broker
file — and the current tree records its state frames as the verdict's backtests do.

Each stand runs as a process of its own from its own tree, against one data home, so both read the
same archive and the same configuration. The release's code comes from a checkout of its commit,
and each run's header says which code ran. The record lies in the divergence store.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from python.configuration.data_home import get_data_home
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.constants.divergence_aa_constants import (
    AA_DIFFERENCES_SHOWN,
    AA_SCENARIO_NAME,
    AA_SCENARIO_SET_NAME,
)
from python.framework.constants.divergence_report_constants import DIVERGENCE_LIVE_INDEX_FILE
from python.framework.factory.recorded_scenario_builder import RecordedScenarioBuilder
from python.framework.reporting.builders.divergence.aa_comparison_builder import (
    compare_order_events,
    compare_trades,
    fills_of,
)
from python.framework.reporting.builders.divergence.backtest_side_collector import (
    BacktestSideCollector,
)
from python.framework.reporting.builders.divergence.live_segment_builder import (
    derive_segments,
    recorded_window_of,
)
from python.framework.reporting.builders.divergence.live_side_collector import LiveSideCollector
from python.framework.reporting.builders.divergence.session_records import (
    configured_records,
    copied_records,
)
from python.framework.reporting.console.divergence_report_console import render_aa_report
from python.framework.reporting.divergence_report_coordinator import (
    archive_reaches_past,
    newest_run_named,
)
from python.framework.reporting.store.divergence_report_store import (
    new_report_directory,
    write_aa_report,
)
from python.framework.store.broker_config_freeze import read_frozen_broker_config
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.types.api.divergence_report_types import (
    AaReport,
    AaStand,
    AaStandRun,
    AaWindowResult,
    RenderStamp,
)
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.types.scenario_types.recorded_scenario_types import RecordedScenarioInputs
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.git_info_utils import get_framework_root, get_git_info
from python.framework.validators.divergence_aa_validator import judge_aa_window

# The backtest command each stand runs, relative to its tree
_STRATEGY_RUNNER = Path('python') / 'cli' / 'strategy_runner_cli.py'
# A backtest that finished with logged errors still computed its events (exit 3); any other
# non-zero exit means it did not run
_RAN_EXIT_CODES = (0, 3)
# The console's colour codes, removed from a failed run's lines before they are quoted
_ANSI = re.compile(r'\x1b\[[0-9;]*m')
# How many of a failed run's error lines a refusal quotes
_ERROR_LINES_QUOTED = 3


class DivergenceAaCoordinator:
    """
    Runs one A/A and keeps its record.

    Args:
        deployment_id: The deployment whose strategy and account are backtested
        release_tree: A checkout of the release the deployment's sessions ran
        windows: The windows to backtest as (start, end); empty for the deployment's own windows
        until: The boundary the deployment's own windows end at; None for the last one sealed
        records_root: A copy of the bot host's data home to read the sessions from; None for this
            machine's stores
        run_purpose: What the backtests' runs are for — REGULAR for an A/A, FIXTURE in a test
    """

    def __init__(self, deployment_id: str, release_tree: Path,
                 windows: Sequence[Tuple[datetime, datetime]] = (),
                 until: Optional[datetime] = None, records_root: Optional[Path] = None,
                 run_purpose: RunPurpose = RunPurpose.REGULAR):
        self._deployment_id = deployment_id
        self._release_tree = release_tree
        self._windows = list(windows)
        self._until = until
        self._records_root = records_root
        self._run_purpose = run_purpose

    def run(self) -> Tuple[AaReport, str, Path]:
        """
        The A/A, from the deployment's records to its record.

        Returns:
            (the report, its text as printed, the A/A's directory)
        """
        report_dir = new_report_directory()
        records = (copied_records(self._records_root, report_dir / DIVERGENCE_LIVE_INDEX_FILE)
                   if self._records_root is not None else configured_records())
        collector = LiveSideCollector(
            ledger=records.ledger, run_index=records.run_index, reader=records.reader,
            store=records.report_store, run_configs=records.run_configs,
            read_broker_config=lambda document_id: read_frozen_broker_config(
                document_id, records.run_configs))
        capture = collector.collect_sessions(self._deployment_id)
        segments, _ = derive_segments(capture, self._until, ())
        commits = sorted({s.code_identity.framework.commit for s in capture.sessions
                          if s.code_identity is not None and s.code_identity.framework is not None
                          and s.code_identity.framework.commit})
        home = get_data_home()
        git = get_git_info()
        report = AaReport(
            report_id=report_dir.name, created_at=datetime.now(timezone.utc),
            deployment_id=self._deployment_id,
            symbol=next((s.symbol for s in capture.sessions if s.symbol), '') or '',
            broker_type=capture.broker_type, session_commit=commits[0] if commits else '',
            release_tree=str(self._release_tree),
            current_commit=git.commit if git is not None else '',
            current_dirty=git.dirty if git is not None else True,
            records_root=records.root, data_home=str(home) if home is not None else '')
        refusal = aa_refusal(home, self._release_tree, commits, bool(segments))
        if refusal:
            report = report.model_copy(update={'refused_because': refusal})
        else:
            first = recorded_window_of(segments[0])
            rendered = records.run_configs.read_frozen(first.rendered_config_id,
                                                       RunConfigKind.AUTOTRADER_RENDERED)
            windows = self._windows or [(s.window.start, s.window.end) for s in segments]
            report = report.model_copy(update={'windows': [
                self._window(index, start, end, rendered, first.balances or {}, report, report_dir,
                             home)
                for index, (start, end) in enumerate(windows, start=1)]})
        text = render_aa_report(report)
        write_aa_report(report, text, report_dir)
        return report, text, report_dir


    def _window(self, index: int, start: Optional[datetime], end: Optional[datetime],
                rendered: Dict[str, Any], balances: Dict[str, float], report: AaReport,
                report_dir: Path, home: Path) -> AaWindowResult:
        """
        One window: both stands' backtests and their comparison.

        Args:
            index: The window's number, from 1
            start: Its start
            end: Its end
            rendered: The sessions' rendered profile
            balances: The account the sessions started with
            report: The A/A so far — its deployment's broker, symbol and commit
            report_dir: The A/A's directory
            home: The data home both stands run against

        Returns:
            The window's result, judged
        """
        scenario = AA_SCENARIO_NAME.format(window=index)
        stands = {stand: AaStandRun(stand=stand, tree=str(tree),
                                    scenario_set_name=AA_SCENARIO_SET_NAME.format(
                                        report_id=report.report_id, window=index,
                                        stand=stand.value))
                  for stand, tree in ((AaStand.RELEASE, self._release_tree),
                                      (AaStand.CURRENT, Path(get_framework_root() or '')))}
        named = AaWindowResult(index=index, start=start, end=end,
                               release=stands[AaStand.RELEASE], current=stands[AaStand.CURRENT])
        if start is None or end is None:
            return named.model_copy(update={
                'not_checked_because': 'the window has no start or no end'})
        refusal = archive_reaches_past(report.broker_type, report.symbol, end)
        if refusal:
            return named.model_copy(update={'not_checked_because': refusal})
        before = self._render_stamps(report.broker_type, report.symbol)
        streams: Dict[AaStand, Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = {}
        for stand, run in stands.items():
            inputs = RecordedScenarioInputs(
                scenario_set_name=run.scenario_set_name, scenario_name=scenario,
                rendered_profile=rendered, warmup_from_run='', broker_config_document='',
                start=start, end=end, balances=balances, run_purpose=self._run_purpose)
            path = RecordedScenarioBuilder.write(
                RecordedScenarioBuilder.build_shared(inputs,
                                                     record_frames=stand is AaStand.CURRENT),
                report_dir)
            stands[stand], streams[stand] = self._backtest(run, path, scenario, home)
        after = self._render_stamps(report.broker_type, report.symbol)
        release_events, release_trades = streams[AaStand.RELEASE]
        current_events, current_trades = streams[AaStand.CURRENT]
        trade_differences, one_sided = compare_trades(release_trades, current_trades)
        differences = compare_order_events(release_events, current_events) + trade_differences
        return judge_aa_window(named.model_copy(update={
            'release': stands[AaStand.RELEASE], 'current': stands[AaStand.CURRENT],
            'render_stamps_before': before, 'render_stamps_after': after,
            'difference_count': len(differences),
            'differences': differences[:AA_DIFFERENCES_SHOWN],
            'one_sided_fields': one_sided}), report.session_commit)

    def _backtest(self, run: AaStandRun, set_path: Path, scenario: str, home: Path
                  ) -> Tuple[AaStandRun, Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
        """
        One stand's backtest, run from its tree, and what its run recorded.

        Args:
            run: The stand, not run yet
            set_path: Its scenario set
            scenario: The scenario's name in the set
            home: The data home it runs against

        Returns:
            (the stand as it ran, (its scenario's order events, its trades))
        """
        tree = Path(run.tree)
        environment = {**os.environ, 'FINIEX_DATA_HOME': str(home),
                       'PYTHONPATH': f'{tree}{os.pathsep}{home}'}
        completed = subprocess.run(
            [sys.executable, str(_STRATEGY_RUNNER), 'run', str(set_path)],
            cwd=tree, env=environment, capture_output=True, text=True, check=False)
        if completed.returncode not in _RAN_EXIT_CODES:
            return run.model_copy(update={'failed_because': (
                f'its backtest ended with exit code {completed.returncode}: '
                f'{_error_lines(completed.stdout + completed.stderr)}')}), ([], [])
        run_id = newest_run_named(run.scenario_set_name)
        if run_id is None:
            return run.model_copy(update={
                'failed_because': f'no run named {run.scenario_set_name} is in the run index'}), \
                ([], [])
        records = configured_records()
        capture = BacktestSideCollector(
            ledger=records.ledger, reader=records.reader,
            run_configs=records.run_configs).collect_run(run_id)
        framework = capture.code_identity.framework if capture.code_identity is not None else None
        events = self._order_events(records.reader, run_id, scenario)
        trades = self._trades(records.reader, run_id, scenario)
        return run.model_copy(update={
            'run_id': run_id,
            'framework_commit': (framework.commit or '') if framework is not None else '',
            'framework_dirty': framework.dirty if framework is not None else None,
            'broker_config_id': ','.join(sorted({row.broker_config_id for row in capture.rows
                                                 if row.broker_config_id})),
            'order_events': len(events) if events is not None else None,
            'fills': fills_of(events) if events is not None else None,
            'trades': len(trades) if trades is not None else None,
        }), (events or [], trades or [])

    @staticmethod
    def _order_events(reader: RunEntryReader, run_id: str,
                      scenario: str) -> Optional[List[Dict[str, Any]]]:
        """
        A run's order events of one scenario, as the stream holds them, in `seq` order.

        Args:
            reader: The run reader
            run_id: The run
            scenario: The scenario

        Returns:
            The events; None where the stream could not be read
        """
        try:
            stream = reader.read_stream(run_id, RunEntryMemberId.ORDER_EVENTS)
        except (OSError, ValueError, KeyError):
            return None
        if stream is None:
            return []
        return sorted((record for record in stream.records
                       if record.get('scenario_name') == scenario),
                      key=lambda record: record.get('seq', 0))

    @staticmethod
    def _trades(reader: RunEntryReader, run_id: str,
                scenario: str) -> Optional[List[Dict[str, Any]]]:
        """
        A run's trades of one scenario, as its trade history holds them.

        Args:
            reader: The run reader
            run_id: The run
            scenario: The scenario

        Returns:
            The trades in their order; None where the history could not be read
        """
        path = reader.path_of(run_id, RunEntryMemberId.TRADE_HISTORY)
        if path is None or not path.exists():
            return []
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        return [row for row in document.get('trades', []) if row.get('scenario_name') == scenario]

    @staticmethod
    def _render_stamps(broker_type: str, symbol: str) -> List[RenderStamp]:
        """
        Every bar file of a symbol the bar index holds, and when each was rendered.

        Args:
            broker_type: The broker
            symbol: The symbol

        Returns:
            The stamps, in timeframe order
        """
        bars_index = BarsIndexManager()
        bars_index.load_index()
        timeframes = list(bars_index.index.get(broker_type, {}).get(symbol, {}))
        records = configured_records()
        return BacktestSideCollector(
            ledger=records.ledger, reader=records.reader,
            run_configs=records.run_configs).collect_render_stamps(
                broker_type, symbol, timeframes, bars_index=bars_index)


def aa_refusal(home: Optional[Path], release_tree: Path, commits: List[str],
               has_segments: bool) -> str:
    """
    Why an A/A cannot run at all, where it cannot.

    Args:
        home: The data home both stands would run against
        release_tree: The checkout of the release
        commits: The framework commits the deployment's sessions ran
        has_segments: Whether the deployment has a segment to take its strategy and account from

    Returns:
        Why not, in a sentence; '' where it can
    """
    if home is None:
        return ('an A/A runs both code stands against one data home, and FINIEX_DATA_HOME names '
                'none — the release\'s tree holds no data of its own')
    if (home / 'python').exists():
        return (f'the data home {home} holds a python/ directory: on the release\'s import path '
                f'its modules would mix into the release\'s own — use a data home without code')
    if not (release_tree / _STRATEGY_RUNNER).is_file():
        return f'the release tree {release_tree} holds no {_STRATEGY_RUNNER}'
    if get_framework_root() is None:
        return 'the current tree is no repository, so nobody can say which code it is'
    if len(commits) != 1:
        return (f'the deployment\'s sessions ran {len(commits)} framework commit(s) '
                f'({", ".join(commits) or "none recorded"}) — an A/A holds one release against '
                f'the current tree')
    if not has_segments:
        return 'the deployment has no segment to take its strategy and account from'
    return ''


def _error_lines(output: str) -> str:
    """
    The lines a failed backtest named its errors in, to quote in its refusal.

    Args:
        output: What the backtest printed

    Returns:
        Its last lines naming an error — the batch's summary names the cause last — or its last
        lines where none does
    """
    lines = [' '.join(_ANSI.sub('', line).split()) for line in output.splitlines()]
    lines = [line for line in lines if line]
    errors = [line for line in lines if line.startswith('Error:')]
    return ' · '.join((errors or lines)[-_ERROR_LINES_QUOTED:])
