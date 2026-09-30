"""
Directory API types (#554) — every configuration that can start a run, as the API serves it.

Pydantic, because FastAPI serializes them and the console renders the same objects. The row is
what one file DECLARES, read from its raw JSON; the run figures are joined from the run index at
serve time and are never part of the cached row.
"""

from typing import List, Optional

from pydantic import BaseModel

from python.framework.types.api.report_types import RunListFigures
from python.framework.types.config_directory_types import (
    ConfigKind,
    ConfigOrigin,
    ConfigReadStatus,
)

# What makes one directory row unique (§49): the FILE NAME, across every root and both kinds.
# A name is how a configuration is addressed everywhere — the run's `config_snapshot`, the
# scenario-set resolver, the run-config store's `source_name` — so the walk resolves each name
# once, by precedence, before it knows what the file holds. `kind` is an attribute of the row,
# and it is None on an unreadable one.
DIRECTORY_ROW_KEY = ['file']


class DirectoryRow(BaseModel):
    """
    One configuration file, as far as reading it — not running it — can tell.

    Args:
        kind: Which pipeline it starts; None when the file could not be read
        file: The file name — the identity a run records in its `config_snapshot`
        origin: The root it lives under
        folder: Its sub-folder inside `configs/` ('' otherwise — the operator's own layout is not
            served)
        status: `readable` or `unreadable` (it does not parse, or its name is also a
            configuration of the other kind); never a validation verdict
        reason: Why it is unreadable, '' otherwise
        name: The scenario set's `scenario_set_name`, or the profile's `profile_name`
        modified_at: When the file last changed, ISO-8601 UTC — a file being edited shows here
        scenarios_declared: Every scenario the file names, `enabled: false` ones included
            (a profile is one unit)
        scenarios_enabled: The ones that would run
        symbols: Distinct symbols of the enabled scenarios
        data_broker_types: Distinct data broker types of the enabled scenarios — the brokers
            whose archives they read (a profile's `scenario_settings.data_broker_type`, else its
            `broker_type`)
        market_types: What those brokers are (`crypto`, `forex`, `unknown` for a broker the
            market config does not know)
        decision_logics: Distinct decision logic types after the per-scenario cascade
        workers: Distinct worker types after the per-scenario cascade
        bot_id: The profile's bot id, '' for a scenario set
        adapter_type: The profile's adapter (`mock` | `live`), '' for a scenario set
        dry_run_declared: What the profile declares for `dry_run` — None means the broker's
            default applies, resolved at session start; always None for a scenario set
        run_count: Runs on record that were started from this file (runs pruned from the run tree
            no longer count)
        last_run_at: Start of the newest of them, '' when none
        last_run_id: Its run id, '' when none
        last_run_figures: What that newest run DID, as the run-results ledger recorded it — the
            same figures the run list carries; None when there is no run or the ledger holds
            nothing for it
        shadowed: Origins of same-named files this one wins over — why an edit elsewhere does not
            take effect
    """
    kind: Optional[ConfigKind] = None
    file: str
    origin: ConfigOrigin
    folder: str = ''
    status: ConfigReadStatus
    reason: str = ''
    name: str = ''
    modified_at: str = ''
    scenarios_declared: int = 0
    scenarios_enabled: int = 0
    symbols: List[str] = []
    data_broker_types: List[str] = []
    market_types: List[str] = []
    decision_logics: List[str] = []
    workers: List[str] = []
    bot_id: str = ''
    adapter_type: str = ''
    dry_run_declared: Optional[bool] = None
    run_count: int = 0
    last_run_at: str = ''
    last_run_id: str = ''
    last_run_figures: Optional[RunListFigures] = None
    shadowed: List[ConfigOrigin] = []


class DirectoryScenario(BaseModel):
    """
    One scenario of a scenario set, as the file declares it after the strategy cascade.

    Args:
        name: The scenario's name
        symbol: Its symbol
        data_broker_type: Its data broker type
        market_type: What that broker is
        start: Its window start as written
        end: Its window end as written, '' when open
        max_ticks: Its tick cap, None when it runs by time
        enabled: False when the set switches it off
        decision_logic: Its decision logic type after the cascade
    """
    name: str
    symbol: str = ''
    data_broker_type: str = ''
    market_type: str = ''
    start: str = ''
    end: str = ''
    max_ticks: Optional[int] = None
    enabled: bool = True
    decision_logic: str = ''


class DirectoryListResponse(BaseModel):
    """Every configuration file that can start a run, newest-changed first."""
    key: List[str] = DIRECTORY_ROW_KEY
    rows: List[DirectoryRow]
    count: int


class DirectoryDetailResponse(BaseModel):
    """
    One configuration file: its row, its scenarios and the runs started from it.

    Args:
        row: The directory row
        scenarios: A scenario set's scenarios, read fresh from the file; empty for a profile and
            for an unreadable file
        runs: Run ids started from this file, newest first
    """
    # What makes one of `scenarios` unique (§49): a scenario is addressed by its name within its set.
    key: List[str] = ['name']
    row: DirectoryRow
    scenarios: List[DirectoryScenario] = []
    runs: List[str] = []
