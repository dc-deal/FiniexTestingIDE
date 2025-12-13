"""
FiniexTestingIDE - Warmup Quality Reporter
Reports quality metrics for warmup bar data
"""

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.bars.bar_rendering_controller import BarRenderingController

vLog = get_global_logger()


def print_warmup_quality_metrics(bar_rendering_controller: BarRenderingController) -> None:
    """
    Print warmup bar quality metrics.

    Analyzes warmup data for synthetic bars which may
    affect indicator warmup accuracy:
    - Synthetic bars: Completely generated (no real tick data)
    - Hybrid bars: Mix of real and synthetic data

    Synthetic bars typically occur when warmup period spans:
    - Weekends
    - Holidays
    - Market closures
    - Data gaps

    Warns if any quality issues detected to alert user that
    indicator warmup may be unrealistic.

    Args:
        bar_rendering_controller: BarRenderingController with warmup data
    """
    # Get warmup quality metrics from bar orchestrator
    warmup_quality = bar_rendering_controller.get_warmup_quality_metrics()

    has_quality_issues = False

    for timeframe, metrics in warmup_quality.items():
        synthetic = metrics['synthetic']
        total = metrics['total']

        if synthetic > 0:
            has_quality_issues = True

            # Build warning message
            issues = []
            if synthetic > 0:
                issues.append(
                    f"{synthetic} synthetic ({metrics['synthetic_pct']:.1f}%)"
                )

            vLog.warning(
                f"⚠️  {timeframe} warmup quality: {', '.join(issues)} of {total} bars"
            )

    if has_quality_issues:
        vLog.warning(
            f"⚠️  Warmup contains synthetic bars - indicator warmup may be unrealistic!"
        )
        vLog.warning(
            f"   This typically happens when warmup period spans weekends/holidays."
        )
