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
    BotIdMalformedError,
    BotIdRequiredError,
    CarryOverIdentityCollisionError,
)
from python.framework.persistence.carry_over_identity import carry_over_key
from python.framework.validators.carry_over_identity_validator import (
    BOT_ID_MAX_LENGTH,
    collisions,
    validate_bot_id,
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


class TestEveryProfileMustDeclareItsIdentity:
    """
    Composing the identity from the NAME is not good enough for ANY profile (#538, widened
    2026-09-24 on the operator's decision).

    The older rule asked only of a continuous deployment, and that protected the case least in
    need of it: a continuous profile is one somebody thought about. The route into a collision
    is copying a profile into another purpose folder and keeping its name — and that copy was
    exactly what the narrow rule exempted.

    Refused at boot rather than warned about: a warning on a thirty-day unattended run is a
    warning nobody is there to read, and the cost of being wrong is a position nobody knows
    about.
    """

    def test_a_profile_without_one_is_refused(self):
        with pytest.raises(BotIdRequiredError):
            validate_bot_id('dotusd_live', 'DOTUSD', '')

    def test_a_one_off_session_is_no_longer_exempt(self):
        """
        The widening, stated as its own case because it REVERSES what this suite used to pin.

        A one-off inherits nothing, which is why it was exempt — but it still WRITES a
        carry-over document, and a document written under a name is one the next rename
        orphans.
        """
        with pytest.raises(BotIdRequiredError):
            validate_bot_id('some_probe', 'BTCUSD', '')

    def test_the_message_carries_a_usable_suggestion(self):
        """
        A complaint the operator cannot act on is a complaint they will work around. The value is
        arbitrary — what matters is that it is unique and never changes — so the message proposes
        one rather than leaving them to invent it.
        """
        with pytest.raises(BotIdRequiredError) as raised:
            validate_bot_id('DOTUSD Live Bot', 'DOTUSD', '')

        message = str(raised.value)
        assert '"bot_id": "dotusd-liv"' in message, (
            f'the suggestion is not offered within the ceiling: {message}')
        assert 'UNIQUE' in message

    def test_the_suggestion_is_itself_acceptable(self):
        """
        A proposal the validator would refuse is worse than none — the operator pastes it and
        gets a second error. The suggestion is therefore cut to the ceiling and re-checked here.
        """
        with pytest.raises(BotIdRequiredError) as raised:
            validate_bot_id('An Extremely Long Profile Name', 'BTCUSD', '')
        suggestion = str(raised.value).split('"bot_id": "')[1].split('"')[0]

        validate_bot_id('An Extremely Long Profile Name', 'BTCUSD', suggestion)

    def test_a_declared_one_passes(self):
        validate_bot_id('dotusd_live', 'DOTUSD', 'dotlive01')


class TestTheShapeOfADeclaredIdentity:
    """
    The id BECOMES half of a filename, so a shape the key cannot carry unchanged is refused
    rather than fixed up.

    `sanitize_identity_part` would rewrite anything outside `[a-z0-9-]` silently — the profile
    would then declare one identity and the store would hold another, which is the confusion
    the id exists to prevent, one level down.
    """

    def test_too_long_is_refused_and_says_by_how_much(self):
        with pytest.raises(BotIdMalformedError) as raised:
            validate_bot_id('p', 'BTCUSD', 'a' * (BOT_ID_MAX_LENGTH + 1))
        assert str(BOT_ID_MAX_LENGTH + 1) in str(raised.value)

    def test_exactly_the_ceiling_passes(self):
        validate_bot_id('p', 'BTCUSD', 'a' * BOT_ID_MAX_LENGTH)

    def test_one_character_passes(self):
        """The floor the operator set: one character, not a minimum length nobody asked for."""
        validate_bot_id('p', 'BTCUSD', 'a')

    @pytest.mark.parametrize('bad', ['Bot01', 'bot_01', 'bot 01', 'bot.01', 'bot/01', 'bot:01'])
    def test_anything_the_filename_could_not_carry_is_refused(self, bad):
        with pytest.raises(BotIdMalformedError):
            validate_bot_id('p', 'BTCUSD', bad)

    def test_the_underscore_is_named_as_the_reserved_separator(self):
        """It is the one rejected character an operator would otherwise think is a typo."""
        with pytest.raises(BotIdMalformedError) as raised:
            validate_bot_id('p', 'BTCUSD', 'bot_01')
        assert 'reserved' in str(raised.value)


class TestEveryShippedProfileIsWellFormedAndUnique:
    """
    The declaration and the thing it describes, held to each other.

    The boot check answers for ONE profile at a time; this answers for the set, which is where
    a collision actually lives. Without it the rule is a promise about profiles nobody has run.
    """

    @staticmethod
    def _shipped():
        """Every AutoTrader profile in the repository. Returns: {relative path: raw config}."""
        root = Path('configs/autotrader_profiles')
        return {p.relative_to(root).as_posix(): json.loads(p.read_text(encoding='utf-8'))
                for p in sorted(root.rglob('*.json'))}

    def test_every_profile_declares_one(self):
        missing = [rel for rel, cfg in self._shipped().items() if not cfg.get('bot_id')]
        assert not missing, f'profiles without a bot_id: {missing}'

    def test_every_declared_id_is_well_formed(self):
        for rel, cfg in self._shipped().items():
            validate_bot_id(cfg.get('name', rel), cfg.get('symbol', ''), cfg['bot_id'])

    def test_no_two_profiles_claim_the_same_id(self):
        seen = {}
        for rel, cfg in self._shipped().items():
            seen.setdefault(cfg['bot_id'], []).append(rel)
        shared = {k: v for k, v in seen.items() if len(v) > 1}
        assert not shared, f'one identity claimed by several profiles: {shared}'


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
        assert collisions([_REAL_PROFILES]) == {}


class TestTheCheckCrossesTheConfigBoundary:
    """
    A COPY is the route into a collision, and a copy of a shipped profile lands in the workspace
    tree — across the boundary the single-root walk never crossed (2026-09-24).

    They are separate BOTS and not a cascade: an AutoTrader profile does not merge with a
    same-named file the way `app_config.json` does, so two files claiming one identity are always
    two bots sharing one position book, whichever directory each sits in.
    """

    @staticmethod
    def _pair(tmp_path):
        """A tracked and a workspace profile tree side by side. Returns: (tracked, workspace)."""
        tracked = tmp_path / 'configs' / 'autotrader_profiles'
        workspace = tmp_path / 'user_configs' / 'autotrader_profiles'
        (tracked / 'production').mkdir(parents=True)
        workspace.mkdir(parents=True)
        return tracked, workspace

    @staticmethod
    def _write(path: Path, name: str, symbol: str, bot_id: str) -> Path:
        path.write_text(json.dumps(
            {'name': name, 'symbol': symbol, 'bot_id': bot_id}), encoding='utf-8')
        return path

    def test_a_forgotten_id_on_a_copy_into_the_workspace_is_caught(self, tmp_path):
        """
        The case the operator named: copy the profile, forget the id. Both halves match, so the
        carry-over envelope's own profile/symbol guard would NOT catch it either — the second bot
        would adopt the first one's position book.
        """
        tracked, workspace = self._pair(tmp_path)
        self._write(tracked / 'production' / 'sol.json', 'solusd_live', 'SOLUSD', 'sollive01')
        mine = self._write(workspace / 'sol_copy.json', 'solusd_live', 'SOLUSD', 'sollive01')

        with pytest.raises(CarryOverIdentityCollisionError) as raised:
            validate_carry_over_identity_unique(mine, 'solusd_live', 'SOLUSD', 'sollive01')

        message = str(raised.value)
        assert 'sol.json' in message and 'sol_copy.json' in message, (
            f'the message does not name both claimants: {message}')
        assert 'bot_id' in message, 'the message still asks for a distinct name, not a distinct id'

    def test_it_fires_from_either_side(self, tmp_path):
        """Whichever of the two is started, the other is found — the pair is symmetric."""
        tracked, workspace = self._pair(tmp_path)
        mine = self._write(
            tracked / 'production' / 'sol.json', 'solusd_live', 'SOLUSD', 'sollive01')
        self._write(workspace / 'sol_copy.json', 'solusd_copy', 'SOLUSD', 'sollive01')

        with pytest.raises(CarryOverIdentityCollisionError):
            validate_carry_over_identity_unique(mine, 'solusd_live', 'SOLUSD', 'sollive01')

    def test_distinct_ids_across_the_boundary_pass(self, tmp_path):
        """The guard against overcorrecting: two trees are not themselves a collision."""
        tracked, workspace = self._pair(tmp_path)
        mine = self._write(
            tracked / 'production' / 'sol.json', 'solusd_live', 'SOLUSD', 'sollive01')
        self._write(workspace / 'sol_copy.json', 'solusd_live', 'SOLUSD', 'sollive02')

        validate_carry_over_identity_unique(mine, 'solusd_live', 'SOLUSD', 'sollive01')

    def test_a_missing_sibling_tree_is_not_an_error(self, tmp_path):
        """Most installations have no workspace profiles at all."""
        tracked = tmp_path / 'configs' / 'autotrader_profiles' / 'production'
        tracked.mkdir(parents=True)
        mine = self._write(tracked / 'sol.json', 'solusd_live', 'SOLUSD', 'sollive01')

        validate_carry_over_identity_unique(mine, 'solusd_live', 'SOLUSD', 'sollive01')
