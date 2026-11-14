"""
Recorded scenario builder (#512) — a backtest of a session's window, built from what the session
recorded.

A backtest built from the configuration files of the day would run what the files say now: the
runtime cache refreshed since, `app_config.json` edited since, a default moved by a release. The
comparison needs what the SESSION ran, so every input comes from its record — the strategy from its
rendered profile, the broker configuration from the document it froze, the warmup from the history
it started from, the account from its first state frame — and the simulator keeps the release's
own model, written out explicitly so that no workspace override can change it unseen.

Every block of a rendered AutoTrader profile is declared here: it reaches the backtest somewhere,
or it has no counterpart and says why. A block nobody declared fails the completeness test before
it can silently go missing from the comparison.
"""

import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict

from python.configuration.app_config_manager import AppConfigManager
from python.framework.autotrader.run_record.rendered_profile_builder import NOT_A_BLOCK
from python.framework.exceptions.rendered_config_errors import RecordedScenarioRefusedError
from python.framework.factory.strategy_config_renderer import LOGIC_TYPE_KEY, WORKER_TYPE_KEY
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.autotrader_defaults_config_types import (
    AutotraderExecutionDefaults,
    IndicatorSeriesDefaults,
    StateFramesDefaults,
)
from python.framework.types.config_types.backtesting_config_types import (
    DefaultScenarioExecutionConfig,
    TradeSimulatorDefaults,
)
from python.framework.types.config_types.verification_config_types import VerificationConfig
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.types.scenario_types.recorded_scenario_types import RecordedScenarioInputs
from python.framework.utils.file_utils import create_directory

# The blocks of a rendered profile that reach the backtest, and where.
RENDERED_BLOCK_TARGETS: Dict[str, str] = {
    'symbol': 'the scenario symbol',
    'broker_type': 'the scenario data_broker_type — a venue session reads its own broker',
    'scenario_settings': 'the scenario data_broker_type, where a mock replay declared another archive',
    'execution': 'field by field, RENDERED_EXECUTION_TARGETS',
    'state_frames': ("execution_config.state_frames, switched on with the session's cadence — the "
                     'backtest records its frames on the slots the session recorded on (#579)'),
    'indicator_series': ('execution_config.indicator_series, as the session ran it — a drawing '
                         'that changes no decision, so the backtest draws the panes the session '
                         'drew (#379)'),
    'order_guard': 'the order_guard block — one model in both pipelines',
}

# The blocks with no counterpart in a backtest, and why.
RENDERED_BLOCKS_WITHOUT_COUNTERPART: Dict[str, str] = {
    'profile_name': "the session's identity; the scenario is named for its segment",
    'bot_id': 'the carry-over identity; a backtest runs through restarts as one process',
    'adapter_type': 'the backtest simulates the venue',
    'tick_source': 'the backtest replays the archive',
    'clipping_monitor': "a live measurement; a backtest's clipping is an input of data preparation",
    'deployment': 'how sessions chain; a backtest runs through restarts as one account',
    'display': 'the console only',
    'safety': ('the live circuit breaker; a backtest has none, so a decision it blocked live is '
               'one-sided, with safety_blocked in its state frame'),
    'drift_audit': "compares the venue's reports; the simulation has no venue",
    'reconciliation': "compares the venue's account; the simulation has no venue",
    'api_monitor': 'the latency of venue calls',
    'state_persistence': "the algo's carry-over; a backtest runs through restarts as one process",
    'cold_start': "adopts the venue's state at boot; a backtest starts flat",
    'session_end': ('what a session does with its orders and positions at its end; the '
                    'comparison ends at a booking boundary'),
    'shutdown': 'the waits of a stop',
    'capital': "the account's exclusivity, which only the live risk limits read",
    'operator_alerts': 'the channels to the operator',
    'dry_run': 'the backtest places no order at a venue',
}

# The execution fields that reach the backtest, and where.
RENDERED_EXECUTION_TARGETS: Dict[str, str] = {
    'parallel_workers': 'execution_config.parallel_workers',
    'heartbeat_interval_ms': 'execution_config.heartbeat_interval_ms — the ghost-pass cadence',
    'verification': ('execution_config.verification — switches that change no result by design '
                     '(#653), carried as the session ran them'),
}

# The execution fields with no counterpart in a backtest, and why.
RENDERED_EXECUTION_WITHOUT_COUNTERPART: Dict[str, str] = {
    'bar_max_history': 'app-level in a backtest; a session that kept another depth is refused',
    'market_data_stale_after_s': "the venue feed's staleness",
    'order_timeout_seconds': "the venue's order timeout",
    'venue_read_settle_seconds': 'the settling of a venue read',
    'unresolved_resolution': "resolving a venue's lost answer",
    'performance_tracking': 'profiling switches; they change no decision',
}


class RecordedScenarioBuilder:
    """Builds and writes the scenario set of a backtest of a session's window."""

    @staticmethod
    def get_rendered_blocks() -> frozenset:
        """
        The blocks a rendered AutoTrader profile carries — the set the declarations above answer.

        Returns:
            The block names
        """
        return frozenset(field.name for field in fields(AutoTraderConfig)
                         if field.name not in NOT_A_BLOCK)

    @staticmethod
    def build(inputs: RecordedScenarioInputs) -> Dict[str, Any]:
        """
        The scenario set of one segment's backtest.

        Args:
            inputs: The segment's recorded inputs

        Returns:
            The scenario set document, JSON-serialisable
        """
        blocks = inputs.rendered_profile.get('blocks')
        strategy = inputs.rendered_profile.get('strategy_config')
        if not isinstance(blocks, dict) or not isinstance(strategy, dict):
            raise RecordedScenarioRefusedError(
                f'{inputs.scenario_name}: the rendered profile lacks its blocks or its strategy — '
                f'it is not a rendered AutoTrader profile.')
        execution = blocks.get('execution', {})
        app_depth = AppConfigManager().get_bar_max_history()
        if execution.get('bar_max_history') != app_depth:
            raise RecordedScenarioRefusedError(
                f"{inputs.scenario_name}: the session kept {execution.get('bar_max_history')} bars "
                f'per timeframe, a backtest keeps {app_depth} (app_config.json) — a worker reading '
                f'its history would see another depth.')
        declared_archive = (blocks.get('scenario_settings') or {}).get('data_broker_type', '')
        document: Dict[str, Any] = {
            'scenario_set_name': inputs.scenario_set_name,
            'description': ('A backtest of a session segment, built from what the session recorded '
                            '(#512).'),
            'global': {
                'strategy_config': RecordedScenarioBuilder._strategy_for_factories(strategy),
                # The release's execution model in full, with what the session ran over it: a
                # workspace override of the backtesting defaults must reach no field
                'execution_config': {
                    **DefaultScenarioExecutionConfig().model_dump(mode='json'),
                    'parallel_workers': execution.get('parallel_workers'),
                    'heartbeat_interval_ms': execution.get('heartbeat_interval_ms'),
                    'verification': execution.get(
                        'verification', VerificationConfig().model_dump(mode='json')),
                    # The frames the divergence report reads, on the slots the session recorded on
                    'state_frames': {
                        'enabled': True,
                        'cadence_timeframe': (blocks.get('state_frames') or {}).get(
                            'cadence_timeframe', StateFramesDefaults().cadence_timeframe),
                    },
                    'indicator_series': {
                        'enabled': (blocks.get('indicator_series') or {}).get(
                            'enabled', IndicatorSeriesDefaults().enabled),
                    },
                },
                # The release's own simulator in full, every field written: a workspace override
                # of the backtesting defaults must not reach the comparison. The account currency
                # stays derived from the symbol's quote currency, as the session derived it
                'trade_simulator_config': TradeSimulatorDefaults(
                    balances=dict(inputs.balances)).model_dump(mode='json'),
                'order_guard': dict(blocks.get('order_guard', {})),
            },
            'scenarios': [{
                'scenario_name': inputs.scenario_name,
                'symbol': blocks.get('symbol'),
                'data_broker_type': declared_archive or blocks.get('broker_type'),
                'start_date': inputs.start.isoformat(),
                'end_date': inputs.end.isoformat(),
                'warmup_from_run': inputs.warmup_from_run,
                'broker_config_document': inputs.broker_config_document,
            }],
        }
        # Absent means regular: a set in a user-owned directory may not declare a purpose at all
        if inputs.run_purpose is not RunPurpose.REGULAR:
            document['run_purpose'] = inputs.run_purpose.value
        return document

    @staticmethod
    def write(document: Dict[str, Any], directory: Path) -> Path:
        """
        Write a built scenario set where the comparison keeps it, named after the set.

        Args:
            document: The scenario set from build()
            directory: Where the comparison keeps its files

        Returns:
            The written file
        """
        create_directory(directory, 'the scenario sets a comparison backtests its segments with')
        path = directory / f"{document['scenario_set_name']}.json"
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(document, handle, indent=2)
        return path

    @staticmethod
    def _strategy_for_factories(rendered_strategy: Dict[str, Any]) -> Dict[str, Any]:
        """
        A rendered strategy as the factories take it: the type key the renderer projected into
        each component's parameters removed again — the factory injects it itself.

        Args:
            rendered_strategy: The rendered profile's `strategy_config`

        Returns:
            The strategy, every parameter as rendered
        """
        strategy = dict(rendered_strategy)
        logic = dict(strategy.get('decision_logic_config', {}))
        logic.pop(LOGIC_TYPE_KEY, None)
        strategy['decision_logic_config'] = logic
        strategy['workers'] = {
            name: {key: value for key, value in parameters.items() if key != WORKER_TYPE_KEY}
            for name, parameters in strategy.get('workers', {}).items()
        }
        return strategy

    @staticmethod
    def get_execution_fields() -> frozenset:
        """
        The fields of a session's execution block — the set the execution declarations answer.

        Returns:
            The field names
        """
        return frozenset(AutotraderExecutionDefaults.model_fields)
