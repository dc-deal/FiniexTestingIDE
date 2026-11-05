"""
FiniexTestingIDE - Verification Configuration Types
Switches that, by design, change no result.

Each one turns off an optimization the framework promises a strategy cannot see, so a run with it
turned off is the reference that proves the promise (#653). The default is always the framework's
ordinary behaviour; a scenario or a profile turns a switch off only to compare. The same block
lives in both pipelines: inside execution_config (Backtesting, 3-level cascade) and inside
autotrader.execution (AutoTrader, app_config → profile).
"""
from python.framework.types.config_types.strict_config_model import StrictConfigModel


class VerificationConfig(StrictConfigModel):
    """
    The verification switches of one run.

    portfolio_cache: true marks open positions lazily — only when something asks, which keeps the
    tick loop fast at spot; false keeps every open position current at every event that can change
    its figures (a new price, a clock advance, a fill, a read), the reference the lazy marking is
    compared against.
    """
    portfolio_cache: bool = True
