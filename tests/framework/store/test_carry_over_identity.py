"""
Two bots must not share one carry-over document.

The carry-over stores file one document per BOT under `<profile name>_<symbol>`, and both halves
are free text nothing validates. A collision is invisible from inside either store: each asks
whether a document belongs to THIS bot, and in a collision it does, for both of them. So the
check has to happen ONCE, across the profile tree, before anything reads or writes.

These tests pin three things: the key composition now has exactly one home, the boot check fires
on a real collision and stays silent otherwise, and the exclusions are the ones that were
reasoned about rather than the ones that were convenient.
"""

import json
from pathlib import Path

import pytest

from python.framework.exceptions.persistence_errors import (
    CarryOverIdentityCollisionError,
    ContinuousDeploymentNeedsBotIdError,
)
from python.framework.persistence.carry_over_identity import carry_over_key
from python.framework.validators.carry_over_identity_validator import (
    collisions,
    validate_carry_over_identity_unique,
    validate_continuous_deployment_declares_bot_id,
)

_REAL_PROFILES = Path('configs/autotrader_profiles')


def _profile(root: Path, relative: str, name: str, symbol: str,
             adapter: str = 'live') -> Path:
    """
    Write one AutoTrader profile into a throwaway tree.

    Args:
        root: The `autotrader_profiles` directory of the tmp tree
        relative: Path below it, purpose folder included
        name: The profile's declared name — the first half of the carry-over identity
        symbol: The traded symbol — the second half
        adapter: 'live' or 'mock'

    Returns:
        The written file
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'name': name, 'symbol': symbol, 'adapter_type': adapter}), encoding='utf-8')
    return path


@pytest.fixture
def profiles(tmp_path) -> Path:
    """An empty `autotrader_profiles` tree — the directory NAME is what the check walks up to."""
    root = tmp_path / 'autotrader_profiles'
    root.mkdir()
    return root


class TestTheKeyHasOneHome:

    def test_both_halves_are_sanitised_and_lowercased(self):
        assert carry_over_key('DOT-USD Live', 'DOTUSD') == 'dot-usd-live_dotusd'

    def test_the_separator_occurs_exactly_once(self):
        # What makes the composition injective (#538). A half cannot contain the join character,
        # because every non-alphanumeric becomes a HYPHEN.
        assert carry_over_key('DOT-USD Live', 'BTC_USD').count('_') == 1

    def test_two_different_bots_no_longer_share_one_document(self):
        """
        The collision this key used to have, and the one that mattered: two UNRELATED bots
        resolving to one file. Bot B opened bot A's document and read a position book it never
        wrote. With the separator reserved the two are distinguishable by construction.
        """
        assert carry_over_key('btc', 'USD_SPOT') != carry_over_key('btc_usd', 'SPOT')
        assert carry_over_key('btc', 'USD_SPOT') == 'btc_usd-spot'
        assert carry_over_key('btc_usd', 'SPOT') == 'btc-usd_spot'

    def test_a_declared_bot_id_takes_precedence_over_the_name(self):
        """
        The structural answer (#538). A display name is something an operator improves; without a
        declared identity the improvement points the bot at a new, empty document while the venue
        still holds its position.
        """
        assert carry_over_key('dotusd_live', 'DOTUSD', bot_id='dot-usd-live') == \
            'dot-usd-live_dotusd'

    def test_the_key_survives_a_rename_when_the_id_is_declared(self):
        renamed = carry_over_key('a completely different name', 'DOTUSD', bot_id='dot-usd-live')

        assert renamed == carry_over_key('dotusd_live', 'DOTUSD', bot_id='dot-usd-live')

    def test_no_declared_id_composes_from_the_name_as_before(self):
        """Optional by design — no profile changes key because this argument was added."""
        assert carry_over_key('dotusd_live', 'DOTUSD') == carry_over_key(
            'dotusd_live', 'DOTUSD', bot_id='')

    def test_two_spellings_of_one_name_still_collapse(self):
        # A DIFFERENT case, and it stays open on purpose: this is one bot written two ways, not
        # two bots merging. The sanitiser is lossy because a filename cannot carry every
        # character, so the boot check is the right answer to it rather than a stricter rule.
        assert carry_over_key('dot live', 'DOTUSD') == carry_over_key('dot-live', 'DOTUSD')


class TestAContinuousDeploymentMustDeclareItsIdentity:
    """
    The one case where composing the identity from the NAME is not good enough (#538).

    A continuous deployment is by definition the case where state survives a restart. Without a
    declared identity that state is filed under what the profile is CALLED — and renaming does
    not fail, it silently points the next session at an empty document while the venue still
    holds the position. So it is a REFUSAL at boot, not a warning: a warning on a thirty-day
    unattended run is a warning nobody is there to read.
    """

    def test_a_continuous_profile_without_one_is_refused(self):
        with pytest.raises(ContinuousDeploymentNeedsBotIdError):
            validate_continuous_deployment_declares_bot_id(
                'dotusd_live', 'DOTUSD', '', continuous=True)

    def test_the_message_carries_a_usable_suggestion(self):
        """
        A complaint the operator cannot act on is a complaint they will work around. The value is
        arbitrary — what matters is that it is unique and never changes — so the message proposes
        one rather than leaving them to invent it.
        """
        with pytest.raises(ContinuousDeploymentNeedsBotIdError) as raised:
            validate_continuous_deployment_declares_bot_id(
                'DOTUSD Live Bot', 'DOTUSD', '', continuous=True)

        message = str(raised.value)
        assert '"bot_id": "dotusd-live-bot"' in message
        assert 'dotusd-live-bot_dotusd' in message, 'the resulting identity is not shown'
        assert 'UNIQUE' in message

    def test_a_declared_one_passes(self):
        validate_continuous_deployment_declares_bot_id(
            'dotusd_live', 'DOTUSD', 'dotusd-live', continuous=True)

    def test_a_one_off_session_is_exempt(self):
        """It inherits nothing and leaves nothing a successor must find."""
        validate_continuous_deployment_declares_bot_id(
            'dotusd_live', 'DOTUSD', '', continuous=False)


class TestTheBootCheck:

    def test_two_live_profiles_sharing_an_identity_abort_the_session(self, profiles):
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'observation/dot.json', 'dot_live', 'DOTUSD')

        with pytest.raises(CarryOverIdentityCollisionError) as raised:
            validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD')

        # Both files are named: the operator has to edit one of them, and which one is their
        # decision — a message naming only "the other" would send them looking.
        message = str(raised.value)
        assert 'production/dot.json' in message and 'observation/dot.json' in message
        assert 'dot-live_dotusd' in message

    def test_distinct_identities_pass(self, profiles):
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'production/eth.json', 'eth_live', 'ETHUSD')
        validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD')

    def test_the_same_name_on_a_different_symbol_is_not_a_collision(self, profiles):
        # The identity is the PAIR. One bot per symbol under one name is the ordinary layout.
        mine = _profile(profiles, 'production/dot.json', 'crypto_live', 'DOTUSD')
        _profile(profiles, 'production/eth.json', 'crypto_live', 'ETHUSD')
        validate_carry_over_identity_unique(mine, 'crypto_live', 'DOTUSD')

    def test_a_collision_between_two_other_profiles_does_not_stop_this_one(self, profiles):
        # Real, but not this run's problem — and it is raised the moment either of them starts,
        # when the message is about the session the operator is actually looking at.
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'production/a.json', 'shared', 'ETHUSD')
        _profile(profiles, 'observation/b.json', 'shared', 'ETHUSD')
        validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD')

    def test_a_config_outside_a_profile_tree_is_skipped(self, tmp_path):
        # A fixture assembled elsewhere has no tree to compare against, and guessing one would
        # measure this session against profiles it has nothing to do with.
        loose = tmp_path / 'somewhere' / 'profile.json'
        loose.parent.mkdir(parents=True)
        loose.write_text('{}', encoding='utf-8')
        validate_carry_over_identity_unique(loose, 'dot_live', 'DOTUSD')

    def test_a_config_with_no_path_is_skipped(self):
        validate_carry_over_identity_unique(None, 'dot_live', 'DOTUSD')


class TestWhatCountsAndWhatDoesNot:

    def test_a_mock_profile_COUNTS(self, profiles):
        # The correction that matters. `adapter_type: mock` selects the tick SOURCE, not the
        # executor — every AutoTrader session runs a LiveTradeExecutor and builds both carry-over
        # stores. Measured 2026-09-21: 15 of the 16 documents in data/runtime/cold_start_state/
        # belong to mock profiles, so excluding them would skip almost the whole population.
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'backtesting/dot_mock.json', 'dot_live', 'DOTUSD', adapter='mock')
        with pytest.raises(CarryOverIdentityCollisionError):
            validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD')

    def test_an_unknown_adapter_counts_too(self, profiles):
        # Nothing is exempt, so an adapter added later (#209's MT5) needs no change here.
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'production/dot_mt5.json', 'dot_live', 'DOTUSD', adapter='mt5')
        with pytest.raises(CarryOverIdentityCollisionError):
            validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD')

    def test_an_unreadable_profile_does_not_abort_the_session(self, profiles):
        # This check answers one question and must not become a second config validator: a
        # broken file elsewhere in the tree is somebody else's error, not a reason to refuse.
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        (profiles / 'production' / 'broken.json').write_text('{ not json', encoding='utf-8')
        validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD')


class TestTheShippedProfiles:

    def test_no_shipped_profile_pair_shares_an_identity(self):
        # The state this check was built to protect, asserted on the real tree: a collision here
        # would mean two of the operator's own bots share a position book.
        assert collisions(_REAL_PROFILES) == {}
