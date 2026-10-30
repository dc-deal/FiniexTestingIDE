"""
FiniexTestingIDE - Security Certificate Rule Catalog (#358)

Every rule of the account protection the security certificate answers for, and HOW it answers:
provoked on real money in a named stage and check, proven offline by a named test, or waiting
for the first margin adapter. Declared once, here, so the certificate cannot be read as claiming
more than it does — a rule a real account cannot provoke safely says so instead of silently
falling out of the list. A test holds the catalog to the rule enum, every live rule to a check the
analyzer computes, and every offline rule to a test that exists.
"""

from dataclasses import dataclass
from typing import Dict, Optional

from python.framework.types.acceptance_study_types.security_cert_types import (
    RuleCertification,
    SecurityRule,
    SecurityStageId,
)


@dataclass(frozen=True)
class SecurityRuleEntry:
    """
    How the certificate stands behind one rule.

    Args:
        title: The rule in one sentence, as the certificate prints it
        certification: Live, offline or margin
        evidence: For a live rule the check the analyzer computes; for an offline rule the test's
            node id; for a margin rule the issue that carries it
        stage: The stage a live rule is provoked in; None otherwise
    """
    title: str
    certification: RuleCertification
    evidence: str
    stage: Optional[SecurityStageId] = None


_LIVE = RuleCertification.LIVE
_OFFLINE = RuleCertification.OFFLINE
_MARGIN = RuleCertification.MARGIN
_GUARDS = SecurityStageId.GUARDS
_SOFT = SecurityStageId.SOFT
_RESTART = SecurityStageId.RESTART
_HARD_KEPT = SecurityStageId.HARD_KEPT
_HARD_RESTART = SecurityStageId.HARD_RESTART

SECURITY_RULE_CATALOG: Dict[SecurityRule, SecurityRuleEntry] = {
    SecurityRule.INVALID_LOT_REFUSED: SecurityRuleEntry(
        'An order below the venue\'s smallest size is refused before it is sent.',
        _LIVE, 'phase:g_invalid_lot_1', _GUARDS),
    SecurityRule.INVALID_LOT_DOES_NOT_ARM: SecurityRuleEntry(
        'A refusal for size is a decision bug, not broker spam — it does not count toward the '
        'cooldown.', _LIVE, 'phase:g_funds_not_cooled', _GUARDS),
    SecurityRule.FUNDS_REFUSED_ON_A_BUY: SecurityRuleEntry(
        'A buy the quote balance cannot pay for is refused before it is sent.',
        _LIVE, 'phase:g_funds_not_cooled', _GUARDS),
    SecurityRule.FUNDS_REFUSED_ON_A_SALE_WITHOUT_BASE: SecurityRuleEntry(
        'At spot, a sale of more base than the account holds is refused before it is sent.',
        _LIVE, 'phase:g_short_not_cooled', _GUARDS),
    SecurityRule.COOLDOWN_ARMS: SecurityRuleEntry(
        'Two refusals in a row that count arm the cooldown, which then refuses even a valid order.',
        _LIVE, 'phase:g_cooled_buy', _GUARDS),
    SecurityRule.COOLDOWN_HOLDS_ONE_DIRECTION: SecurityRuleEntry(
        'The cooldown holds back the direction that was refused, not the other one.',
        _LIVE, 'phase:g_short_not_cooled', _GUARDS),
    SecurityRule.COOLDOWN_RUNS_OUT: SecurityRuleEntry(
        'The cooldown ends after its configured time.', _LIVE, 'phase:g_after_expiry', _GUARDS),
    SecurityRule.COOLDOWN_COUNT_DOES_NOT_DECAY: SecurityRuleEntry(
        'The count of refusals does not decay with time — one more refusal re-arms the cooldown.',
        _LIVE, 'phase:g_rearmed', _GUARDS),
    SecurityRule.A_FILL_RESETS_THE_COUNT: SecurityRuleEntry(
        'A fill resets the count: two fresh refusals are needed to arm the cooldown again.',
        _LIVE, 'phase:g_after_success_2', _GUARDS),
    SecurityRule.VENUE_REFUSAL_ARMS: SecurityRuleEntry(
        'A refusal the VENUE sends back arms the cooldown like a local one — not provokable on '
        'demand without a venue that refuses on purpose.',
        _OFFLINE, 'tests/autotrader/live_executor/test_async_submit.py::TestAsyncSubmitRejection'
                  '::test_async_reject_triggers_outcome_listener_chain'),
    SecurityRule.SOFT_BLOCK_ON_THE_ACCOUNT_VALUE: SecurityRuleEntry(
        'The soft circuit breaker measures the real account value and blocks every new entry.',
        _LIVE, 'safety:soft_blocked', _SOFT),
    SecurityRule.SOFT_BLOCK_NAMES_EVERY_LIMIT: SecurityRuleEntry(
        'The block names every limit that fired: session drawdown as a share and as an amount, '
        'and the daily loss as both.', _LIVE, 'safety:soft_reasons', _SOFT),
    SecurityRule.CLOSING_STAYS_ALLOWED: SecurityRuleEntry(
        'While new entries are blocked, an open position can still be closed.',
        _LIVE, 'phase:s_force_close', _SOFT),
    SecurityRule.SOFT_BLOCK_CLEARS: SecurityRuleEntry(
        'The block lifts once the account value recovers — that needs the market to move.',
        _OFFLINE, 'tests/autotrader/safety/test_safety_unit.py::TestDrawdown'
                  '::test_drawdown_recovers_clears_block'),
    SecurityRule.MIN_EQUITY_FLOOR: SecurityRuleEntry(
        'An account value under the configured floor blocks new entries — the same value the '
        'live stages show the breaker reading.',
        _OFFLINE, 'tests/autotrader/safety/test_safety_unit.py::TestSpotMinEquity'
                  '::test_equity_recovers_clears_block'),
    SecurityRule.HIGH_WATER_MARK_TRAILS: SecurityRuleEntry(
        'A high-water-mark baseline rises with every new peak — that needs a rising market.',
        _OFFLINE, 'tests/autotrader/safety/test_risk_baseline.py::TestTheHighWaterMark'
                  '::test_it_rises_with_a_new_peak'),
    SecurityRule.DAILY_BASELINE_RESETS: SecurityRuleEntry(
        'The daily loss starts from zero at each trading day\'s boundary — that needs a day to pass.',
        _OFFLINE, 'tests/autotrader/safety/test_safety_report.py::TestOneRowPerDay'
                  '::test_the_new_day_starts_from_zero_again'),
    SecurityRule.BASELINE_SURVIVES_RESTART: SecurityRuleEntry(
        'The session baseline comes back after a restart, with its original stamp — the loss '
        'before the restart is not forgotten.', _LIVE, 'safety:restart_baseline_restored',
        _RESTART),
    SecurityRule.DAILY_BASELINE_STARTS_FRESH: SecurityRuleEntry(
        'The daily baseline is not carried across a restart — only the session limits fire.',
        _LIVE, 'safety:restart_reasons', _RESTART),
    SecurityRule.HARD_STOP_KEEPS_A_SPOT_HOLDING: SecurityRuleEntry(
        'At spot, the hard stop keeps the holding when it may not sell, and ends the session.',
        _LIVE, 'safety:hard_kept', _HARD_KEPT),
    SecurityRule.HARD_STOP_FIRES_AGAIN_AFTER_RESTART: SecurityRuleEntry(
        'A restart after the hard stop takes over the kept holding and the stop fires again at the '
        'first valued tick — today\'s behaviour, which #590 turns into a refused start.',
        _LIVE, 'safety:hard_restart_fired', _HARD_RESTART),
    SecurityRule.HARD_STOP_SELLS_WHEN_ALLOWED: SecurityRuleEntry(
        'With selling allowed, the hard stop sells the holding and the account ends flat.',
        _LIVE, 'safety:hard_restart_complete', _HARD_RESTART),
    SecurityRule.HARD_STOP_NAMES_AN_UNCONFIRMED_CLOSE: SecurityRuleEntry(
        'A close the venue never confirms ends the session at a ceiling and is named — that '
        'needs a venue that stays silent.',
        _OFFLINE, 'tests/autotrader/safety/test_emergency_flatten.py'
                  '::TestTheSessionEndsOnlyAfterTheFillsArrive'
                  '::test_it_ends_anyway_at_the_ceiling_and_names_what_is_still_open'),
    SecurityRule.MARGIN_EMERGENCY_FLATTEN: SecurityRuleEntry(
        'At margin, the hard stop closes every position.', _MARGIN, '#209'),
    SecurityRule.MARGIN_BALANCE_FLOOR: SecurityRuleEntry(
        'At margin, an account value under `min_balance` blocks new entries.', _MARGIN, '#209'),
}
