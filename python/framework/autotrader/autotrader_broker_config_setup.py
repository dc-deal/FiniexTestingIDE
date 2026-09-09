"""
FiniexTestingIDE - AutoTrader Broker Config Setup
Loads the session's broker config and attaches the right adapter
(mock static JSON / live dynamic fetch with runtime cache / live static).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from python.configuration.autotrader.abstract_broker_config_fetcher import (
    AbstractBrokerConfigFetcher,
)
from python.configuration.autotrader.broker_config_fetcher_factory import BrokerConfigFetcherFactory
from python.configuration.autotrader.kraken_config_fetcher import get_runtime_cache_path
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.testing.mock_broker_adapter import MockBrokerAdapter
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.config_types.market_config_types import (
    BrokerEntryConfig,
    ConfigMode,
)
from python.framework.types.trading_env_types.broker_types import BrokerType


def create_broker_config(
    config: AutoTraderConfig, logger: ScenarioLogger, balances: Dict[str, float]
) -> BrokerConfig:
    """
    Load broker config and attach appropriate adapter.

    For adapter_type='mock': always uses static JSON + MockBrokerAdapter (balances untouched —
    the mock replays a scenario and uses its scenario_settings.balances).
    For adapter_type='live': routes by config_mode from market_config.json. The live paths FILL
    `balances` for the symbol's authoritative base/quote currencies (#265) — the profile declares
    no balances; dynamic fetches the real amounts from the broker, static seeds 0.
      - DYNAMIC: fetches from broker API with runtime cache + staleness guard
      - STATIC:  loads static JSON with info log (no API fetch)

    Args:
        config: AutoTrader configuration
        logger: ScenarioLogger for status messages
        balances: Balances dict, filled in place for live (mock: left untouched)

    Returns:
        BrokerConfig with adapter
    """
    # Mock always uses static JSON, regardless of config_mode
    if config.adapter_type == 'mock':
        broker_config_path = MarketConfigManager().get_broker_config_path(config.broker_type)
        broker_config = BrokerConfigFactory.build_broker_config(broker_config_path)
        mock_adapter = MockBrokerAdapter(
            broker_config=broker_config.adapter.broker_config
        )
        result = BrokerConfig(
            broker_type=broker_config.broker_type,
            adapter=mock_adapter
        )
        _log_broker_config_loaded(result, broker_config_path, logger)
        return result

    # Live: route by config_mode
    config_mode = MarketConfigManager().get_config_mode(config.broker_type)
    if config_mode == ConfigMode.DYNAMIC:
        return _create_live_broker_config_dynamic(config, logger, balances)
    return _create_live_broker_config_static(config, logger, balances)


def _apply_fee_tier(
    config_dict: Dict[str, Any],
    entry: BrokerEntryConfig,
    fetcher: AbstractBrokerConfigFetcher,
    symbol: str,
    logger: ScenarioLogger,
) -> None:
    """
    Ask the venue which fee tier this account is on, and say so when it disagrees (#337).

    TWO separate jobs, and the second one runs even when the first is switched off:

    APPLY — with `auto_detect_fee_tier` on, the fetched rates replace the declared ones for
    this session. A venue that prices per account volume knows the answer; a number written
    into a config file is a guess about which tier the account sits in.

    REPORT — whether or not they are applied, a divergence from the declared rates is a
    WARNING. That is the whole reason this is not silent: measured 2026-09-08, the declared
    0.40/0.25 were HALF the account's real 0.80/0.40, and nothing in any run said so. The
    warning names both numbers and what to do, because the backtest reads the git-tracked
    seed on purpose and only a human can re-freeze it.

    A fetcher that cannot answer returns None and nothing happens — the session keeps its
    declared rates, which is the state this function was added to.

    Args:
        config_dict: The broker config under construction; its `fee_structure` is updated
            in place when the rates are applied
        entry: The broker's market_config entry, carrying the opt-in
        fetcher: The broker's config fetcher — answers only if its venue has account tiers
        symbol: The symbol this session trades
        logger: Session channel for the divergence warning (§35)
    """
    tier = fetcher.fetch_fee_tier(symbol)
    if tier is None:
        return

    declared = config_dict.get('fee_structure', {})
    declared_maker = declared.get('maker_fee')
    declared_taker = declared.get('taker_fee')
    diverges = (declared_maker != tier.maker_pct) or (declared_taker != tier.taker_pct)

    if diverges:
        action = (
            'applied for this session'
            if entry.auto_detect_fee_tier
            else 'NOT applied — auto_detect_fee_tier is off, so this session still prices '
                 'with the declared rates'
        )
        logger.warning(
            f'⚠️ Fee tier mismatch on {symbol} ({tier.pair}): the account is charged '
            f'maker {tier.maker_pct}% / taker {tier.taker_pct}%, the config declares '
            f'maker {declared_maker}% / taker {declared_taker}% — {action}. '
            f'The BACKTEST reads the git-tracked seed, so it keeps the declared rates '
            f'until someone re-freezes them: update '
            f'configs/brokers/<broker>/<broker>_broker_config.json and commit.'
        )

    if not entry.auto_detect_fee_tier:
        return

    declared['maker_fee'] = tier.maker_pct
    declared['taker_fee'] = tier.taker_pct
    config_dict['fee_structure'] = declared
    logger.info(
        f'💱 Fee tier applied for {symbol} ({tier.pair}): '
        f'maker {tier.maker_pct}% / taker {tier.taker_pct}%'
        + (f' — next tier {tier.next_maker_pct}% / {tier.next_taker_pct}% '
           f'above a 30-day volume of {tier.next_volume_threshold}'
           if tier.next_volume_threshold else '')
    )


def _create_live_broker_config_dynamic(
    config: AutoTraderConfig, logger: ScenarioLogger, balances: Dict[str, float]
) -> BrokerConfig:
    """
    Fetch broker config from broker API with runtime cache and staleness guard.

    Lookup chain:
      1. Cache fresh (< 7 days)  → use cache, no API call
      2. Cache stale (7-30 days) → try API refresh; on failure use cache + warning
      3. Cache very stale (>30d) → try API refresh; on failure use cache + strong warning
      4. No cache               → must fetch from API; failure = hard error

    Args:
        config: AutoTrader configuration
        logger: ScenarioLogger for status messages

    Returns:
        BrokerConfig with live-enabled KrakenAdapter
    """
    entry = MarketConfigManager().get_broker_entry(config.broker_type)

    # Profile-level dry_run override (#332): a profile may scope dry_run to itself
    # instead of relying on the global market_config default. Deliberately LOUD —
    # overriding (especially forcing LIVE) reintroduces the forget-risk we originally
    # avoided by keeping dry_run global, so it is never silent.
    dry_run = config.dry_run if config.dry_run is not None else entry.dry_run
    if config.dry_run is not None and config.dry_run != entry.dry_run:
        note = (
            'LIVE TRADING — real orders will be placed'
            if not config.dry_run else 'validate-only'
        )
        logger.warning(
            f"⚠️ dry_run OVERRIDE by profile '{config.name}': dry_run={config.dry_run} "
            f"(market_config default={entry.dry_run}) → {note}"
        )
        print(f'  ⚠️  dry_run OVERRIDE by profile → dry_run={config.dry_run} ({note})')

    logger.info(f'🔧 Broker config: {config.broker_type} (dry_run={dry_run})')
    print(f'  ▸ Broker: {config.broker_type} (dry_run={dry_run})')

    fetcher = BrokerConfigFetcherFactory.create(
        broker_type=config.broker_type,
        logger=logger,
    )

    # === Fetch broker config with cache + staleness guard ===
    config_dict = fetcher.fetch_broker_config_with_cache(
        symbol=config.symbol,
        broker_type=config.broker_type,
    )

    # ONE declared source for both pipelines (#337). The runtime cache's own fee block comes
    # from a literal inside the config fetcher, so a re-frozen seed would never have reached a
    # live session. Starting from the seed means live and the backtest agree about what was
    # EXPECTED, and the venue's answer below is the only thing that may differ from it.
    config_dict['fee_structure'] = BrokerConfigFactory.fee_structure_from(
        MarketConfigManager().get_broker_config_path(config.broker_type))

    _apply_fee_tier(config_dict, entry, fetcher, config.symbol, logger)

    # Build BrokerConfig from fetched dict
    broker_config = BrokerConfigFactory.from_serialized_dict(
        broker_type=BrokerType(config.broker_type),
        config_dict=config_dict,
    )

    # === Fetch account balances (no fallback — must succeed for live) ===
    # Currencies come from the symbol's authoritative base/quote (#265) — the profile declares no
    # balances; the broker provides the real amounts.
    spec = broker_config.get_symbol_specification(config.symbol)
    for currency in (spec.base_currency, spec.quote_currency):
        balance = fetcher.fetch_account_balance(currency)
        if balance is None:
            raise ConnectionError(
                f"Could not fetch account balance for '{currency}' from Kraken API. "
                f"Live trading requires a confirmed balance. "
                f"Check API credentials and account permissions."
            )
        logger.info(f'💰 Live balance: {balance} {currency}')
        print(f'  ▸ Live balance: {balance} {currency}')
        balances[currency] = balance

    # === Enable live execution on adapter ===
    broker_config.adapter.enable_live(
        credentials_file=entry.credentials_file,
        dry_run=dry_run,
        transport=entry.broker_transport,
    )
    mode_label = 'DRY RUN (validate only)' if dry_run else 'LIVE TRADING'
    logger.info(f'🚀 Mode: {mode_label}')
    print(f'  ▸ Mode: {mode_label}')

    # Pass the actual runtime cache file path so _log_broker_config_loaded can
    # surface cache age in the startup output (helps diagnose stale-fee issues).
    cache_path = str(get_runtime_cache_path(config.broker_type))
    _log_broker_config_loaded(broker_config, cache_path, logger)
    return broker_config


def _create_live_broker_config_static(
    config: AutoTraderConfig, logger: ScenarioLogger, balances: Dict[str, float]
) -> BrokerConfig:
    """
    Load broker config from static JSON for live sessions with config_mode=static.

    Symbol specs are not refreshed from the broker API. Use config_mode=dynamic
    in market_config.json to enable auto-refresh via runtime cache.

    Args:
        config: AutoTrader configuration
        logger: ScenarioLogger for status messages

    Returns:
        BrokerConfig (no live adapter — caller must call enable_live() separately)
    """
    broker_config_path = MarketConfigManager().get_broker_config_path(config.broker_type)
    logger.info(
        f'ℹ️  Using static broker config for live session.\n'
        f'    File:   {broker_config_path}\n'
        f'    Note:   config_mode=static — symbol specs are not refreshed from the broker API.\n'
        f'    Tip:    Set \"config_mode\": \"dynamic\" for {config.broker_type} in '
        f'configs/market_config.json to enable auto-refresh.'
    )
    print(f'  ▸ Static broker config: {broker_config_path}')

    broker_config = BrokerConfigFactory.build_broker_config(broker_config_path)
    # Static live has no API fetch — seed the symbol's base/quote at 0 (#265); real amounts
    # come from the broker/operator at runtime.
    spec = broker_config.get_symbol_specification(config.symbol)
    for currency in (spec.base_currency, spec.quote_currency):
        balances.setdefault(currency, 0.0)
    _log_broker_config_loaded(broker_config, broker_config_path, logger)
    return broker_config


def _log_broker_config_loaded(broker_config: BrokerConfig, source: str, logger: ScenarioLogger) -> None:
    """
    Log broker config load event with config hash, active symbol count, and
    fee rates currently in effect. For runtime-cached configs the cache file
    age is surfaced — makes stale-fee scenarios visible at first glance
    (diagnoses cases where a stale cache silently overrides updated defaults).

    Args:
        broker_config: Loaded BrokerConfig
        source: File path or description of config source
        logger: ScenarioLogger for status messages
    """
    config_hash = broker_config.config_hash
    raw_config = broker_config.adapter.broker_config
    symbols = raw_config.get('symbols', {})
    active_count = sum(
        1 for s in symbols.values()
        if s.get('_active', True)  # missing _active defaults to active (legacy format)
    )
    # Labelled, because the fetcher prints `symbols_hash` in the same bracket shape a few
    # lines earlier. The two used to be one value and diverge as soon as a fee block exists.
    hash_tag = f' [config {config_hash}]' if config_hash else ''

    # Fee rates currently in effect (drift-audit empirics revealed cache-vs-actual
    # mismatches were silent before this line existed).
    fee_structure = raw_config.get('fee_structure', {})
    maker = fee_structure.get('maker_fee')
    taker = fee_structure.get('taker_fee')
    fee_line = (
        f'maker={maker}% / taker={taker}%'
        if maker is not None and taker is not None
        else 'no fee_structure block'
    )

    # Cache age — visible only if source resolves to an existing file
    source_path = Path(source) if source else None
    age_part = ''
    if source_path and source_path.exists():
        age_days = (datetime.now(timezone.utc).timestamp() - source_path.stat().st_mtime) / 86400.0
        age_part = f'  [cache age={age_days:.1f}d]'

    logger.info(
        f'🗄  Broker config loaded: {broker_config.broker_type.value}{hash_tag}\n'
        f'    Source:    {source}{age_part}\n'
        f'    Symbols:   {active_count} active\n'
        f'    Fee rates: {fee_line}'
    )
    print(f'  ▸ Broker config: {broker_config.broker_type.value}{hash_tag} — {active_count} active symbols')
    print(f'  ▸ Fee rates:     {fee_line}{age_part}')
