"""
Kraken History Commands — the steps of bringing Kraken's trade history into the archive (#561).

The operator's import protocol, with the steps that are new for external data:

    verify    the parts against Kraken's published checksums → the fetch's manifest
    list      per symbol we collect: the archive member, its size, the cut
    convert   collector-shaped files into the import inbox, one record per run
    (import)  the ordinary import — python/cli/data_index_cli.py import --only ticks
    archive   the four checks over a conversion, the zip, the loose copies removed

Each step reads what the one before it wrote, so a step cannot run on material the earlier one
did not check.
"""

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pyarrow.parquet as pq

from python.configuration.import_config_manager import ImportConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.importers.external.kraken_trade_history_archive import (
    KrakenTradeHistoryArchive,
)
from python.data_management.importers.external.kraken_trade_history_converter import (
    BROKER_TYPE,
    KrakenTradeHistoryConverter,
)
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.utils.file_utils import create_directory

CONVERSIONS_DIR = 'conversions'
ARCHIVES_DIR = 'Archives'


class KrakenHistoryCommands:
    """
    The protocol steps over one fetch folder of the external history store.

    Args:
        fetch_dir: The fetch folder
    """

    def __init__(self, fetch_dir: Path):
        self._archive = KrakenTradeHistoryArchive(fetch_dir)

    def verify(self) -> int:
        """
        Check every part against Kraken's checksum and record the fetch.

        Returns:
            Exit code
        """
        manifest = self._archive.verify()
        vendor = manifest.vendor_manifest
        print(f'✅ {len(manifest.parts)} part(s) match Kraken\'s published checksums '
              f'({sum(part.size for part in manifest.parts) / 1e9:.1f} GB)')
        for increment in manifest.increments:
            print(f'   increment {increment.name}: sha256 {increment.sha256[:16]}… '
                  f'(no published checksum — recorded here)')
        print(f'   vendor manifest: schema {vendor.get("schema_version")} · release '
              f'{vendor.get("release")} · coverage {vendor.get("coverage", {}).get("start")} → '
              f'{vendor.get("coverage", {}).get("end")} · {vendor.get("files")} pair file(s)')
        return 0

    def list_pairs(self) -> int:
        """
        Show, per symbol we collect, the member it would be read from and where it would stop.

        Returns:
            Exit code
        """
        self._archive.load_manifest()
        broker_config = self._broker_config()
        index = self._tick_index()
        with self._archive.open() as archive:
            members = {info.filename: info for info in archive.infolist()}
            for symbol in index.list_symbols(BROKER_TYPE):
                spec = broker_config.get_symbol_specification(symbol)
                member = KrakenTradeHistoryArchive.member_name(
                    spec.base_currency, spec.quote_currency)
                info = members.get(member)
                cut = self._cut_for(index, symbol)
                size = f'{info.file_size / 1e9:6.2f} GB csv' if info else '   not in the archive'
                print(f'{symbol:8s} {member:14s} {size}   cut '
                      f'{cut:%Y-%m-%d %H:%M} UTC' if cut else
                      f'{symbol:8s} {member:14s} {size}   no collected data — no cut')
        return 0

    def convert(self, symbols: List[str], raw_dir: Optional[Path] = None,
                dry_run: bool = False) -> int:
        """
        Convert the given symbols into the import inbox and record what was written.

        Args:
            symbols: Our symbols
            raw_dir: The import inbox (default: the configured one)
            dry_run: Name the files without writing them

        Returns:
            Exit code
        """
        raw_dir = Path(raw_dir or ImportConfigManager().get_data_raw_path())
        broker_config = self._broker_config()
        index = self._tick_index()
        converter = KrakenTradeHistoryConverter(self._archive, raw_dir)
        converted_at = datetime.now(timezone.utc)
        record: Dict = {'converted_at': converted_at.isoformat(), 'raw_dir': str(raw_dir),
                        'symbols': {}, 'archived_to': None}

        for symbol in symbols:
            spec = broker_config.get_symbol_specification(symbol)
            result = converter.convert(
                symbol, spec.base_currency, spec.quote_currency,
                self._cut_for(index, symbol), dry_run=dry_run)
            cut = f'{result.cut_at:%Y-%m-%d %H:%M} UTC' if result.cut_at else 'none'
            print(f'{"(dry run) " if dry_run else ""}{symbol}: {result.rows_converted:,} of '
                  f'{result.rows_read:,} row(s) read → {len(result.files)} file(s), cut {cut}')
            if result.files:
                print(f'   {result.files[0].first_tick:%Y-%m-%d} → '
                      f'{result.files[-1].last_tick:%Y-%m-%d %H:%M} UTC')
            record['symbols'][symbol] = {
                'member': result.member, 'rows_read': result.rows_read,
                'rows_converted': result.rows_converted,
                'cut_at': result.cut_at.isoformat() if result.cut_at else None,
                'files': [file.name for file in result.files]}

        if not dry_run:
            path = self._conversions_dir() / f'{converted_at:%Y%m%dT%H%M%SZ}.json'
            create_directory(path.parent, holds='the converted history files')
            path.write_text(json.dumps(record, indent=2), encoding='utf-8')
            print(f'→ conversion record {path}')
            print('→ next: python python/cli/data_index_cli.py import --only ticks')
        return 0

    def archive(self, zip_name: str, record_path: Optional[Path] = None) -> int:
        """
        Prove a conversion imported, zip its files into the raw archive, remove the loose copies.

        The four checks of the import protocol, over exactly the files the conversion wrote:
        count and names against the tick index, none left in the inbox, every parquet written
        after the conversion. Only all four together prove the import complete; a file the
        import refused keeps whatever was there before, and count and names alone would pass.

        Args:
            zip_name: File name of the zip under data/finished/Archives/
            record_path: The conversion record (default: the newest one not yet archived)

        Returns:
            Exit code — 1 when a check fails, and nothing is zipped or removed then
        """
        record_path = record_path or self._newest_open_record()
        record = json.loads(Path(record_path).read_text(encoding='utf-8'))
        names = [name for entry in record['symbols'].values() for name in entry['files']]
        converted_at = datetime.fromisoformat(record['converted_at'])
        raw_dir = Path(record['raw_dir'])
        finished_dir = Path(ImportConfigManager().get_data_finished_path())

        index = self._tick_index(force=True)
        parquet_by_source = {
            entry['source_file']: entry['path']
            for symbol in record['symbols']
            for entry in index.get_symbol_entries(BROKER_TYPE, symbol)}

        failures = []
        missing = [name for name in names if name not in parquet_by_source]
        if missing:
            failures.append(f'names: {len(missing)} converted file(s) have no parquet, '
                            f'e.g. {missing[0]}')
        left = [name for name in names if (raw_dir / name).exists()]
        if left:
            failures.append(f'inbox: {len(left)} file(s) still in {raw_dir} — refused by the '
                            f'import, or not imported yet, e.g. {left[0]}')
        stale = [name for name in names if name in parquet_by_source
                 and self._processed_at(parquet_by_source[name]) <= converted_at]
        if stale:
            failures.append(f'rewritten: {len(stale)} parquet(s) predate the conversion, '
                            f'e.g. {stale[0]}')
        loose = [finished_dir / name for name in names]
        absent = [path.name for path in loose if not path.exists()]
        if absent:
            failures.append(f'finished: {len(absent)} imported file(s) are not in '
                            f'{finished_dir}, e.g. {absent[0]}')
        if failures:
            print('❌ The conversion is not proven imported — nothing zipped, nothing removed:')
            for failure in failures:
                print(f'   {failure}')
            return 1
        print(f'✅ count {len(names)} = {len(names)} · names ✓ · inbox 0 · rewritten {len(names)}')

        zip_path = finished_dir / ARCHIVES_DIR / zip_name
        create_directory(zip_path.parent, holds='the zipped history archive')
        with zipfile.ZipFile(zip_path, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
            present = set(archive.namelist())
            for path in loose:
                if path.name not in present:
                    archive.write(path, arcname=path.name)
        with zipfile.ZipFile(zip_path) as archive:
            bad = archive.testzip()
            stored = {info.filename: info.file_size for info in archive.infolist()}
        if bad or any(stored.get(path.name) != path.stat().st_size for path in loose):
            print(f'❌ {zip_path.name} does not hold every file intact ({bad or "size mismatch"}) '
                  f'— the loose copies stay.')
            return 1
        for path in loose:
            path.unlink()
        record['archived_to'] = zip_path.name
        record['archived_at'] = datetime.now(timezone.utc).isoformat()
        Path(record_path).write_text(json.dumps(record, indent=2), encoding='utf-8')
        print(f'✅ {len(loose)} file(s) in {zip_path.name}, loose copies removed')
        return 0

    @staticmethod
    def _cut_for(index: TickIndexManager, symbol: str) -> Optional[datetime]:
        """
        Where conversion stops for a symbol: our first collected tick, floored to the minute.

        Args:
            index: The tick index
            symbol: Our symbol

        Returns:
            The cut, or None where we never collected the symbol
        """
        starts = [datetime.fromisoformat(entry['start_time'])
                  for entry in index.get_symbol_entries(BROKER_TYPE, symbol)
                  if entry.get('origin_class') != OriginClass.EXTERNAL.value
                  and entry.get('start_time')]
        if not starts:
            return None
        first = min(starts)
        first = first if first.tzinfo else first.replace(tzinfo=timezone.utc)
        return first.replace(second=0, microsecond=0)

    @staticmethod
    def _processed_at(parquet_path: str) -> datetime:
        """
        When a parquet was written, from its own header.

        Args:
            parquet_path: The parquet

        Returns:
            Its processed_at instant
        """
        metadata = pq.read_schema(parquet_path).metadata or {}
        return datetime.fromisoformat(metadata.get(b'processed_at', b'1970-01-01T00:00:00+00:00')
                                      .decode('utf-8'))

    @staticmethod
    def _broker_config() -> BrokerConfig:
        """
        Kraken's broker configuration, the authority for base and quote currency.

        Returns:
            The broker config
        """
        return BrokerConfigFactory.build_broker_config(
            MarketConfigManager().get_broker_config_path(BROKER_TYPE))

    @staticmethod
    def _tick_index(force: bool = False) -> TickIndexManager:
        """
        The tick index of the configured archive.

        Args:
            force: Rebuild rather than load — the archive step must see the import just run

        Returns:
            The loaded index
        """
        index = TickIndexManager()
        index.build_index(force_rebuild=force, check_stale=not force)
        return index

    def _conversions_dir(self) -> Path:
        """
        Where this fetch's conversion records live.

        Returns:
            The folder
        """
        return self._archive.get_fetch_dir() / CONVERSIONS_DIR

    def _newest_open_record(self) -> Path:
        """
        The newest conversion record not yet archived.

        Returns:
            Its path
        """
        for path in sorted(self._conversions_dir().glob('*.json'), reverse=True):
            if not json.loads(path.read_text(encoding='utf-8')).get('archived_to'):
                return path
        raise FileNotFoundError(f'No open conversion record in {self._conversions_dir()}')
