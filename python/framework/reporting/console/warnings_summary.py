"""
FiniexTestingIDE - Warnings & Errors Summary

Renders the unified "Warnings & Errors" section from the `WarningsErrorsReport` model (#395):
per-unit errors first, then Tier-1 major warnings (run-scoped first for prominence, e.g.
debug-mode), then a Tier-2 minor summary. This presenter makes NO decisions — every warning was
produced by a validator upstream; it only formats. See docs/architecture/warnings_errors_tiers.md.
"""

from python.framework.reporting.console.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import UnitErrorRow, WarningRow, WarningsErrorsReport
from python.framework.utils.console_renderer import ConsoleRenderer


class WarningsSummary(AbstractBatchSummarySection):
    """
    Warnings & errors section, rendered from the unified model.

    Always rendered (both pipelines) — a clean zero-state line when there are none.
    Always displayed regardless of summary_detail flag.
    """

    _section_title = '⚠️ WARNINGS & ERRORS'

    def __init__(self, warnings_errors_report: WarningsErrorsReport):
        """
        Initialize warnings & errors summary.

        Args:
            warnings_errors_report: The unified warnings/errors report
        """
        self._report = warnings_errors_report

    def render(self, renderer: ConsoleRenderer) -> None:
        """
        Render the section — always, with a clean zero-state when there are none.

        Args:
            renderer: Console renderer for formatting
        """
        blocks = []

        # Errors first — most important
        if self._report.errors:
            blocks.append(self._build_errors_block(renderer))

        # Tier-1 major warnings (run-scoped first for prominence, e.g. debug-mode)
        major = [w for w in self._report.warnings if w.tier == 'major']
        for warning in sorted(major, key=lambda w: w.scope != 'run'):
            blocks.append(self._format_major(warning, renderer))

        # Tier-2 minor warnings — summarized, low-key
        for warning in (w for w in self._report.warnings if w.tier == 'minor'):
            blocks.append(renderer.gray(warning.message))

        self._render_section_header(renderer)
        if not blocks:
            print(renderer.green("✅ No warnings or errors"))
            return
        for i, block in enumerate(blocks):
            print(block)
            if i < len(blocks) - 1:
                print()

    _MAX_DETAIL = 120

    def _build_errors_block(self, renderer: ConsoleRenderer) -> str:
        """Build the per-unit errors block (the prominent red headline + per-unit detail)."""
        errors = self._report.errors
        lines = [renderer.red(renderer.bold(
            f"❌ Scenario errors detected — {len(errors)} unit(s) with error(s)"))]
        for err in errors:
            lines.append(renderer.red(self._error_head(err)))
            # Validation failures carry the structured error list; a pure execution villain
            # has none → show its exception message instead (no multi-line report dump).
            if err.validation_errors:
                for ve in err.validation_errors[:3]:
                    lines.append(renderer.yellow(f"      ✗ {self._trim(ve)}"))
                extra = len(err.validation_errors) - 3
                if extra > 0:
                    lines.append(renderer.yellow(f"      … +{extra} more validation error(s)"))
            elif err.error_type or err.error_message:
                detail = f"{err.error_type}: {self._trim(err.error_message)}".strip(': ')
                lines.append(renderer.yellow(f"      {detail}"))
            if err.logged_errors:
                lines.append(renderer.yellow(
                    f"      {len(err.logged_errors)} logged error(s) — see scenario log"))
        return '\n'.join(lines)

    def _error_head(self, err: UnitErrorRow) -> str:
        """Unit header line for an error row."""
        head = f"  • {err.name}"
        if err.symbol:
            head += f" ({err.symbol})"
        return head

    def _trim(self, text: str) -> str:
        """First non-empty line of a message, truncated for the console."""
        first = next((line.strip() for line in text.split('\n') if line.strip()), '')
        return first if len(first) <= self._MAX_DETAIL else first[:self._MAX_DETAIL - 3] + '...'

    def _format_major(self, warning: WarningRow, renderer: ConsoleRenderer) -> str:
        """Format a Tier-1 major warning: run-scope is prominent (bold red head), per-scenario yellow."""
        msg_lines = warning.message.split('\n')
        if warning.scope == 'run':
            out = [renderer.red(renderer.bold(msg_lines[0]))]
            out += [renderer.yellow(line) for line in msg_lines[1:]]
            return '\n'.join(out)

        # Per-scenario major warning — prefix with the unit scope
        head = renderer.yellow(f"[{warning.scope}] {msg_lines[0]}")
        rest = [renderer.yellow(line) for line in msg_lines[1:]]
        return '\n'.join([head] + rest)
