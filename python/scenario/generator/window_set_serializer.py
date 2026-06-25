"""
Window Set Serializer
=====================
Present-layer for the generator: serializes a WindowSet to its output formats. Today two JSON
targets — a runnable scenario-set config (blocks) and a profile artifact (volatility / continuous).

The model (WindowSet) is the source of truth; this is the swappable output stage. When a shared
result Store / DB / RAM backend lands (the #21 memory-aware-runtime direction, FiniexViewer API),
only this stage changes — the model and the materializer stay untouched.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.config_types.robustness_config_types import RobustnessConfig
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy
from python.framework.types.scenario_types.window_set_types import (
    GeneratedWindow,
    WindowSet,
    WindowSplitConfig,
)
from python.scenario.generator.balance_defaults import ensure_quote_balance, resolve_quote_currency
from python.scenario.generator.window_materializer import WindowMaterializer
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()

PROFILE_OUTPUT_DIR = Path('configs/generator_profiles')


class WindowSetSerializer:
    """Serializes a WindowSet to a scenario-set JSON or a profile artifact JSON."""

    def __init__(self, profile_output_dir: Path = PROFILE_OUTPUT_DIR):
        """
        Initialize serializer with template / output paths.

        Args:
            profile_output_dir: Directory for profile artifact JSON output
        """
        app_config = AppConfigManager()
        self._template_path = Path(app_config.get_generator_template_path())
        self._scenario_set_output_dir = Path(app_config.get_generator_output_path())
        self._scenario_set_output_dir.mkdir(parents=True, exist_ok=True)
        self._profile_output_dir = profile_output_dir
        self._profile_output_dir.mkdir(parents=True, exist_ok=True)
        self._materializer = WindowMaterializer()

    # =========================================================================
    # SCENARIO SET JSON (blocks path)
    # =========================================================================

    def save_scenario_set(
        self,
        window_set: WindowSet,
        filename: str,
        robustness: Optional[RobustnessConfig] = None,
    ) -> Path:
        """
        Save a WindowSet as a runnable scenario-set config.

        Args:
            window_set: The window set to serialize
            filename: Output filename
            robustness: Optional robustness mode (#367) — writes the top-level block + assigns
                time-ordered IS/OOS roles to the scenarios when enabled

        Returns:
            Path to the saved config file
        """
        if not self._template_path.exists():
            raise FileNotFoundError(
                f"Scenario template not found: {self._template_path}\n"
                f"Configure 'generator_template' in app_config.json paths section.\n"
                f"This file is required for generating scenario configs."
            )

        with open(self._template_path, 'r') as f:
            config = json.load(f)

        # Update metadata
        config['version'] = "1.0"
        config['scenario_set_name'] = filename.replace('.json', '')
        config['created'] = datetime.now(timezone.utc).isoformat()

        # Always provide a balance in the symbol's quote currency, so the generated set passes
        # validation and runs out of the box (cascade-capable keys are not emitted per scenario,
        # so the balance lives set-wide in global). The quote is resolved authoritatively from
        # the broker config (#265).
        global_config = config.setdefault('global', {})
        if window_set.windows:
            quote_currency = resolve_quote_currency(
                window_set.symbol, window_set.broker_type)
            global_config['trade_simulator_config'] = ensure_quote_balance(
                global_config.get('trade_simulator_config'), quote_currency)

        # Robustness mode (#367): write the set-wide block ABOVE `global` (set-wide mode, not a
        # cascade default).
        if robustness is not None and robustness.enabled:
            rob = robustness.model_dump(mode='json')
            reordered = {}
            for key, value in config.items():
                if key == 'global':
                    reordered['robustness'] = rob
                reordered[key] = value
            reordered.setdefault('robustness', rob)  # no `global` in template → still present
            config = reordered

        # Add scenarios (role assignment lives in the materializer)
        config['scenarios'] = self._materializer.to_scenario_dicts(window_set, robustness)

        output_path = self._scenario_set_output_dir / filename
        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2, default=str)

        vLog.info(f"Saved {len(config['scenarios'])} scenarios to {output_path}")

        return output_path

    # =========================================================================
    # PROFILE ARTIFACT JSON (volatility / continuous path)
    # =========================================================================

    def save_profile(
        self,
        window_set: WindowSet,
        filename: str,
    ) -> Path:
        """
        Save a WindowSet as a profile artifact JSON.

        Args:
            window_set: The window set to serialize
            filename: Output filename (with or without .json)

        Returns:
            Path to the saved profile file
        """
        if not filename.endswith('.json'):
            filename += '.json'

        # Organize by split mode then broker_type: <mode>/<broker_type>/<filename>
        out_dir = self._profile_output_dir / window_set.mode / window_set.broker_type
        out_dir.mkdir(parents=True, exist_ok=True)

        output_path = out_dir / filename
        with open(output_path, 'w') as f:
            json.dump(self.to_profile_dict(window_set), f, indent=2, default=str)

        vLog.info(f"Profile saved to {output_path}")

        return output_path

    def to_profile_dict(self, window_set: WindowSet) -> Dict[str, Any]:
        """
        Serialize a WindowSet to the profile artifact dict (stable on-disk format).

        Args:
            window_set: The window set to serialize

        Returns:
            Profile dict (profile_meta + blocks)
        """
        split_config = window_set.split_config
        split_config_dict = {
            'min_block_hours': split_config.min_block_hours,
            'max_block_hours': split_config.max_block_hours,
            'atr_percentile_threshold': split_config.atr_percentile_threshold,
            'split_algorithm': split_config.split_algorithm,
        } if split_config else {}

        return {
            'profile_meta': {
                'symbol': window_set.symbol,
                'broker_type': window_set.broker_type,
                'generator_mode': window_set.mode,
                'generated_at': window_set.generated_at.isoformat(),
                'total_coverage_hours': window_set.total_coverage_hours,
                'block_count': window_set.block_count,
                'discovery_fingerprints': window_set.discovery_fingerprints,
                'split_config': split_config_dict,
            },
            'blocks': [self._window_to_block_dict(w) for w in window_set.windows],
        }

    @staticmethod
    def from_profile_dict(data: Dict[str, Any]) -> WindowSet:
        """
        Deserialize a profile artifact dict into a WindowSet.

        Args:
            data: Profile dict (profile_meta + blocks)

        Returns:
            WindowSet instance
        """
        meta = data['profile_meta']
        split_config_data = meta.get('split_config') or {}
        split_config = WindowSplitConfig(
            min_block_hours=split_config_data['min_block_hours'],
            max_block_hours=split_config_data['max_block_hours'],
            atr_percentile_threshold=split_config_data['atr_percentile_threshold'],
            split_algorithm=split_config_data['split_algorithm'],
        ) if split_config_data else None

        windows = [
            WindowSetSerializer._block_dict_to_window(b)
            for b in data.get('blocks', [])
        ]

        return WindowSet(
            symbol=meta['symbol'],
            broker_type=meta['broker_type'],
            strategy=GenerationStrategy(meta['generator_mode']),
            windows=windows,
            generated_at=datetime.fromisoformat(meta['generated_at']),
            mode=meta['generator_mode'],
            split_config=split_config,
            discovery_fingerprints=meta.get('discovery_fingerprints', {}),
        )

    @staticmethod
    def _window_to_block_dict(window: GeneratedWindow) -> Dict[str, Any]:
        """
        Serialize one window to a profile block dict (stable keys).

        Args:
            window: The window to serialize

        Returns:
            Block dict
        """
        result = {
            'block_index': window.block_index,
            'start_time': window.start_time.isoformat(),
            'end_time': window.end_time.isoformat(),
            'block_duration_hours': round(window.block_duration_hours, 2),
            'split_reason': window.split_reason,
            'atr_at_split': window.atr,
            'regime_at_split': window.regime.value,
            'session': window.session.value,
            'estimated_ticks': window.estimated_ticks,
        }
        if window.distance_to_next_block_hours is not None:
            result['distance_to_next_block_hours'] = window.distance_to_next_block_hours
        return result

    @staticmethod
    def _block_dict_to_window(data: Dict[str, Any]) -> GeneratedWindow:
        """
        Deserialize one profile block dict to a GeneratedWindow.

        Args:
            data: Block dict

        Returns:
            GeneratedWindow instance
        """
        return GeneratedWindow(
            block_index=data['block_index'],
            start_time=datetime.fromisoformat(data['start_time']),
            end_time=datetime.fromisoformat(data['end_time']),
            regime=VolatilityRegime(data['regime_at_split']),
            session=TradingSession(data['session']),
            estimated_ticks=data.get('estimated_ticks', 0),
            atr=data['atr_at_split'],
            split_reason=data['split_reason'],
            distance_to_next_block_hours=data.get('distance_to_next_block_hours'),
        )
