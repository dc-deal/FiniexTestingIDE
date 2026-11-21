"""
A Mock Session Over a Venue's Published History (#647).

A backtest says when it read external data; a mock session that replays the same archive now says
it too, through the same shared checks. External data is not admitted by default, so this test
admits it for its own run — the way an operator widens `admitted_origin_classes` — and replays a
stretch from before our own collection began.
"""

from datetime import datetime, timezone

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.data_management.index.index_entry_times import entry_time_bounds
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.config_types.data_origin_config_types import OriginClass
from tests.shared.fixture_helpers import remove_run_dir

EXTERNAL_PROFILE = 'configs/autotrader_profiles/mock/external_history_replay_test.json'
# The replay's day, inside a venue's published history
REPLAY_DAY = datetime(2025, 6, 2, 12, 0, tzinfo=timezone.utc)


def _external_history_is_held() -> bool:
    """
    Whether this machine's archive holds the venue's published ETHUSD history on the replay's day.

    A contributor's published dataset never carries it, so there the test is skipped, not failed.

    Returns:
        True where a stamped external file covers the day
    """
    index = TickIndexManager()
    index.load_index()
    entries = index.get_symbol_entries('kraken_spot', 'ETHUSD')
    if not entries:
        return False
    starts, ends = entry_time_bounds(entries)
    return any(entry.get('origin_class') == 'external' and start <= REPLAY_DAY <= end
               for entry, start, end in zip(entries, starts, ends))


@pytest.fixture(scope='module')
def external_session():
    """Run the replay once with external data admitted, shared across the tests."""
    if not _external_history_is_held():
        pytest.skip('this archive holds no external ETHUSD history for the replay day — a '
                    'published dataset never carries it')
    admitted = AppConfigManager().get_admitted_origin_classes()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(AppConfigManager, 'get_admitted_origin_classes',
                      lambda self: [*admitted, OriginClass.EXTERNAL])
        trader = AutotraderMain(load_autotrader_config(EXTERNAL_PROFILE))
        result = trader.run()
    yield result
    remove_run_dir(trader.get_run_dir())


class TestTheReplayReportsWhatItRead:

    def test_external_data_is_reported_with_the_warmup_into_it(self, external_session):
        findings = {finding.check: finding.message
                    for result in external_session.session_validation_result
                    for finding in result.findings}

        assert 'data_origin_external' in findings, f'checks raised: {sorted(findings)}'
        assert "a venue's published history" in findings['data_origin_external']
        assert 'warmup from external history' in findings['data_origin_external']

    def test_what_the_files_lack_is_reported(self, external_session):
        checks = {finding.check for result in external_session.session_validation_result
                  for finding in result.findings}

        assert 'data_no_quote' in checks
