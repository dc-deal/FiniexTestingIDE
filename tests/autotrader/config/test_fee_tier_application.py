"""
Which fee rates a live session prices with, and what happens when the venue disagrees.

A volume-tiered venue prices per ACCOUNT, so the rate written into a broker config is a guess
about which tier that account sits in. Measured 2026-09-08 against this project's own Kraken
account: charged taker 0.8000 % / maker 0.4000 % while the config declared 0.40 / 0.25 — half
of both, and nothing in any run said so. Combined with the exit fee that was never booked
(#506, fixed), the booking came to a QUARTER of the charge.

Two behaviours are pinned here, and the second one matters more than the first:

**Applying** the fetched rates is OPT-IN (`auto_detect_fee_tier`), because turning a declared
number into a fetched one should be a decision rather than a default.

**Reporting** a divergence is NOT optional. It runs whether the switch is on or off, because
the BACKTEST reads the git-tracked seed on purpose — a live session that quietly corrected
itself would leave the backtest wrong and silent, which is the state this replaces. Only a
human can re-freeze the seed, so only a warning can ask for it.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from python.framework.autotrader.autotrader_broker_config_setup import _apply_fee_tier
from python.framework.types.trading_env_types.broker_types import FeeTierInfo

_DECLARED = {'model': 'maker_taker', 'maker_fee': 0.25, 'taker_fee': 0.40,
             'fee_currency': 'quote'}
_VENUE = FeeTierInfo(maker_pct=0.4, taker_pct=0.8, next_maker_pct=0.3, next_taker_pct=0.6,
                     next_volume_threshold=2500.0, volume=267.01908, pair='XETHZUSD')


def _fetcher(tier):
    """
    A config fetcher answering with one tier, or declining.

    Args:
        tier: The FeeTierInfo to return, or None

    Returns:
        A stand-in fetcher
    """
    fetcher = MagicMock()
    fetcher.fetch_fee_tier.return_value = tier
    return fetcher


def _apply(tier, auto_detect: bool):
    """
    Run the resolution for one combination.

    Args:
        tier: What the venue answers
        auto_detect: The broker entry's opt-in

    Returns:
        (the config dict after resolution, the logger stand-in)
    """
    config_dict = {'fee_structure': dict(_DECLARED)}
    entry = SimpleNamespace(auto_detect_fee_tier=auto_detect)
    logger = MagicMock()
    _apply_fee_tier(config_dict, entry, _fetcher(tier), 'ETHUSD', logger)
    return config_dict, logger


class TestApplyingIsOptIn:

    def test_with_the_switch_on_the_session_prices_with_the_venue_s_rates(self):
        config_dict, _ = _apply(_VENUE, auto_detect=True)

        assert config_dict['fee_structure']['maker_fee'] == 0.4
        assert config_dict['fee_structure']['taker_fee'] == 0.8

    def test_with_the_switch_off_the_declared_rates_stand(self):
        config_dict, _ = _apply(_VENUE, auto_detect=False)

        assert config_dict['fee_structure']['maker_fee'] == 0.25
        assert config_dict['fee_structure']['taker_fee'] == 0.40

    def test_a_venue_without_tiers_changes_nothing(self):
        """A spread broker's fetcher answers None, and the caller needs no branch for it."""
        config_dict, logger = _apply(None, auto_detect=True)

        assert config_dict['fee_structure'] == _DECLARED
        logger.warning.assert_not_called()


class TestReportingIsNot:
    """The half that must fire even when nothing is applied."""

    def test_a_divergence_warns_even_with_the_switch_off(self):
        _, logger = _apply(_VENUE, auto_detect=False)

        assert logger.warning.called
        message = logger.warning.call_args[0][0]
        assert '0.8' in message and '0.4' in message, 'both numbers, so it can be compared'
        assert 'NOT applied' in message

    def test_a_divergence_warns_with_the_switch_on_too(self):
        """
        Applied is not the same as resolved: the backtest still reads the seed, so the
        operator still has to re-freeze it.
        """
        _, logger = _apply(_VENUE, auto_detect=True)

        assert logger.warning.called
        assert 'seed' in logger.warning.call_args[0][0]

    def test_agreement_is_quiet(self):
        agreeing = FeeTierInfo(maker_pct=0.25, taker_pct=0.40, pair='XETHZUSD')

        _, logger = _apply(agreeing, auto_detect=True)

        logger.warning.assert_not_called()
