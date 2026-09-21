"""
Two live bots must not share one carry-over document.

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

from python.framework.exceptions.persistence_errors import CarryOverIdentityCollisionError
from python.framework.persistence.carry_over_identity import carry_over_key
from python.framework.validators.carry_over_identity_validator import (
    collisions,
    validate_carry_over_identity_unique,
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
        assert carry_over_key('DOT-USD Live', 'DOTUSD') == 'dot_usd_live_dotusd'

    def test_two_spellings_of_one_name_collapse_to_one_identity(self):
        # This IS the collision mechanism, pinned as a property rather than as a bug: the
        # sanitiser is lossy on purpose (a filename cannot carry every character), so distinct
        # names legitimately meet. That is why the check exists instead of a stricter sanitiser.
        assert carry_over_key('dot live', 'DOTUSD') == carry_over_key('dot-live', 'DOTUSD')


class TestTheBootCheck:

    def test_two_live_profiles_sharing_an_identity_abort_the_session(self, profiles):
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'observation/dot.json', 'dot_live', 'DOTUSD')

        with pytest.raises(CarryOverIdentityCollisionError) as raised:
            validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD', 'live')

        # Both files are named: the operator has to edit one of them, and which one is their
        # decision — a message naming only "the other" would send them looking.
        message = str(raised.value)
        assert 'production/dot.json' in message and 'observation/dot.json' in message
        assert 'dot_live_dotusd' in message

    def test_distinct_identities_pass(self, profiles):
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'production/eth.json', 'eth_live', 'ETHUSD')
        validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD', 'live')

    def test_the_same_name_on_a_different_symbol_is_not_a_collision(self, profiles):
        # The identity is the PAIR. One bot per symbol under one name is the ordinary layout.
        mine = _profile(profiles, 'production/dot.json', 'crypto_live', 'DOTUSD')
        _profile(profiles, 'production/eth.json', 'crypto_live', 'ETHUSD')
        validate_carry_over_identity_unique(mine, 'crypto_live', 'DOTUSD', 'live')

    def test_a_collision_between_two_other_profiles_does_not_stop_this_one(self, profiles):
        # Real, but not this run's problem — and it is raised the moment either of them starts,
        # when the message is about the session the operator is actually looking at.
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'production/a.json', 'shared', 'ETHUSD')
        _profile(profiles, 'observation/b.json', 'shared', 'ETHUSD')
        validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD', 'live')

    def test_a_config_outside_a_profile_tree_is_skipped(self, tmp_path):
        # A fixture assembled elsewhere has no tree to compare against, and guessing one would
        # measure this session against profiles it has nothing to do with.
        loose = tmp_path / 'somewhere' / 'profile.json'
        loose.parent.mkdir(parents=True)
        loose.write_text('{}', encoding='utf-8')
        validate_carry_over_identity_unique(loose, 'dot_live', 'DOTUSD', 'live')

    def test_a_config_with_no_path_is_skipped(self):
        validate_carry_over_identity_unique(None, 'dot_live', 'DOTUSD', 'live')


class TestWhatIsExcluded:

    def test_a_mock_profile_cannot_collide(self, profiles):
        # Both carry-over stores are built only behind a live executor, so a mock session writes
        # no document. Aborting a real session over one would be a false alarm on the money path.
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'backtesting/dot_mock.json', 'dot_live', 'DOTUSD', adapter='mock')
        validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD', 'live')

    def test_an_unknown_adapter_still_counts(self, profiles):
        # The test is NEGATIVE on purpose: only mock is proven harmless. An adapter added later
        # (#209's MT5) must be included by default, because an exemption in a guard is the
        # failure the guard exists to prevent.
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        _profile(profiles, 'production/dot_mt5.json', 'dot_live', 'DOTUSD', adapter='mt5')
        with pytest.raises(CarryOverIdentityCollisionError):
            validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD', 'live')

    def test_a_mock_session_is_not_stopped_by_someone_else_s_collision(self, profiles):
        # The mirror of the exclusion above. A mock run writes no carry-over, so nothing can be
        # taken from it — and stopping a test run over a problem it cannot have is how a check
        # earns the reputation of being in the way.
        mine = _profile(profiles, 'backtesting/probe.json', 'shared', 'ETHUSD', adapter='mock')
        _profile(profiles, 'production/a.json', 'shared', 'ETHUSD')
        _profile(profiles, 'observation/b.json', 'shared', 'ETHUSD')
        validate_carry_over_identity_unique(mine, 'shared', 'ETHUSD', 'mock')

    def test_an_unreadable_profile_does_not_abort_the_session(self, profiles):
        # This check answers one question and must not become a second config validator: a
        # broken file elsewhere in the tree is somebody else's error, not a reason to refuse.
        mine = _profile(profiles, 'production/dot.json', 'dot_live', 'DOTUSD')
        (profiles / 'production' / 'broken.json').write_text('{ not json', encoding='utf-8')
        validate_carry_over_identity_unique(mine, 'dot_live', 'DOTUSD', 'live')


class TestTheShippedProfiles:

    def test_no_shipped_profile_pair_shares_an_identity(self):
        # The state this check was built to protect, asserted on the real tree: a collision here
        # would mean two of the operator's own bots share a position book.
        assert collisions(_REAL_PROFILES) == {}
