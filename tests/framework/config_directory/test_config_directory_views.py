"""
Config directory console views (#554).

The views only format — every figure comes off the model — so what can go wrong here is what the
operator READS: a file name cut short that `show` then cannot find, a broken file that vanishes
from the list, a real-money profile that does not say so, a validation that reads as a promise.
"""

from python.framework.reporting.console.config_directory_summary import (
    render_config_directory,
    render_config_directory_entry,
    render_config_validation,
)
from python.framework.types.api.directory_types import (
    DirectoryDetailResponse,
    DirectoryListResponse,
    DirectoryRow,
)
from python.framework.types.config_directory_types import (
    ConfigKind,
    ConfigOrigin,
    ConfigReadStatus,
    ConfigValidationResult,
)

_LONG_NAME = 'a_scenario_set_whose_file_name_is_far_longer_than_any_column_default.json'


def _row(file: str, kind=ConfigKind.SCENARIO_SET, status=ConfigReadStatus.READABLE,
         **fields) -> DirectoryRow:
    """
    A directory row with only what a case needs.

    Args:
        file: The file name
        kind: Its kind, None for an unreadable file
        status: Readable or unreadable
        fields: Any further row fields

    Returns:
        The row
    """
    return DirectoryRow(kind=kind, file=file, origin=ConfigOrigin.CONFIGS, status=status,
                        **fields)


def _listing(*rows: DirectoryRow) -> DirectoryListResponse:
    """
    A listing over the given rows.

    Args:
        rows: The rows

    Returns:
        The listing
    """
    return DirectoryListResponse(rows=list(rows), count=len(rows))


class TestTheList:
    """What the operator reads in `config_directory_cli.py list`."""

    def test_a_long_file_name_is_never_cut(self, capsys):
        """The file name is what `show` takes — a cut one is a name that finds nothing."""
        render_config_directory(_listing(_row(_LONG_NAME), _row('short.json')))
        assert _LONG_NAME in capsys.readouterr().out

    def test_an_unreadable_file_is_listed_with_its_reason(self, capsys):
        """A file being edited is a line with its reason, never a file that silently vanished."""
        broken = _row('my_wip.json', kind=None, status=ConfigReadStatus.UNREADABLE,
                      reason="line 4: Expecting ',' delimiter")
        render_config_directory(_listing(_row('fine.json'), broken))
        out = capsys.readouterr().out
        unreadable_section = out[out.index('UNREADABLE'):]
        assert 'my_wip.json' in unreadable_section
        assert "line 4: Expecting ',' delimiter" in unreadable_section

    def test_the_kind_filter_shows_only_that_kind(self, capsys):
        render_config_directory(
            _listing(_row('a_set.json'),
                     _row('a_profile.json', kind=ConfigKind.AUTOTRADER_PROFILE)),
            kind=ConfigKind.AUTOTRADER_PROFILE)
        out = capsys.readouterr().out
        assert 'a_profile.json' in out
        assert 'a_set.json' not in out


class TestOneEntry:
    """What the operator reads in `config_directory_cli.py show <file>`."""

    def test_a_profile_declaring_real_orders_says_so(self, capsys):
        profile = _row('live.json', kind=ConfigKind.AUTOTRADER_PROFILE, adapter_type='live',
                       dry_run_declared=False)
        render_config_directory_entry(DirectoryDetailResponse(row=profile))
        assert 'FALSE — real orders' in capsys.readouterr().out

    def test_an_undeclared_dry_run_is_the_broker_default_not_false(self, capsys):
        """`null` is decided at session start — rendering it as false would be a guess."""
        profile = _row('mock.json', kind=ConfigKind.AUTOTRADER_PROFILE, adapter_type='mock')
        render_config_directory_entry(DirectoryDetailResponse(row=profile))
        out = capsys.readouterr().out
        assert 'dry_run broker default' in out
        assert 'real orders' not in out

    def test_a_shadowing_file_names_the_copy_that_does_not_run(self, capsys):
        winner = _row('same.json', shadowed=[ConfigOrigin.CONFIGS])
        render_config_directory_entry(DirectoryDetailResponse(row=winner))
        assert 'NOT the one that runs' in capsys.readouterr().out

    def test_an_unreadable_entry_shows_its_reason_and_nothing_it_cannot_know(self, capsys):
        broken = _row('my_wip.json', kind=None, status=ConfigReadStatus.UNREADABLE,
                      reason='line 1: Expecting value')
        render_config_directory_entry(DirectoryDetailResponse(row=broken))
        out = capsys.readouterr().out
        assert 'line 1: Expecting value' in out
        assert 'runs on record' not in out


class TestValidation:
    """What `strategy_runner_cli.py validate` prints."""

    def test_an_accepted_set_is_not_presented_as_runnable(self, capsys):
        """The loader does not check parameter names — the caveat stands even when all pass."""
        render_config_validation([ConfigValidationResult(file='a.json', ok=True, scenarios=3)])
        out = capsys.readouterr().out
        assert '1 of 1 accepted' in out
        assert 'Parameter names are checked later' in out

    def test_a_refusal_shows_the_first_line_of_its_reason(self, capsys):
        render_config_validation([
            ConfigValidationResult(file='a.json', ok=True, scenarios=3),
            ConfigValidationResult(file='b.json', ok=False,
                                   reason='Unknown key: recompute\nfull trace follows'),
        ])
        out = capsys.readouterr().out
        assert '1 of 2 accepted' in out
        assert 'Unknown key: recompute' in out
        assert 'full trace follows' not in out
