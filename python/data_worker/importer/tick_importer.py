"""
FiniexTestingIDE Tick Data Importer
===================================

Konvertiert MQL5 JSON-Exports zu optimierten Parquet-Files mit UTC-Konvertierung.
Workflow: JSON laden → Validieren → Optimieren → UTC-Konvertierung → Parquet speichern

Author: FiniexTestingIDE Team
Version: 1.3 (UTC Conversion with Manual Offset Support)
"""

import json
from python.components.logger.bootstrap_logger import setup_logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration import AppConfigLoader
from python.components.logger.bootstrap_logger import setup_logging
from python.data_worker.importer.bar_importer import BarImporter
from python.data_worker.data_loader.parquet_index import ParquetIndexManager

# Import duplicate detection
from python.data_worker.data_loader.exceptions import (
    ArtificialDuplicateException,
    DuplicateReport
)

setup_logging(name="StrategyRunner")
vLog = setup_logging(name="StrategyRunner")


class TickDataImporter:
    """
    Konvertiert MQL5 JSON-Exports zu Parquet-Format mit UTC-Konvertierung.

    Hauptfunktionen:
    - JSON → Parquet Konvertierung (10:1 Kompression)
    - Datentyp-Optimierung für Performance
    - UTC Timezone Conversion mit manuellem Offset
    - Session-Neuberechnung basierend auf UTC
    - Qualitäts-Checks und Bereinigung
    - Batch-Verarbeitung mit Error-Handling
    - Duplicate Prevention mit Override-Option
    - Hierarchical directory structure

    Args:
        source_dir (str): Verzeichnis mit JSON-Files
        target_dir (str): Zielverzeichnis für Parquet-Output
        override (bool): Wenn True, überschreibe existierende Files
        time_offset (int): Manueller UTC-Offset in Stunden (z.B. -3 für GMT+3)
    """

    VERSION = "1.3"

    def __init__(self, source_dir: str, target_dir: str,
                 override: bool = False, time_offset: int = 0):
        """
        Initialisiert Importer mit Source- und Target-Pfaden.

        Args:
            source_dir (str): MQL5 JSON-Export-Verzeichnis
            target_dir (str): Parquet-Zielverzeichnis
            override (bool): Überschreibe existierende Dateien
            time_offset (int): UTC-Offset in Stunden
        """
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.override = override
        self.time_offset = time_offset

        # Batch-Processing-Statistiken
        self.processed_files = 0
        self.total_ticks = 0
        self.errors = []
        self.appConfig = AppConfigLoader()

    def process_all_mql5_exports(self):
        """
        Sucht alle TickCollector-Exports und konvertiert sie sequenziell.
        Fehler stoppen nicht die Verarbeitung weiterer Files.
        """
        json_files = list(self.source_dir.glob("*_ticks.json"))

        if not json_files:
            vLog.warning(
                f"Keine JSON-Files gefunden in {self.source_dir}. Just rebuilding Index.")
            self.rebuild_parquet_index()
            return

        vLog.info("\n" + "=" * 80)
        vLog.info(f"FiniexTestingIDE Tick Data Importer V{self.VERSION}")
        vLog.info("=" * 80)
        vLog.info(f"Gefunden: {len(json_files)} JSON-Files")
        vLog.info(
            f"Override Mode: {'ENABLED' if self.override else 'DISABLED'}")
        if self.time_offset != 0:
            vLog.info(f"Time Offset: {self.time_offset:+d} hours")
            vLog.warning("⚠️ CRITICAL: After offset ALL TIMES ARE UTC!")
            vLog.warning("⚠️ Sessions will be RECALCULATED based on UTC time!")
        else:
            vLog.info(f"Time Offset: NONE (timestamps remain as-is)")
        vLog.info("=" * 80 + "\n")

        # Sequenzielle Verarbeitung mit Error-Recovery
        for json_file in json_files:
            vLog.info(f"\n📄 Verarbeite: {json_file.name}")
            try:
                self.convert_json_to_parquet(json_file)
                self.processed_files += 1
            except ArtificialDuplicateException as e:
                # Special handling for duplicate detection
                error_msg = f"DUPLICATE DETECTED bei {json_file.name}"
                vLog.error(error_msg)
                vLog.error(str(e))
                self.errors.append(error_msg)
                vLog.info("→ Überspringe Import (Duplikat existiert bereits)")
            except Exception as e:
                error_msg = f"FEHLER bei {json_file.name}: {str(e)}"
                vLog.error(error_msg)
                self.errors.append(error_msg)

        self.rebuild_parquet_index()

        # === AUTO-TRIGGER BAR RENDERING ===
        # After all ticks imported, render bars automatically
        if self.processed_files > 0:
            self._trigger_bar_rendering()

        self._print_summary()

    def rebuild_parquet_index(self):
        """Rebuild index after successful imports"""
        vLog.info("\n🔄 Rebuilding Parquet index...")
        try:

            index_manager = ParquetIndexManager(self.target_dir)
            index_manager.build_index(force_rebuild=True)

            symbols = index_manager.list_symbols()
            vLog.info(f"✅ Index rebuilt: {len(symbols)} symbols indexed")

        except Exception as e:
            vLog.error(f"❌ Failed to rebuild index: {e}")
            vLog.error("   Index may be outdated - run manual rebuild!")

    def convert_json_to_parquet(self, json_file: Path):
        """
        Konvertiert einzelne JSON-Datei zu optimiertem Parquet mit UTC-Konvertierung.

        Pipeline:
        1. JSON laden und Struktur validieren
        2. DataFrame erstellen und Datentypen optimieren
        3. Time Offset anwenden (wenn gesetzt)
        4. Sessions neu berechnen (wenn Offset angewendet)
        5. Qualitäts-Checks
        6. Check for existing duplicates (mit Override-Support)
        7. Als Parquet mit Metadaten speichern
        """

        # ===========================================
        # 1. JSON LADEN UND VALIDIEREN
        # ===========================================

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "ticks" not in data or "metadata" not in data:
            raise ValueError(
                "Ungültige JSON-Struktur - 'ticks' oder 'metadata' fehlt")

        ticks = data["ticks"]
        metadata = data["metadata"]

        if not ticks:
            vLog.warning(f"Keine Ticks in {json_file.name}")
            return

        # ===========================================
        # 2. BROKER METADATA ANZEIGEN (FIXED)
        # ===========================================

        vLog.info(f"📊 Broker Metadata:")

        # Get data version to show in "not available" messages
        data_version = metadata.get('data_format_version', 'unknown')

        # Detected Offset (available since v1.0.5)
        detected_offset = metadata.get('broker_utc_offset_hours', None)
        if detected_offset is not None:
            sign = '+' if detected_offset >= 0 else ''
            vLog.info(f"   Detected Offset: GMT{sign}{detected_offset}")
        else:
            vLog.info(
                f"   Detected Offset: Not available (pre v1.0.5 data, version: {data_version})")

        # Local Device Time (planned for v1.0.5+, but not yet implemented in MQL5)
        local_device = metadata.get('local_device_time', None)
        if local_device:
            vLog.info(f"   Local Device:    {local_device}")
        else:
            vLog.info(f"   Local Device:    Not available (pre v1.0.5 data)")

        # Broker Time (planned for v1.0.5+, but not yet implemented in MQL5)
        broker_time = metadata.get('broker_server_time', None)
        if broker_time:
            vLog.info(f"   Broker Time:     {broker_time}")
        else:
            vLog.info(f"   Broker Time:     Not available (pre v1.0.5 data)")

        # User Offset (always show if set)
        if self.time_offset != 0:
            vLog.info(
                f"   User Offset:     {self.time_offset:+d} hours → ALL TIMES WILL BE UTC!")

        # ===========================================
        # 3. DATAFRAME ERSTELLEN UND OPTIMIEREN
        # ===========================================

        df = pd.DataFrame(ticks)
        df = self._optimize_datatypes(df)

        # Parse timestamps as timezone-naive
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        # ===========================================
        # 4. TIME OFFSET ANWENDEN
        # ===========================================

        df = self._apply_time_offset(df)

        # ===========================================
        # 5. SESSIONS NEU BERECHNEN (wenn Offset)
        # ===========================================

        if self.time_offset != 0:
            df = self._recalculate_sessions(df)
            vLog.info(f"   ✅ Sessions recalculated based on UTC time")

        # ===========================================
        # 6. QUALITÄTS-CHECKS
        # ===========================================

        df = self._quality_checks(df)
        df = df.sort_values("timestamp").reset_index(drop=True)

        # ===========================================
        # 7. PARQUET-OUTPUT VORBEREITEN
        # CHANGED: Neue Pfad-Konstruktion!
        # ===========================================

        data_collector = metadata.get("data_collector", "mt5")
        symbol = metadata.get("symbol", "UNKNOWN")
        start_time = pd.to_datetime(metadata.get("start_time", datetime.now()))

        # NEUE STRUKTUR: data_collector / ticks / symbol
        target_path = self.target_dir / data_collector / "ticks" / symbol
        target_path.mkdir(parents=True, exist_ok=True)

        parquet_name = f"{symbol}_{start_time.strftime('%Y%m%d_%H%M%S')}.parquet"
        parquet_path = target_path / parquet_name

        # Metadaten für Parquet-Header
        parquet_metadata = {
            "source_file": json_file.name,
            "symbol": symbol,
            "broker": metadata.get("broker", "unknown"),
            "collector_version": metadata.get("data_format_version", "1.0"),
            "data_collector": data_collector,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "tick_count": str(len(df)),
            "importer_version": self.VERSION,
            "user_time_offset_hours": str(self.time_offset),
            "utc_conversion_applied": "true" if self.time_offset != 0 else "false",
        }

        # ===========================================
        # 8. CHECK FOR EXISTING DUPLICATES
        # ===========================================

        vLog.debug(f"Checking for existing duplicates...")
        duplicate_report = self._check_for_existing_duplicate(
            json_file.name,
            data_collector,
            symbol,
            df,
            parquet_path
        )

        if duplicate_report:
            if self.override:
                vLog.warning(f"⚠️  Override enabled - deleting existing file")
                for dup_file in duplicate_report.duplicate_files:
                    dup_file.unlink()
                    vLog.info(f"   🗑️  Deleted: {dup_file.name}")
            else:
                raise ArtificialDuplicateException(duplicate_report)

        # ===========================================
        # 9. PARQUET SCHREIBEN
        # ===========================================

        try:
            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata(parquet_metadata)
            pq.write_table(table, parquet_path, compression="snappy")

            json_size = json_file.stat().st_size
            parquet_size = parquet_path.stat().st_size
            compression_ratio = json_size / parquet_size if parquet_size > 0 else 0

            if self.appConfig.get_move_processed_files:
                finished_dir = Path("./data/finished/")
                finished_dir.mkdir(exist_ok=True)
                finished_file = finished_dir / json_file.name
                json_file.rename(finished_file)
                vLog.info(f"→ Moved {json_file.name} to finished/")

            self.total_ticks += len(df)

            time_suffix = " (UTC)" if self.time_offset != 0 else ""
            vLog.info(
                f"✅ {data_collector}/ticks/{symbol}/{parquet_name}: {len(df):,} Ticks{time_suffix}, "
                f"Kompression {compression_ratio:.1f}:1 "
                f"({json_size/1024/1024:.1f}MB → {parquet_size/1024/1024:.1f}MB)"
            )

        except Exception as e:
            vLog.error(f"FEHLER beim Schreiben von {parquet_path}")
            vLog.error(f"Original Error: {str(e)}")
            vLog.error(f"Error Type: {type(e)}")
            raise

    def _apply_time_offset(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Wendet manuellen Time-Offset auf Timestamps an.

        Args:
            df: DataFrame mit 'timestamp' Spalte

        Returns:
            DataFrame mit angepassten Timestamps
        """
        if self.time_offset == 0:
            return df

        if "timestamp" not in df.columns:
            return df

        # Store original for logging
        original_first = df["timestamp"].iloc[0]
        original_last = df["timestamp"].iloc[-1]

        # Apply offset
        offset_timedelta = pd.Timedelta(hours=self.time_offset)
        df["timestamp"] = df["timestamp"] - offset_timedelta

        utc_first = df["timestamp"].iloc[0]
        utc_last = df["timestamp"].iloc[-1]

        vLog.info(f"   🕐 Time Offset Applied: {self.time_offset:+d} hours")
        vLog.info(f"      Original: {original_first} → {original_last}")
        vLog.info(f"      UTC:      {utc_first} → {utc_last}")

        return df

    def _recalculate_sessions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Berechnet Trading-Sessions neu basierend auf UTC-Zeit.

        KORREKTE Forex Sessions (UTC):
        - sydney_tokyo: 22:00 - 08:00 UTC (10h Asian session)
        - london:       08:00 - 16:00 UTC (8h European session)  
        - new_york:     13:00 - 21:00 UTC (8h US session)
        - transition:   21:00 - 22:00 UTC (1h gap)

        Note: London/NY overlap = 13:00-16:00 UTC (markiert als "london")
        """

        def get_session_from_utc_hour(hour):
            """Determine trading session from UTC hour"""
            if 22 <= hour <= 23 or 0 <= hour < 8:
                return "sydney_tokyo"
            elif 8 <= hour < 13:
                return "london"
            elif 13 <= hour < 16:
                return "london"  # London/NY overlap - bleibt "london"
            elif 16 <= hour < 21:
                return "new_york"
            else:  # 21:00 - 21:59
                return "transition"

        df['session'] = df['timestamp'].dt.hour.apply(
            get_session_from_utc_hour)
        return df

    def _check_for_existing_duplicate(
        self,
        source_json_name: str,
        data_collector: str,
        symbol: str,
        new_df: pd.DataFrame,
        target_path: Path
    ) -> Optional[DuplicateReport]:
        """
        Check if Parquet file already exists with same source.

        Mit Override-Support: Gibt Duplicate-Report zurück, aber
        löscht NICHT automatisch (das macht der Caller).
        """

        search_pattern = f"*/{symbol}/{symbol}_*.parquet"
        existing_files = list(self.target_dir.glob(search_pattern))

        if not existing_files:
            return None

        for existing_file in existing_files:
            try:
                parquet_file = pq.ParquetFile(existing_file)
                metadata_raw = parquet_file.metadata.metadata

                existing_metadata = {
                    key.decode('utf-8') if isinstance(key, bytes) else key:
                    value.decode('utf-8') if isinstance(value,
                                                        bytes) else value
                    for key, value in metadata_raw.items()
                }

                existing_source = existing_metadata.get('source_file', '')
                existing_collector = existing_metadata.get(
                    'data_collector', 'unknown')

                if existing_source == source_json_name:
                    relative_path = existing_file.relative_to(self.target_dir)
                    collector_path = relative_path.parts[0] if len(
                        relative_path.parts) > 0 else "unknown"

                    vLog.warning(
                        f"⚠️  Found existing Parquet: {collector_path}/{symbol}/{existing_file.name}"
                    )
                    vLog.warning(
                        f"    Existing: data_collector='{existing_collector}' | "
                        f"Importing: data_collector='{data_collector}'"
                    )

                    existing_df = pd.read_parquet(existing_file)

                    return DuplicateReport(
                        source_file=source_json_name,
                        duplicate_files=[existing_file],
                        tick_counts=[len(existing_df)],
                        time_ranges=[
                            (existing_df['timestamp'].min(),
                             existing_df['timestamp'].max())
                        ],
                        file_sizes_mb=[
                            existing_file.stat().st_size / (1024 * 1024)],
                        metadata=[existing_metadata]
                    )

            except Exception as e:
                vLog.warning(
                    f"Could not read metadata from {existing_file.name}: {e}")

        return None

    def _optimize_datatypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimiert DataFrame-Datentypen für Performance."""

        float_cols = ["bid", "ask", "last", "spread_pct", "real_volume"]
        for col in float_cols:
            if col in df.columns:
                df[col] = df[col].astype("float32")

        int_cols = ["tick_volume", "chart_tick_volume", "spread_points"]
        for col in int_cols:
            if col in df.columns:
                df[col] = df[col].astype("int32")

        return df

    def _quality_checks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Führt Qualitäts-Checks auf Tick-Daten durch."""

        # Check 1: Invalid Prices
        invalid_prices = df[(df["bid"] <= 0) | (df["ask"] <= 0)]
        if len(invalid_prices) > 0:
            vLog.warning(
                f"⚠️  {len(invalid_prices)} Ticks mit invaliden Preisen")

        # Check 2: Extreme Spreads
        if "spread_pct" in df.columns:
            extreme_spreads = df[df["spread_pct"] > 5.0]
            if len(extreme_spreads) > 0:
                vLog.warning(
                    f"⚠️  {len(extreme_spreads)} Ticks mit extremen Spreads")

        # Check 3: Price Jumps
        df["bid_pct_change"] = df["bid"].pct_change().abs() * 100
        large_jumps = df[df["bid_pct_change"] > 10.0]
        if len(large_jumps) > 0:
            vLog.warning(
                f"⚠️  {len(large_jumps)} Ticks mit großen Preissprüngen")

        df = df.drop(columns=["bid_pct_change"], errors="ignore")
        return df

    def _print_summary(self):
        """Gibt Zusammenfassung der Batch-Verarbeitung aus."""

        vLog.info("\n" + "=" * 80)
        vLog.info("VERARBEITUNGS-ZUSAMMENFASSUNG")
        vLog.info("=" * 80)
        vLog.info(f"✅ Verarbeitete Dateien: {self.processed_files}")
        vLog.info(f"✅ Gesamte Ticks: {self.total_ticks:,}")
        if self.time_offset != 0:
            vLog.info(
                f"✅ Time Offset: {self.time_offset:+d} hours (ALL TIMES ARE UTC!)")
        vLog.info(f"❌ Fehler: {len(self.errors)}")

        if self.errors:
            vLog.error("\nFEHLER-LISTE:")
            for error in self.errors:
                vLog.error(f"  - {error}")

        vLog.info("=" * 80 + "\n")

    def _trigger_bar_rendering(self):
        """
        Trigger automatic bar rendering after tick import.

        Renders bars for all symbols that were just imported.
        """
        vLog.info("\n" + "=" * 80)
        vLog.info("🔄 AUTO-TRIGGERING BAR RENDERING")
        vLog.info("=" * 80)

        try:
            bar_importer = BarImporter(str(self.target_dir))
            bar_importer.render_bars_for_all_symbols(data_collector="mt5")

            vLog.info("✅ Bar rendering completed!")

        except Exception as e:
            vLog.error(f"❌ Bar rendering failed: {e}")
            vLog.error("   You can manually trigger it later with:")
            vLog.error("   python -m bar_importer")
