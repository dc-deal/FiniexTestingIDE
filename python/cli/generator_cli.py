"""
Generator CLI
==============
Command-line interface for block generation.

Commands:
- generate: Generate scenario configs based on data coverage analysis
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from python.configuration.generator_config_loader import GeneratorConfigLoader
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.discoveries.volatility_profile_analyzer.volatility_profile_analyzer import VolatilityProfileAnalyzer
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    GenerationResult,
    GenerationStrategy,
    ProfileStrategyConfig,
)
from python.framework.utils.time_utils import ensure_utc_aware
from python.scenario.generator.blocks_generator import BlocksGenerator
from python.scenario.generator.profile_generator import ProfileGenerator
from python.scenario.generator.profile_saver import ProfileSaver
from python.scenario.generator.scenario_generator_config_saver import ScenarioGeneratorConfigSaver
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class GeneratorCli:
    """
    CLI handler for block generation.
    """

    def __init__(self):
        """Initialize CLI handler."""
        pass

    # =========================================================================
    # GENERATE-BLOCKS COMMAND
    # =========================================================================

    def cmd_generate_blocks(
        self,
        broker_type: str,
        symbols: List[str],
        count: Optional[int] = None,
        block_size: Optional[int] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        output: Optional[str] = None,
        max_ticks: Optional[int] = None
    ) -> None:
        """
        Generate scenario configurations.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbols: List of symbols
            count: Number of blocks (max limit, None=all)
            block_size: Block size in hours
            start: Start date filter (ISO format)
            end: End date filter (ISO format)
            output: Output filename
            max_ticks: Max ticks per scenario
        """
        # Parse date filters
        start_dt: Optional[datetime] = None
        end_dt: Optional[datetime] = None
        if start:
            start_dt = ensure_utc_aware(datetime.fromisoformat(start))
        if end:
            end_dt = ensure_utc_aware(datetime.fromisoformat(end))
        try:
            config = GeneratorConfigLoader().get_generator_config()
            symbol = symbols[0]
            if len(symbols) > 1:
                vLog.warning(
                    'Multi-symbol generation not yet implemented. Using first symbol.')

            # Build volatility profile (for metadata)
            analyzer = VolatilityProfileAnalyzer()
            analyzer.build_profile(broker_type, symbol)

            # Generate blocks
            hours = block_size or config.blocks.default_block_hours
            vLog.info(f"Generating scenarios using blocks strategy")

            blocks_gen = BlocksGenerator(config)
            scenarios = blocks_gen.generate(broker_type, symbol, hours, count)
            vLog.info(f"Generated {len(scenarios)} blocks (max {hours}h each)")

            result = self._build_generation_result(
                symbol, scenarios, config
            )

            # Save config
            output_file = output or self._generate_output_name(symbols)

            saver = ScenarioGeneratorConfigSaver()
            config_path = saver.save_config(result, output_file)

            # Print summary
            self._print_generation_summary(result, config_path)

        except Exception as e:
            print(f"❌ Generation failed: {e}")
            vLog.error(f"Generation failed: {e}")
            raise

    # =========================================================================
    # GENERATE-PROFILE COMMAND
    # =========================================================================

    def cmd_generate_profile(
        self,
        broker_type: str,
        symbol: str,
        start: str,
        end: str,
        mode: str = 'volatility_split',
        output: Optional[str] = None
    ) -> None:
        """
        Generate a profile artifact with ATR-minima splitting.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol (single)
            start: Start date (ISO format, required)
            end: End date (ISO format, required)
            mode: Generation mode ('volatility_split' or 'continuous')
            output: Output filename (auto-generated if None)
        """
        start_dt = ensure_utc_aware(datetime.fromisoformat(start))
        end_dt = ensure_utc_aware(datetime.fromisoformat(end))

        try:
            # Resolve profile config: market_config overrides → generator_config fallback
            profile_config = self._resolve_profile_config(broker_type)

            generator = ProfileGenerator(
                config=profile_config
            )

            profile = generator.generate(
                broker_type=broker_type,
                symbol=symbol,
                start_time=start_dt,
                end_time=end_dt,
                mode=mode,
            )

            # Save profile
            output_file = output or self._generate_profile_output_name(
                broker_type, symbol, mode
            )
            saver = ProfileSaver()
            profile_path = saver.save_profile(profile, output_file)

            print(f"\n📂 Profile saved to: {profile_path}")
            print(f"\nℹ️  Next steps:")
            print(f"   • View profile: cat {profile_path}")
            print(f"   • Run with profile:")
            print(f"     python python/cli/strategy_runner_cli.py run <scenario_set>.json "
                  f"--generator-profile {profile_path}")

        except Exception as e:
            print(f"❌ Profile generation failed: {e}")
            vLog.error(f"Profile generation failed: {e}")
            raise

    def _generate_profile_output_name(
        self,
        broker_type: str,
        symbol: str,
        mode: str
    ) -> str:
        """
        Generate output filename for profile.

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

    # =========================================================================
    # GENERATE-ALL-PROFILES COMMAND
    # =========================================================================

    def cmd_generate_all_profiles(
        self,
        broker_starts: Dict[str, str],
        broker_ends: Dict[str, str],
        mode: str = 'volatility_split'
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
        saver = ProfileSaver()

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
            print(f"\n{'─' * 60}")
            print(f"  {broker_type}: {len(symbols)} symbols | "
                  f"{start_dt.strftime('%Y-%m-%d')} → {end_dt.strftime('%Y-%m-%d')} | "
                  f"max_block={profile_config.max_block_hours}h")
            print(f"{'─' * 60}")

            generator = ProfileGenerator(config=profile_config)

            for symbol in symbols:
                try:
                    profile = generator.generate(
                        broker_type=broker_type,
                        symbol=symbol,
                        start_time=start_dt,
                        end_time=end_dt,
                        mode=mode,
                    )

                    output_file = self._generate_profile_output_name(
                        broker_type, symbol, mode
                    )
                    profile_path = saver.save_profile(profile, output_file)
                    generated_files.append(str(profile_path))
                    total_generated += 1

                except Exception as e:
                    print(f"  ❌ {symbol}: {e}")
                    vLog.error(f"Profile generation failed for {broker_type}/{symbol}: {e}")
                    total_failed += 1

        # Summary
        print(f"\n{'=' * 60}")
        print(f"  Batch Profile Generation Complete")
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
        Resolve profile config: market_config profile_defaults → generator_config fallback.

        Args:
            broker_type: Broker type identifier

        Returns:
            ProfileStrategyConfig with market-specific or fallback values
        """
        # Start with generator_config.json as base
        gen_config = GeneratorConfigLoader().get_generator_config()
        base = gen_config.profile

        if base is None:
            raise ValueError(
                'No "profile" section found in generator_config.json. '
                'Add profile configuration before generating profiles.'
            )

        # Override with market-specific defaults if available
        market_config = MarketConfigManager()
        profile_defaults = market_config.get_profile_defaults_for_broker(broker_type)

        if profile_defaults is not None:
            return ProfileStrategyConfig(
                min_block_hours=profile_defaults.min_block_hours,
                max_block_hours=profile_defaults.max_block_hours,
                atr_percentile_threshold=profile_defaults.atr_percentile_threshold,
                split_algorithm=base.split_algorithm,
            )

        return base

    def _build_generation_result(
        self,
        symbol: str,
        scenarios: list,
        config: object
    ) -> GenerationResult:
        """
        Build GenerationResult from generated scenarios.

        Args:
            symbol: Trading symbol
            scenarios: Generated ScenarioCandidate list
            config: GeneratorConfig used

        Returns:
            GenerationResult
        """
        total_ticks = sum(s.estimated_ticks for s in scenarios)
        avg_ticks = total_ticks / len(scenarios) if scenarios else 0

        regime_coverage = {regime: 0 for regime in VolatilityRegime}
        for s in scenarios:
            regime_coverage[s.regime] += 1

        session_coverage = {session: 0 for session in TradingSession}
        for s in scenarios:
            session_coverage[s.session] += 1

        return GenerationResult(
            symbol=symbol,
            strategy=GenerationStrategy.BLOCKS,
            scenarios=scenarios,
            total_estimated_ticks=total_ticks,
            avg_ticks_per_scenario=avg_ticks,
            regime_coverage=regime_coverage,
            session_coverage=session_coverage,
            generated_at=datetime.now(timezone.utc),
            config_used=config
        )

    def _generate_output_name(self, symbols: List[str]) -> str:
        """
        Generate output filename from symbols.

        Args:
            symbols: List of symbols

        Returns:
            Filename string
        """
        if len(symbols) == 1:
            symbol_part = symbols[0]
        else:
            symbol_part = f"multi_{len(symbols)}"

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
        return f"{symbol_part}_blocks_{timestamp}.json"

    def _print_generation_summary(self, result: GenerationResult, config_path: Path) -> None:
        """
        Print generation result summary.

        Args:
            result: GenerationResult
            config_path: Path to saved config
        """
        print('\n' + '=' * 60)
        print(f"✅ Generated {len(result.scenarios)} blocks")
        print('=' * 60)

        print(f"\nSymbol:     {result.symbol}")
        print(f"Strategy:   {result.strategy.value}")

        if result.scenarios:
            first_start = min(s.start_time for s in result.scenarios)
            last_end = max(s.end_time for s in result.scenarios)
            total_hours = sum(
                (s.end_time - s.start_time).total_seconds() / 3600
                for s in result.scenarios
            )
            avg_hours = total_hours / len(result.scenarios)

            print(
                f"Time range: {first_start.strftime('%Y-%m-%d')} → {last_end.strftime('%Y-%m-%d')}")
            print(
                f"Total:      {total_hours:.0f}h ({avg_hours:.1f}h avg/block)")

        print(f"\n📂 Config saved to: {config_path}")

        print("\nℹ️  Next steps:")
        print(f"   • View config: cat {config_path}")
        print(f"   • Run test:    python strategy_runner.py")
        print('=' * 60 + '\n')


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Generator CLI — block generation for backtesting scenarios',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # GENERATE command
    # ─────────────────────────────────────────────────────────────────────────
    generate_parser = subparsers.add_parser(
        'generate-blocks',
        help='Generate block-based scenario configurations'
    )
    generate_parser.add_argument(
        'broker_type',
        help='Broker type (e.g., mt5, kraken_spot)'
    )
    generate_parser.add_argument(
        'symbols',
        nargs='+',
        help='Symbols for scenarios (e.g., EURUSD GBPUSD)'
    )
    generate_parser.add_argument(
        '--count',
        type=int,
        default=None,
        help='Max number of blocks to generate (None=all blocks)'
    )
    generate_parser.add_argument(
        '--block-size',
        type=int,
        default=None,
        help='Max block size in hours (default: 6, min: 1)'
    )
    generate_parser.add_argument(
        '--start',
        type=str,
        default=None,
        help='Start date filter (ISO format)'
    )
    generate_parser.add_argument(
        '--end',
        type=str,
        default=None,
        help='End date filter (ISO format)'
    )
    generate_parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output filename'
    )
    generate_parser.add_argument(
        '--max-ticks',
        type=int,
        default=None,
        help='Max ticks per scenario'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # GENERATE-PROFILE command
    # ─────────────────────────────────────────────────────────────────────────
    profile_parser = subparsers.add_parser(
        'generate-profile',
        help='Generate a profile artifact with ATR-minima splitting'
    )
    profile_parser.add_argument(
        'broker_type',
        help='Broker type (e.g., mt5, kraken_spot)'
    )
    profile_parser.add_argument(
        'symbol',
        help='Trading symbol (single, e.g., EURUSD)'
    )
    profile_parser.add_argument(
        '--start',
        type=str,
        required=True,
        help='Start date (ISO format, required)'
    )
    profile_parser.add_argument(
        '--end',
        type=str,
        required=True,
        help='End date (ISO format, required)'
    )
    profile_parser.add_argument(
        '--mode',
        type=str,
        choices=['volatility_split', 'continuous'],
        default='volatility_split',
        help='Generation mode (default: volatility_split)'
    )
    profile_parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output filename'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # GENERATE-ALL-PROFILES command
    # ─────────────────────────────────────────────────────────────────────────
    all_profiles_parser = subparsers.add_parser(
        'generate-all-profiles',
        help='Generate profiles for all symbols across all brokers'
    )
    all_profiles_parser.add_argument(
        '--mt5-start',
        type=str,
        required=True,
        help='Start date for mt5 symbols (ISO format)'
    )
    all_profiles_parser.add_argument(
        '--mt5-end',
        type=str,
        required=True,
        help='End date for mt5 symbols (ISO format)'
    )
    all_profiles_parser.add_argument(
        '--kraken-spot-start',
        type=str,
        required=True,
        help='Start date for kraken_spot symbols (ISO format)'
    )
    all_profiles_parser.add_argument(
        '--kraken-spot-end',
        type=str,
        required=True,
        help='End date for kraken_spot symbols (ISO format)'
    )
    all_profiles_parser.add_argument(
        '--mode',
        type=str,
        choices=['volatility_split', 'continuous'],
        default='volatility_split',
        help='Generation mode (default: volatility_split)'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = GeneratorCli()

    if args.command == 'generate-blocks':
        cli.cmd_generate_blocks(
            broker_type=args.broker_type,
            symbols=args.symbols,
            count=args.count,
            block_size=args.block_size,
            start=args.start,
            end=args.end,
            output=args.output,
            max_ticks=args.max_ticks
        )
    elif args.command == 'generate-profile':
        cli.cmd_generate_profile(
            broker_type=args.broker_type,
            symbol=args.symbol,
            start=args.start,
            end=args.end,
            mode=args.mode,
            output=args.output,
        )
    elif args.command == 'generate-all-profiles':
        broker_starts = {
            'mt5': args.mt5_start,
            'kraken_spot': args.kraken_spot_start,
        }
        broker_ends = {
            'mt5': args.mt5_end,
            'kraken_spot': args.kraken_spot_end,
        }
        cli.cmd_generate_all_profiles(
            broker_starts=broker_starts,
            broker_ends=broker_ends,
            mode=args.mode,
        )


if __name__ == '__main__':
    main()
