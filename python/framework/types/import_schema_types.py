"""
Import Pipeline Schema Types.

TypedDict definitions for MQL5 JSON tick data export format.
Defines mandatory and optional fields for import validation.
"""

from typing import Dict, List, TypedDict


class SymbolInfoSchema(TypedDict, total=False):
    """Symbol info sub-structure from MQL5 metadata."""
    point_value: float
    digits: int
    tick_size: float
    tick_value: float


class CollectionSettingsSchema(TypedDict, total=False):
    """Collection settings sub-structure from MQL5 metadata."""
    max_ticks_per_file: int
    max_errors_per_file: int
    include_real_volume: bool
    include_tick_flags: bool
    stop_on_fatal_errors: bool


class ErrorTrackingSchema(TypedDict, total=False):
    """Error tracking sub-structure from MQL5 metadata."""
    enabled: bool
    log_negligible: bool
    log_serious: bool
    log_fatal: bool
    max_spread_percent: float
    max_price_jump_percent: float
    max_data_gap_seconds: int


class ImportMetadataSchema(TypedDict, total=False):
    """
    Metadata section of MQL5 JSON tick export.

    Required fields: symbol, broker_type (or legacy data_collector), start_time.
    All other fields are optional and version-dependent (v1.0.5+).

    Args:
        symbol: Trading instrument identifier (e.g. "EURUSD", "BTCUSD")
        broker_type: Normalized broker type (e.g. "mt5", "kraken_spot")
        start_time: Collection start timestamp string

    Returns:
        N/A (TypedDict - used for type checking only)
    """
    # Required (all versions)
    symbol: str
    broker: str
    start_time: str

    # Required (v1.0.4+ — one of broker_type or data_collector must be present)
    broker_type: str
    data_collector: str  # Legacy field, same purpose as broker_type

    # Optional (v1.0.5+)
    server: str
    broker_utc_offset_hours: int
    local_device_time: str
    broker_server_time: str
    start_time_unix: int
    timeframe: str
    volume_timeframe: str
    volume_timeframe_minutes: int
    data_format_version: str
    collection_purpose: str
    operator: str

    # Nested optional
    symbol_info: SymbolInfoSchema
    collection_settings: CollectionSettingsSchema
    error_tracking: ErrorTrackingSchema


class ImportTickSchema(TypedDict, total=False):
    """
    Single tick entry from MQL5 JSON export.

    Required: timestamp, bid, ask.
    All other fields are optional and version-dependent.

    Args:
        timestamp: Tick timestamp string (format: "YYYY.MM.DD HH:MM:SS")
        bid: Bid price
        ask: Ask price

    Returns:
        N/A (TypedDict - used for type checking only)
    """
    timestamp: str
    time_msc: int
    bid: float
    ask: float
    last: float
    tick_volume: int
    real_volume: float
    chart_tick_volume: int
    spread_points: int
    spread_pct: float
    tick_flags: str
    session: str
    server_time: str


class ImportJsonSchema(TypedDict):
    """
    Top-level structure of MQL5 JSON tick data export.

    Args:
        metadata: Import metadata dict
        ticks: List of tick data dicts

    Returns:
        N/A (TypedDict - used for type checking only)
    """
    metadata: ImportMetadataSchema
    ticks: List[ImportTickSchema]


# =============================================================================
# MANDATORY FIELD DEFINITIONS
# =============================================================================

# Fields that MUST be present in metadata for a valid import
MANDATORY_METADATA_FIELDS: List[str] = [
    "symbol",
    "start_time",
]

# At least one of these must be present (broker identification)
BROKER_IDENTIFICATION_FIELDS: List[str] = [
    "broker_type",
    "data_collector",
]

# Fields that MUST be present in each tick entry
MANDATORY_TICK_FIELDS: List[str] = [
    "timestamp",
    "bid",
    "ask",
]

# Nested metadata keys that are stored as JSON strings in Parquet
NESTED_METADATA_KEYS: List[str] = [
    "symbol_info",
    "collection_settings",
    "error_tracking",
]

# Metadata keys already captured at top level in Parquet header
# (skipped during source_meta_ prefix passthrough to avoid duplication)
ALREADY_CAPTURED_METADATA_KEYS: List[str] = [
    "symbol",
    "broker",
    "ticks",
]


class ImportConfigPathsSchema(TypedDict):
    """
    Path configuration for the import pipeline.

    Args:
        data_raw: Source directory for JSON tick exports
        import_output: Output directory for processed Parquet files
        data_finished: Directory for JSON files moved after successful import

    Returns:
        N/A (TypedDict - used for type checking only)
    """
    data_raw: str
    import_output: str
    data_finished: str


class ImportTestPathsSchema(TypedDict):
    """
    Dedicated test paths for isolated import pipeline testing.

    Args:
        data_raw: Test source directory
        import_output: Test output directory
        data_finished: Test finished directory

    Returns:
        N/A (TypedDict - used for type checking only)
    """
    data_raw: str
    import_output: str
    data_finished: str


class OffsetRegistryEntrySchema(TypedDict):
    """
    Single broker offset entry in the offset registry.

    Args:
        default_offset_hours: Hours to subtract from broker timestamps for UTC conversion
        description: Human-readable explanation of the offset

    Returns:
        N/A (TypedDict - used for type checking only)
    """
    default_offset_hours: int
    description: str


class ProcessingConfigSchema(TypedDict):
    """
    Import processing behavior configuration.

    Args:
        move_processed_files: Move JSON to finished/ after successful import
        auto_render_bars: Automatically render bars after tick import

    Returns:
        N/A (TypedDict - used for type checking only)
    """
    move_processed_files: bool
    auto_render_bars: bool


class ImportConfigSchema(TypedDict):
    """
    Top-level import_config.json schema.

    Args:
        version: Config format version
        description: Human-readable config description
        paths: Import pipeline path configuration
        test_paths: Isolated test environment paths
        offset_registry: Per-broker UTC offset defaults
        processing: Import behavior settings

    Returns:
        N/A (TypedDict - used for type checking only)
    """
    version: str
    description: str
    paths: ImportConfigPathsSchema
    test_paths: ImportTestPathsSchema
    offset_registry: Dict[str, OffsetRegistryEntrySchema]
    processing: ProcessingConfigSchema
