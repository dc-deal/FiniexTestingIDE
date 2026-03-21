"""
Profile Saver
==============
Saves GeneratorProfile artifacts as JSON files.

Output directory: configs/generator_profiles/
"""

import json
from pathlib import Path

from python.framework.types.scenario_types.generator_profile_types import GeneratorProfile
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()

PROFILE_OUTPUT_DIR = Path('configs/generator_profiles')


class ProfileSaver:
    """Saves GeneratorProfile artifacts to JSON."""

    def __init__(self, output_dir: Path = PROFILE_OUTPUT_DIR):
        """
        Initialize profile saver.

        Args:
            output_dir: Directory for profile JSON output
        """
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def save_profile(
        self,
        profile: GeneratorProfile,
        filename: str
    ) -> Path:
        """
        Save profile to JSON file.

        Args:
            profile: GeneratorProfile to save
            filename: Output filename (with or without .json)

        Returns:
            Path to saved file
        """
        if not filename.endswith('.json'):
            filename += '.json'

        output_path = self._output_dir / filename

        profile_dict = profile.to_dict()

        with open(output_path, 'w') as f:
            json.dump(profile_dict, f, indent=2, default=str)

        vLog.info(f"Profile saved to {output_path}")

        return output_path
