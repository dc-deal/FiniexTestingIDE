"""
Bar Index Report Generator
Generates structured reports for pre-rendered bar data

Location: python/framework/reports/bar_index_report.py
Output: framework/reports/bar_index_YYYYMMDD_HHMMSS.json
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List

from python.data_management.index.bars_index_manager import BarsIndexManager

from python.framework.logging.bootstrap_logger import get_global_logger
vLog = get_global_logger()


class BarIndexReportGenerator:
    """
    Generates comprehensive reports about bar index status.

    Reports include:
    - Overview statistics
    - Per-symbol breakdown
    - Per-timeframe statistics
    - Data collector information
    - File locations and sizes
    """

    def __init__(self, index_manager: BarsIndexManager):
        """
        Initialize report generator.

        Args:
            index_manager: Initialized BarsIndexManager
        """
        self.index_manager = index_manager
        self.reports_dir = Path("./framework/reports/")
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def generate_report(self) -> Path:
        """
        Generate complete bar index report.

        Returns:
            Path to saved report file
        """
        vLog.info("📊 Generating bar index report...")

        # Build report structure
        report = {
            "metadata": self._generate_metadata(),
            "overview": self._generate_overview(),
            "symbols": self._generate_symbol_details(),
            "data_collectors": self._generate_collector_summary()
        }

        # Save report
        report_path = self._save_report(report)

        vLog.info(f"✅ Report generated: {report_path}")
        return report_path

    def _generate_metadata(self) -> Dict:
        """Generate report metadata"""
        return {
            "report_type": "bar_index_report",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_directory": str(self.index_manager.data_dir),
            "index_file": str(self.index_manager.index_file),
            "index_exists": self.index_manager.index_file.exists()
        }

    def _generate_overview(self) -> Dict:
        """Generate overview statistics"""
        symbols = self.index_manager.list_symbols()

        total_bars = 0
        total_size_mb = 0
        total_files = 0
        timeframe_counts = {}

        for symbol in symbols:
            stats = self.index_manager.get_symbol_stats(symbol)

            for tf, tf_stats in stats.items():
                total_bars += tf_stats['bar_count']
                total_size_mb += tf_stats['file_size_mb']
                total_files += 1

                # Count timeframe occurrences
                timeframe_counts[tf] = timeframe_counts.get(tf, 0) + 1

        # Collect market types and activity totals
        market_types = set()
        total_tick_count = 0

        for symbol in symbols:
            for tf, entry in self.index_manager.index[symbol].items():
                market_types.add(entry.get('market_type', 'unknown'))
                total_tick_count += entry.get('total_tick_count', 0)

        return {
            "total_symbols": len(symbols),
            "total_timeframes": sum(timeframe_counts.values()),
            "total_bar_files": total_files,
            "total_bars": total_bars,
            "total_size_mb": round(total_size_mb, 2),
            "total_tick_count": total_tick_count,
            "market_types": sorted(list(market_types)),
            "timeframe_distribution": timeframe_counts
        }

    def _generate_symbol_details(self) -> Dict:
        """Generate detailed per-symbol information"""
        symbols = self.index_manager.list_symbols()
        symbol_details = {}

        for symbol in symbols:
            stats = self.index_manager.get_symbol_stats(symbol)
            timeframes = self.index_manager.get_available_timeframes(symbol)

            # Calculate symbol totals
            total_bars = sum(tf_stats['bar_count']
                             for tf_stats in stats.values())
            total_size = sum(tf_stats['file_size_mb']
                             for tf_stats in stats.values())

            # Get time ranges (earliest start, latest end)
            start_times = [tf_stats['start_time']
                           for tf_stats in stats.values()]
            end_times = [tf_stats['end_time'] for tf_stats in stats.values()]

            # Get metadata from first timeframe entry
            first_tf = sorted(timeframes)[0]
            first_entry = self.index_manager.index[symbol][first_tf]

            market_type = first_entry.get('market_type', 'unknown')
            source_version_min = first_entry.get(
                'source_version_min', 'unknown')
            source_version_max = first_entry.get(
                'source_version_max', 'unknown')
            data_source = first_entry.get('data_source', 'unknown')

            # Calculate total tick count for symbol
            total_tick_count = sum(
                self.index_manager.index[symbol][tf].get('total_tick_count', 0)
                for tf in timeframes
            )

            symbol_details[symbol] = {
                "available_timeframes": sorted(timeframes),
                "timeframe_count": len(timeframes),
                "total_bars": total_bars,
                "total_size_mb": round(total_size, 2),
                "total_tick_count": total_tick_count,
                "time_range": {
                    "start": min(start_times) if start_times else None,
                    "end": max(end_times) if end_times else None
                },
                # Metadata
                "market_type": market_type,
                "data_source": data_source,
                "source_version": {
                    "min": source_version_min,
                    "max": source_version_max
                },
                "timeframes": {}
            }

            # Add per-timeframe details
            for tf in sorted(timeframes):
                tf_stats = stats[tf]
                entry = self.index_manager.index[symbol][tf]

                # Get file path for this timeframe
                bar_file = self.index_manager.get_bar_file(symbol, tf)

                symbol_details[symbol]["timeframes"][tf] = {
                    "bar_count": tf_stats['bar_count'],
                    "file_size_mb": tf_stats['file_size_mb'],
                    "start_time": tf_stats['start_time'],
                    "end_time": tf_stats['end_time'],
                    "file_path": str(bar_file) if bar_file else None,
                    # Activity statistics
                    "total_tick_count": entry.get('total_tick_count', 0),
                    "avg_ticks_per_bar": entry.get('avg_ticks_per_bar', 0),
                    "min_ticks_per_bar": entry.get('min_ticks_per_bar', 0),
                    "max_ticks_per_bar": entry.get('max_ticks_per_bar', 0),
                    # Bar type distribution
                    "real_bar_count": entry.get('real_bar_count', 0),
                    "synthetic_bar_count": entry.get('synthetic_bar_count', 0),
                }

        return symbol_details

    def _generate_collector_summary(self) -> Dict:
        """Generate data collector summary"""
        collectors = {}

        # Scan all indexed files to detect collectors
        for symbol, timeframes in self.index_manager.index.items():
            for tf, entry in timeframes.items():
                file_path = Path(entry['path'])

                # Extract collector from path (e.g., mt5/bars/EURUSD/...)
                parts = file_path.parts

                # Find 'bars' directory and get parent (collector name)
                try:
                    bars_idx = parts.index('bars')
                    if bars_idx > 0:
                        collector = parts[bars_idx - 1]
                    else:
                        collector = 'unknown'
                except ValueError:
                    collector = 'unknown'

                if collector not in collectors:
                    collectors[collector] = {
                        "symbols": set(),
                        "total_bars": 0,
                        "total_size_mb": 0,
                        "timeframes": set()
                    }

                collectors[collector]["symbols"].add(symbol)
                collectors[collector]["total_bars"] += entry['bar_count']
                collectors[collector]["total_size_mb"] += entry['file_size_mb']
                collectors[collector]["timeframes"].add(tf)

        # Convert sets to lists for JSON serialization
        for collector in collectors:
            collectors[collector]["symbols"] = sorted(
                list(collectors[collector]["symbols"]))
            collectors[collector]["timeframes"] = sorted(
                list(collectors[collector]["timeframes"]))
            collectors[collector]["symbol_count"] = len(
                collectors[collector]["symbols"])
            collectors[collector]["timeframe_count"] = len(
                collectors[collector]["timeframes"])
            collectors[collector]["total_size_mb"] = round(
                collectors[collector]["total_size_mb"], 2)

        return collectors

    def _save_report(self, report: Dict) -> Path:
        """
        Save report to JSON file.

        Args:
            report: Report dictionary

        Returns:
            Path to saved report
        """
        timestamp = datetime.now(timezone.utc) .strftime("%Y%m%d_%H%M%S")
        filename = f"bar_index_{timestamp}.json"
        report_path = self.reports_dir / filename

        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Also save a "latest" symlink/copy
        latest_path = self.reports_dir / "bar_index_latest.json"
        with open(latest_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        return report_path
