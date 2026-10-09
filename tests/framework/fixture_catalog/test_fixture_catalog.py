"""
The fixture catalog (#576) — the runs a consumer pins, produced by one command and checked.

This suite pins that the catalog is complete and every entry starts from a configuration that
declares itself a fixture; that the production record derives the current fixture and never lets a
failed production replace a good one; that a session sequence runs every declared session with its
own changes and its own carry-over; and that one real production — the report-coverage backtest —
still carries every property its consumers assert.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

import pytest

from python.framework.fixture_catalog.fixture_catalog import (
    FIXTURE_CATALOG,
    FIXTURE_CATALOG_BY_ID,
)
from python.framework.fixture_catalog.fixture_producer import FixtureProducer, session_profile
from python.framework.fixture_catalog.fixture_production_store import (
    PRODUCTION_RECORD_FILE,
    FixtureProductionStore,
)
from python.framework.reporting.store.report_store import ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import DeploymentSummary, RunHeader
from python.framework.types.fixture_catalog_types import (
    FixtureEntry,
    FixtureEvidence,
    FixtureProducerKind,
    FixtureProduction,
)
from python.framework.types.run_purpose_types import RunPurpose
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_ROOT = Path(__file__).resolve().parents[3]


def _declared_purpose_of(entry: FixtureEntry) -> str:
    """
    What the configuration an entry's runs start from declares — a sweep's is its base set,
    resolved the way the sweep resolves it.

    Args:
        entry: The catalog entry

    Returns:
        The declared run purpose
    """
    source = json.loads((_ROOT / entry.source).read_text(encoding='utf-8'))
    if entry.producer == FixtureProducerKind.SWEEP:
        return ScenarioConfigLoader().load_config(source['base_scenario_set']).run_purpose.value
    if entry.producer == FixtureProducerKind.SCENARIO_SET:
        return ScenarioConfigLoader().load_config(str(_ROOT / entry.source)).run_purpose.value
    return source.get('run_purpose', RunPurpose.REGULAR.value)


def _check(entry_id: str, property_id: str) -> Callable[[FixtureEvidence], bool]:
    """
    One property's check, by id.

    Args:
        entry_id: The entry
        property_id: The property

    Returns:
        The check function
    """
    entry = FIXTURE_CATALOG_BY_ID[entry_id]
    return next(prop.check for prop in entry.properties if prop.property_id == property_id)


def _production(entry_id: str, produced_at: str, verified: bool) -> FixtureProduction:
    return FixtureProduction(entry_id=entry_id, produced_at=produced_at,
                             run_ids=[f'run_{produced_at}'], verified=verified,
                             report_contract=24)


class TestTheCatalogIsComplete:

    def test_every_entry_is_named_once(self):
        ids = [entry.entry_id for entry in FIXTURE_CATALOG]
        assert ids and len(ids) == len(set(ids))

    def test_every_entry_asserts_something_and_names_it_once(self):
        for entry in FIXTURE_CATALOG:
            ids = [prop.property_id for prop in entry.properties]
            assert ids, f'{entry.entry_id} asserts nothing'
            assert len(ids) == len(set(ids)), f'{entry.entry_id} names a property twice'
            assert all(prop.sentence for prop in entry.properties)
            assert entry.consumers, f'{entry.entry_id} names no consumer'

    def test_every_source_exists(self):
        missing = [entry.source for entry in FIXTURE_CATALOG if not (_ROOT / entry.source).exists()]
        assert not missing

    def test_every_entry_starts_from_a_configuration_that_declares_itself_a_fixture(self):
        """A catalog run is a fixture by its own header — a certificate is never produced here."""
        undeclared = [entry.entry_id for entry in FIXTURE_CATALOG
                      if _declared_purpose_of(entry) != RunPurpose.FIXTURE.value]
        assert not undeclared

    def test_only_a_session_sequence_declares_sessions_and_a_bot(self):
        for entry in FIXTURE_CATALOG:
            sequence = entry.producer == FixtureProducerKind.SESSION_SEQUENCE
            declared = (bool(entry.sessions), bool(entry.session_profile_name),
                        bool(entry.session_bot_id), entry.session_max_ticks > 0)
            assert declared == (sequence,) * 4, entry.entry_id


class TestTheRecordDerivesTheCurrentFixture:

    def test_a_production_reads_back_as_it_was_recorded(self, tmp_path):
        store = FixtureProductionStore(tmp_path / 'record.jsonl')
        production = _production('report_coverage', '2026-10-08T10:00:00+00:00', True)
        store.append(production)
        assert store.read() == [production]

    def test_the_newest_verified_production_is_current(self, tmp_path):
        store = FixtureProductionStore(tmp_path / 'record.jsonl')
        store.append(_production('report_coverage', '2026-10-08T10:00:00+00:00', True))
        store.append(_production('report_coverage', '2026-10-08T11:00:00+00:00', True))
        assert store.current()['report_coverage'].produced_at == '2026-10-08T11:00:00+00:00'

    def test_a_failed_production_never_replaces_a_good_one(self, tmp_path):
        """It stays in the record as what happened — and the consumer's pin stays valid."""
        store = FixtureProductionStore(tmp_path / 'record.jsonl')
        store.append(_production('report_coverage', '2026-10-08T10:00:00+00:00', True))
        store.append(_production('report_coverage', '2026-10-08T11:00:00+00:00', False))
        assert store.current()['report_coverage'].produced_at == '2026-10-08T10:00:00+00:00'
        assert len(store.read()) == 2

    def test_a_cut_off_last_line_does_not_hide_the_ones_before_it(self, tmp_path):
        path = tmp_path / 'record.jsonl'
        store = FixtureProductionStore(path)
        store.append(_production('report_coverage', '2026-10-08T10:00:00+00:00', True))
        with path.open('a', encoding='utf-8') as stream:
            stream.write('{"entry_id": "report_cov')
        assert [p.produced_at for p in store.read()] == ['2026-10-08T10:00:00+00:00']

    def test_a_missing_record_holds_nothing(self, tmp_path):
        assert FixtureProductionStore(tmp_path / 'absent.jsonl').current() == {}

    def test_an_entry_the_catalog_no_longer_declares_is_current_no_more(self, tmp_path):
        """Renamed or removed — nobody can produce it again, so nothing may pin it for ever."""
        store = FixtureProductionStore(tmp_path / 'record.jsonl',
                                       declared_entries={'report_coverage'})
        retired = _production('retired_entry', '2026-10-08T10:00:00+00:00', True)
        store.append(retired)
        store.append(_production('report_coverage', '2026-10-08T11:00:00+00:00', True))

        assert set(store.current()) == {'report_coverage'}
        assert all(store.fixture_superseded_by_run()[run_id] for run_id in retired.run_ids)


class TestASessionSequenceRunsEveryDeclaredSession:
    """Run with a session runner that starts nothing — the sessions themselves are not run here."""

    def test_each_session_runs_its_own_profile_with_its_own_flags(self, tmp_path):
        entry = FIXTURE_CATALOG_BY_ID['demo_deployment']
        seen: List[tuple] = []

        arguments_seen: List[str] = []

        def runner(arguments: List[str], kill_after: Optional[float]) -> str:
            profile = json.loads(Path(arguments[2]).read_text(encoding='utf-8'))
            arguments_seen.append(arguments[2])
            seen.append((profile, '--new-deployment' in arguments, kill_after))
            return 'not run'

        production = FixtureProducer(session_runner=runner,
                                     store=FixtureProductionStore(tmp_path / 'record.jsonl'),
                                     progress=lambda line: None).produce(entry)

        assert len(seen) == len(entry.sessions)
        for (profile, new_deployment, kill_after), session in zip(seen, entry.sessions):
            assert profile['profile_name'] == entry.session_profile_name
            assert profile['bot_id'] == entry.session_bot_id
            assert profile['deployment'] == {'continuous': True}
            assert profile['scenario_settings']['start_date'] == session.start_date
            assert profile['scenario_settings']['max_ticks'] == entry.session_max_ticks
            # Its own carry-over, shared by the sequence and by nothing outside it.
            assert profile['cold_start']['path'].startswith(str(Path(arguments_seen[0]).parent))
            assert (new_deployment, kill_after) == (session.new_deployment,
                                                    session.kill_after_seconds)
        assert not production.verified, 'a production that made no run cannot be current'
        assert production.run_ids == []

    def test_a_session_s_changes_land_at_their_dotted_paths(self):
        entry = FIXTURE_CATALOG_BY_ID['demo_deployment']
        base = json.loads((_ROOT / entry.source).read_text(encoding='utf-8'))
        changed = session_profile(base, entry, entry.sessions[3])

        assert changed['strategy_config']['decision_logic_config']['rsi_oversold'] == 35
        assert changed['safety']['max_drawdown_pct'] == 12.0
        assert changed['safety']['enabled'] is True
        assert base['profile_name'] != changed['profile_name'], 'the base profile was altered'


class TestAPropertyReadsWhatIsServed:

    def test_two_histories_of_one_bot(self):
        def history(deployment_id: str, bot_id: str) -> DeploymentSummary:
            return DeploymentSummary(deployment_id=deployment_id, sessions=1, first_started=None,
                                     last_started=None, net_pnl=0.0, max_drawdown=0.0,
                                     max_drawdown_pct=0.0, currency='USD',
                                     bot='demo_btcusd_bot', bot_id=bot_id, changed=False,
                                     orders_to=[])
        check = _check('demo_deployment', 'two_histories')
        assert check(FixtureEvidence(deployment_rows=[history('d1', 'demo-btc'),
                                                      history('d2', 'demo-btc')]))
        assert not check(FixtureEvidence(deployment_rows=[history('d1', 'demo-btc'),
                                                          history('d2', 'other')]))



class TestTheRunListSaysWhichFixtureIsCurrent:
    """Derived each time the list is served, from the record beside the index — never stored."""

    def test_each_run_reads_current_superseded_or_neither(self, tmp_path):
        index_path = tmp_path / 'runs_index.parquet'
        for run_id in ('20261008_100000_aaaaaaaa', '20261008_110000_bbbbbbbb',
                       '20261008_120000_cccccccc'):
            RunIndex(index_path).register_run(
                RunHeader(run_id=run_id, start_time=datetime.now(timezone.utc),
                          run_type='simulation', run_name='report_coverage_reference'),
                tmp_path / run_id)
        record = FixtureProductionStore(tmp_path / PRODUCTION_RECORD_FILE)
        record.append(FixtureProduction(entry_id='report_coverage',
                                        produced_at='2026-10-08T10:00:00+00:00',
                                        run_ids=['20261008_100000_aaaaaaaa'], verified=True,
                                        report_contract=24))
        record.append(FixtureProduction(entry_id='report_coverage',
                                        produced_at='2026-10-08T11:00:00+00:00',
                                        run_ids=['20261008_110000_bbbbbbbb'], verified=True,
                                        report_contract=24))

        runs = {run.run_id: run.fixture_superseded for run in
                ReportStore(index_path, tmp_path / 'ledger').list_runs_with_results()}

        assert runs == {'20261008_100000_aaaaaaaa': True, '20261008_110000_bbbbbbbb': False,
                        '20261008_120000_cccccccc': None}

class TestTheReportCoverageEntryCarriesEveryProperty:
    """
    One real production, under the session's isolated stores: if the coverage set stops producing
    a state its consumers assert, this fails here rather than in their capture.
    """

    @pytest.fixture(scope='class')
    @classmethod
    def production(cls, tmp_path_factory) -> FixtureProduction:
        store = FixtureProductionStore(tmp_path_factory.mktemp('fixture_record') / 'record.jsonl')
        return FixtureProducer(store=store, progress=lambda line: None).produce(
            FIXTURE_CATALOG_BY_ID['report_coverage'])

    def test_it_makes_exactly_one_run(self, production):
        assert len(production.run_ids) == 1

    def test_every_property_holds(self, production):
        assert production.failed_properties == []
        assert production.verified
