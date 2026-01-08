"""
FiniexTestingIDE - System Fingerprint Utility
Detects system hardware for benchmark validation

Provides:
- CPU model detection
- CPU core count
- RAM availability check
- System matching against registered systems
"""

import platform
import psutil
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple


@dataclass
class SystemFingerprint:
    """Current system hardware fingerprint."""
    cpu_model: str
    cpu_cores: int
    ram_total_gb: float
    ram_available_gb: float
    platform: str

    def matches_reference(
        self,
        reference: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if this system matches a reference system.

        Args:
            reference: Reference system dict from reference_systems.json

        Returns:
            Tuple of (matches: bool, reason: Optional[str])
        """
        hardware = reference.get("hardware", {})

        # CPU model must match exactly
        ref_cpu = hardware.get("cpu_model", "")
        if ref_cpu not in self.cpu_model and self.cpu_model not in ref_cpu:
            return False, f"CPU mismatch: '{self.cpu_model}' != '{ref_cpu}'"

        # CPU cores must match
        ref_cores = hardware.get("cpu_cores", 0)
        if self.cpu_cores != ref_cores:
            return False, f"Core count mismatch: {self.cpu_cores} != {ref_cores}"

        # RAM must meet minimum
        ref_ram_min = hardware.get("ram_minimum_gb", 0)
        if self.ram_total_gb < ref_ram_min:
            return False, f"Insufficient RAM: {self.ram_total_gb:.1f}GB < {ref_ram_min}GB minimum"

        return True, None


def get_system_fingerprint() -> SystemFingerprint:
    """
    Detect current system hardware.

    Returns:
        SystemFingerprint with current hardware specs
    """
    # CPU model
    cpu_model = _get_cpu_model()

    # CPU cores
    cpu_cores = psutil.cpu_count(logical=True)

    # RAM
    mem = psutil.virtual_memory()
    ram_total_gb = mem.total / (1024 ** 3)
    ram_available_gb = mem.available / (1024 ** 3)

    # Platform
    plat = f"{platform.system()} {platform.release()}"

    return SystemFingerprint(
        cpu_model=cpu_model,
        cpu_cores=cpu_cores,
        ram_total_gb=ram_total_gb,
        ram_available_gb=ram_available_gb,
        platform=plat
    )


def _get_cpu_model() -> str:
    """
    Get CPU model string.

    Tries multiple methods for cross-platform support.
    """
    # Try platform.processor() first
    cpu = platform.processor()
    if cpu and cpu != "":
        return cpu

    # Try reading from /proc/cpuinfo on Linux
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":")[1].strip()
    except (FileNotFoundError, PermissionError):
        pass

    # Fallback
    return "Unknown CPU"


def find_matching_system(
    fingerprint: SystemFingerprint,
    reference_systems: Dict[str, Any]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Find a registered system that matches the current hardware.

    Args:
        fingerprint: Current system fingerprint
        reference_systems: Dict from reference_systems.json

    Returns:
        Tuple of (system_id, None) if found, or (None, error_message) if not
    """
    systems = reference_systems.get("systems", {})

    if not systems:
        return None, "No systems registered in reference_systems.json"

    rejection_reasons = []

    for system_id, system_config in systems.items():
        matches, reason = fingerprint.matches_reference(system_config)
        if matches:
            return system_id, None
        else:
            rejection_reasons.append(f"  - {system_id}: {reason}")

    error_msg = (
        f"Current system not registered.\n"
        f"Detected: {fingerprint.cpu_model} ({fingerprint.cpu_cores} cores, "
        f"{fingerprint.ram_total_gb:.1f}GB RAM)\n"
        f"\nRejection reasons:\n" + "\n".join(rejection_reasons) +
        f"\n\nTo register this system:\n"
        f"1. Run the benchmark scenario manually\n"
        f"2. Add your system to tests/mvp_benchmark/config/reference_systems.json\n"
        f"3. Include your hardware specs and measured baseline values"
    )

    return None, error_msg


def get_git_commit() -> Optional[str]:
    """
    Get current git commit hash.

    Returns:
        Short commit hash or None if not in git repo
    """
    import subprocess
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
