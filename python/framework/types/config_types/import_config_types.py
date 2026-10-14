"""
FiniexTestingIDE - Import Config Types
Pydantic schema for the import screen's settings in import_config.json.
"""

from pydantic import Field

from python.framework.types.config_types.strict_config_model import StrictConfigModel


class ImportDisplayConfig(StrictConfigModel):
    """
    The live screen an import shows (#175). Mirrored in configs/import_config.json → display.

    Args:
        enabled: Show the screen; without it the import logs line by line as before. The
            screen also stays off when stdout is not a terminal, whatever this says
        update_interval_ms: How often the screen redraws and the import publishes a frame
    """
    enabled: bool = True
    update_interval_ms: int = Field(default=250, ge=50)
