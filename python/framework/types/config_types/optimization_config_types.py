"""
Parameter Optimization config types.

Pydantic schema for a sweep spec (`configs/sweeps/<name>.json`): a base scenario set
reference + a parameter grid (dotted-path → list of candidate values) + the ranking
objective. The grid expands to the Cartesian product of fully-specified deterministic
batches; each batch records its KPIs in the run-results ledger.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class SweepSpec(BaseModel):
    """A parameter-sweep specification (grid search over a base scenario set)."""
    model_config = ConfigDict(extra='forbid')

    base_scenario_set: str          # scenario set file the grid varies (resolved like a normal set)
    grid: Dict[str, List[Any]]      # dotted path (decision_logic_config.<x> | workers.<name>.<x>) → values
    objective: str = 'expectancy'   # RunSummary currency field to rank by
    objective_currency: Optional[str] = None  # required only when the run has > 1 currency
    maximize: bool = True           # rank direction (False e.g. for max_drawdown / total_fees)
    sweep_name: str = ''            # defaults to the spec file stem
