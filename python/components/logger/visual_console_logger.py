"""
VisualConsoleLogger - Farbige, kompakte Logging-Ausgabe
Vorbereitung für späteres TUI (Terminal User Interface)
"""

import logging
import sys
from datetime import datetime
from typing import Optional
from config import DEBUG_LOGGING, DEV_MODE, MOVE_PROCESSED_FILES


class ColorCodes:
    """ANSI Color Codes"""
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    GRAY = '\033[90m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


class VisualConsoleLogger:
    """
    Custom Logger mit:
    - Farbigen Log-Levels (ERROR=Rot, WARNING=Gelb, INFO=Blau, DEBUG=Grau)
    - Kompakten Klassennamen (statt vollqualifiziert)
    - Relativer Zeitanzeige (ms seit Start)
    - Gruppierung von Log-Sektionen
    - Terminal-optimiert (~60 Zeilen)
    """

    def __init__(self, name: str = "FiniexTestingIDE", terminal_height: int = 60):
        self.name = name
        self.terminal_height = terminal_height
        self.start_time = datetime.now()
        self.log_buffer = []

        # Logging Setup
        self._setup_custom_logger()

    def _setup_custom_logger(self):
        """Konfiguriert Python Logging mit Custom Formatter"""
        # Custom Formatter
        formatter = VisualLogFormatter(self.start_time)

        # Console Handler
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)

        # Root Logger konfigurieren
        root_logger = logging.getLogger()
        root_logger.handlers.clear()  # Alte Handler entfernen
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

    def info(self, message: str, logger_name: Optional[str] = None):
        """Log INFO message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.info(message)

    def warning(self, message: str, logger_name: Optional[str] = None):
        """Log WARNING message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.warning(message)

    def error(self, message: str, logger_name: Optional[str] = None):
        """Log ERROR message"""
        logger = logging.getLogger(logger_name or self.name)
        logger.error(message)

    def debug(self, message: str, logger_name: Optional[str] = None):
        """Log DEBUG message"""
        if not DEBUG_LOGGING:
            return
        logger = logging.getLogger(logger_name or self.name)
        logger.debug(message)

    def section_header(self, title: str, width: int = 60, char: str = "="):
        """Ausgabe einer Sektion-Überschrift"""
        print(f"\n{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{title.center(width)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")

    def section_separator(self, width: int = 60, char: str = "-"):
        """Ausgabe eines Sektion-Trenners"""
        print(f"{char * width}")

    def print_results_table(self, results: dict):
        """
        Gibt finale Results-Tabelle farbig formatiert aus
        Kompakt mit Scenarios nebeneinander (Grid-Layout)
        """
        self.section_header("🎉 EXECUTION RESULTS")

        # Haupt-Statistiken (kompakt in 2 Spalten)
        # FIXED: Korrekte Keys vom BatchOrchestrator
        success = results.get('success', False)
        scenarios_count = results.get('scenarios_count', 0)
        exec_time = results.get('execution_time', 0)

        # Worker/Parallel Info aus letztem Scenario extrahieren
        parallel_mode = False
        max_workers = 0
        if 'results' in results and len(results['results']) > 0:
            first_scenario = results['results'][0]
            if 'worker_statistics' in first_scenario:
                parallel_mode = first_scenario.get('parallel_mode', False)
            if 'global_contract' in results:
                max_workers = results['global_contract'].get(
                    'total_workers', 0)

        print(f"{ColorCodes.GREEN}✅ Success: {success}{ColorCodes.RESET}  |  "
              f"{ColorCodes.BLUE}📊 Scenarios: {scenarios_count}{ColorCodes.RESET}  |  "
              f"{ColorCodes.BLUE}⏱️  Time: {exec_time:.2f}s{ColorCodes.RESET}")
        print(f"{ColorCodes.GRAY}⚙️  Parallel: {parallel_mode}{ColorCodes.RESET}  |  "
              f"{ColorCodes.GRAY}⚙️  Workers: {max_workers}{ColorCodes.RESET}")

        # NEUE REIHENFOLGE: Global Contract ZUERST
        if "global_contract" in results:
            self._print_global_contract(results["global_contract"])

        # Scenario Details (als Grid)
        if "results" in results and len(results["results"]) > 0:
            self.section_separator()
            print(f"{ColorCodes.BOLD}SCENARIO DETAILS{ColorCodes.RESET}")
            self.section_separator()

            self._print_scenario_grid(results["results"])

        # Worker Statistics DANACH (aus letztem Scenario)
        if 'results' in results and len(results['results']) > 0:
            last_scenario = results['results'][-1]
            if 'worker_statistics' in last_scenario:
                self._print_worker_statistics(
                    last_scenario['worker_statistics'])

        print("=" * 120)

    def _print_scenario_grid(self, scenarios: list, columns: int = 3):
        """
        Gibt Scenarios als Grid aus (nebeneinander)
        FIXED: String-Längen ohne ANSI-Codes für korrekte Ausrichtung
        """
        box_width = 38

        for i in range(0, len(scenarios), columns):
            row_scenarios = scenarios[i:i+columns]

            # Erstelle Zeilen für jede Box
            lines = [[] for _ in range(8)]

            for scenario in row_scenarios:
                scenario_name = scenario.get('scenario_name', 'Unknown')[:28]
                symbol = scenario.get('symbol', 'N/A')
                ticks = scenario.get('ticks_processed', 0)
                signals = scenario.get('signals_generated', 0)
                rate = scenario.get('signal_rate', 0)

                # Worker stats
                worker_calls = 0
                decisions = 0
                if 'worker_statistics' in scenario:
                    stats = scenario['worker_statistics']
                    worker_calls = stats.get('worker_calls', 0)
                    decisions = stats.get('decisions_made', 0)

                # String-Formatierung mit exakter Länge (ohne ANSI in Berechnung!)
                def pad_line(text: str, width: int = 36) -> str:
                    """Pad line to exact width, truncate if too long"""
                    if len(text) > width:
                        return text[:width]
                    return text + ' ' * (width - len(text))

                # Zeilen erstellen (exakte Breite)
                line1_text = f"📋 {scenario_name}"
                line2_text = f"Symbol: {symbol}"
                line3_text = f"Ticks: {ticks:,}"
                line4_text = f"Signals: {signals} ({rate:.1%})"
                line5_text = f"Calls: {worker_calls:,}"
                line6_text = f"Decisions: {decisions}"

                # Box mit exaktem Padding
                lines[0].append(f"┌{'─' * (box_width-2)}┐")
                lines[1].append(f"│ {pad_line(line1_text)} │")
                lines[2].append(f"│ {pad_line(line2_text)} │")
                lines[3].append(f"│ {pad_line(line3_text)} │")
                lines[4].append(f"│ {pad_line(line4_text)} │")
                lines[5].append(f"│ {pad_line(line5_text)} │")
                lines[6].append(f"│ {pad_line(line6_text)} │")
                lines[7].append(f"└{'─' * (box_width-2)}┘")

            # Ausgabe
            for line_parts in lines:
                print("  ".join(line_parts))

            print()  # Leerzeile zwischen Rows

    def _print_worker_statistics(self, stats: dict):
        """
        Worker-Statistiken kompakt nebeneinander
        """
        self.section_separator(width=120)

        # Basis-Statistiken
        ticks_processed = stats.get('ticks_processed', 0)
        worker_calls = stats.get('worker_calls', 0)
        decisions_made = stats.get('decisions_made', 0)
        parallel_workers = stats.get('parallel_workers')

        # Erste Zeile: Basis-Stats
        mode_str = f"({'Parallel' if parallel_workers else 'Sequential'})" if parallel_workers is not None else ""
        print(f"{ColorCodes.BOLD}📊 WORKER STATS{ColorCodes.RESET} {mode_str}  |  "
              f"Ticks: {ticks_processed:,}  |  "
              f"Calls: {worker_calls:,}  |  "
              f"Decisions: {decisions_made}")

        # Zweite Zeile: Parallelization Metrics (falls vorhanden)
        if 'parallel_execution_time_saved_ms' in stats or 'parallel_stats' in stats:
            # Neue Struktur
            if 'parallel_stats' in stats:
                pstats = stats['parallel_stats']
                time_saved = pstats.get('total_time_saved_ms', 0)
                avg_saved = pstats.get('avg_saved_per_tick_ms', 0)
                status = pstats.get('status', 'N/A')
            # Alte Struktur
            else:
                time_saved = stats.get('parallel_execution_time_saved_ms', 0)
                avg_saved = stats.get('avg_time_saved_per_tick_ms', 0)

                if time_saved > 0.01:
                    status = "✅ Faster"
                elif time_saved < -0.01:
                    status = "⚠️  Slower"
                else:
                    status = "≈ Equal"

            print(f"{ColorCodes.BOLD}  ⚡ PARALLEL{ColorCodes.RESET}  |  "
                  f"Saved: {time_saved:.2f}ms  |  "
                  f"Avg/tick: {avg_saved:.3f}ms  |  "
                  f"Status: {status}")

    def _print_global_contract(self, contract: dict):
        """Global Contract ausgeben (kompakt)"""
        self.section_separator(width=120)
        print(f"{ColorCodes.BOLD}GLOBAL CONTRACT{ColorCodes.RESET}  |  "
              f"Warmup: {contract.get('max_warmup_bars', 0)} bars  |  "
              f"Timeframes: {', '.join(contract.get('timeframes', []))}  |  "
              f"Workers: {contract.get('total_workers', 0)}")


class VisualLogFormatter(logging.Formatter):
    """
    Custom Formatter:
    - Farbige Log-Levels
    - Kompakte Klassennamen (mit C/ Präfix falls Klasse erkannt)
    - Relative Zeit (ms seit Start)
    """

    def __init__(self, start_time: datetime):
        super().__init__()
        self.start_time = start_time

        # Level -> Farbe Mapping
        self.level_colors = {
            logging.ERROR: ColorCodes.RED,
            logging.WARNING: ColorCodes.YELLOW,
            logging.INFO: ColorCodes.BLUE,
            logging.DEBUG: ColorCodes.GRAY,
        }

    def format(self, record: logging.LogRecord) -> str:
        """Formatiert Log-Eintrag"""
        # Relative Zeit berechnen (ms seit Start)
        now = datetime.now()
        elapsed_ms = int((now - self.start_time).total_seconds() * 1000)

        # Zeitformat: ab 1000ms → "Xs XXXms" für bessere Lesbarkeit
        if elapsed_ms >= 1000:
            seconds = elapsed_ms // 1000
            millis = elapsed_ms % 1000
            time_display = f"{seconds:>3}s {millis:03d}ms"
        else:
            time_display = f"   {elapsed_ms:>3}ms   "

        # Klassennamen extrahieren und ggf. mit C/ präfixen
        logger_name = record.name
        if '.' in logger_name:
            class_name = logger_name.split('.')[-1]
            # Erkennung: Großbuchstabe am Anfang = Klasse
            if class_name and class_name[0].isupper():
                display_name = f"C/{class_name}"
            else:
                display_name = class_name
        else:
            display_name = logger_name

        # Farbe für Level
        level_color = self.level_colors.get(record.levelno, ColorCodes.RESET)
        level_name = record.levelname

        # Formatierung
        formatted = (
            f"{ColorCodes.GRAY}{time_display}{ColorCodes.RESET} - "
            f"{ColorCodes.GRAY}{display_name:<25}{ColorCodes.RESET} - "
            f"{level_color}{level_name:<7}{ColorCodes.RESET} - "
            f"{record.getMessage()}"
        )

        return formatted
