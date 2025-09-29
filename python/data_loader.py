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

            # Zeitraum berechnen
            start_time = combined_df["timestamp"].min()
            end_time = combined_df["timestamp"].max()
            duration = end_time - start_time

            # Dauer in verschiedenen Einheiten
            duration_days = duration.days
            duration_hours = duration.total_seconds() / 3600
            duration_minutes = duration.total_seconds() / 60

            # Wochenenden zählen
            weekend_info = self._count_weekends(start_time, end_time)

            # Trading-Tage (Wochentage ohne Wochenenden)
            trading_days = duration_days - (weekend_info["full_weekends"] * 2)

            return {
                "symbol": symbol,
                "files": len(files),
                "total_ticks": len(combined_df),
                "date_range": {
                    "start": start_time.isoformat(),
                    "end": end_time.isoformat(),
                    "start_formatted": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "end_formatted": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration": {
                        "days": duration_days,
                        "hours": round(duration_hours, 2),
                        "minutes": round(duration_minutes, 2),
                        "total_seconds": duration.total_seconds(),
                        "trading_days": trading_days,  # Nur Wochentage
                        "weekends": weekend_info,
                    },
                },
                "statistics": {
                    "avg_spread_points": (
                        round(combined_df["spread_points"].mean(), 2)
                        if "spread_points" in combined_df
                        else None
                    ),
                    "avg_spread_pct": (
                        round(combined_df["spread_pct"].mean(), 4)
                        if "spread_pct" in combined_df
                        else None
                    ),
                    "tick_frequency_per_second": (
                        round(len(combined_df) / duration.total_seconds(), 2)
                        if duration.total_seconds() > 0
                        else 0
                    ),
                },
                "file_size_mb": round(total_size / 1024 / 1024, 2),
                "sessions": (
                    combined_df["session"].value_counts().to_dict()
                    if "session" in combined_df
                    else {}
                ),
            }

        except Exception as e:
            logger.error(f"Fehler beim Laden von Symbol-Info {symbol}: {e}")
            return {"error": f"Fehler beim Laden von {symbol}: {str(e)}"}

    def _count_weekends(
        self, start_date: datetime, end_date: datetime
    ) -> Dict[str, any]:
        """
        Zählt Wochenenden zwischen zwei Daten

        Args:
            start_date: Start-Datum
            end_date: End-Datum

        Returns:
            Dict mit Wochenend-Informationen
        """
        # Anzahl voller Wochen
        full_weeks = (end_date - start_date).days // 7

        # Restliche Tage
        remaining_days = (end_date - start_date).days % 7

        # Prüfe ob Start-/End-Tag auf Wochenende fällt
        # weekday(): Monday=0, Sunday=6
        start_weekday = start_date.weekday()  # 0=Mo, 5=Sa, 6=So
        end_weekday = end_date.weekday()

        # Zähle Samstage und Sonntage separat
        saturdays = full_weeks
        sundays = full_weeks

        # Prüfe Restliche Tage
        current_date = start_date
        for _ in range(remaining_days + 1):
            if current_date.weekday() == 5:  # Samstag
                saturdays += 1
            elif current_date.weekday() == 6:  # Sonntag
                sundays += 1
            current_date += timedelta(days=1)

        # Wochenend-Tage (Sa+So zusammen)
        weekend_days = saturdays + sundays

        # Anzahl kompletter Wochenenden (Sa+So Paare)
        full_weekends = min(saturdays, sundays)

        return {
            "full_weekends": full_weekends,
            "saturdays": saturdays,
            "sundays": sundays,
            "total_weekend_days": weekend_days,
            "start_is_weekend": start_weekday >= 5,
            "end_is_weekend": end_weekday >= 5,
        }

    def get_available_date_range(self, symbol: str) -> Tuple[datetime, datetime]:
        """
        Gibt verfügbaren Zeitraum für ein Symbol zurück

        Returns:
            Tuple[start_datetime, end_datetime]
        """
        files = self._get_symbol_files(symbol)
        if not files:
            raise ValueError(f"Keine Daten für Symbol {symbol} gefunden")

        # Schneller Check: Nur Metadaten lesen
        first_file = pq.read_table(files[0], columns=["timestamp"])
        last_file = pq.read_table(files[-1], columns=["timestamp"])

        start = pd.to_datetime(first_file["timestamp"][0].as_py())
        end = pd.to_datetime(last_file["timestamp"][-1].as_py())

        return start, end

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

        # Datums-Filter anwenden (mit Debug-Output)
        logger.info(f"Anwenden Datums-Filter: start={start_date}, end={end_date}")

        if start_date:
            # ⚡ WICHTIG: Timezone-aware Timestamp erstellen
            start_dt = pd.to_datetime(start_date).tz_localize("UTC")
            logger.info(f"Filtere nach start_date >= {start_dt}")

            # Vor-Filter Count
            count_before = len(combined_df)

            # Filtere DataFrame
            combined_df = combined_df[combined_df["timestamp"] >= start_dt]

            # Nach-Filter Count
            count_after = len(combined_df)
            logger.info(
                f"Nach start_date Filter: {count_before:,} -> {count_after:,} Ticks ({count_after/count_before*100:.1f}% behalten)"
            )

        if end_date:
            # ⚡ WICHTIG: Timezone-aware Timestamp erstellen
            end_dt = pd.to_datetime(end_date).tz_localize("UTC")
            logger.info(f"Filtere nach end_date <= {end_dt}")

            # Vor-Filter Count
            count_before = len(combined_df)

            # Filtere DataFrame
            combined_df = combined_df[combined_df["timestamp"] <= end_dt]

            # Nach-Filter Count
            count_after = len(combined_df)
            logger.info(
                f"Nach end_date Filter: {count_before:,} -> {count_after:,} Ticks ({count_after/count_before*100:.1f}% behalten)"
            )

        # Finale Validierung
        if len(combined_df) == 0:
            logger.error("⚠️ WARNUNG: Keine Daten nach Filterung übrig!")
            logger.error(f"Angeforderter Zeitraum: {start_date} bis {end_date}")
            return combined_df

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

        # Detaillierte Übersicht mit Zeiträumen
        print("\n" + "=" * 100)
        print("SYMBOL-ÜBERSICHT MIT ZEITRÄUMEN")
        print("=" * 100)

        for symbol in symbols:
            info = loader.get_symbol_info(symbol)

            if "error" not in info:
                weekends = info["date_range"]["duration"]["weekends"]

                print(f"\n📊 {symbol}")
                print(
                    f"   ├─ Zeitraum:     {info['date_range']['start_formatted']} bis {info['date_range']['end_formatted']}"
                )
                print(
                    f"   ├─ Dauer:        {info['date_range']['duration']['days']} Tage ({info['date_range']['duration']['hours']:.1f} Stunden)"
                )
                print(
                    f"   ├─ Trading-Tage: {info['date_range']['duration']['trading_days']} (ohne {weekends['full_weekends']} Wochenenden)"
                )
                print(
                    f"   │  └─ Wochenenden: {weekends['full_weekends']}x komplett ({weekends['saturdays']} Sa, {weekends['sundays']} So)"
                )
                print(f"   ├─ Ticks:        {info['total_ticks']:,}")
                print(f"   ├─ Dateien:      {info['files']}")
                print(f"   ├─ Größe:        {info['file_size_mb']:.1f} MB")

                if info["statistics"]["avg_spread_points"]:
                    print(
                        f"   ├─ Ø Spread:     {info['statistics']['avg_spread_points']:.1f} Points ({info['statistics']['avg_spread_pct']:.4f}%)"
                    )

                print(
                    f"   └─ Frequenz:     {info['statistics']['tick_frequency_per_second']:.2f} Ticks/Sekunde"
                )

                if info.get("sessions"):
                    print(
                        f"      Sessions:     {', '.join([f'{k}: {v}' for k, v in info['sessions'].items()])}"
                    )
            else:
                print(f"\n❌ {symbol}: {info['error']}")

        print("\n" + "=" * 100)

        # Test: Lade Daten für erstes Symbol
        if symbols:
            test_symbol = symbols[0]
            print(f"\n🧪 TEST-LADEN: {test_symbol}")
            print("=" * 100)

            info = loader.get_symbol_info(test_symbol)

            df = loader.load_symbol_data(
                test_symbol,
                start_date=info["date_range"]["start_formatted"].split()[0],
                end_date=info["date_range"]["end_formatted"].split()[0],
            )

            print(f"✓ Geladen:    {len(df):,} Ticks")
            print(f"✓ Zeitraum:   {df['timestamp'].min()} bis {df['timestamp'].max()}")
            print(
                f"✓ Spalten:    {', '.join(df.columns[:5])}... ({len(df.columns)} total)"
            )
            print(f"\n📋 Sample-Daten (erste 3 Ticks):")
            print(df.head(3).to_string())

    except Exception as e:
        logger.error(f"Fehler beim Test: {e}", exc_info=True)


if __name__ == "__main__":
    # Logging für Demo aktivieren
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    main()
