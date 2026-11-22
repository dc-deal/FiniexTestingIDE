"""
FiniexTestingIDE - Signal Data Importer

Converts archived signal JSONL (#429) into columnar parquet and rebuilds the signal index.

One JSONL line = one AnalysisEnvelope + collected_msc. Each envelope explodes into one row
per (collected_msc, symbol) for present result symbols, plus one envelope-level sentinel row
(symbol = SIGNAL_ENVELOPE_SYMBOL) so every collected_msc stays resolvable for every covered
symbol — preserving the v0 provider's partial/error → defensive-HOLD behavior. Output:
<target_dir>/<pipeline_id>/<day>.parquet, keyed by pipeline_id (= data_sentiment_type).

A producer's export names a file after its UTC day (`2026-11-10.jsonl`); a producer's package
names it after its pipeline and day (`crypto_sentiment_2026-11-10.jsonl`), so that two
pipelines' days never share a name outside their zip. Both write the same parquet, named by the
day, and the pipeline in a name must be the one its envelopes carry.

A loose JSONL lies flat in the one import inbox beside the tick exports, and leaves it the way
they do: to the finished directory once the archive holds it, to the refused directory with its
reason when not. The pipeline is read from the envelopes, never from a folder, so the inbox needs
none. The raw file is not a by-product: the parquet is a lean projection, and the envelope's
sources / metadata / errors survive nowhere else — the finished file, or the zip the file came
in, stays the audit source and must remain readable.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

from python.configuration.data_origin_registry import DataOriginRegistry
from python.data_management.importers.import_inbox_conveyor import ImportInboxConveyor
from python.data_management.index.signal_index_manager import SignalIndexManager
from python.framework.exceptions.signal_data_errors import (
    SignalAlreadyImportedError,
    SignalSchemaError,
)
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.signal_data.signal_jsonl_loader import load_signal_series
from python.framework.types.import_types.import_inbox_types import (
    InboxDisposition,
    InboxImportOutcome,
    InboxImportResult,
)
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL,
    SentimentResult,
    SignalParquetColumn,
    SignalSnapshot,
)
from python.framework.types.store_types import StoreId
from python.framework.utils.atomic_file_utils import write_file_atomic
from python.framework.utils.file_utils import create_directory

vLog = get_global_logger()

# A package member's name: the pipeline id (itself holding underscores), an underscore, the
# UTC day — read from the right, as a package id is
_PIPELINE_DAY_STEM = re.compile(r'^(?P<pipeline_id>.+)_(?P<day>\d{4}-\d{2}-\d{2})$')


def _epoch_ms(moment: Optional[datetime]) -> Optional[int]:
    """
    Render a datetime as epoch milliseconds for a parquet column.

    Args:
        moment: A tz-aware datetime, or None

    Returns:
        Epoch-ms int, or None when the moment is absent — never a placeholder, so
        "absent" stays distinguishable from zero
    """
    return None if moment is None else int(moment.timestamp() * 1000)


class SignalDataImporter:
    """
    Converts signal JSONL into columnar parquet (#429).

    Args:
        source_dir: The import inbox the loose JSONL lie in, flat
        target_dir: Parquet output root (e.g. data/processed/signals)
        override: Replace an existing parquet instead of skipping it. Same meaning as the
            tick importer's flag of that name, deliberately — one word, one contract
        move_processed_files: Move each loose JSONL out of the inbox after its import
        finished_dir: Where a loose JSONL goes once the archive holds it
        refused_dir: Where a loose JSONL goes that the import refused or failed on, with its
            reason beside it; None keeps it in the inbox
    """

    VERSION = '1.1'

    def __init__(self, source_dir: str, target_dir: str, override: bool = False,
                 move_processed_files: bool = True, finished_dir: Optional[str] = None,
                 refused_dir: Optional[str] = None):
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        create_directory(self.target_dir, holds='the imported signal files')

        self.override = override
        self._conveyor = ImportInboxConveyor(
            finished_dir=Path(finished_dir) if move_processed_files and finished_dir else None,
            refused_dir=Path(refused_dir) if move_processed_files and refused_dir else None)

        # Import statistics
        self.processed_files = 0
        self.total_rows = 0
        self.moved_files = 0
        # The loose files this import moved to the refused directory
        self._refused_files: List[str] = []
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def process_all_signals(self) -> None:
        """
        Convert every loose *.jsonl of the inbox, then rebuild the index.
        Errors do not stop processing of remaining files.

        The import's own steps, run one after the other — the same methods the import
        coordinator calls one by one, so both paths share one implementation.
        """
        jsonl_files = self.collect_files()

        if not jsonl_files:
            vLog.warning(
                f'No signal JSONL found in {self.source_dir}. Rebuilding index only.')
            self.rebuild_index()
            return

        self.log_header(len(jsonl_files))

        for jsonl_file in jsonl_files:
            self.import_file(jsonl_file)

        self.rebuild_index()
        self._print_summary()

    def collect_files(self) -> List[Path]:
        """
        The loose signal files of the inbox. They lie flat in it, as the tick exports do.

        Returns:
            The *.jsonl files, sorted by name
        """
        return sorted(self.source_dir.glob('*.jsonl'))

    def log_header(self, file_count: int) -> None:
        """
        Log the import's header: version, file count, override mode.

        Args:
            file_count: Number of files about to be imported

        Returns:
            None
        """
        vLog.info('\n' + '=' * 80)
        vLog.info(f'FiniexTestingIDE Signal Data Importer V{self.VERSION}')
        vLog.info('=' * 80)
        vLog.info(f'Found: {file_count} JSONL file(s)')
        vLog.info(f"Override Mode: {'ENABLED' if self.override else 'DISABLED'}")
        vLog.info('=' * 80 + '\n')

    def import_file(self, jsonl_file: Path) -> bool:
        """
        Import one loose signal JSONL. An already-imported, refused or failing file is counted
        and logged, never raised — the batch goes on with the next file.

        Args:
            jsonl_file: The JSONL to import

        Returns:
            True when the file was written, False when it was skipped, refused or failed
        """
        return self.import_file_result(jsonl_file).outcome is InboxImportOutcome.IMPORTED

    def import_file_result(self, jsonl_file: Path,
                           archived_in: Optional[Path] = None) -> InboxImportResult:
        """
        Import one signal JSONL and say how it ended. A loose file then leaves the inbox: to
        the finished directory when the archive holds it, to the refused directory when not.
        A file unpacked from a zip is left where it is; the zip's handling decides about it.

        Args:
            jsonl_file: The JSONL to import
            archived_in: The zip the file was unpacked from, None for a loose file

        Returns:
            How the import of the file ended, and why when it was not taken
        """
        origin = f' (from {archived_in.name})' if archived_in is not None else ''
        vLog.info(f'\n📄 Processing: {jsonl_file.name}{origin}')
        result = self._convert_and_judge(jsonl_file, origin)
        if archived_in is None:
            self._leave_inbox(jsonl_file, result)
        return result

    def get_refused_files(self) -> List[str]:
        """
        The loose files this import moved to the refused directory.

        Returns:
            Their names, in the order they were refused
        """
        return list(self._refused_files)

    def _convert_and_judge(self, jsonl_file: Path, origin: str) -> InboxImportResult:
        """
        Convert one JSONL, counting and logging whatever stops it.

        Args:
            jsonl_file: The JSONL
            origin: Where it came from, for the log lines — empty for a loose file

        Returns:
            How its import ended
        """
        try:
            written = self.convert_jsonl_to_parquet(jsonl_file)
        except SignalAlreadyImportedError as e:
            # A warning, never an error: re-running an import whose days are already
            # archived is the normal case, and the tick importer grades it the same.
            warning_msg = f'ALREADY IMPORTED: {jsonl_file.name}{origin}'
            vLog.warning(warning_msg)
            vLog.warning(str(e))
            self.warnings.append(warning_msg)
            vLog.info('→ Skipping import (parquet already exists)')
            return InboxImportResult(outcome=InboxImportOutcome.ALREADY_ARCHIVED)
        except SignalSchemaError as e:
            error_msg = f'REFUSED {jsonl_file.name}{origin}: {e}'
            vLog.error(error_msg)
            self.errors.append(error_msg)
            return InboxImportResult(outcome=InboxImportOutcome.REFUSED, reason=str(e))
        except Exception as e:
            error_msg = f'ERROR in {jsonl_file.name}{origin}: {str(e)}'
            vLog.error(error_msg)
            self.errors.append(error_msg)
            return InboxImportResult(outcome=InboxImportOutcome.FAILED,
                                    reason=f'{type(e).__name__}: {e}')
        if written is None:
            reason = 'it holds no envelope'
            self.errors.append(f'REFUSED {jsonl_file.name}{origin}: {reason}')
            return InboxImportResult(outcome=InboxImportOutcome.REFUSED, reason=reason)
        self.processed_files += 1
        return InboxImportResult(outcome=InboxImportOutcome.IMPORTED)

    def _leave_inbox(self, jsonl_file: Path, result: InboxImportResult) -> None:
        """
        Move a loose JSONL out of the inbox once its import ended, and count a move that could
        not happen as an error.

        Args:
            jsonl_file: The JSONL
            result: How its import ended

        Returns:
            None
        """
        if result.outcome.is_taken():
            move = self._conveyor.to_finished(jsonl_file)
        else:
            move = self._conveyor.to_refused(jsonl_file, result.reason or result.outcome.value)
        name = jsonl_file.name
        if move.disposition is InboxDisposition.MOVED_TO_FINISHED:
            self.moved_files += 1
            vLog.info(f'→ Moved {name} to {move.destination}')
        elif move.disposition is InboxDisposition.MOVED_TO_REFUSED:
            self._refused_files.append(name)
            vLog.info(f'→ Moved {name} to {move.destination}, the reason beside it')
        elif move.disposition is InboxDisposition.ALREADY_THERE:
            self.moved_files += 1
            vLog.info(f'→ An identical {name} lies in {move.destination}; the inbox copy '
                      'was removed')
        elif move.destination is not None:
            error_msg = f'KEPT IN THE INBOX {name}: {move.detail}'
            vLog.error(error_msg)
            self.errors.append(error_msg)

    def convert_jsonl_to_parquet(self, jsonl_file: Path) -> Optional[Path]:
        """
        Convert one signal JSONL file to a columnar parquet.

        Args:
            jsonl_file: Archived signal JSONL path

        Returns:
            The written parquet path, or None if the file held no snapshots
        """
        # Reuse the validated parse (schema_version gate + time order)
        snapshots = load_signal_series(jsonl_file, signal_kind='').snapshots
        if not snapshots:
            vLog.warning(f'No snapshots in {jsonl_file.name}')
            return None

        pipeline_id = self._resolve_pipeline_id(snapshots, jsonl_file)
        parquet_stem = self._parquet_stem(jsonl_file, pipeline_id)
        self._validate_stream_identity(snapshots, jsonl_file)
        rows = self._explode(snapshots)
        df = pd.DataFrame(rows, columns=[c.value for c in SignalParquetColumn])

        target_path = self.target_dir / pipeline_id / f'{parquet_stem}.parquet'
        create_directory(target_path.parent, holds='a signal parquet file')
        if target_path.exists() and not self.override:
            raise SignalAlreadyImportedError(
                f'{target_path.name} was already imported for {pipeline_id} — '
                f'use --override to replace it')
        write_file_atomic(target_path, lambda temporary: df.to_parquet(temporary, index=False),
                          StoreId.SIGNALS)

        self.total_rows += len(df)
        symbols = sorted(
            {r[SignalParquetColumn.SYMBOL.value] for r in rows} - {SIGNAL_ENVELOPE_SYMBOL})
        vLog.info(
            f"✅ {jsonl_file.name} → {target_path.relative_to(self.target_dir)} "
            f"({len(df)} rows; symbols: {', '.join(symbols)})")
        return target_path

    def _parquet_stem(self, jsonl_file: Path, pipeline_id: str) -> str:
        """
        Name a JSONL's parquet by its day, whether the file is named by the day alone or by its
        pipeline and day, so one day never becomes two parquets.

        Args:
            jsonl_file: The JSONL
            pipeline_id: The pipeline its envelopes carry

        Returns:
            The day of a pipeline-and-day name, otherwise the file's stem as it stands
        """
        named = _PIPELINE_DAY_STEM.match(jsonl_file.stem)
        if named is None:
            return jsonl_file.stem
        if named.group('pipeline_id') != pipeline_id:
            raise SignalSchemaError(
                f"{jsonl_file.name}: its name gives the pipeline '{named.group('pipeline_id')}', "
                f"its envelopes '{pipeline_id}'.")
        return named.group('day')

    def _resolve_pipeline_id(self, snapshots: List[SignalSnapshot], jsonl_file: Path) -> str:
        """Derive the output folder key (= data_sentiment_type) from the envelope pipeline_id."""
        ids = {s.pipeline_id for s in snapshots if s.pipeline_id}
        if not ids:
            raise SignalSchemaError(
                f"{jsonl_file.name}: no 'pipeline_id' in any envelope — cannot key the source.")
        if len(ids) > 1:
            raise SignalSchemaError(
                f"{jsonl_file.name}: mixed 'pipeline_id' values {sorted(ids)} in one file.")
        return ids.pop()

    def _validate_stream_identity(
        self, snapshots: List[SignalSnapshot], jsonl_file: Path
    ) -> None:
        """
        Check the stream identity of a file: epoch monotonicity (hard) and seq contiguity (soft).

        A REPEATED epoch is refused. seq is unique only WITHIN an epoch, so two series
        carrying the same epoch would silently merge under the (pipeline_id, stream_epoch,
        seq) key — the exact failure the identity exists to prevent. A seq HOLE is reported
        and imported: the file is incomplete, not wrong, and refusing it would discard the
        envelopes we do have. Lines without a stream identity (the pre-stream era) are
        unverifiable, which is a distinct state from verified-contiguous and is not an error.

        Args:
            snapshots: Parsed snapshots of one file
            jsonl_file: The file, for messages

        Raises:
            SignalSchemaError: If an epoch reappears after a later one was seen
        """
        identified = [s for s in snapshots if s.seq is not None
                      and s.stream_epoch is not None]
        if not identified:
            return

        # Snapshots arrive in time order (the loader sorts them), so both violations below
        # are "went backwards in time" — which is exactly what a rewound series looks like
        # from the outside.
        highest_epoch = identified[0].stream_epoch
        last_seq: Dict[int, int] = {}
        seqs_per_epoch: Dict[int, List[int]] = {}
        for snapshot in identified:
            epoch, seq = snapshot.stream_epoch, snapshot.seq
            if epoch < highest_epoch:
                raise SignalSchemaError(
                    f"{jsonl_file.name}: stream_epoch went backwards ({highest_epoch} → "
                    f"{epoch}) — the producer's series was rewound and two series would "
                    f"merge under one key. Refusing the file."
                )
            if epoch in last_seq and seq < last_seq[epoch]:
                raise SignalSchemaError(
                    f'{jsonl_file.name}: seq went backwards within epoch {epoch} '
                    f'({last_seq[epoch]} → {seq}) — the epoch was reissued for a second '
                    f'series. seq is unique only within an epoch, so refusing the file.'
                )
            highest_epoch = epoch
            last_seq[epoch] = seq
            seqs_per_epoch.setdefault(epoch, []).append(seq)

        for epoch, seqs in seqs_per_epoch.items():
            holes = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]) if b - a > 1)
            if holes:
                message = (f'{jsonl_file.name}: {holes} missing seq in epoch {epoch} '
                           f'({seqs[0]}→{seqs[-1]}) — envelopes were never received')
                vLog.warning(f'   ⚠️ {message}')
                self.errors.append(message)

    def _explode(self, snapshots: List[SignalSnapshot]) -> List[Dict]:
        """One row per (collected_msc, symbol) + one envelope-level sentinel row each."""
        rows: List[Dict] = []
        registry = DataOriginRegistry()
        for snap in snapshots:
            msc = int(snap.collected_msc.timestamp() * 1000)
            # Resolved per ENVELOPE rather than per file, and that is not caution for its own
            # sake: the producer may legitimately re-mint its identity when an instance is
            # cloned, and a day bucket can straddle that moment. Per file, one of the two
            # writers would be recorded under the other's identity.
            origin = registry.resolve(
                snap.instance_id or None, snap.schema_version,
                pipeline_id=snap.pipeline_id)
            envelope = {
                SignalParquetColumn.STATUS.value: snap.status,
                SignalParquetColumn.SCHEMA_VERSION.value: snap.schema_version,
                SignalParquetColumn.PIPELINE_ID.value: snap.pipeline_id,
                SignalParquetColumn.PROMPT_VERSION.value: snap.prompt_version,
                SignalParquetColumn.PROMPT_ID.value: snap.prompt_id,
                SignalParquetColumn.PROMPT_HASH.value: snap.prompt_hash,
                SignalParquetColumn.DATA_ORIGIN.value: snap.data_origin,
                SignalParquetColumn.CONFIG_FINGERPRINT.value: snap.config_fingerprint,
                # What the producer stated, and what this side makes of it. Written here and
                # read back everywhere else — the resolution never runs twice on one file.
                SignalParquetColumn.ORIGIN_INSTANCE_ID.value: origin.instance_id or '',
                SignalParquetColumn.ORIGIN_CLASS.value: origin.origin_class.value,
                SignalParquetColumn.ORIGIN_EVIDENCE.value: origin.evidence.value,
                # Lives in metadata, not top-level. Missing key and null collapse to the
                # same '' = unknown state — never to 'scheduled' (a boot pass would be
                # mislabelled as a grid point).
                # Top-level since the producer promoted it out of metadata; the model lifts
                # the legacy metadata location on read, so both eras land here. Missing and
                # null collapse to '' = unknown — never to 'scheduled', which would mislabel
                # a boot pass as a grid point.
                SignalParquetColumn.TRIGGER_REASON.value: snap.trigger_reason,
                # Stream identity (#141 Part 2a) — absent for every line written before the
                # stream contract, a state the reader distinguishes from zero.
                SignalParquetColumn.SEQ.value: snap.seq,
                SignalParquetColumn.STREAM_EPOCH.value: snap.stream_epoch,
                SignalParquetColumn.AVAILABLE_MSC.value: _epoch_ms(snap.available_msc),
                # Envelope-level, repeated per row: the runtime series is projected to one
                # symbol, so a max over a projected snapshot's rows would be that row's
                # stamp. RC-4 compares envelopes, never rows.
                SignalParquetColumn.ENVELOPE_EVIDENCE_AS_OF.value: _epoch_ms(
                    snap.get_evidence_as_of()),
            }
            # Envelope sentinel row (symbol = '*') — keeps this collected_msc resolvable
            # for every covered symbol even when the envelope omits it (partial/error).
            rows.append(self._row(msc, SIGNAL_ENVELOPE_SYMBOL, None, envelope))
            for result in snap.result:
                rows.append(self._row(msc, result.symbol, result, envelope))
        return rows

    def _row(self, msc: int, symbol: str,
             result: Optional[SentimentResult], envelope: Dict) -> Dict:
        """Build one parquet row (envelope sentinel when result is None)."""
        row = {c.value: '' for c in SignalParquetColumn}
        row.update(envelope)
        row[SignalParquetColumn.COLLECTED_MSC.value] = msc
        row[SignalParquetColumn.SYMBOL.value] = symbol
        if result is None:
            row[SignalParquetColumn.SIGNAL.value] = ''
            row[SignalParquetColumn.SENTIMENT_SCORE.value] = 0.0
            row[SignalParquetColumn.CONFIDENCE.value] = 0.0
            row[SignalParquetColumn.REASONING.value] = ''
            row[SignalParquetColumn.URGENCY.value] = 0.0
            row[SignalParquetColumn.IS_BREAKING.value] = False
            row[SignalParquetColumn.BREAKING_EPISODE_ID.value] = ''
            row[SignalParquetColumn.BREAKING_EPISODE_START.value] = False
            row[SignalParquetColumn.BASIS.value] = ''
            row[SignalParquetColumn.EVIDENCE_AS_OF.value] = None
        else:
            row[SignalParquetColumn.SIGNAL.value] = result.signal
            row[SignalParquetColumn.SENTIMENT_SCORE.value] = result.sentiment_score
            row[SignalParquetColumn.CONFIDENCE.value] = result.confidence
            row[SignalParquetColumn.REASONING.value] = result.reasoning
            row[SignalParquetColumn.URGENCY.value] = result.urgency
            row[SignalParquetColumn.IS_BREAKING.value] = result.is_breaking
            row[SignalParquetColumn.BREAKING_EPISODE_ID.value] = result.breaking_episode_id
            row[SignalParquetColumn.BREAKING_EPISODE_START.value] = result.breaking_episode_start
            row[SignalParquetColumn.BASIS.value] = result.basis
            row[SignalParquetColumn.EVIDENCE_AS_OF.value] = _epoch_ms(
                result.evidence_as_of)
        return row

    def rebuild_index(
        self,
        on_file_scanned: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """
        Rebuild the signal index over the target directory. A failure is counted among the
        errors, and so is a file the scan could not read, so the summary cannot read clean over
        an index that no longer describes the archive.

        Args:
            on_file_scanned: Called with (file number, file count) for each parquet scanned

        Returns:
            True when the index was rebuilt and lists every signal file on disk
        """
        try:
            index_manager = SignalIndexManager(data_dir=str(self.target_dir))
            index_manager.build_index(force_rebuild=True, on_file_scanned=on_file_scanned)
            vLog.info('✅ Signal index rebuilt')
        except Exception as e:
            vLog.error(f'Signal index rebuild failed: {e}')
            self.errors.append(f'Signal index rebuild failed: {e}')
            return False

        unreadable = index_manager.get_unreadable_files()
        if unreadable:
            self.errors.append(
                f'Signal index: {len(unreadable)} file(s) could not be read and are missing '
                f'from the index (first: {unreadable[0]}) — re-import them from their source '
                f'files')
            return False
        return True

    def _print_summary(self) -> None:
        """Print the import summary."""
        vLog.info('\n' + '=' * 80)
        vLog.info(
            f'Signal Import Summary: {self.processed_files} file(s), {self.total_rows} rows')
        if self.moved_files:
            vLog.info(f'Moved to the finished directory: {self.moved_files} file(s)')
        if self.warnings:
            vLog.info(f'Warnings: {len(self.warnings)}')
        if self.errors:
            vLog.info(f'Errors: {len(self.errors)}')
        vLog.info('=' * 80)
