"""
FiniexTestingIDE - Credential Guard Tests

Pins the rule that a real broker call may not run on the committed placeholder credentials,
and pins the mistake the first implementation made.

That mistake is the reason this file exists. The rule is "the answering file was the tracked
default", and the first version tested it with `'configs/credentials' in path` — which is
also true for `user_configs/credentials`, so it refused the REAL key and would have failed
every live run. A substring test on two paths where one contains the other is the bug; the
test below is what caught it.
"""

from pathlib import Path

import pytest

from python.configuration.credential_guard import assert_real_credential

PURPOSE = 'Enabling live Kraken execution'


class TestTheRealKeyIsAccepted:
    """The user override is where a real credential belongs, and it must pass."""

    @pytest.mark.parametrize('path', [
        'user_configs/credentials/kraken_credentials.json',
        '/app/user_configs/credentials/rag_credentials.json',
        './user_configs/credentials/kraken_credentials.json',
    ])
    def test_the_user_override_passes(self, path):
        """
        The case the substring bug broke.

        `user_configs/credentials` CONTAINS `configs/credentials`, so any implementation
        comparing by substring refuses the real key. Relative, absolute and dot-relative
        forms are all pinned, because the caller passes whatever the cascade built.
        """
        assert_real_credential(Path(path), PURPOSE)


class TestTheTrackedDefaultIsRefused:
    """The committed placeholder must never reach a real broker call."""

    @pytest.mark.parametrize('path', [
        'configs/credentials/kraken_credentials.json',
        '/app/configs/credentials/rag_credentials.json',
        './configs/credentials/kraken_credentials.json',
    ])
    def test_the_tracked_default_raises(self, path):
        assert_real_credential  # keeps the name readable in the failure output
        with pytest.raises(ValueError):
            assert_real_credential(Path(path), PURPOSE)

    def test_the_error_names_the_purpose_the_file_and_the_fix(self):
        """
        A guard is only worth having if its message ends the search.

        Without the purpose the operator does not know which call tripped; without the
        override path they do not know where the real key goes; and the last line exists
        because a real key sitting in the tracked file is the more expensive hazard — that
        file is committed.
        """
        with pytest.raises(ValueError) as caught:
            assert_real_credential(
                Path('configs/credentials/kraken_credentials.json'), PURPOSE)
        message = str(caught.value)
        assert PURPOSE in message
        assert 'configs/credentials/kraken_credentials.json' in message
        assert 'user_configs/credentials/kraken_credentials.json' in message
        assert 'committed' in message


class TestUnrelatedPathsAreNotTouched:
    """The guard judges one directory and stays out of everything else."""

    @pytest.mark.parametrize('path', [
        'configs/app_config.json',
        'configs/broker_settings/kraken_spot.json',
        'data/runtime/brokers/kraken_spot/kraken_spot_broker_config.json',
        'some/other/credentials/file.json',
    ])
    def test_other_paths_pass(self, path):
        """
        Note the last case: a `credentials` directory whose parent is not `configs`.

        The rule is a specific pair of directory names, not the word 'credentials'
        anywhere in the path.
        """
        assert_real_credential(Path(path), PURPOSE)
