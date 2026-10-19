"""
FiniexTestingIDE - Broker Config Freeze

Keeps the broker configuration a run trades with, as it stands when the run starts (#547) — one
path for both pipelines, so a session and a backtest of its window name their configuration the
same way.
"""

from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.store.run_config_store import RunConfigStore
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.run_config_types import RunConfigKind
from python.framework.utils.config_fingerprint_utils import to_plain


def freeze_broker_config(broker_config: BrokerConfig, logger: AbstractLogger) -> None:
    """
    Freeze the broker configuration a run trades with, as it stands at the start (#547).

    What a run trades with is assembled when it starts: for a dynamic broker the symbol
    specifications come from the runtime cache, which a later fetch overwrites and nothing
    versions, beside the seed's fee structure — and in a live session the fee tier the venue
    reports. An eight-character hash of it says later THAT two runs differed, never what either
    had; the frozen copy is what a reader of an old run, or a backtest of a session's window,
    reads again.

    The id travels on the configuration object into the run's broker section, and into the log
    right now — the one record that survives a run killed before its report.

    Never fatal: a store that cannot be written costs the record, not the run.

    Args:
        broker_config: The configuration the run trades with
        logger: The run's logger
    """
    try:
        store = RunConfigStore(Path(AppConfigManager().get_run_configs_path()))
        entry = store.register_document(
            to_plain(broker_config.adapter.broker_config), RunConfigKind.BROKER_CONFIG)
        store.note_run(entry.config_id)
    except (OSError, ValueError, KeyError) as error:
        logger.warning(
            f'⚠️  The broker configuration could not be frozen ({type(error).__name__}: {error})'
            f' — what this run traded with cannot be read back later.')
        return
    broker_config.set_frozen_config_id(entry.config_id)
    logger.info(
        f'🏦 Broker configuration frozen as broker_config {entry.config_id[:12]} '
        f'(config_hash {broker_config.config_hash or "—"})')
