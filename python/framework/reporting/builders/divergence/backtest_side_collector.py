"""
The backtest side of the divergence report (#512) — what a finished backtest run recorded, read
once, before anything is judged.

The divergence gate reads what is here; the measures read more of the same run later. Nothing is
derived: a record that cannot be read is named on the capture, never replaced by a default.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.constants.run_layout_constants import RUN_TYPE_SIMULATION
from python.framework.data_preparation.tick_parquet_reader import read_tick_parquet
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.artifact_specs import SCENARIO_DETAILS_ARTIFACT
from python.framework.reporting.io.order_event_stream_io import order_events_of
from python.framework.reporting.io.state_frame_stream_io import read_recorded_frames
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.divergence_report_types import RenderStamp
from python.framework.types.api.report_types import (
    DataWindow,
    OrderEventRow,
    RunHeader,
    ScenarioDetailsRow,
)
from python.framework.types.divergence_capture_types import (
    BacktestRunCapture,
    BacktestScenarioCapture,
    BacktestScenarioRecords,
)
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.store_member_types import RunEntryMemberId
from python.framework.utils.time_utils import parse_iso_datetime

# The tick columns a run of missing trade ids is found from — with the two the tick reader derives
# its price from, which it cannot do without them
_TRADE_COLUMNS = ['trade_id', 'time_msc', 'timestamp', 'bid', 'ask']

# What a read can fail with when a record is missing, damaged or of a shape this reader does not
# know — a data condition, named on the record; anything else is a fault and propagates
_UNREADABLE = (ReportArtifactUnreadableError, OSError, ValueError, KeyError)


class BacktestSideCollector:
    """
    Reads one finished backtest run's records.
    """

    def __init__(self,
                 ledger: Optional[RunResultsLedger] = None,
                 reader: Optional[RunEntryReader] = None,
                 run_configs: Optional[RunConfigStore] = None):
        """
        Args:
            ledger: The run-results ledger; the configured one when omitted
            reader: The run reader; one over the configured run index when omitted
            run_configs: The run-config store the run's rendered scenario set is read from; the
                configured one when omitted
        """
        if ledger is None or reader is None or run_configs is None:
            config = AppConfigManager()
            ledger = ledger or RunResultsLedger(Path(config.get_run_ledger_path()))
            reader = reader or RunEntryReader(
                Path(config.get_file_logging_config_object().run_index))
            run_configs = run_configs or RunConfigStore(Path(config.get_run_configs_path()))
        self._ledger = ledger
        self._reader = reader
        self._run_configs = run_configs

    def collect_run(self, run_id: str) -> BacktestRunCapture:
        """
        One backtest run: its header, its ledger rows and its scenarios as they ran.

        Args:
            run_id: The run

        Returns:
            The run as its records show it
        """
        unreadable: Dict[str, str] = {}
        header = self._header(run_id, unreadable)
        rows = self._ledger.read_rows(run_id=run_id)
        if header is None and not rows:
            raise ValueError(f'No run {run_id!r} — neither a header nor a ledger row names it.')
        if header is not None and header.run_type != RUN_TYPE_SIMULATION:
            raise ValueError(f'The run {run_id!r} is a {header.run_type} run, not a backtest.')
        details = self._scenario_details(run_id, unreadable)
        windows = {window.unit_name: window for window in (
            header.data_windows if header is not None and header.data_windows else [])}
        names = sorted(set(details) | set(windows))
        strategies = self._rendered_strategies(
            header.rendered_config_id if header is not None else '', unreadable)
        return BacktestRunCapture(
            run_id=run_id,
            app_version=header.app_version if header is not None else None,
            code_identity=header.code_identity if header is not None else None,
            rows=rows,
            scenarios=[self._scenario(run_id, name, windows.get(name), details.get(name),
                                      strategies.get(name), unreadable) for name in names],
            unreadable=unreadable)

    def collect_scenario_records(self, run_id: str, scenario: str) -> BacktestScenarioRecords:
        """
        What one scenario of a backtest run recorded as it ran: its order events and its frames.

        Args:
            run_id: The run
            scenario: The scenario

        Returns:
            Its records; what could not be read is named, never filled in
        """
        unreadable: Dict[str, str] = {}
        events: List[OrderEventRow] = []
        try:
            stream = self._reader.read_stream(run_id, RunEntryMemberId.ORDER_EVENTS)
            if stream is not None:
                rows, _truths, truncated = order_events_of(
                    stream, RunEntryMemberId.ORDER_EVENTS.value, run_id)
                events = sorted((row for row in rows if row.scenario_name == scenario),
                                key=lambda row: row.seq)
                if truncated:
                    unreadable['order_events'] = 'its last line was cut off mid-write'
        except _UNREADABLE as e:
            unreadable['order_events'] = str(e)
        return BacktestScenarioRecords(
            scenario=scenario, order_events=events,
            frames=read_recorded_frames(self._reader, run_id,
                                        RunEntryMemberId.SCENARIO_STATE_FRAMES, scenario=scenario),
            unreadable=unreadable)

    def collect_render_stamps(self, broker_type: str, symbol: str, timeframes: List[str],
                              bars_index: Optional[BarsIndexManager] = None) -> List[RenderStamp]:
        """
        The bar files a backtest of a symbol read, and when each was rendered.

        Args:
            broker_type: The broker
            symbol: The symbol
            timeframes: The timeframes the backtest read
            bars_index: The bar index; the configured one, loaded, when omitted

        Returns:
            One stamp per timeframe the index holds a file for, in timeframe order
        """
        if bars_index is None:
            bars_index = BarsIndexManager()
            bars_index.load_index()
        entries = bars_index.index.get(broker_type, {}).get(symbol, {})
        stamps: List[RenderStamp] = []
        for timeframe in sorted(timeframes):
            entry = entries.get(timeframe)
            if entry is None:
                continue
            rendered_at = entry.get('rendered_at')
            stamps.append(RenderStamp(
                timeframe=timeframe, bar_file=Path(entry['path']).name,
                rendered_at=rendered_at if rendered_at and rendered_at != 'unknown' else None))
        return stamps

    def _header(self, run_id: str, unreadable: Dict[str, str]) -> Optional[RunHeader]:
        """
        A run's header.

        Args:
            run_id: The run
            unreadable: Where a failed read is named

        Returns:
            The header; None where it is missing or cannot be read
        """
        try:
            return self._reader.read_header(run_id)
        except _UNREADABLE as e:
            unreadable['header'] = str(e)
            return None

    def _scenario_details(self, run_id: str,
                          unreadable: Dict[str, str]) -> Dict[str, ScenarioDetailsRow]:
        """
        What each scenario of a run processed, from its scenario details.

        Args:
            run_id: The run
            unreadable: Where a failed read is named

        Returns:
            Scenario name → its row; empty where the section is missing or cannot be read
        """
        try:
            report = self._reader.read_section(run_id, SCENARIO_DETAILS_ARTIFACT)
        except _UNREADABLE as e:
            unreadable['scenario_details'] = str(e)
            return {}
        if report is None:
            unreadable['scenario_details'] = 'the run holds no scenario details'
            return {}
        return {row.name: row for row in report.units}

    def _rendered_strategies(self, rendered_config_id: str,
                             unreadable: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
        """
        Each scenario's strategy as its components were built, from the run's rendered set.

        Args:
            rendered_config_id: The run-config store id its header names; '' where it names none
            unreadable: Where a failed read is named

        Returns:
            Scenario name → its rendered strategy; empty where the set cannot be read
        """
        if not rendered_config_id:
            unreadable['rendered_config'] = 'its header names no rendered scenario set'
            return {}
        try:
            document = self._run_configs.read_frozen(rendered_config_id,
                                                     RunConfigKind.SCENARIO_SET_RENDERED)
        except _UNREADABLE as e:
            unreadable['rendered_config'] = str(e)
            return {}
        scenarios = document.get('scenarios')
        if not isinstance(scenarios, dict):
            unreadable['rendered_config'] = 'its rendered scenario set holds no scenarios'
            return {}
        return {name: scenario['strategy_config'] for name, scenario in scenarios.items()
                if isinstance(scenario, dict) and isinstance(scenario.get('strategy_config'), dict)}

    def _scenario(self, run_id: str, name: str, window: Optional[DataWindow],
                  details: Optional[ScenarioDetailsRow],
                  strategy: Optional[Dict[str, Any]],
                  unreadable: Dict[str, str]) -> BacktestScenarioCapture:
        """
        One scenario as the run recorded it.

        Args:
            run_id: The run
            name: The scenario
            window: The window its header declares; None where it declares none
            details: Its scenario details; None where the run holds none for it
            strategy: Its rendered strategy; None where it cannot be read
            unreadable: Where a failed read is named

        Returns:
            The scenario's capture
        """
        return BacktestScenarioCapture(
            name=name, declared_window=window,
            first_tick=_tick_instant(details.first_tick_time if details else '',
                                     f'{name}.first_tick_time', unreadable),
            last_tick=_tick_instant(details.last_tick_time if details else '',
                                    f'{name}.last_tick_time', unreadable),
            records_frames=self._reader.has(
                run_id, RunEntryMemberId.SCENARIO_STATE_FRAMES, scenario=name),
            rendered_strategy_config=strategy)


def _tick_instant(text: str, name: str, unreadable: Dict[str, str]) -> Optional[datetime]:
    """
    A tick time the scenario details wrote, read back.

    Args:
        text: The ISO text; '' where the scenario processed no tick
        name: What it is, for a failed read
        unreadable: Where a failed read is named

    Returns:
        The instant; None where there is none or it cannot be read
    """
    if not text:
        return None
    try:
        return parse_iso_datetime(text)
    except ValueError as e:
        unreadable[name] = str(e)
        return None


def window_trades(tick_index: TickIndexManager, broker_type: str, symbol: str, start: datetime,
                  end: datetime) -> Tuple[np.ndarray, np.ndarray]:
    """
    The trade ids and event times of the archive's ticks inside a window.

    Args:
        tick_index: The tick index
        broker_type: The broker
        symbol: The symbol
        start: The window's start
        end: The window's end

    Returns:
        (trade ids, event times in epoch milliseconds) of every tick that carries an id
    """
    frames = [read_tick_parquet(path, columns=_TRADE_COLUMNS)
              for path in tick_index.get_relevant_files(broker_type, symbol, start, end)]
    frames = [frame for frame in frames if 'trade_id' in frame.columns]
    if not frames:
        return np.array([], dtype='int64'), np.array([], dtype='int64')
    ticks = pd.concat(frames, ignore_index=True)
    ticks = ticks[ticks['trade_id'].notna()]
    msc = _event_msc(ticks)
    inside = (msc >= _to_msc(start)) & (msc < _to_msc(end))
    return ticks['trade_id'].to_numpy(dtype='int64')[inside], msc[inside]


def _event_msc(ticks: pd.DataFrame) -> np.ndarray:
    """
    Each tick's event time in epoch milliseconds — the same rule the deficiency scan reads by,
    written here because that scan lies in the backtest's code path.

    Args:
        ticks: The ticks

    Returns:
        Milliseconds, from time_msc where present, else from the timestamp
    """
    if 'time_msc' in ticks.columns and ticks['time_msc'].notna().all():
        return ticks['time_msc'].astype('int64').to_numpy()
    stamps = pd.to_datetime(ticks['timestamp'], utc=True)
    return (stamps.astype('int64') // 1_000_000).to_numpy()


def _to_msc(instant: datetime) -> int:
    """
    An instant in epoch milliseconds.

    Args:
        instant: A timezone-aware instant

    Returns:
        Its milliseconds since the epoch
    """
    return int(instant.timestamp() * 1000)
