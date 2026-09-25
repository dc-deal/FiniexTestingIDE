"""
FiniexTestingIDE - Host Identity Manager Tests (#551)

Guards the three rules the `host` field of every run header rests on: a missing identity is
minted once and announced, a present but broken one refuses the start and is NEVER re-minted, and
config isolation states the declared test identity without touching the file at all.

The refusal is what these tests exist for. A manager that quietly minted a new identity over a
broken file would pass every other test here — and from that moment every run from the same
machine would claim to come from a different one, with nothing anywhere saying so.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

import pytest

import python.configuration.host_identity_manager as host_identity_module
from python.configuration.host_identity_manager import HostIdentityManager
from python.framework.exceptions.host_identity_errors import HostIdentityError
from python.framework.types.config_types.host_identity_config_types import (
    HOST_ID_ALPHABET,
    HOST_ID_PATTERN,
    HOST_ID_PREFIX,
    HOST_ID_RANDOM_LENGTH,
    TEST_HOST_ID,
)

_VALID_ID = 'h_7k2m9q'
_VALID_MINTED_AT = '2026-09-24T10:00:00Z'


class _RecordingLogger:
    """Stands in for the global logger so a test can read what the mint announced."""

    def __init__(self):
        self.warnings = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)


@pytest.fixture(autouse=True)
def _drop_cache():
    """The cache is per process — a leftover would answer the next test's question."""
    HostIdentityManager.clear_cache()
    yield
    HostIdentityManager.clear_cache()


@pytest.fixture(autouse=True)
def _isolation_off(monkeypatch):
    """
    The suite runs isolated, and the mint and read paths only exist without it. Every test
    points the manager at its own temporary file; the isolation tests switch it back on.
    """
    monkeypatch.setenv('FINIEX_CONFIG_ISOLATION', '0')


@pytest.fixture(autouse=True)
def logger(monkeypatch) -> _RecordingLogger:
    """Capture the mint announcement instead of writing it to the global log."""
    recording = _RecordingLogger()
    monkeypatch.setattr(host_identity_module, 'get_global_logger', lambda: recording)
    return recording


def _identity_path(tmp_path: Path) -> Path:
    """
    The identity file inside a nested directory that does not exist yet.

    Args:
        tmp_path: pytest's per-test directory

    Returns:
        The path the manager is pointed at
    """
    return tmp_path / 'user_configs' / 'host_identity.json'


def _write(path: Path, content: str) -> None:
    """
    Place an identity file with exactly this content.

    Args:
        path: Where to write it
        content: The raw file content
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


class TestAMissingIdentityIsMintedOnce:

    def test_the_mint_writes_the_minted_shape(self, tmp_path, logger):
        path = _identity_path(tmp_path)
        host_id = HostIdentityManager(str(path)).get_host_id()

        assert re.fullmatch(HOST_ID_PATTERN, host_id)
        stored = json.loads(path.read_text(encoding='utf-8'))
        assert stored['host_id'] == host_id
        minted_at = datetime.fromisoformat(stored['minted_at'])
        assert minted_at.tzinfo is not None
        assert minted_at.utcoffset().total_seconds() == 0

    def test_the_mint_is_announced_naming_the_file(self, tmp_path, logger):
        path = _identity_path(tmp_path)
        host_id = HostIdentityManager(str(path)).get_host_id()

        assert len(logger.warnings) == 1
        assert host_id in logger.warnings[0]
        assert str(path) in logger.warnings[0]

    def test_a_second_start_reads_the_same_identity(self, tmp_path, logger):
        path = _identity_path(tmp_path)
        first = HostIdentityManager(str(path)).get_host_id()
        content = path.read_bytes()

        HostIdentityManager.clear_cache()
        second = HostIdentityManager(str(path)).get_host_id()

        assert second == first
        assert path.read_bytes() == content
        # Announced once: the second start READ the identity, it did not mint one.
        assert len(logger.warnings) == 1

    def test_no_temporary_file_is_left_behind(self, tmp_path, logger):
        path = _identity_path(tmp_path)
        HostIdentityManager(str(path)).get_host_id()

        assert sorted(entry.name for entry in path.parent.iterdir()) == [path.name]

    def test_an_existing_identity_is_read_as_it_is(self, tmp_path, logger):
        path = _identity_path(tmp_path)
        _write(path, json.dumps({'host_id': _VALID_ID, 'minted_at': _VALID_MINTED_AT}))

        assert HostIdentityManager(str(path)).get_host_id() == _VALID_ID
        assert logger.warnings == []

    def test_a_comment_key_is_allowed(self, tmp_path, logger):
        # §28: `_comment` is how a file explains itself, and the strict shape honours it.
        path = _identity_path(tmp_path)
        _write(path, json.dumps({'_comment': 'restored from backup 2026-09-24',
                                 'host_id': _VALID_ID, 'minted_at': _VALID_MINTED_AT}))

        assert HostIdentityManager(str(path)).get_host_id() == _VALID_ID


class TestTheAnswerIsCachedPerProcess:

    def test_the_file_is_read_once(self, tmp_path, logger):
        path = _identity_path(tmp_path)
        _write(path, json.dumps({'host_id': _VALID_ID, 'minted_at': _VALID_MINTED_AT}))
        HostIdentityManager(str(path)).get_host_id()

        # Removing the file proves the second answer did not come from disk.
        path.unlink()
        assert HostIdentityManager(str(path)).get_host_id() == _VALID_ID

    def test_the_cache_is_keyed_on_the_file(self, tmp_path, logger):
        first_path = tmp_path / 'first' / 'host_identity.json'
        second_path = tmp_path / 'second' / 'host_identity.json'
        _write(first_path, json.dumps({'host_id': 'h_aaaaaa', 'minted_at': _VALID_MINTED_AT}))
        _write(second_path, json.dumps({'host_id': 'h_bbbbbb', 'minted_at': _VALID_MINTED_AT}))

        assert HostIdentityManager(str(first_path)).get_host_id() == 'h_aaaaaa'
        assert HostIdentityManager(str(second_path)).get_host_id() == 'h_bbbbbb'


class TestABrokenIdentityRefusesAndIsNeverReMinted:

    @pytest.mark.parametrize('content', [
        pytest.param('', id='empty'),
        pytest.param('{"host_id": "h_7k2m9q",', id='truncated_json'),
        pytest.param('["h_7k2m9q"]', id='not_an_object'),
        pytest.param(json.dumps({'minted_at': _VALID_MINTED_AT}), id='missing_host_id'),
        pytest.param(json.dumps({'host_id': _VALID_ID}), id='missing_minted_at'),
        pytest.param(json.dumps({'host_id': 'H_7K2M9Q', 'minted_at': _VALID_MINTED_AT}),
                     id='uppercase_id'),
        pytest.param(json.dumps({'host_id': 'h_7k2m9', 'minted_at': _VALID_MINTED_AT}),
                     id='short_id'),
        pytest.param(json.dumps({'host_id': 'my-laptop', 'minted_at': _VALID_MINTED_AT}),
                     id='hostname_instead_of_id'),
        pytest.param(json.dumps({'host_id': TEST_HOST_ID, 'minted_at': _VALID_MINTED_AT}),
                     id='test_id_on_disk'),
        pytest.param(json.dumps({'host_id': 7, 'minted_at': _VALID_MINTED_AT}),
                     id='id_not_a_string'),
        pytest.param(json.dumps({'host_id': _VALID_ID, 'minted_at': '2026-09-24T10:00:00'}),
                     id='naive_minted_at'),
        pytest.param(json.dumps({'host_id': _VALID_ID, 'minted_at': 'yesterday'}),
                     id='unparseable_minted_at'),
        pytest.param(json.dumps({'host_id': _VALID_ID, 'minted_at': _VALID_MINTED_AT,
                                 'hostid': 'h_zzzzzz'}), id='unknown_key'),
    ])
    def test_the_start_is_refused_and_the_file_left_alone(
            self, tmp_path, logger, content):
        path = _identity_path(tmp_path)
        _write(path, content)
        before = path.read_bytes()

        with pytest.raises(HostIdentityError) as refusal:
            HostIdentityManager(str(path)).get_host_id()

        message = str(refusal.value)
        assert str(path) in message
        assert 'never re-minted' in message
        assert path.read_bytes() == before
        assert logger.warnings == []

    def test_an_unreadable_file_is_refused(self, tmp_path, logger):
        # A directory where the file should be: present, and not readable as one.
        path = _identity_path(tmp_path)
        path.mkdir(parents=True)

        with pytest.raises(HostIdentityError, match='cannot be read'):
            HostIdentityManager(str(path)).get_host_id()
        assert path.is_dir()

    def test_a_file_that_is_not_text_is_refused(self, tmp_path, logger):
        path = _identity_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b'\xff\xfe\x00garbage')

        with pytest.raises(HostIdentityError, match='cannot be read'):
            HostIdentityManager(str(path)).get_host_id()

    def test_a_refusal_is_not_cached_as_an_answer(self, tmp_path, logger):
        # Restoring the file is the documented way forward, so the next attempt must see it.
        path = _identity_path(tmp_path)
        _write(path, '{')
        with pytest.raises(HostIdentityError):
            HostIdentityManager(str(path)).get_host_id()

        _write(path, json.dumps({'host_id': _VALID_ID, 'minted_at': _VALID_MINTED_AT}))
        assert HostIdentityManager(str(path)).get_host_id() == _VALID_ID


class TestIsolationStatesTheTestIdentity:

    def test_no_file_is_minted(self, tmp_path, monkeypatch, logger):
        monkeypatch.setenv('FINIEX_CONFIG_ISOLATION', '1')
        path = _identity_path(tmp_path)

        assert HostIdentityManager(str(path)).get_host_id() == TEST_HOST_ID
        assert not path.parent.exists()
        assert logger.warnings == []

    def test_no_file_is_read(self, tmp_path, monkeypatch, logger):
        # A broken file would refuse if it were read — the test identity proves it was not.
        monkeypatch.setenv('FINIEX_CONFIG_ISOLATION', '1')
        path = _identity_path(tmp_path)
        _write(path, 'not json at all')

        assert HostIdentityManager(str(path)).get_host_id() == TEST_HOST_ID

    def test_the_test_identity_cannot_pass_for_a_minted_one(self):
        assert re.fullmatch(HOST_ID_PATTERN, TEST_HOST_ID) is None


class TestTheMintIsFirstWriterWins:

    def test_a_process_that_published_first_keeps_its_identity(
            self, tmp_path, logger, monkeypatch):
        # Another process publishes between this one's write and its publish. The earlier
        # identity may already stand in a run header, so it must not be replaced.
        path = _identity_path(tmp_path)
        real_link = os.link

        def _other_process_publishes_first(source, target):
            Path(target).write_text(
                json.dumps({'host_id': 'h_winner', 'minted_at': _VALID_MINTED_AT}),
                encoding='utf-8')
            real_link(source, target)

        monkeypatch.setattr(os, 'link', _other_process_publishes_first)

        assert HostIdentityManager(str(path)).get_host_id() == 'h_winner'
        assert json.loads(path.read_text(encoding='utf-8'))['host_id'] == 'h_winner'
        assert sorted(entry.name for entry in path.parent.iterdir()) == [path.name]
        assert logger.warnings == []

    def test_a_filesystem_without_hard_links_still_mints(
            self, tmp_path, logger, monkeypatch):
        path = _identity_path(tmp_path)

        def _no_hard_links(source, target):
            raise PermissionError(1, 'Operation not permitted')

        monkeypatch.setattr(os, 'link', _no_hard_links)

        host_id = HostIdentityManager(str(path)).get_host_id()
        assert json.loads(path.read_text(encoding='utf-8'))['host_id'] == host_id
        assert sorted(entry.name for entry in path.parent.iterdir()) == [path.name]


class TestTheMintedShapeIsOneDefinition:

    def test_every_alphabet_character_is_accepted_by_the_pattern(self):
        # The pattern spells the alphabet as a range for the operator's sake; this holds the
        # two to the same characters, in both directions.
        for character in HOST_ID_ALPHABET:
            assert re.fullmatch(HOST_ID_PATTERN,
                                HOST_ID_PREFIX + character * HOST_ID_RANDOM_LENGTH)
        for character in 'AZ_-.é ':
            assert re.fullmatch(HOST_ID_PATTERN,
                                HOST_ID_PREFIX + character * HOST_ID_RANDOM_LENGTH) is None
