"""
FiniexTestingIDE - Warmup Quality Reporter
Reports quality metrics for warmup bar data
"""

from python.components.logger.bootstrap_logger import get_logger
from python.framework.bars.bar_rendering_controller import BarRenderingController

vLog = get_logger()


def print_warmup_quality_metrics(bar_orchestrator: BarRenderingController) -> None:
    """
    Print warmup bar quality metrics.

    Analyzes warmup data for synthetic and hybrid bars which may
    affect indicator warmup accuracy:
    - Synthetic bars: Completely generated (no real tick data)
    - Hybrid bars: Mix of real and synthetic data

    Synthetic/hybrid bars typically occur when warmup period spans:
    - Weekends
    - Holidays
    - Market closures
    - Data gaps

    Warns if any quality issues detected to alert user that
    indicator warmup may be unrealistic.

    Args:
        bar_orchestrator: BarRenderingController with warmup data

    Example Output:
        ⚠️  1m warmup quality: 12 synthetic (2.4%), 5 hybrid (1.0%) of 500 bars
        ⚠️  5m warmup quality: 3 hybrid (0.6%) of 500 bars
        ⚠️  Warmup contains synthetic/hybrid bars - indicator warmup may be unrealistic!
           This typically happens when warmup period spans weekends/holidays.
    """
    # Get warmup quality metrics from bar orchestrator
    warmup_quality = bar_orchestrator.get_warmup_quality_metrics()

    has_quality_issues = False

    for timeframe, metrics in warmup_quality.items():
        synthetic = metrics['synthetic']
        hybrid = metrics['hybrid']
        total = metrics['total']

        if synthetic > 0 or hybrid > 0:
            has_quality_issues = True

            # Build warning message
            issues = []
            if synthetic > 0:
                issues.append(
                    f"{synthetic} synthetic ({metrics['synthetic_pct']:.1f}%)"
                )
            if hybrid > 0:
                issues.append(
                    f"{hybrid} hybrid ({metrics['hybrid_pct']:.1f}%)"
                )

            vLog.warning(
                f"⚠️  {timeframe} warmup quality: {', '.join(issues)} of {total} bars"
            )

    if has_quality_issues:
        vLog.warning(
            f"⚠️  Warmup contains synthetic/hybrid bars - indicator warmup may be unrealistic!"
        )
        vLog.warning(
            f"   This typically happens when warmup period spans weekends/holidays."
        )
