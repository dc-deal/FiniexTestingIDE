"""
Framework Exceptions - Custom errors for data validation and warmup
"""

from datetime import datetime

from python.framework.utils.time_utils import format_duration, format_minutes


class InsufficientHistoricalDataError(Exception):
    """
    Raised when historical data doesn't cover required warmup period.

    This occurs when the earliest available tick is later than
    the required warmup start time.
    """

    def __init__(
        self,
        required_start: datetime,
        first_available: datetime,
        symbol: str,
        scenario_name: str = None,
        scenario_start: datetime = None,
        config_path: str = None
    ):
        self.required_start = required_start
        self.first_available = first_available
        self.symbol = symbol
        self.scenario_name = scenario_name
        self.scenario_start = scenario_start
        self.missing_time = first_available - required_start

        message = (
            f"❌ Insufficient historical data for {symbol}!\n"
            f"\n"
            f"   📊 YOUR SCENARIO:\n"
        )

        if scenario_name:
            message += f"      Scenario:           '{scenario_name}'\n"
        if scenario_start:
            message += f"      Starts at:          {scenario_start.isoformat()}\n"
            message += f"      Must load from:     {required_start.isoformat()}\n"

        message += (
            f"\n"
            f"   📁 AVAILABLE DATA:\n"
            f"      First tick:         {first_available.isoformat()}\n"
            f"      Missing timespan:   {self.missing_time}\n"
            f"\n"
            f"   💡 SOLUTION:\n"
        )

        if config_path and scenario_name:
            message += f"      1. Open: {config_path}\n"
            message += f"      2. Find scenario: '{scenario_name}'\n"

        # Calculate recommended start date (first_available + warmup + buffer)
        message += f"      → Move 'start_date' later to match available data\n"

        super().__init__(message)


class InsufficientWarmupDataError(Exception):
    """
    Raised when not enough bars could be rendered for warmup.

    This occurs when tick data exists but doesn't render enough
    complete bars for the required warmup period.
    """

    def __init__(
        self,
        timeframe: str,
        required_bars: int,
        rendered_bars: int,
        last_bar_timestamp: str = None
    ):
        self.timeframe = timeframe
        self.required_bars = required_bars
        self.rendered_bars = rendered_bars
        self.missing_bars = required_bars - rendered_bars
        self.last_bar_timestamp = last_bar_timestamp

        message = (
            f"❌ Failed to render enough warmup bars for {timeframe}!\n"
            f"   Required bars:  {required_bars}\n"
            f"   Rendered bars:  {rendered_bars}\n"
            f"   Missing bars:   {self.missing_bars}\n"
        )

        if last_bar_timestamp:
            message += f"   Last bar:       {last_bar_timestamp}\n"

        message += "   → Check data quality or extend warmup period"

        super().__init__(message)


class WeekendOverlapError(Exception):
    """
    Raised when warmup period spans weekend and timeframe doesn't support it.

    For intraday timeframes (M1-H4), weekends are handled automatically
    by skipping weekend ticks. For daily+ timeframes, special logic is needed.
    """

    def __init__(
        self,
        timeframe: str,
        start: datetime,
        end: datetime,
        weekend_days: int
    ):
        self.timeframe = timeframe
        self.start = start
        self.end = end
        self.weekend_days = weekend_days

        message = (
            f"⚠️ Warmup period spans weekend for {timeframe}!\n"
            f"   Period:         {start.isoformat()} → {end.isoformat()}\n"
            f"   Weekend days:   {weekend_days}\n"
        )

        if timeframe in ['D1', 'W1', 'MN']:
            message += (
                f"   → Daily+ timeframes require weekend gap handling\n"
                f"   → Use intraday timeframes (M1-H4) or implement weekend logic"
            )
        else:
            message += (
                f"   → Weekend ticks will be automatically skipped\n"
                f"   → Warmup will use Friday's bars"
            )

        super().__init__(message)
