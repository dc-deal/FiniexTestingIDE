"""
FiniexTestingIDE - Security Study Stages (#358)

The stages of the security study, in the order they run, and where each one's profile lives. The
command that runs the study and the certificate that judges it read the same list, so a stage
added here is run and judged — and one removed is neither.
"""

import json
from pathlib import Path
from typing import Tuple

from python.framework.types.acceptance_study_types.security_cert_types import (
    SecurityStage,
    SecurityStageId,
)

# The security study's profiles — a purpose folder of their own, beside the field study's. The
# mock rehearsal's twins carry the same file names in a folder under mock/, so one stage list
# serves both.
SECURITY_CERT_PROFILE_DIR = Path('configs/autotrader_profiles/security_cert')

# The order is the study's: stage 3 reads the baseline stage 2 wrote, stage 5 takes over the lot
# stage 4 kept.
SECURITY_STAGES: Tuple[SecurityStage, ...] = (
    SecurityStage(SecurityStageId.GUARDS, 'security_cert_1_guards.json'),
    SecurityStage(SecurityStageId.SOFT, 'security_cert_2_soft.json'),
    SecurityStage(SecurityStageId.RESTART, 'security_cert_3_restart.json'),
    SecurityStage(SecurityStageId.HARD_KEPT, 'security_cert_4_hard_kept.json'),
    SecurityStage(SecurityStageId.HARD_RESTART, 'security_cert_5_hard_restart.json'),
)


def stage_profile_path(stage: SecurityStage, profile_dir: Path = SECURITY_CERT_PROFILE_DIR) -> Path:
    """
    Where a stage's profile is.

    Args:
        stage: The stage
        profile_dir: The folder its profile is read from

    Returns:
        The profile's path
    """
    return profile_dir / stage.profile


def stage_unit_name(stage: SecurityStage, profile_dir: Path = SECURITY_CERT_PROFILE_DIR) -> str:
    """
    The unit a stage's runs are filed under — its profile's name.

    Args:
        stage: The stage
        profile_dir: The folder its profile is read from

    Returns:
        The profile_name the stage's profile declares
    """
    profile = json.loads(stage_profile_path(stage, profile_dir).read_text(encoding='utf-8'))
    return profile['profile_name']
