"""
FiniexTestingIDE Tick Data Importer
Konvertiert MQL5 JSON-Exports zu optimierten Parquet-Files
"""

import json
import pandas as pd
import numpy as np
import os
from pathlib import Path
from datetime import datetime, timezone
import pyarrow as pa
import pyarrow.parquet as pq
import logging
from typing import List, Dict, Optional
from config import MOVE_PROCESSED_FILES, DEV_MODE, DEBUG_LOGGING

# Logging Setup
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TickDataImporter:
    """Konvertiert MQL5 JSON-Exports zu Parquet-Format"""

    def __init__(self, source_dir: str, target_dir: str):
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        # Statistiken
        self.processed_files = 0
        self.total_ticks = 0
        self.errors = []

    def process_all_mql5_exports(self):
        """Verarbeitet alle JSON-Files aus MQL5-Export"""
        json_files = list(self.source_dir.glob("*_ticks.json"))

        if not json_files:
            logger.warning(f"Keine JSON-Files gefunden in {self.source_dir}")
            return

        logger.info(f"Gefunden: {len(json_files)} JSON-Files")

        for json_file in json_files:
            logger.info(f"Verarbeite: {json_file.name}")
            try:
                self.convert_json_to_parquet(json_file)
                self.processed_files += 1
            except Exception as e:
                error_msg = f"FEHLER bei {json_file.name}: {str(e)}"
                logger.error(error_msg)
                self.errors.append(error_msg)

        self._print_summary()

    def convert_json_to_parquet(self, json_file: Path):
        """Konvertiert einzelne JSON-Datei zu optimiertem Parquet"""

        # JSON laden und validieren
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Datenstruktur prüfen
        if 'ticks' not in data or 'metadata' not in data:
            raise ValueError(
                "Ungültige JSON-Struktur - 'ticks' oder 'metadata' fehlt")

        ticks = data['ticks']
        metadata = data['metadata']

        if not ticks:
            logger.warning(f"Keine Ticks in {json_file.name}")
            return

        # DataFrame erstellen
        df = pd.DataFrame(ticks)

        # Datentyp-Optimierung
        df = self._optimize_datatypes(df)

        # Zeitstempel-Normalisierung
        df = self._normalize_timestamps(df)

        # Qualitäts-Checks
        df = self._quality_checks(df)

        # Sortierung
        df = df.sort_values('timestamp').reset_index(drop=True)

        # Duplikate entfernen (behalte letzten)
        # Dublikatentfernung erstmal irrelevant. Ticks sollen so interpretiert werden wie sie reinkommen.
        # initial_count = len(df)
        # df = df.drop_duplicates(subset=['time_msc'], keep='last')
        # if len(df) < initial_count:
        #     logger.info(f"Entfernt {initial_count - len(df)} Duplikate")

        # Parquet-Dateiname generieren
        symbol = metadata.get('symbol', 'UNKNOWN')
        start_time = pd.to_datetime(metadata.get('start_time', datetime.now()))
        parquet_name = f"{symbol}_{start_time.strftime('%Y%m%d_%H%M%S')}.parquet"
        parquet_path = self.target_dir / parquet_name

        # Metadaten für Parquet
        parquet_metadata = {
            'source_file': json_file.name,
            'symbol': symbol,
            'broker': metadata.get('broker', 'unknown'),
            'collector_version': metadata.get('collector_version', '1.0'),
            'processed_at': datetime.now().isoformat(),
            'tick_count': str(len(df))
        }

        # Als Parquet speichern mit Metadaten
        try:
            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata(parquet_metadata)
            pq.write_table(table, parquet_path, compression='snappy')
            
            # Statistiken HIER berechnen (bevor File moved wird)
            json_size = json_file.stat().st_size
            parquet_size = parquet_path.stat().st_size
            compression_ratio = json_size / parquet_size if parquet_size > 0 else 0
            
            # DANN erst das File moven
            if MOVE_PROCESSED_FILES:
                finished_dir = Path("./data/finished/")
                finished_dir.mkdir(exist_ok=True)
                finished_file = finished_dir / json_file.name
                json_file.rename(finished_file)
                logger.info(f"→ Moved {json_file.name} to finished/")
            else:
                logger.info(f"→ DEV_MODE: File bleibt in raw/ (MOVE_PROCESSED_FILES=false)")

            # Logging
            logger.info(f"✓ {parquet_name}: {len(df):,} Ticks, "
                        f"Kompression {compression_ratio:.1f}:1 "
                        f"({json_size/1024/1024:.1f}MB → {parquet_size/1024/1024:.1f}MB)")
            logger.info(f"→ Moved {json_file.name} to finished/")
        except Exception as e:
            logger.error(f"FEHLER beim Schreiben von {parquet_path}")
            logger.error(f"Original Error: {str(e)}")
            logger.error(f"Error Type: {type(e)}")
            logger.error(f"Metadaten waren: {parquet_metadata}")
            # Zeige Datentypen der Metadaten
            for key, value in parquet_metadata.items():
                logger.error(f"  {key}: {value} (Type: {type(value)})")
            raise  # Re-raise den Original-Error

    def _optimize_datatypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimiert Datentypen für bessere Performance und Speichernutzung"""

        # Numerische Optimierung
        for col in ['bid', 'ask', 'last', 'spread_pct']:
            if col in df.columns:
                df[col] = df[col].astype('float32')  # float64 → float32

        for col in ['tick_volume', 'real_volume', 'spread_points']:
            if col in df.columns:
                df[col] = df[col].astype('int32')    # int64 → int32

        # String-Kategorien für bessere Kompression
        if 'session' in df.columns:
            df['session'] = df['session'].astype('category')

        return df

    def _normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalisiert Zeitstempel zu UTC"""

        if 'timestamp' in df.columns:
            # Zu datetime konvertieren
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Timezone-aware machen (annahme: UTC)
            if df['timestamp'].dt.tz is None:
                df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')

        return df

    def _quality_checks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Führt Datenqualitäts-Checks durch"""

        initial_count = len(df)

        # Entferne Rows mit invaliden Bid/Ask-Werten
        df = df[(df['bid'] > 0) & (df['ask'] > 0) & (df['ask'] >= df['bid'])]

        # Entferne extreme Outliers (Spreads > 10%)
        if 'spread_pct' in df.columns:
            df = df[df['spread_pct'] <= 10.0]

        removed = initial_count - len(df)
        if removed > 0:
            logger.warning(
                f"Qualitäts-Check: {removed} invalide Ticks entfernt")

        return df

    def _print_summary(self):
        """Druckt Zusammenfassung der Verarbeitung"""
        logger.info("=" * 50)
        logger.info("VERARBEITUNGS-ZUSAMMENFASSUNG")
        logger.info("=" * 50)
        logger.info(f"Verarbeitete Dateien: {self.processed_files}")
        logger.info(f"Gesamte Ticks: {self.total_ticks:,}")
        logger.info(f"Fehler: {len(self.errors)}")

        if self.errors:
            logger.error("FEHLER-LISTE:")
            for error in self.errors:
                logger.error(f"  - {error}")


# Hauptfunktion
if __name__ == "__main__":
    # Konfiguration
    SOURCE_DIR = "./data/raw/"          # MQL5 Export-Ordner
    TARGET_DIR = "./data/processed/"    # Parquet-Ziel

    logger.info("FiniexTestingIDE Tick Data Importer gestartet")

    # Importer initialisieren und ausführen
    importer = TickDataImporter(SOURCE_DIR, TARGET_DIR)
    importer.process_all_mql5_exports()

    logger.info("Import abgeschlossen!")
