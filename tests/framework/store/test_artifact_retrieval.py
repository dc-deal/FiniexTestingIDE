"""
Form-A retrieval tests (#486).

Eighteen hand-written readers became one, and the property that had to survive the collapse is
STATIC TYPING: `get(run_id, BROKER_ARTIFACT)` must still be a BrokerReport and nothing else. A
runtime test cannot check a static type, so what is asserted here is the pair that makes the
static claim true — every spec's model matches the artifact it names, and the round trip returns
that model.
"""

import pytest

from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io import artifact_specs
from python.framework.reporting.io.artifact_specs import (
    BROKER_ARTIFACT,
    PORTFOLIO_ARTIFACT,
    WARNINGS_ERRORS_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import (
    ArtifactSpec,
    read_artifact,
    write_artifact,
)
from python.framework.reporting.store.report_store import ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import (
    BrokerReport,
    RunHeader,
    RunReporting,
    WarningsErrorsReport,
)
from python.framework.types.log_layout_types import IO_SUBDIR, RUN_TYPE_SIMULATION


def _all_specs():
    """Every spec the registry declares."""
    return [getattr(artifact_specs, name) for name in dir(artifact_specs)
            if name.endswith('_ARTIFACT')]


class TestSpecRegistry:
    """The registry is the whole of what a run's report units carry between them."""

    def test_every_spec_names_a_json_file_and_a_model(self):
        # No expected COUNT: the set still grows, and a hard number here goes stale on the
        # next section while adding nothing the loop below does not already prove. What is
        # worth asserting is that the collection found anything at all — an empty registry
        # would pass every check inside the loop.
        specs = _all_specs()
        assert specs, 'the spec registry collected nothing'
        for spec in specs:
            assert isinstance(spec, ArtifactSpec)
            assert spec.filename.endswith('.json')
            assert hasattr(spec.model, 'model_validate_json')

    def test_no_two_artifacts_share_a_file_name(self):
        """Two specs on one name would silently overwrite each other inside a run."""
        names = [spec.filename for spec in _all_specs()]
        assert len(names) == len(set(names)), f'duplicate artifact names: {names}'


class TestRoundTrip:
    """Write, read back, and get the same model out."""

    def test_the_round_trip_returns_the_model_the_spec_names(self, tmp_path):
        report = BrokerReport(run_id='20260901_120000_aaaaaaaa', units=[])
        path = write_artifact(report, tmp_path, BROKER_ARTIFACT)

        assert path.name == BROKER_ARTIFACT.filename
        back = read_artifact(path, BROKER_ARTIFACT)
        assert isinstance(back, BrokerReport)
        assert back == report


class TestStoreRetrieval:
    """The store resolves through the index, and names an artifact it cannot decode."""

    @staticmethod
    def _run(tmp_path, run_id='20260901_120000_aaaaaaaa'):
        run_dir = tmp_path / 'simulation' / 'my_set' / run_id
        (run_dir / IO_SUBDIR).mkdir(parents=True)
        index = RunIndex(tmp_path / 'index.parquet')
        index.register_run(RunHeader(
            run_id=run_id,
            start_time='2026-09-01T12:00:00+00:00',
            run_type=RUN_TYPE_SIMULATION,
            run_name='my_set',
            config_snapshot='scenario_config.json',
            app_version='1.4.0',
            reporting=RunReporting.EXPECTED,
        ), run_dir)
        return run_dir, ReportStore(run_index_path=tmp_path / 'index.parquet')

    def test_a_missing_artifact_is_none_rather_than_an_error(self, tmp_path):
        _, store = self._run(tmp_path)
        assert store.get('20260901_120000_aaaaaaaa', PORTFOLIO_ARTIFACT) is None

    def test_an_unknown_run_is_none(self, tmp_path):
        _, store = self._run(tmp_path)
        assert store.get('20260101_000000_ffffffff', BROKER_ARTIFACT) is None

    def test_a_present_artifact_comes_back_decoded(self, tmp_path):
        run_dir, store = self._run(tmp_path)
        report = BrokerReport(run_id='20260901_120000_aaaaaaaa', units=[])
        write_artifact(report, run_dir / IO_SUBDIR, BROKER_ARTIFACT)

        back = store.get('20260901_120000_aaaaaaaa', BROKER_ARTIFACT)
        assert isinstance(back, BrokerReport)
        assert back == report

    def test_an_undecodable_artifact_is_named_rather_than_escaping_raw(self, tmp_path):
        """
        One behaviour for every artifact.

        This guard used to exist for exactly one of the fifteen getters — the one where it had
        bitten. Collapsing them made it the rule instead of the exception.
        """
        run_dir, store = self._run(tmp_path)
        (run_dir / IO_SUBDIR / WARNINGS_ERRORS_ARTIFACT.filename).write_text(
            '{"unexpected": true}', encoding='utf-8')

        with pytest.raises(ReportArtifactUnreadableError) as excinfo:
            store.get('20260901_120000_aaaaaaaa', WARNINGS_ERRORS_ARTIFACT)
        assert WARNINGS_ERRORS_ARTIFACT.filename in str(excinfo.value)
