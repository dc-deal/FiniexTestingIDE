"""
A backtest never inherits a live session's equity curve (#497).

The drawdown carry-over is a LIVE mechanism: a thirty-day unattended run restarts, and the
report has to continue the curve rather than open a new one. The simulation is the opposite
case by construction — a scenario starts at its own start, and a peak read from a file outside
its own inputs would make the run depend on which bot happened to trade yesterday.

That is not a style preference. Reproducibility is this project's central claim, and the
thirty-day run is settled by comparing a live month against a backtest over the same window —
a backtest whose starting peak varies with a carry-over file could not be that control.

The boundary holds today because nothing in the simulation constructs the store at all. This
suite pins that, because the failure would be silent: the sim shares the PortfolioManager with
the live path, so wiring the restore one level too low would be invisible in every report
until two runs over identical data disagreed.
"""

from pathlib import Path
from typing import List

_SIM_TREES = (Path('python/framework/process'), Path('python/framework/batch'))
_RESTORE = 'restore_drawdown_state'


def _python_files(root: Path) -> List[Path]:
    """
    Every source file under one tree.

    Args:
        root: Directory to walk

    Returns:
        Sorted list of .py paths
    """
    return sorted(p for p in root.rglob('*.py'))


class TestTheSimulationNeverRestoresACurve:
    """The caller check, which is what a behavioural test cannot express."""

    def test_no_simulation_unit_calls_the_restore(self):
        offenders = [
            str(path)
            for root in _SIM_TREES
            for path in _python_files(root)
            if _RESTORE in path.read_text(encoding='utf-8')
        ]

        assert offenders == [], (
            f'{_RESTORE} is reachable from the simulation pipeline ({offenders}) — a backtest '
            f'would then open its equity curve at a peak carried over from a live session, and '
            f'two runs over identical data would report different drawdowns')

    def test_the_simulation_never_constructs_the_carry_over_store(self):
        """
        The store is the only route to a carried record, so its absence is the real guard.

        Asserted separately from the call above: someone could reach the restore through a
        helper this grep does not see, but they cannot reach it without a store.
        """
        offenders = [
            str(path)
            for root in _SIM_TREES
            for path in _python_files(root)
            if 'ColdStartStateStore' in path.read_text(encoding='utf-8')
        ]

        assert offenders == [], (
            f'the simulation reaches the cold-start store ({offenders}) — that is the only '
            f'route to a carried curve, and a backtest reading one stops being reproducible '
            f'from its own inputs')
