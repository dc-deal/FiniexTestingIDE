"""
FiniexTestingIDE - AutoTrader Logger Bundle

What `create_autotrader_loggers` hands back: the session's three log channels plus the run
identity that is minted together with the run directory.

A bundle rather than a tuple, and here the hazard was the sharpest in the project: THREE of
the five returns are the same type (`ScenarioLogger`), returned positionally into three
attributes. Swapping two of them is invisible to the type checker, to every test, and to the
reader — and the consequence is not cosmetic, because which channel a message goes to decides
whether it reaches the session summary and the error pot (§35) or only `global.log`.

The tuple annotation had also drifted: it declared four elements while the function returned
five. Nothing caught that either.

Lives beside its builder rather than in `framework/types/` (§6): the loggers are live
collaborators.
"""

from dataclasses import dataclass
from pathlib import Path

from python.framework.logging.scenario_logger import ScenarioLogger


@dataclass
class AutotraderLoggerBundle:
    """
    One live session's log channels and run identity.

    Args:
        global_logger: Startup phases, shutdown and errors (file plus direct console print)
        session_logger: Per-tick processing; the operator-relevant channel that reaches the
            session summary, so anything the operator must see goes here (§35)
        summary_logger: The post-session summary (file plus console flush)
        run_dir: The run's output directory, created here
        run_id: The run identity minted with that directory
    """
    global_logger: ScenarioLogger
    session_logger: ScenarioLogger
    summary_logger: ScenarioLogger
    run_dir: Path
    run_id: str
