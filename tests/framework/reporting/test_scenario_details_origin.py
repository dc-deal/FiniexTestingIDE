"""
FiniexTestingIDE - Scenario-level data origin tests (#518)

The run-level roll-up in the ledger says a run mixed eras or classes; it cannot say WHICH
scenario did. For a set that mixes brokers — two of them exist in this project and they differ
on the account model, the price formation and the market type at once — that is exactly the
question the roll-up raises and cannot answer.

So the per-scenario row carries the same three values at its own grain, in the same encoding,
so the two can be compared rather than parsed against each other.

The row is built from the scenario directly, which is why the assertions below include a FAILED
scenario: a run that failed over development data and one that failed over production data are
different failures, and this row is the only per-scenario place that survives the run.
"""

from python.framework.reporting.builders.scenario_details_report_builder import _to_row
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


def _scenario(versions, classes, grades, symbol='GBPUSD', broker='mt5') -> SingleScenario:
    """
    A scenario carrying the input lists the mount fills.

    Args:
        versions: Data format versions of the files it read
        classes: Their resolved origin classes
        grades: Their resolved evidence grades
        symbol: The traded symbol
        broker: The archive it read from

    Returns:
        A scenario with those fields set
    """
    scenario = SingleScenario(
        name='s', scenario_index=0, symbol=symbol,
        data_broker_type=broker, start_date='2026-01-01')
    scenario.data_format_versions = list(versions)
    scenario.origin_classes = list(classes)
    scenario.origin_evidence_grades = list(grades)
    return scenario


def _result(name='s', error='') -> ProcessResult:
    return ProcessResult(scenario_name=name, success=not error, error_message=error,
                         error_type='ValueError' if error else '')


class TestAScenarioRecordsWhatItRead:
    """The grain the ledger's per-run roll-up cannot reach."""

    def test_the_three_values_reach_the_row(self):
        scenario = _scenario(['1.5.0', '1.7.0', '1.7.0'],
                             ['production', 'production', 'production'],
                             ['attested', 'stamped', 'stamped'])

        row = _to_row(_result(), scenario)

        assert row.data_format_versions == '1.5.0,1.7.0'
        assert row.origin_classes == 'production'
        assert row.origin_evidence_grades == 'attested,stamped'

    def test_a_FAILED_scenario_still_says_what_it_read(self):
        """
        The row where it matters most, and the one an early return could have skipped.

        A scenario that failed over development data and one that failed over production data
        are different failures; without this the distinction dies with the run.
        """
        scenario = _scenario(['1.7.0'], ['development'], ['stamped'])

        row = _to_row(_result(error='boom'), scenario)

        assert row.status == 'failed'
        assert row.origin_classes == 'development'
        assert row.origin_evidence_grades == 'stamped'

    def test_the_encoding_is_the_one_the_ledger_uses(self):
        """
        Sorted and distinct, so the two grains are comparable rather than merely both present.

        Stability matters as much as the deduplication: the same set of files read in a
        different order must produce the same string, or two identical runs look different.
        """
        one = _to_row(_result(), _scenario(
            ['1.7.0', '1.5.0'], ['unknown', 'production'], ['unknown', 'stamped']))
        other = _to_row(_result(), _scenario(
            ['1.5.0', '1.7.0'], ['production', 'unknown'], ['stamped', 'unknown']))

        assert one.data_format_versions == other.data_format_versions == '1.5.0,1.7.0'
        assert one.origin_classes == other.origin_classes == 'production,unknown'

    def test_a_scenario_that_read_nothing_reports_empty_rather_than_a_placeholder(self):
        row = _to_row(_result(), _scenario([], [], []))

        assert row.data_format_versions == ''
        assert row.origin_classes == ''
        assert row.origin_evidence_grades == ''
