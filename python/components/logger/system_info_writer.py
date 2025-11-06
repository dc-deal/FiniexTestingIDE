"""
FiniexTestingIDE - System Information Writer
Captures system and version control information for performance tracking

Multi-Level Logging:
- INFO:  Essential performance metrics (console + file)
- DEBUG: Detailed specs for deep analysis (file only)

Usage:
    from python.components.logger.system_info_writer import write_system_version_parameters
    
    system_info_logger = ScenarioLogger(...)
    write_system_version_parameters(system_info_logger)
    system_info_logger.flush_buffer()
"""

import os
import platform
import psutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from python.components.logger.scenario_logger import ScenarioLogger


def write_system_version_parameters(logger: ScenarioLogger) -> None:
    """
    Write system and version control information to logger.

    INFO Level:
    - Platform, Python, CPU (model + cores), RAM, Git summary

    DEBUG Level:
    - Python implementation, architecture, CPU frequencies
    - Dependencies (NumPy, Pandas)
    - Paths (app root, data, logs)
    - Docker resource limits

    Args:
        logger: ScenarioLogger instance for output
    """
    # Collect all info
    platform_info = _get_platform_info()
    python_info = _get_python_info()
    cpu_basic, cpu_detailed = _get_cpu_info()
    ram_info = _get_ram_info()
    env_info = _get_environment_info()
    git_info = _get_git_info(logger)

    # INFO: Essential summary
    info_output = _format_info_summary(
        platform_info=platform_info,
        python_info=python_info,
        cpu_basic=cpu_basic,
        ram_info=ram_info,
        env_info=env_info,
        git_info=git_info
    )
    logger.info(info_output)

    # DEBUG: Detailed specs
    python_detailed = _get_python_detailed()
    dependencies = _get_dependencies()
    paths = _get_paths()
    docker_limits = _get_docker_limits() if env_info == "docker" else None

    debug_output = _format_debug_details(
        python_detailed=python_detailed,
        cpu_detailed=cpu_detailed,
        dependencies=dependencies,
        paths=paths,
        docker_limits=docker_limits
    )
    logger.debug(debug_output)


def _get_platform_info() -> str:
    """Get platform information (OS and version)"""
    system = platform.system()
    release = platform.release()
    return f"{system} {release}"


def _get_python_info() -> str:
    """Get Python version only"""
    return platform.python_version()


def _get_python_detailed() -> Dict[str, str]:
    """Get detailed Python information"""
    return {
        'version': platform.python_version(),
        'implementation': platform.python_implementation(),
        'architecture': platform.architecture()[0]
    }


def _get_cpu_info() -> Tuple[str, Dict[str, Any]]:
    """
    Get CPU information.

    Returns:
        Tuple of (basic_info, detailed_info)
        - basic_info: "Intel i7-14700K (16 cores @ 3.4 GHz)"
        - detailed_info: Dict with model, frequencies, etc.
    """
    cpu_count = os.cpu_count() or 0

    # Try to get CPU model
    cpu_model = "Unknown"
    try:
        if platform.system() == "Linux":
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.strip().startswith('model name'):
                        cpu_model = line.split(':', 1)[1].strip()
                        break
        elif platform.system() == "Windows":
            cpu_model = platform.processor()
    except Exception:
        pass

    # Simplify model name (remove redundant info)
    cpu_model_short = cpu_model
    if "Intel" in cpu_model or "AMD" in cpu_model:
        # Extract just the meaningful part
        parts = cpu_model.split()
        if len(parts) >= 2:
            cpu_model_short = f"{parts[0]} {parts[-2]}"  # "Intel i7-14700K"

    # Try to get CPU frequency
    cpu_freq_str = ""
    cpu_freq_detailed = {}
    try:
        freq = psutil.cpu_freq()
        if freq:
            cpu_freq_str = f" @ {freq.current / 1000:.1f} GHz"
            cpu_freq_detailed = {
                'current': freq.current,
                'min': freq.min,
                'max': freq.max
            }
    except Exception:
        pass

    # Basic info for INFO level
    basic_info = f"{cpu_model_short} ({cpu_count} cores{cpu_freq_str})"

    # Detailed info for DEBUG level
    detailed_info = {
        'model': cpu_model,
        'count': cpu_count,
        'frequencies': cpu_freq_detailed
    }

    return basic_info, detailed_info


def _get_ram_info() -> Dict[str, float]:
    """
    Get RAM information.

    Returns:
        Dict with total and available RAM in GB
    """
    mem = psutil.virtual_memory()
    return {
        'total_gb': mem.total / (1024 ** 3),
        'available_gb': mem.available / (1024 ** 3)
    }


def _get_environment_info() -> str:
    """
    Auto-detect execution environment.

    Returns:
        "docker", "vscode_devcontainer", or "native"
    """
    # Check for Docker
    if Path('/.dockerenv').exists():
        return "docker"

    # Check for VS Code devcontainer
    if os.environ.get('VSCODE_DEVCONTAINER'):
        return "vscode_devcontainer"

    # Default to native
    return "native"


def _get_git_info(logger: ScenarioLogger) -> Optional[Dict[str, Any]]:
    """
    Get Git repository information.

    Returns None if Git is not available or not in a Git repo.
    Logs warning if uncommitted changes detected.

    Args:
        logger: Logger for warnings

    Returns:
        Dict with git info or None if unavailable
    """
    try:
        # Check if git is available
        subprocess.run(
            ['git', '--version'],
            capture_output=True,
            check=True,
            timeout=5
        )

        # Get branch
        branch = subprocess.run(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()

        # Get commit hash (short)
        commit = subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()

        # Get commit date (UTC)
        commit_date_str = subprocess.run(
            ['git', 'log', '-1', '--format=%cI'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()

        # Parse and convert to UTC
        commit_date = datetime.fromisoformat(
            commit_date_str).astimezone(timezone.utc)

        # Get commit message (first line only)
        commit_message = subprocess.run(
            ['git', 'log', '-1', '--format=%s'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()

        # Check for uncommitted changes
        status = subprocess.run(
            ['git', 'status', '--porcelain'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5
        ).stdout.strip()

        dirty = bool(status)

        # Warn if dirty
        if dirty:
            num_changes = len(status.splitlines())
            logger.warning(
                f"Git working tree has {num_changes} uncommitted change(s) - "
                f"this performance snapshot may not be reproducible!"
            )

        return {
            'branch': branch,
            'commit': commit,
            'date': commit_date,
            'message': commit_message,
            'dirty': dirty
        }

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        # Git not available or not in a git repo - not critical
        return None


def _get_dependencies() -> Dict[str, str]:
    """
    Get versions of critical dependencies.

    Returns:
        Dict with package versions
    """
    deps = {}

    # NumPy
    try:
        import numpy
        deps['numpy'] = numpy.__version__
    except ImportError:
        deps['numpy'] = 'not installed'

    # Pandas
    try:
        import pandas
        deps['pandas'] = pandas.__version__
    except ImportError:
        deps['pandas'] = 'not installed'

    return deps


def _get_paths() -> Dict[str, str]:
    """
    Get important application paths.

    Returns:
        Dict with paths
    """
    cwd = Path.cwd()

    return {
        'app_root': str(cwd),
        'data_path': str(cwd / 'data' / 'parquet'),
        'log_path': str(cwd / 'logs' / 'scenario_sets')
    }


def _get_docker_limits() -> Optional[Dict[str, str]]:
    """
    Get Docker resource limits (if running in Docker).

    Returns:
        Dict with limits or None if not detectable
    """
    try:
        # Try to read cgroup limits
        cpu_limit = "No limit"
        mem_limit = "No limit"

        # CPU limit
        cpu_quota_path = Path('/sys/fs/cgroup/cpu/cpu.cfs_quota_us')
        if cpu_quota_path.exists():
            quota = int(cpu_quota_path.read_text().strip())
            if quota > 0:
                period = int(
                    Path('/sys/fs/cgroup/cpu/cpu.cfs_period_us').read_text().strip())
                cpu_limit = f"{quota / period:.1f} cores"

        # Memory limit
        mem_limit_path = Path('/sys/fs/cgroup/memory/memory.limit_in_bytes')
        if mem_limit_path.exists():
            limit = int(mem_limit_path.read_text().strip())
            # Very high number = no limit
            if limit < 9223372036854771712:  # Max value
                mem_limit = f"{limit / (1024 ** 3):.1f} GB"

        return {
            'cpu_limit': cpu_limit,
            'memory_limit': mem_limit
        }
    except Exception:
        return None


def _format_info_summary(
    platform_info: str,
    python_info: str,
    cpu_basic: str,
    ram_info: Dict[str, float],
    env_info: str,
    git_info: Optional[Dict[str, Any]]
) -> str:
    """
    Format INFO-level summary (essential performance metrics).

    Args:
        platform_info: Platform string
        python_info: Python version
        cpu_basic: CPU basic info (model + cores + freq)
        ram_info: RAM info dict
        env_info: Environment (docker/native)
        git_info: Git information dict or None

    Returns:
        Formatted multi-line string
    """
    lines = [
        "=== SYSTEM & VERSION INFORMATION (Summary) ===",
        f"Platform:    {platform_info} ({env_info})",
        f"Python:      {python_info}",
        f"CPU:         {cpu_basic}",
        f"RAM:         {ram_info['available_gb']:.1f} GB available / {ram_info['total_gb']:.1f} GB total",
    ]

    if git_info:
        # Add -DIRTY marker if uncommitted changes
        commit_str = git_info['commit']
        if git_info['dirty']:
            commit_str += "-DIRTY"

        # Truncate message if too long
        message = git_info['message']
        if len(message) > 45:
            message = message[:42] + "..."

        git_summary = f"{git_info['branch']} @ {commit_str} ({git_info['date'].strftime('%Y-%m-%d')}: \"{message}\")"
        lines.append(f"Git:         {git_summary}")
    else:
        lines.append("Git:         (not available)")

    lines.append("=" * 80)

    return "\n".join(lines)


def _format_debug_details(
    python_detailed: Dict[str, str],
    cpu_detailed: Dict[str, Any],
    dependencies: Dict[str, str],
    paths: Dict[str, str],
    docker_limits: Optional[Dict[str, str]]
) -> str:
    """
    Format DEBUG-level details (comprehensive specs).

    Args:
        python_detailed: Python implementation details
        cpu_detailed: CPU detailed info
        dependencies: Package versions
        paths: Application paths
        docker_limits: Docker resource limits (if applicable)

    Returns:
        Formatted multi-line string
    """
    lines = [
        "=== DETAILED SYSTEM INFORMATION ===",
        f"Python Implementation: {python_detailed['implementation']}",
        f"Python Architecture:   {python_detailed['architecture']}",
        f"CPU Model:             {cpu_detailed['model']}",
    ]

    # CPU frequencies (nur zeigen wenn verfügbar)
    if cpu_detailed['frequencies'] and cpu_detailed['frequencies'].get('max', 0) > 0:
        freq = cpu_detailed['frequencies']
        lines.append(
            f"CPU Min/Max Freq:      {freq['min']:.0f} MHz / {freq['max']:.0f} MHz")

    lines.append("")
    lines.append("Dependencies:")
    lines.append(f"  NumPy:    {dependencies['numpy']}")
    lines.append(f"  Pandas:   {dependencies['pandas']}")

    lines.append("")
    lines.append("Paths:")
    lines.append(f"  App Root:     {paths['app_root']}")
    lines.append(f"  Data Path:    {paths['data_path']}")
    lines.append(f"  Log Path:     {paths['log_path']}")

    # Docker limits (if applicable)
    if docker_limits:
        lines.append("")
        lines.append("Docker Resource Limits:")
        lines.append(f"  CPU Limit:    {docker_limits['cpu_limit']}")
        lines.append(f"  Memory Limit: {docker_limits['memory_limit']}")

    lines.append("=" * 40)

    return "\n".join(lines)
