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

from python.framework.reporting.builders.scenario_details_report_builder import (
    _data_brokers,
    _market_types,
    _to_row,
)
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


def _scenario(versions, classes, grades, symbol='GBPUSD', broker='mt5',
              bases=()) -> SingleScenario:
    """
    A scenario carrying the input lists the mount fills.

    Args:
        versions: Data format versions of the files it read
        classes: Their resolved origin classes
        grades: Their resolved evidence grades
        symbol: The traded symbol
        broker: The archive it read from
        bases: Price bases of the BAR files it mounted — a different archive from the three
            above, so it defaults to none rather than mirroring them

    Returns:
        A scenario with those fields set
    """
    scenario = SingleScenario(
        name='s', scenario_index=0, symbol=symbol,
        data_broker_type=broker, start_date='2026-01-01')
    scenario.data_format_versions = list(versions)
    scenario.origin_classes = list(classes)
    scenario.origin_evidence_grades = list(grades)
    scenario.price_bases = list(bases)
    return scenario


def _result(name='s', error='') -> ProcessResult:
    return ProcessResult(scenario_name=name, success=not error, error_message=error,
                         error_type='ValueError' if error else '')


def _row(result: ProcessResult, scenario: SingleScenario):
    """
    Build one row the way the builder does, its market type resolved through the real config.

    Args:
        result: The scenario's process result
        scenario: The scenario

    Returns:
        The scenario-details row
    """
    return _to_row(result, scenario, _market_types({scenario.data_broker_type}))


class TestAScenarioRecordsWhatItRead:
    """The grain the ledger's per-run roll-up cannot reach."""

    def test_the_three_values_reach_the_row(self):
        scenario = _scenario(['1.5.0', '1.7.0', '1.7.0'],
                             ['production', 'production', 'production'],
                             ['attested', 'stamped', 'stamped'])

        row = _row(_result(), scenario)

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

        row = _row(_result(error='boom'), scenario)

        assert row.status == 'failed'
        assert row.origin_classes == 'development'
        assert row.origin_evidence_grades == 'stamped'

    def test_the_encoding_is_the_one_the_ledger_uses(self):
        """
        Sorted and distinct, so the two grains are comparable rather than merely both present.

        Stability matters as much as the deduplication: the same set of files read in a
        different order must produce the same string, or two identical runs look different.
        """
        one = _row(_result(), _scenario(
            ['1.7.0', '1.5.0'], ['unknown', 'production'], ['unknown', 'stamped']))
        other = _row(_result(), _scenario(
            ['1.5.0', '1.7.0'], ['production', 'unknown'], ['stamped', 'unknown']))

        assert one.data_format_versions == other.data_format_versions == '1.5.0,1.7.0'
        assert one.origin_classes == other.origin_classes == 'production,unknown'

    def test_the_price_basis_reaches_the_row_at_this_grain_too(self):
        """
        Per SCENARIO, because that is the grain where a mixed archive is visible.

        The run-level roll-up would answer 'order_driven,unknown' for a set in which one
        scenario read a fully re-rendered symbol and another did not — true, and useless for
        deciding which scenario's numbers to trust.
        """
        row = _row(_result(), _scenario(
            ['1.7.0'], ['production'], ['stamped'],
            bases=['order_driven', 'unknown']))

        assert row.price_bases == 'order_driven,unknown'

    def test_a_scenario_that_read_nothing_reports_empty_rather_than_a_placeholder(self):
        row = _row(_result(), _scenario([], [], []))

        assert row.data_format_versions == ''
        assert row.origin_classes == ''
        assert row.origin_evidence_grades == ''
        assert row.price_bases == ''


class TestTheDataBrokerRollUpIsDerivedOnce:
    """
    The roll-up is a DERIVE stage, not something a renderer does on the way past (§12).

    It used to live in the console: the executive summary grouped the scenario rows itself
    AND instantiated a config manager to resolve each broker's market type. Two violations of
    one rule, and the second one silently — config answers what a broker is TODAY, so the
    console could print one thing while the artifact beside it held another, and no other
    surface got the grouping at all.
    """

    @staticmethod
    def _sources(*scenarios):
        """Roll the given scenarios up the way the builder does.

        Args:
            scenarios: The scenarios to project and group

        Returns:
            The derived data-broker rows
        """
        return _data_brokers([_row(_result(), sc) for sc in scenarios],
                             _market_types({sc.data_broker_type for sc in scenarios}))

    def test_the_sources_are_grouped_with_their_market_type_resolved(self):
        sources = self._sources(
            _scenario(['1.7.0'], ['production'], ['stamped'],
                      symbol='BTCUSD', broker='kraken_spot', bases=['order_driven']),
            _scenario(['1.7.0'], ['production'], ['stamped'],
                      symbol='ETHUSD', broker='kraken_spot', bases=['order_driven']),
            _scenario(['1.5.0'], ['production'], ['attested'],
                      symbol='EURUSD', broker='mt5', bases=['quote_driven']))

        by_broker = {s.data_broker_type: s for s in sources}
        assert by_broker['kraken_spot'].scenario_count == 2
        assert by_broker['kraken_spot'].symbols == ['BTCUSD', 'ETHUSD']
        assert by_broker['kraken_spot'].market_type == 'crypto'
        assert by_broker['mt5'].market_type == 'forex'

    def test_each_row_carries_the_market_type_its_source_row_does(self):
        """
        A consumer filters ROWS, so the answer sits on the row — the same resolution the
        roll-up uses, never a second one and never a join the consumer has to write.
        """
        crypto = _scenario(['1.7.0'], ['production'], ['stamped'],
                           symbol='BTCUSD', broker='kraken_spot')
        forex = _scenario(['1.5.0'], ['production'], ['attested'],
                          symbol='EURUSD', broker='mt5')
        market_types = _market_types({'kraken_spot', 'mt5'})

        rows = [_to_row(_result(), crypto, market_types),
                _to_row(_result(error='boom'), forex, market_types)]
        sources = {s.data_broker_type: s.market_type for s in _data_brokers(rows, market_types)}

        assert [row.market_type for row in rows] == ['crypto', 'forex']
        assert all(row.market_type == sources[row.data_broker_type] for row in rows)

    def test_the_price_basis_is_de_duplicated_across_a_source_scenarios(self):
        """
        The roll-up SPLITS the rows' joined strings rather than concatenating them.

        Two scenarios over one source would otherwise report
        'order_driven,order_driven,unknown', which is not an answer to "what was this
        rendered from".
        """
        sources = self._sources(
            _scenario(['1.7.0'], ['production'], ['stamped'],
                      broker='kraken_spot', bases=['order_driven']),
            _scenario(['1.7.0'], ['production'], ['stamped'],
                      broker='kraken_spot', bases=['order_driven', 'unknown']))

        assert sources[0].price_bases == 'order_driven,unknown'

    def test_a_source_whose_scenarios_mounted_no_bars_reports_no_basis(self):
        """Empty, never a placeholder — and never the broker's declaration."""
        sources = self._sources(
            _scenario(['1.7.0'], ['production'], ['stamped'], broker='kraken_spot'))

        assert sources[0].price_bases == ''
