"""
FiniexTestingIDE - Reporting Package
Unified reporting for batch execution results

Exports:
- BatchSummary: Main orchestrator for all summaries
- PortfolioSummary: Portfolio and trading statistics
- PerformanceSummary: Worker and decision logic performance
- ConsoleRenderer: Unified console rendering
"""

from python.framework.reporting.batch_summary import BatchSummary
from python.framework.reporting.portfolio_summary import PortfolioSummary
from python.framework.reporting.scenario_set_performance_manager import ScenarioSetPerformanceManager
from python.framework.reporting.performance_summary import PerformanceSummary
from python.framework.reporting.console_renderer import ConsoleRenderer
from python.framework.reporting.bar_index_report import BarIndexReportGenerator

__all__ = [
    'BatchSummary',
    'PortfolioSummary',
    'ScenarioSetPerformanceManager',
    'PerformanceSummary',
    'ConsoleRenderer',
    'BarIndexReportGenerator'
]
