"""
FiniexTestingIDE - Sealed Period Booker (#476)

Writes an AutoTrader session's booking periods into the results ledger as each trading day seals,
not only when the session ends. A session killed on day 29 used to leave only its header behind;
it now leaves a ledger row for every day it sealed, and the missing closing row is what says it
did not end.

Every write replaces the session's one fragment with all periods so far — never a duplicate, and
the session's last write (the report coordinator's) replaces it with the complete set, the
run-end columns filled. The rows written here carry what the start already knows and leave the
run-end columns empty.

A booking that cannot be written is said in the session log, where it counts as an error of the
run, and never ends the session: the day's line in that log carries the same figures, and the
next seal writes the whole fragment again.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.store.run_provenance_builder import (
    build_run_provenance_from_session,
)
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.run_results_types import BookingPeriod, RunProvenance
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


class SealedPeriodBooker:
    """
    Books a running session's sealed periods into the results ledger.

    Args:
        config: The session's profile
        run_id: The session's run id
        run_timestamp: When the session started (UTC)
        deployment_id: The resolved deployment, '' for a session that stands alone
        run_dir: The session's run directory, where its header names the code it runs
        logger: The session logger — where a failed write is said
        replayed_scenario: A mock session's scenario after the mount filled what it read; None
            for a live-adapter session
        broker_config_id: The run-config store id the session froze its broker configuration
            under at its start; '' where it froze none
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        run_id: str,
        run_timestamp: datetime,
        deployment_id: str,
        run_dir: Optional[Path],
        logger: AbstractLogger,
        replayed_scenario: Optional[SingleScenario] = None,
        broker_config_id: str = '',
    ):
        self._config = config
        self._run_id = run_id
        self._run_timestamp = run_timestamp
        self._deployment_id = deployment_id
        self._run_dir = run_dir
        self._logger = logger
        self._replayed_scenario = replayed_scenario
        self._broker_config_id = broker_config_id
        # Built at the first seal and kept: nothing in it moves while the session runs
        self._provenance: Optional[RunProvenance] = None

    def book(self, periods: List[BookingPeriod]) -> None:
        """
        Write the periods sealed so far as the session's ledger fragment.

        Args:
            periods: Every period the session sealed, oldest first
        """
        try:
            if self._provenance is None:
                self._provenance = build_run_provenance_from_session(
                    self._config, self._run_id, self._run_timestamp, None,
                    deployment_id=self._deployment_id, run_dir=self._run_dir,
                    logger=self._logger, replayed_scenario=self._replayed_scenario,
                    broker_config_id=self._broker_config_id)
            RunResultsLedger(Path(AppConfigManager().get_run_ledger_path())).write_sealed_periods(
                self._provenance, periods)
        except Exception as error:   # noqa: BLE001 — a booking must never end a trading session
            which = f' (booking period {periods[-1].period_no})' if periods else ''
            self._logger.error(
                f'📒 The ledger rows of the sealed trading day{which} could not be written '
                f'({type(error).__name__}: {error}). The session goes on; the period line above '
                f'carries the same figures, and the next seal writes every period again.')
