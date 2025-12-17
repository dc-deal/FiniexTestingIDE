"""
FiniexTestingIDE - Backtesting Sample Worker
Worker for MVP validation testing

Responsibilities:
1. Validate warmup bar counts match configured requirements
2. Capture bar snapshots at configured tick numbers
3. Return all validation data in WorkerResult.metadata

This worker does NO computation - it only validates and captures data
for the test suite to verify against prerendered bars.

Configuration Example:
{
    "periods": {
        "M5": 14,      # Expect 14 warmup bars for M5
        "M30": 20      # Expect 20 warmup bars for M30
    },
    "bar_snapshot_checks": [
        {
            "timeframe": "M5",
            "bar_index": 3,
            "check_at_tick": 150
        }
    ]
}

Data Flow:
1. First tick: validate warmup bars against periods config
2. At configured ticks: capture bar snapshots
3. Every tick: return metadata with validation status and snapshots
4. BacktestingDeterministic extracts metadata for BacktestingMetadata
"""

from typing import Any, Dict, List

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstactWorker


class BacktestingSampleWorker(AbstactWorker):
    """
    Validation worker for backtesting - captures warmup status and bar snapshots.

    This worker validates that warmup bars are correctly loaded and captures
    bar snapshots at specific ticks for comparison with prerendered bars.

    Unlike computation workers (RSI, Envelope), this worker:
    - Returns no computation value (value=None)
    - Only populates metadata with validation data
    - Is designed for testing, not production trading

    Configuration:
        periods: Dict[timeframe, expected_count] - warmup requirements
        bar_snapshot_checks: List of snapshot configurations
            - timeframe: Which timeframe to snapshot
            - bar_index: Which bar in history (0 = oldest)
            - check_at_tick: At which tick to capture
    """

    def __init__(
        self,
        name: str,
        parameters: Dict,
        logger: ScenarioLogger,
        **kwargs
    ):
        """
        Initialize BacktestingSampleWorker.

        Args:
            name: Worker instance name
            parameters: Configuration dict with 'periods' and 'bar_snapshot_checks'
            logger: ScenarioLogger instance
            **kwargs: Legacy constructor support
        """
        super().__init__(name=name, parameters=parameters, logger=logger, **kwargs)

        params = parameters or {}

        # Extract 'periods' - defines warmup requirements per timeframe
        # Same structure as RSI worker for consistency
        self.periods = params.get('periods', kwargs.get('periods', {}))

        if not self.periods:
            raise ValueError(
                f"BacktestingSampleWorker '{name}' requires 'periods' in config "
                f"(e.g. {{'M5': 14, 'M30': 20}})"
            )

        # Extract snapshot check configurations
        self.snapshot_checks = params.get(
            'bar_snapshot_checks',
            kwargs.get('bar_snapshot_checks', [])
        )

        # Internal state
        self.warmup_validated = False
        self.warmup_status: Dict[str, Dict] = {}
        self.snapshots: Dict[str, Dict[str, Any]] = {}
        self.tick_count = 0

        self.logger.debug(
            f"BacktestingSampleWorker '{name}' initialized: "
            f"periods={self.periods}, "
            f"snapshot_checks={len(self.snapshot_checks)}"
        )

    # ============================================
    # Class Methods (Factory Interface)
    # ============================================

    @classmethod
    def get_required_parameters(cls) -> Dict[str, type]:
        """
        No additional required parameters.

        'periods' validation happens in parent class for INDICATOR type.
        """
        return {}

    @classmethod
    def get_optional_parameters(cls) -> Dict[str, Any]:
        """Optional parameters with defaults."""
        return {
            'bar_snapshot_checks': [],  # No snapshots by default
        }

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        """This is an INDICATOR-type worker (requires warmup bars)."""
        return WorkerType.INDICATOR

    # ============================================
    # Instance Methods (Runtime Interface)
    # ============================================

    def get_warmup_requirements(self) -> Dict[str, int]:
        """
        Return warmup requirements from 'periods' config.

        This tells the system how many bars to load per timeframe
        before starting the tick loop.

        Returns:
            Dict[timeframe, bars_needed] - e.g. {"M5": 14, "M30": 20}
        """
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        """
        Return required timeframes from 'periods' config.

        Returns:
            List of timeframe strings - e.g. ["M5", "M30"]
        """
        return list(self.periods.keys())

    def get_max_computation_time_ms(self) -> float:
        """
        This worker is fast - just validation and dict operations.
        """
        return 10.0

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        """
        Always recompute to check for snapshot triggers.

        Unlike RSI which only needs to recompute when bars update,
        this worker needs to check every tick for snapshot captures.
        """
        return True

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """
        Validate warmup and capture bar snapshots.

        First tick:
            - bar_history contains warmup bars (loaded before tick loop)
            - Validate counts against periods config

        Subsequent ticks:
            - Check if current tick matches any snapshot_check
            - Capture bar snapshot if match found

        Args:
            tick: Current tick data
            bar_history: Historical bars per timeframe (includes warmup)
            current_bars: Current (incomplete) bars per timeframe

        Returns:
            WorkerResult with metadata containing validation data
        """
        self.tick_count += 1

        # ============================================
        # Warmup Validation (First Tick Only)
        # ============================================
        if not self.warmup_validated:
            self.warmup_status = self._validate_warmup(bar_history)
            self.warmup_validated = True

            # Log validation results
            for tf, status in self.warmup_status.items():
                if status['valid']:
                    self.logger.debug(
                        f"✅ Warmup valid: {tf} = {status['actual']} bars "
                        f"(expected {status['expected']})"
                    )
                else:
                    self.logger.warning(
                        f"❌ Warmup invalid: {tf} - {status['error']}"
                    )

        # ============================================
        # Bar Snapshot Capture (At Configured Ticks)
        # ============================================
        for check in self.snapshot_checks:
            if self.tick_count == check['check_at_tick']:
                self._capture_snapshot(check, bar_history, current_bars)

        # ============================================
        # Return Result with Metadata
        # ============================================
        return WorkerResult(
            worker_name=self.name,
            value=None,  # No computation value - validation only
            confidence=1.0,  # Always confident in validation data
            metadata={
                'warmup_status': self.warmup_status,
                'bar_snapshots': self.snapshots,
                'tick_count': self.tick_count
            }
        )

    def _validate_warmup(self, bar_history: Dict[str, List[Bar]]) -> Dict[str, Dict]:
        """
        Validate warmup bar counts against configured requirements.

        Checks each timeframe in 'periods' config:
        - Get actual bar count from bar_history
        - Compare with expected count from periods
        - Record validation status

        Args:
            bar_history: Historical bars loaded before tick loop

        Returns:
            Dict[timeframe, status_dict] where status_dict contains:
                - valid: bool
                - expected: int
                - actual: int
                - error: Optional[str]
        """
        status = {}

        for timeframe, expected_count in self.periods.items():
            bars = bar_history.get(timeframe, [])
            actual_count = len(bars)
            valid = actual_count == expected_count

            status[timeframe] = {
                'valid': valid,
                'expected': expected_count,
                'actual': actual_count,
                'error': None if valid else (
                    f"Expected {expected_count} warmup bars, got {actual_count}"
                )
            }

        return status

    def _capture_snapshot(
        self,
        check: Dict[str, Any],
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar]
    ) -> None:
        """
        Capture bar snapshot at specific tick.

        Stores bar as dict for JSON serialization.
        Uses Bar.to_dict() for consistent serialization.

        Args:
            check: Snapshot check config with timeframe, bar_index, check_at_tick
            bar_history: Historical bars
            current_bars: Current (incomplete) bars
        """
        timeframe = check['timeframe']
        bar_index = check.get('bar_index', -1)  # -1 = current bar

        # Determine which bar to snapshot
        if bar_index == -1:
            # Snapshot current (incomplete) bar
            bar = current_bars.get(timeframe)
        else:
            # Snapshot from history
            bars = bar_history.get(timeframe, [])
            if bar_index < len(bars):
                bar = bars[bar_index]
            else:
                bar = None

        if bar is not None:
            # Create unique key for this snapshot
            key = f"{timeframe}_bar{bar_index}_tick{self.tick_count}"

            # Serialize bar to dict using Bar.to_dict()
            self.snapshots[key] = bar.to_dict()

            self.logger.debug(
                f"📸 Captured snapshot: {key} | "
                f"close={bar.close:.5f} | ticks={bar.tick_count}"
            )
        else:
            self.logger.warning(
                f"⚠️ Cannot capture snapshot at tick {self.tick_count}: "
                f"bar not found (timeframe={timeframe}, index={bar_index})"
            )
