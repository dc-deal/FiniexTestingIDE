"""
FiniexTestingIDE - Machine Resources Config Types (#595)

The thresholds the machine-resources route grades memory against. Memory alone: a busy processor
is the machine doing its work, while memory that runs out is the moment the kernel ends a process.
"""

from pydantic import model_validator

from python.framework.types.config_types.strict_config_model import StrictConfigModel


class MachineResourcesConfig(StrictConfigModel):
    """
    When the machine's memory is reported as under pressure.

    Args:
        memory_warn_pct: The share of the memory limit in use from which the level is `warn`
        memory_critical_pct: The share from which it is `critical`
    """

    memory_warn_pct: float = 80.0
    memory_critical_pct: float = 90.0

    @model_validator(mode='after')
    def _thresholds_in_order(self) -> 'MachineResourcesConfig':
        """
        Refuse thresholds that cannot grade: out of range, or a warning above the critical mark.

        Returns:
            The config unchanged when the thresholds are usable
        """
        if not 0 < self.memory_warn_pct < self.memory_critical_pct <= 100:
            raise ValueError(
                f'machine_resources: memory_warn_pct ({self.memory_warn_pct}) must lie above 0 '
                f'and below memory_critical_pct ({self.memory_critical_pct}), which may be '
                f'at most 100')
        return self
