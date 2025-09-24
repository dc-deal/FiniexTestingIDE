"""
FiniexTestingIDE Data Loader
Schneller Zugriff auf Parquet-basierte Tick-Daten
"""

import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class TickDataLoader:
    """Lädt und verwaltet Tick-Daten für Backtesting"""

    def __init__(self, data_dir: str = "./data/processed/"):
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Daten-Verzeichnis nicht gefunden: {data_dir}")

        self._symbol_cache = {}  # Cache für geladene Daten

    def list_available_symbols(self) -> List[str]:
        """Listet alle verfügbaren Symbole auf"""
        parquet_files = list(self.data_dir.glob("*.parquet"))
        symbols = set()

        for file in parquet_files:
            try:
                # Symbol aus Dateiname extrahieren (FORMAT: SYMBOL_YYYYMMDD_HHMMSS.parquet)
                symbol = file.name.split("_")[0]
                symbols.add(symbol)
            except IndexError:
                logger.warning(f"Dateiname hat unerwartetes Format: {file.name}")

        return sorted(list(symbols))

    def get_symbol_info(self, symbol: str) -> Dict[str, any]:
        """Gibt detaillierte Informationen über ein Symbol zurück"""
        files = self._get_symbol_files(symbol)

        if not files:
            return {"error": f"Keine Daten für Symbol {symbol} gefunden"}

        try:
            # Alle Files einlesen für Statistiken
            dataframes = []
            total_size = 0

            for file in files:
                df = pd.read_parquet(file)
                dataframes.append(df)
                total_size += file.stat().st_size

            # Kombinieren
            combined_df = pd.concat(dataframes, ignore_index=True)
            combined_df = combined_df.sort_values("timestamp")

            return {
                "symbol": symbol,
                "files": len(files),
                "total_ticks": len(combined_df),
                "date_range": {
                    "start": combined_df["timestamp"].min().isoformat(),
                    "end": combined_df["timestamp"].max().isoformat(),
                    "days": (
                        combined_df["timestamp"].max() - combined_df["timestamp"].min()
                    ).days,
                },
                "statistics": {
                    "avg_spread_points": (
                        combined_df["spread_points"].mean()
                        if "spread_points" in combined_df
                        else None
                    ),
                    "avg_spread_pct": (
                        combined_df["spread_pct"].mean()
                        if "spread_pct" in combined_df
                        else None
                    ),
                    "tick_frequency_per_second": len(combined_df)
                    / (
                        (
                            combined_df["timestamp"].max()
                            - combined_df["timestamp"].min()
                        ).total_seconds()
                    ),
                },
                "file_size_mb": total_size / 1024 / 1024,
                "sessions": (
                    combined_df["session"].value_counts().to_dict()
                    if "session" in combined_df
                    else {}
                ),
            }

        except Exception as e:
            return {"error": f"Fehler beim Laden von {symbol}: {str(e)}"}

    def load_symbol_data(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Lädt Tick-Daten für ein Symbol

        Args:
            symbol: Währungspaar (z.B. 'EURUSD')
            start_date: Start-Datum (ISO format oder None für alle Daten)
            end_date: End-Datum (ISO format oder None für alle Daten)
            use_cache: Ob gecachte Daten verwendet werden sollen

        Returns:
            DataFrame mit Tick-Daten
        """

        cache_key = f"{symbol}_{start_date}_{end_date}"

        # Cache check
        if use_cache and cache_key in self._symbol_cache:
            logger.info(f"Verwende gecachte Daten für {symbol}")
            return self._symbol_cache[cache_key].copy()

        # Files für Symbol finden
        files = self._get_symbol_files(symbol)

        if not files:
            raise ValueError(f"Keine Daten für Symbol {symbol} gefunden")

        logger.info(f"Lade {len(files)} Dateien für {symbol}")

        # Alle Files einlesen und kombinieren
        dataframes = []
        for file in files:
            try:
                df = pd.read_parquet(file)
                dataframes.append(df)
            except Exception as e:
                logger.warning(f"Fehler beim Lesen von {file}: {e}")

        if not dataframes:
            raise ValueError(f"Keine gültigen Daten für {symbol} gefunden")

        # Kombinieren und sortieren
        combined_df = pd.concat(dataframes, ignore_index=True)
        combined_df = combined_df.sort_values("timestamp").reset_index(drop=True)

        # Duplikate entfernen (neueste behalten)
        combined_df = combined_df.drop_duplicates(subset=["timestamp"], keep="last")

        # Datums-Filter anwenden
        if start_date:
            combined_df = combined_df[
                combined_df["timestamp"] >= pd.to_datetime(start_date)
            ]
        if end_date:
            combined_df = combined_df[
                combined_df["timestamp"] <= pd.to_datetime(end_date)
            ]

        # Cache update
        if use_cache:
            self._symbol_cache[cache_key] = combined_df.copy()

        logger.info(f"✓ Geladen: {len(combined_df):,} Ticks für {symbol}")
        return combined_df

    def get_data_summary(self) -> Dict[str, Dict]:
        """Erstellt Übersicht über alle verfügbaren Daten"""
        symbols = self.list_available_symbols()
        summary = {}

        logger.info(f"Erstelle Übersicht für {len(symbols)} Symbole...")

        for symbol in symbols:
            summary[symbol] = self.get_symbol_info(symbol)

        return summary

    def _get_symbol_files(self, symbol: str) -> List[Path]:
        """Findet alle Parquet-Files für ein Symbol"""
        pattern = f"{symbol}_*.parquet"
        files = list(self.data_dir.glob(pattern))
        return sorted(files)  # Chronologische Sortierung

    def clear_cache(self):
        """Leert den Daten-Cache"""
        self._symbol_cache.clear()
        logger.info("Daten-Cache geleert")


# Test- und Demo-Funktion
def main():
    """Demo-Funktion für Data Loader"""
    logger.info("=== FiniexTestingIDE Data Loader Demo ===")

    try:
        loader = TickDataLoader()

        # Verfügbare Symbole auflisten
        symbols = loader.list_available_symbols()
        logger.info(f"Verfügbare Symbole: {symbols}")

        if not symbols:
            logger.error(
                "Keine Daten gefunden! Bitte erst MQL5-Daten sammeln und konvertieren."
            )
            return

        # Detaillierte Übersicht
        summary = loader.get_data_summary()

        print("\n" + "=" * 80)
        print("DATEN-ÜBERSICHT")
        print("=" * 80)

        for symbol, info in summary.items():
            if "error" not in info:
                print(f"\n{symbol}:")
                print(f"  📊 {info['total_ticks']:,} Ticks")
                print(
                    f"  📅 {info['date_range']['start']} bis {info['date_range']['end']}"
                )
                print(f"  💾 {info['file_size_mb']:.1f} MB")
                if info["statistics"]["avg_spread_points"]:
                    print(
                        f"  📈 Ø Spread: {info['statistics']['avg_spread_points']:.1f} Points"
                    )
            else:
                print(f"\n{symbol}: ❌ {info['error']}")

        # Test: Lade Daten für erstes Symbol
        if symbols:
            test_symbol = symbols[0]
            print(f"\n" + "=" * 80)
            print(f"TEST: Lade Daten für {test_symbol}")
            print("=" * 80)

            df = loader.load_symbol_data(test_symbol)

            print(f"✓ Geladen: {len(df):,} Ticks")
            print(f"Zeitbereich: {df['timestamp'].min()} bis {df['timestamp'].max()}")
            print(f"Spalten: {list(df.columns)}")
            print(f"Sample-Daten:")
            print(df.head(3))

    except Exception as e:
        logger.error(f"Fehler beim Test: {e}")


if __name__ == "__main__":
    # Logging für Demo aktivieren
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    main()
