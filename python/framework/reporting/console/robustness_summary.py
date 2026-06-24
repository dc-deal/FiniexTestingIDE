"""
FiniexTestingIDE - Robustness Validation Summary (#367)

Thin presenter over the RobustnessReport: the multi-window distribution, the IS/OOS comparison,
and (Profile Runs) the per-regime breakdown. Rendered only when robustness mode is enabled.
Only the ROBUST/⚠/OVERFIT display class is applied here — the verdict warning itself fires from
the PostRunValidator (no decisions in reports).
"""
from python.framework.reporting.console.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import RobustnessReport
from python.framework.utils.console_renderer import ConsoleRenderer


class RobustnessSummary(AbstractBatchSummarySection):
    """Robustness validation section — multi-window distribution + IS/OOS + regime breakdown."""

    _section_title = '🎯 ROBUSTNESS VALIDATION'

    def __init__(self, report: RobustnessReport):
        """
        Initialize the robustness presenter.

        Args:
            report: The built robustness report (distribution + role aggregates + regime rows)
        """
        self._report = report

    def render(self, renderer: ConsoleRenderer) -> None:
        """
        Render the robustness section. Skips when robustness is off or there are no windows.

        Args:
            renderer: Console renderer for formatting
        """
        report = self._report
        if not report.enabled or not report.windows:
            return

        self._render_section_header(renderer)
        self._render_distribution(renderer)
        self._render_regime_breakdown(renderer)
        self._render_in_out_of_sample(renderer)
        self._render_caveats(renderer)
        print()

    def _render_distribution(self, renderer: ConsoleRenderer) -> None:
        """Render the cross-window distribution of the primary metric."""
        dist = self._report.distribution
        if dist is None:
            return
        print(renderer.bold(
            f"  Distribution ({dist.window_count} windows · metric: {self._report.metric})"))
        if dist.window_count < self._report.min_windows:
            print(renderer.yellow(
                f"    ⚠ Only {dist.window_count} windows (< {self._report.min_windows}) — "
                f"distribution is statistically weak"))
        print(
            f"    profitable: {dist.pct_profitable:.0f}%  |  "
            f"mean {dist.mean:+.4f}  median {dist.median:+.4f}  std {dist.std:.4f}")
        print(
            f"    best {dist.best_value:+.4f} ({dist.best_window})  |  "
            f"worst {dist.worst_value:+.4f} ({dist.worst_window})  |  "
            f"CoV {dist.coefficient_of_variation:.2f}")

    def _render_regime_breakdown(self, renderer: ConsoleRenderer) -> None:
        """Render the per-regime metric breakdown (Profile Runs only)."""
        if not self._report.regime_breakdown:
            return
        print(renderer.bold('  By regime:'))
        for row in self._report.regime_breakdown:
            print(
                f"    {row.regime:<8} ({row.window_count} windows): "
                f"mean {row.mean_metric:+.4f}  profitable {row.pct_profitable:.0f}%")

    def _render_in_out_of_sample(self, renderer: ConsoleRenderer) -> None:
        """Render the IS/OOS comparison + Walk-Forward Efficiency verdict display class."""
        report = self._report
        if report.in_sample is None or report.out_of_sample is None:
            return
        print(renderer.bold('  In-Sample → Out-of-Sample:'))
        print(
            f"    IS  ({report.in_sample.window_count}): mean {report.in_sample.mean_metric:+.4f}  "
            f"profitable {report.in_sample.pct_profitable:.0f}%")
        print(
            f"    OOS ({report.out_of_sample.window_count}): mean {report.out_of_sample.mean_metric:+.4f}  "
            f"profitable {report.out_of_sample.pct_profitable:.0f}%")
        print(f"    {self._wfe_line(renderer)}")

    def _wfe_line(self, renderer: ConsoleRenderer) -> str:
        """The Walk-Forward Efficiency line with its display class (presentation only)."""
        report = self._report
        wfe = report.walk_forward_efficiency
        if wfe is None:
            return renderer.gray('WFE: n/a (IS not profitable — degradation undefined)')
        label = f"WFE {wfe:.2f} (OOS/IS)"
        if wfe >= report.robust_wfe_threshold:
            return renderer.green(f"{label} → ROBUST ✓")
        if wfe < report.overfit_wfe_threshold:
            return renderer.red(f"{label} → ⚠ OVERFIT")
        return renderer.yellow(f"{label} → moderate degradation")

    def _render_caveats(self, renderer: ConsoleRenderer) -> None:
        """Render the param-drift + low-trust caveats (the verdict warning is in the validator)."""
        report = self._report
        if not report.params_constant:
            print(renderer.red(
                f"    ⚠ Parameters NOT constant across windows "
                f"({len(report.drifting_windows)} drift) — comparison is not fair"))
        if report.disposition_pct > report.disposition_trust_pct:
            print(renderer.red(
                f"    ⚠ Block-splitting distortion {report.disposition_pct:.1f}% "
                f"(> {report.disposition_trust_pct:.0f}%) — per-window numbers unreliable, "
                f"verdict suppressed"))
