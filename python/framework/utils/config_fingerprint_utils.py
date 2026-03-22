"""
Config Fingerprint Utilities
==============================
SHA256-based fingerprinting for configuration sections.

Used by discovery caches to detect when config parameters change,
and by generator profiles to record which discovery configs were active
during profile generation.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

import pyarrow.parquet as pq


def read_fingerprint_from_parquet(cache_path: Path) -> Optional[str]:
    """
    Read config_fingerprint from Parquet Arrow metadata.

    Args:
        cache_path: Path to Parquet cache file

    Returns:
        Fingerprint string or None if not found/readable
    """
    if not cache_path.exists():
        return None
    try:
        schema = pq.read_schema(cache_path)
        metadata = schema.metadata or {}
        raw = metadata.get(b'config_fingerprint')
        return raw.decode() if raw else None
    except Exception:
        return None


def generate_config_fingerprint(config_section: Dict[str, Any]) -> str:
    """
    Generate a deterministic SHA256 fingerprint for a config section.

    Keys are sorted recursively to ensure identical output
    regardless of dict ordering.

    Args:
        config_section: Configuration dictionary to fingerprint

    Returns:
        SHA256 hex digest string
    """
    normalized = json.dumps(config_section, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()
