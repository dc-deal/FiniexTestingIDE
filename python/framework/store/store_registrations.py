"""
Store registrations (#486) — every data store, declared once.

This module is DATA, not machinery: it names every place the application persists bytes, and
what kind of place it is. A store that is not declared here has no read path — which is the
whole point, and the reason CLAUDE.md §44 makes an entry here part of adding a store.

Roots are resolved from the config managers on every call rather than frozen at import.
`FINIEX_CONFIG_ISOLATION` points a test run at a different tree, and a module-level constant
would happily report the operator's real one.
"""

from pathlib import Path
from typing import Dict

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.kraken_config_fetcher import RUNTIME_CACHE_BASE
from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.signal_index_manager import SignalIndexManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.discoveries.discovery_cache_index import (
    DISCOVERY_INDEX_FILE,
    DiscoveryCacheIndex,
)
from python.framework.persistence.cold_start_state_index import (
    COLD_START_INDEX_FILE,
    ColdStartStateIndex,
)
from python.framework.reporting.certificates.certificate_index import (
    CERTIFICATE_INDEX_FILE,
    CertificateIndex,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_ledger_index import (
    LEDGER_INDEX_FILE,
    RunLedgerIndex,
)
from python.framework.reporting.store.run_results_ledger import LEDGER_COLUMNS
from python.framework.store.store_descriptor import StoreDescriptor
from python.framework.types.log_layout_types import GLOBAL_LOG_FILE
from python.framework.types.store_types import (
    DISCOVERY_CACHE_DIRNAME,
    RetrievalForm,
    StoreBackend,
    StoreId,
    StoreKind,
)
from python.scenario.generator.window_set_serializer import PROFILE_OUTPUT_DIR

# Where the certificate families live. Not configured anywhere and deliberately so: the release
# checklist names these paths, and the certificates are the one store that is committed.
CERTIFICATES_ROOT = Path('tests')
# `**/reports/` and not `*/reports/`: the benchmark family sits one level deeper
# (tests/simulation/benchmark/reports), and a shallower pattern silently drops it.
CERTIFICATE_GLOB = '**/reports/*_report_*.json'


def build_registrations() -> Dict[StoreId, StoreDescriptor]:
    """
    Every registered store, with its root resolved from the current configuration.

    Returns:
        One descriptor per StoreId
    """
    app_config = AppConfigManager()
    file_logging = app_config.get_file_logging_config_object()
    processed = Path(app_config.get_data_processed_path())
    autotrader_defaults = app_config.get_autotrader_defaults()
    state_path = Path(
        autotrader_defaults.get('state_persistence', {}).get(
            'path', 'data/runtime/session_state'))
    cold_start_path = Path(
        autotrader_defaults.get('cold_start', {}).get(
            'path', 'data/runtime/cold_start_state'))

    # The run tree's root is the index's parent by construction (#478 put the index at the top of
    # the tree it describes), so it is derived rather than declared a fourth time.
    runs_root = Path(file_logging.run_index).parent
    ledger_root = Path(app_config.get_run_ledger_path())
    discovery_root = processed / DISCOVERY_CACHE_DIRNAME

    return {
        StoreId.RUNS: StoreDescriptor(
            store_id=StoreId.RUNS,
            kind=StoreKind.RECORD,
            root=runs_root,
            key='run_id',
            form=RetrievalForm.DOCUMENT,
            backend=StoreBackend.DISK,
            entry_glob='**/header.json',
            index_path=Path(file_logging.run_index),
            index_factory=lambda: RunIndex(file_logging.run_index, file_logging.run_logs),
        ),
        StoreId.RUN_LEDGER: StoreDescriptor(
            store_id=StoreId.RUN_LEDGER,
            kind=StoreKind.RECORD,
            root=ledger_root,
            key='run_id (a column, never a folder)',
            form=RetrievalForm.SET,
            backend=StoreBackend.DISK,
            entry_glob='*.parquet',
            index_path=ledger_root / LEDGER_INDEX_FILE,
            index_factory=lambda: RunLedgerIndex(ledger_root, LEDGER_COLUMNS),
            self_healing=True,
        ),
        StoreId.CERTIFICATES: StoreDescriptor(
            store_id=StoreId.CERTIFICATES,
            kind=StoreKind.RECORD,
            root=CERTIFICATES_ROOT,
            key='family + release version + date',
            form=RetrievalForm.DOCUMENT,
            backend=StoreBackend.DISK,
            entry_glob=CERTIFICATE_GLOB,
            index_path=CERTIFICATES_ROOT / CERTIFICATE_INDEX_FILE,
            index_factory=lambda: CertificateIndex(CERTIFICATES_ROOT),
        ),
        StoreId.COLD_START_STATE: StoreDescriptor(
            store_id=StoreId.COLD_START_STATE,
            kind=StoreKind.CARRY_OVER,
            root=cold_start_path,
            key='<profile>_<symbol>',
            form=RetrievalForm.DOCUMENT,
            backend=StoreBackend.DISK,
            entry_glob='*.json',
            index_path=cold_start_path / COLD_START_INDEX_FILE,
            index_factory=lambda: ColdStartStateIndex(cold_start_path),
            note='The FRAMEWORK carry-over beside the algo one (#355): the session keys this '
                 'bot sent orders under, the highest position counter it minted, and the open '
                 'position book — at spot a holding is a balance the venue cannot describe as '
                 'a position, so it only survives a restart because we wrote it down. Its own '
                 'store rather than a section in session_state, because that one is gated '
                 'behind the algo opt-in while this must be written for every live bot. It '
                 'HAS an index because something searches ACROSS bots — which bot carries '
                 'what, since when, from which run. Written at boot, at shutdown, and '
                 'whenever the open book changes. History is deliberately absent: a '
                 'carry-over overwrites, so what a given boot adopted lives in that run RECORD '
                 'and the two indexes are joined.',
        ),
        StoreId.SESSION_STATE: StoreDescriptor(
            store_id=StoreId.SESSION_STATE,
            kind=StoreKind.CARRY_OVER,
            root=state_path,
            key='<profile>_<symbol>',
            form=RetrievalForm.DOCUMENT,
            backend=StoreBackend.DISK,
            entry_glob='*.json',
            note='No index: a bot opens its own file by key, which is not a search.',
        ),
        StoreId.TICKS: StoreDescriptor(
            store_id=StoreId.TICKS,
            kind=StoreKind.ARCHIVE,
            root=processed,
            key='broker / symbol / file',
            form=RetrievalForm.RANGE,
            backend=StoreBackend.DISK,
            entry_glob='*/ticks/**/*.parquet',
            index_path=processed / TickIndexManager.INDEX_FILE_PARQUET,
            note='Index owned by TickIndexManager; converges on the shared base under #175.',
        ),
        StoreId.BARS: StoreDescriptor(
            store_id=StoreId.BARS,
            kind=StoreKind.DERIVED,
            root=processed,
            key='broker / symbol / timeframe',
            form=RetrievalForm.RANGE,
            backend=StoreBackend.DISK,
            # The manager scans '*_BARS.parquet' (bars_index_manager.py:94). The catalog must
            # count what the index holds, not a wider set that happens to be identical today.
            entry_glob='*/bars/**/*_BARS.parquet',
            index_path=processed / BarsIndexManager.INDEX_FILE_PARQUET,
            derived_from=StoreId.TICKS,
            note=('DERIVED, not archive: rendered from the tick archive, and today only by a full '
                  're-render (bar_importer.py:347 clean_mode → _clean_bars). Every bar file already '
                  'carries its build stamp — importer_version, rendered_at, source_version_min/max '
                  '— and nothing compares it; that check is #175. Index owned by BarsIndexManager.'),
        ),
        StoreId.SIGNALS: StoreDescriptor(
            store_id=StoreId.SIGNALS,
            kind=StoreKind.ARCHIVE,
            root=processed / 'signals',
            key='sentiment type / symbol / day',
            form=RetrievalForm.RANGE,
            backend=StoreBackend.DISK,
            # Matches SignalIndexManager's own scan depth — one level per sentiment type.
            entry_glob='*/*.parquet',
            index_path=processed / 'signals' / SignalIndexManager.INDEX_FILE_PARQUET,
            note='Index owned by SignalIndexManager; converges on the shared base under #175.',
        ),
        StoreId.DISCOVERY_CACHES: StoreDescriptor(
            store_id=StoreId.DISCOVERY_CACHES,
            kind=StoreKind.DERIVED,
            root=discovery_root,
            key='family / broker_symbol',
            form=RetrievalForm.DOCUMENT,
            backend=StoreBackend.DISK,
            entry_glob='**/*.parquet',
            index_path=discovery_root / DISCOVERY_INDEX_FILE,
            index_factory=lambda: DiscoveryCacheIndex(discovery_root),
            derived_from=StoreId.BARS,
        ),
        StoreId.BROKER_RUNTIME: StoreDescriptor(
            store_id=StoreId.BROKER_RUNTIME,
            kind=StoreKind.DERIVED,
            root=RUNTIME_CACHE_BASE,
            key='broker_type',
            form=RetrievalForm.DOCUMENT,
            backend=StoreBackend.DISK,
            entry_glob='*/*_broker_config.json',
            derived_from=None,
            note=('No index: one file per broker, opened by a known key. `derived_from` stays None '
                  'on purpose — its source is a remote broker API, which is not a store and cannot '
                  'be named as one. Its own freshness ladder lives in the fetcher (7/30 days).'),
        ),
        StoreId.GENERATOR_PROFILES: StoreDescriptor(
            store_id=StoreId.GENERATOR_PROFILES,
            kind=StoreKind.DERIVED,
            root=PROFILE_OUTPUT_DIR,
            key='mode / broker / symbol',
            form=RetrievalForm.DOCUMENT,
            backend=StoreBackend.DISK,
            entry_glob='*/*/*.json',
            derived_from=StoreId.DISCOVERY_CACHES,
            note=('Generated window sets. Each carries `profile_meta.discovery_fingerprints` for '
                  'all three cache families, and `ProfileLoader.validate_fingerprints` already '
                  'compares them — the one derived store in this project whose validity rule was '
                  'complete before the model existed. Lives under configs/ and is VERSIONED, which '
                  'is unusual for a DERIVED store; the kind describes its nature, not its tracking.'),
        ),
        StoreId.FINISHED_ARCHIVE: StoreDescriptor(
            store_id=StoreId.FINISHED_ARCHIVE,
            kind=StoreKind.ARCHIVE,
            root=Path(ImportConfigManager().get_data_finished_path()),
            key='file name',
            form=RetrievalForm.DOCUMENT,
            backend=StoreBackend.DISK,
            entry_glob='**/*.json',
            note=('Where the conveyor ends: the raw collector JSON the whole archive was imported '
                  'from, moved here unchanged. No index — nothing searches it; it is opened by '
                  'name when a file has to be re-examined.'),
        ),
        StoreId.RAW_INBOX: StoreDescriptor(
            store_id=StoreId.RAW_INBOX,
            kind=StoreKind.SPECIAL,
            root=Path(ImportConfigManager().get_data_raw_path()),
            key='file name',
            form=RetrievalForm.NONE,
            backend=StoreBackend.DISK,
            note=('Conveyor, not a store: a file lies here in order to disappear. The importer '
                  'reads it and MOVES it to the finished archive — it never rewrites the '
                  'content. A header and an index would make it something it is not.'),
        ),
        StoreId.GLOBAL_LOG: StoreDescriptor(
            store_id=StoreId.GLOBAL_LOG,
            kind=StoreKind.SPECIAL,
            root=Path(file_logging.global_log_dir) / GLOBAL_LOG_FILE,
            key='—',
            form=RetrievalForm.NONE,
            backend=StoreBackend.DISK,
            note=('Append stream without identity. It gets bounding and rotation (#476), '
                  'never an index.'),
        ),
    }
