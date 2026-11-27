"""
FiniexTestingIDE - AutoTrader Storage Watch (#632)

The free space under a running session's records, read on a timer of the session's timeline. A disk
that fills during a thirty-day run stops every record at once, and without a watch the first sign is
the first failed write; with one, the operator hears it while there is still room to act. One alert
episode per filesystem: once when its free space falls below the warning floor, once when it has
stayed above it for the operator alerts' all-clear period.
"""

from pathlib import Path
from typing import Dict, Optional

from python.framework.autotrader.operator_alerts.alert_episode import AlertEpisode, all_clear_detail
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.config_types.autotrader_defaults_config_types import StorageDefaults
from python.framework.types.store_types import StoreId
from python.framework.utils.machine_resources_reader import MachineResourcesReader

_BYTES_PER_GB = 1024 ** 3


class AutotraderStorageWatch:
    """
    Watches the filesystems a session's stores live on.

    Args:
        storage: The session's storage block — the warning floor and the interval
        roots: Each store the session writes into, with its root
        executor: The session's executor — the canonical clock
        operator_alerts: Where a low disk is reported out of band; None without a channel
        all_clear_after_s: How long the space must stay above the floor before the all-clear
        logger: The session logger
        reader: The machine's reader
    """

    def __init__(self, storage: StorageDefaults, roots: Dict[StoreId, Path],
                 executor: AbstractTradeExecutor, operator_alerts: Optional[OperatorAlerts],
                 all_clear_after_s: float, logger: AbstractLogger,
                 reader: Optional[MachineResourcesReader] = None):
        self._warn_bytes = storage.warn_free_gb * _BYTES_PER_GB
        self._warn_free_gb = storage.warn_free_gb
        self._interval_s = storage.check_interval_s
        self._roots = roots
        self._executor = executor
        self._operator_alerts = operator_alerts
        self._all_clear_after_s = all_clear_after_s
        self._logger = logger
        self._reader = reader if reader is not None else MachineResourcesReader()
        # One episode per filesystem, keyed by its device
        self._episodes: Dict[int, AlertEpisode] = {}
        # Whether the last reading failed — said once per stretch of failures
        self._read_failed = False

    def get_interval_s(self) -> float:
        """
        How often the space is read.

        Returns:
            Seconds between two readings
        """
        return self._interval_s

    def check(self) -> None:
        """
        Read the free space of every filesystem once, and alert on crossing the warning floor.

        A reading that fails — a mount that went away, a folder that cannot be listed — is said once
        and tried again at the next interval: the watch reports on the session, it never stops it.
        """
        now = self._executor.get_current_time()
        try:
            readings = self._reader.read_disks(self._roots)
        except OSError as error:
            if not self._read_failed:
                self._logger.warning(f'⚠️ Disk unreadable: the free space under the session\'s records '
                                     f'could not be read ({type(error).__name__}: {error}) — read again '
                                     f'every {self._interval_s:g} s (autotrader.storage.check_interval_s)')
            self._read_failed = True
            return
        self._read_failed = False
        for reading in readings:
            episode = self._episodes.setdefault(reading.device, AlertEpisode(self._all_clear_after_s))
            stores = ', '.join(store.value for store in reading.stores)
            disk = f'the disk under {self._roots[reading.stores[0]].absolute()} (the {stores} store(s))'
            free_gb = reading.free_bytes / _BYTES_PER_GB
            if reading.free_bytes < self._warn_bytes:
                if episode.opens(now):
                    message = (f'{free_gb:.1f} GB free on {disk}, below '
                               f'the warning floor of {self._warn_free_gb:g} GB '
                               f'(autotrader.storage.warn_free_gb) — the session\'s records stop '
                               f'when it is full. Free space there, or the session goes on '
                               f'without a record.')
                    self._logger.warning(f'⚠️ Disk low: {message}')
                    self._send(OperatorAlertKind.DISK_LOW, OperatorAlertLevel.WARNING,
                               f'Disk low — {free_gb:.1f} GB free', message)
                continue
            episode.clears(now)
            if episode.all_clear_due(now):
                self._logger.info(f'✅ Disk recovered: {free_gb:.1f} GB free on {disk}')
                self._send(OperatorAlertKind.DISK_RECOVERED, OperatorAlertLevel.INFO,
                           f'Disk recovered — {free_gb:.1f} GB free', all_clear_detail(episode))

    def _send(self, kind: OperatorAlertKind, level: OperatorAlertLevel, title: str,
              detail: str) -> None:
        """
        Report out of band, when the session has a channel.

        Args:
            kind: Which moment
            level: How urgent
            title: The preview line
            detail: The further lines
        """
        if self._operator_alerts is not None:
            self._operator_alerts.send(kind, level, title, detail)
