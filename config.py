import os
from pathlib import Path

# Development Settings
DEV_MODE = os.getenv('FINIEX_DEV_MODE', 'false').lower() == 'true'
DEBUG_LOGGING = os.getenv('FINIEX_DEBUG', 'false').lower() == 'true'

# Importer Settings
MOVE_PROCESSED_FILES = os.getenv('FINIEX_MOVE_FILES', 'true').lower() == 'true'
DELETE_ON_ERROR = os.getenv(
    'FINIEX_DELETE_ON_ERROR', 'false').lower() == 'true'

# Paths
PROJECT_ROOT = Path(__file__).parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_FINISHED = PROJECT_ROOT / "data" / "finished"

print(
    f"🔧 Config loaded - DEV_MODE: {DEV_MODE}, MOVE_FILES: {MOVE_PROCESSED_FILES}")
