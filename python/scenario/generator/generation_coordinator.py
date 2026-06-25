"""
Generation Coordinator
======================
Orchestrates scenario generation: resolves config, picks the split strategy (SplitterFactory),
runs it, and serializes the result (WindowSetSerializer). Keeps the CLI to parameter reception
only (§13).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from python.configuration.generator_config_loader import GeneratorConfigLoader
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.config_types.robustness_config_types import RobustnessConfig
from python.framework.types.scenario_types.scenario_generator_types import (
    BlocksStrategyConfig,
    GenerationStrategy,
    ProfileStrategyConfig,
)
from python.framework.types.scenario_types.window_set_types import WindowSet
from python.framework.utils.time_utils import ensure_utc_aware
from python.scenario.generator.splitters.splitter_factory import SplitterFactory
from python.scenario.generator.window_set_serializer import WindowSetSerializer
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()

# CLI mode string → generation strategy (profile splitters)
_PROFILE_MODE_STRATEGY = {
    'volatility_split': GenerationStrategy.VOLATILITY_SPLIT,
    'continuous': GenerationStrategy.CONTINUOUS,
}


class GenerationCoordinator:
    """Coordinates the generate-blocks / generate-profile workflows."""

    def __init__(self):
        """Initialize the coordinator with the splitter factory + serializer."""
        self._splitter_factory = SplitterFactory()
        self._serializer = WindowSetSerializer()

    # =========================================================================
    # GENERATE-BLOCKS
    # =========================================================================

    def generate_blocks(
        self,
        broker_type: str,
        symbols: List[str],
        count: Optional[int] = None,
        block_size: Optional[int] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        output: Optional[str] = None,
        max_ticks: Optional[int] = None,
        oos_split: Optional[float] = None,
    ) -> Path:
        """
        Generate a block-based scenario set.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbols: List of symbols (first symbol used; multi-symbol not yet implemented)
            count: Max number of blocks (None = all)
            block_size: Block size in hours (None = config default)
            start: Start date filter (ISO format)
            end: End date filter (ISO format)
            output: Output filename
            max_ticks: Max ticks per scenario
            oos_split: Trailing Out-of-Sample fraction (#367) — enables robustness mode

        Returns:
            Path to the saved scenario-set config
        """
        start_dt = ensure_utc_aware(datetime.fromisoformat(start)) if start else None
        end_dt = ensure_utc_aware(datetime.fromisoformat(end)) if end else None

        config = GeneratorConfigLoader().get_generator_config()
        symbol = symbols[0]
        if len(symbols) > 1:
            vLog.warning(
                'Multi-symbol generation not yet implemented. Using first symbol.')

        # Resolve block size: CLI override → config default
        hours = block_size or config.blocks.default_block_hours
        blocks_config = BlocksStrategyConfig(
            default_block_hours=hours,
            min_block_hours=config.blocks.min_block_hours,
        )

        vLog.info('Generating scenarios using blocks strategy')
        splitter = self._splitter_factory.create_splitter(
            GenerationStrategy.BLOCKS, blocks_config)
        window_set = splitter.split(
            broker_type, symbol, start_dt, end_dt, count)
        vLog.info(f"Generated {window_set.block_count} blocks (max {hours}h each)")

        # Robustness mode (#367): --oos-split turns the block set into an IS/OOS set.
        robustness = (
            RobustnessConfig(enabled=True, oos_split=oos_split)
            if oos_split is not None else None
        )

        # Save config (robustness sets are named with a _robustness marker)
        output_file = output or self._blocks_output_name(
            symbols, robustness=robustness is not None)
        config_path = self._serializer.save_scenario_set(
            window_set, output_file, robustness)

        self._print_blocks_summary(window_set, config_path)

        return config_path

    # =========================================================================
    # GENERATE-PROFILE
    # =========================================================================

    def generate_profile(
        self,
        broker_type: str,
        symbol: str,
        start: str,
        end: str,
        mode: str = 'volatility_split',
        output: Optional[str] = None,
    ) -> Path:
        """
        Generate a profile artifact (volatility_split or continuous).

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol (single)
            start: Start date (ISO format, required)
            end: End date (ISO format, required)
            mode: Generation mode ('volatility_split' or 'continuous')
            output: Output filename (auto-generated if None)

        Returns:
            Path to the saved profile artifact
        """
        start_dt = ensure_utc_aware(datetime.fromisoformat(start))
        end_dt = ensure_utc_aware(datetime.fromisoformat(end))

        profile_config = self._resolve_profile_config(broker_type)
        splitter = self._splitter_factory.create_splitter(
            _PROFILE_MODE_STRATEGY[mode], profile_config)
        window_set = splitter.split(broker_type, symbol, start_dt, end_dt)

        output_file = output or self._profile_output_name(broker_type, symbol, mode)
        profile_path = self._serializer.save_profile(window_set, output_file)

        print(f"\n📂 Profile saved to: {profile_path}")
        print("\nℹ️  Next steps:")
        print(f"   • View profile: cat {profile_path}")
        print("   • Run with profile:")
        print(f"     python python/cli/strategy_runner_cli.py run <scenario_set>.json "
              f"--generator-profile {profile_path}")

        return profile_path

    # =========================================================================
    # GENERATE-ALL-PROFILES
    # =========================================================================

    def generate_all_profiles(
        self,
        broker_starts: Dict[str, str],
        broker_ends: Dict[str, str],
        mode: str = 'volatility_split',
    ) -> None:
        """
        Generate profiles for all symbols across all configured brokers.

        Args:
            broker_starts: Dict of broker_type → start ISO string
            broker_ends: Dict of broker_type → end ISO string
            mode: Generation mode ('volatility_split' or 'continuous')
        """
        bar_index = BarsIndexManager()
        bar_index.build_index()

        total_generated = 0
        total_failed = 0
        generated_files = []

        for broker_type in sorted(broker_starts.keys()):
            start_dt = ensure_utc_aware(datetime.fromisoformat(broker_starts[broker_type]))
            end_dt = ensure_utc_aware(datetime.fromisoformat(broker_ends[broker_type]))
            symbols = bar_index.list_symbols(broker_type)

            if not symbols:
                print(f"\n⚠️  No symbols found for {broker_type}, skipping")
                continue

            profile_config = self._resolve_profile_config(broker_type)
            splitter = self._splitter_factory.create_splitter(
                _PROFILE_MODE_STRATEGY[mode], profile_config)

            print(f"\n{'─' * 60}")
            print(f"  {broker_type}: {len(symbols)} symbols | "
                  f"{start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')} | "
                  f"max_block={profile_config.max_block_hours}h")
            print(f"{'─' * 60}")

            for symbol in symbols:
                try:
                    window_set = splitter.split(broker_type, symbol, start_dt, end_dt)
                    output_file = self._profile_output_name(broker_type, symbol, mode)
                    profile_path = self._serializer.save_profile(window_set, output_file)
                    generated_files.append(str(profile_path))
                    total_generated += 1
                except Exception as e:
                    print(f"  ❌ {symbol}: {e}")
                    vLog.error(f"Profile generation failed for {broker_type}/{symbol}: {e}")
                    total_failed += 1

        # Summary
        print(f"\n{'=' * 60}")
        print('  Batch Profile Generation Complete')
        print(f"{'=' * 60}")
        print(f"  Generated: {total_generated} profiles")
        if total_failed > 0:
            print(f"  Failed:    {total_failed} profiles")
        print(f"  Mode:      {mode}")
        for path in generated_files:
            print(f"  📂 {path}")
        print(f"{'=' * 60}\n")

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _resolve_profile_config(self, broker_type: str) -> ProfileStrategyConfig:
        """
        Resolve profile config: market_config generator_profile_defaults → generator_config fallback.

        Args:
            broker_type: Broker type identifier

        Returns:
            ProfileStrategyConfig with market-specific or fallback values
        """
        gen_config = GeneratorConfigLoader().get_generator_config()
        base = gen_config.profile

        if base is None:
            raise ValueError(
                'No "profile" section found in generator_config.json. '
                'Add profile configuration before generating profiles.'
            )

        market_config = MarketConfigManager()
        profile_defaults = market_config.get_generator_profile_defaults_for_broker(broker_type)

        if profile_defaults is not None:
            return ProfileStrategyConfig(
                min_block_hours=profile_defaults.min_block_hours,
                max_block_hours=profile_defaults.max_block_hours,
                atr_percentile_threshold=profile_defaults.atr_percentile_threshold,
                split_algorithm=base.split_algorithm,
            )

        return base

    def _blocks_output_name(self, symbols: List[str], robustness: bool = False) -> str:
        """
        Generate output filename for a block set.

        Args:
            symbols: List of symbols
            robustness: When True, mark the file as a robustness (IS/OOS) set (#367)

        Returns:
            Filename string
        """
        if len(symbols) == 1:
            symbol_part = symbols[0]
        else:
            symbol_part = f"multi_{len(symbols)}"

        suffix = 'blocks_robustness' if robustness else 'blocks'
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
        return f"{symbol_part}_{suffix}_{timestamp}.json"

    def _profile_output_name(self, broker_type: str, symbol: str, mode: str) -> str:
        """
        Generate output filename for a profile artifact.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            mode: Generation mode

        Returns:
            Filename string
        """
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
        mode_short = 'vol' if mode == 'volatility_split' else 'cont'
        return f"{broker_type}_{symbol}_profile_{mode_short}_{timestamp}.json"

    def _print_blocks_summary(self, window_set: WindowSet, config_path: Path) -> None:
        """
        Print a short post-save summary for a block set.

        Args:
            window_set: The generated window set
            config_path: Path to the saved config
        """
        print('\n' + '=' * 60)
        print(f"✅ Generated {window_set.block_count} blocks")
        print('=' * 60)

        print(f"\nSymbol:     {window_set.symbol}")
        print(f"Strategy:   {window_set.strategy.value}")

        if window_set.windows:
            first_start = min(w.start_time for w in window_set.windows)
            last_end = max(w.end_time for w in window_set.windows)
            total_hours = window_set.total_coverage_hours
            avg_hours = total_hours / window_set.block_count

            print(
                f"Time range: {first_start.strftime('%Y-%m-%d')} → {last_end.strftime('%Y-%m-%d')}")
            print(
                f"Total:      {total_hours:.0f}h ({avg_hours:.1f}h avg/block)")

        print(f"\n📂 Config saved to: {config_path}")
        print("\nℹ️  Next steps:")
        print(f"   • View config: cat {config_path}")
        print("   • Run test:    python strategy_runner.py")
        print('=' * 60 + '\n')
