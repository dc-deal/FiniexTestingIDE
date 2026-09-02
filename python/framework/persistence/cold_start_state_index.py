"""
Cold-start state index (#355) — the read path over the framework's own carry-over.

One row per bot that carries cold-start state. The bot itself never needs this: it opens its
own document by key, which is not a search. What needs it is the question an operator asks
after a 03:00 restart — WHICH bot carries what, since when, from which run — and any later
diagnostics layer asking the same across a fleet.

It answers WHAT IS CARRIED NOW, not what happened. A carry-over overwrites by definition
(§44), so it has no history to offer: what a given boot adopted belongs to that run's RECORD,
which is immutable and already indexed. A diagnostics reader joins the two.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd
from pydantic import ValidationError

from python.framework.store.abstract_store_index import (
    AbstractStoreIndex,
    store_index_filename,
)
from python.framework.types.persistence_types import CarryOverEnvelope, ColdStartPayload
from python.framework.types.store_types import StoreId

COLD_START_INDEX_FILE = store_index_filename(StoreId.COLD_START_STATE)


class ColdStartStateIndex(AbstractStoreIndex):
    """
    One row per bot carrying framework carry-over state.

    Args:
        state_root: The `cold_start_state` directory
    """

    COLUMNS: List[str] = [
        'profile', 'symbol', 'file', 'saved_at_utc', 'written_by_run_id',
        'session_keys', 'highest_position_counter', 'status', 'modified_at',
    ]
    LOGIC_VERSION: int = 1

    def __init__(self, state_root: Path):
        super().__init__(Path(state_root) / COLD_START_INDEX_FILE)
        self._root = Path(state_root)

    def state_files(self) -> List[Path]:
        """
        Every bot's carry-over document below the root.

        Returns:
            Sorted document paths; empty when the root does not exist
        """
        if not self._root.exists():
            return []
        return sorted(self._root.glob('*.json'))

    def staleness_reason(self) -> Optional[str]:
        """
        Why the index may not be served.

        The row COUNT is compared as well as the timestamps, and that is not belt-and-braces:
        removing a bot deletes its document and leaves every surviving document's mtime
        untouched, so a purely time-based rule would keep reporting a bot that is gone.

        Returns:
            The reason, or None when the index still describes the directory
        """
        code = super().staleness_reason()
        if code is not None:
            return code

        files = self.state_files()
        rows = len(self.read())
        if len(files) != rows:
            return f'{len(files)} state file(s) on disk, {rows} row(s) indexed'

        index_mtime = self.get_path().stat().st_mtime
        if any(f.stat().st_mtime > index_mtime for f in files):
            return 'a state file is newer than the index'
        return None

    def rebuild(self) -> int:
        """
        Rebuild the index from the documents themselves.

        A document that cannot be parsed still gets a ROW, marked `status='unreadable'`. Two
        reasons, and the second is the one that bit: refusing to describe nine healthy bots
        because a tenth file is damaged would fail the read path in the very case an operator
        opens it for — and SKIPPING the tenth would leave the row count below the file count,
        which is exactly what `staleness_reason` measures. A skip would therefore build an
        index that could never satisfy its own validity gate. Describing the damage keeps both
        promises: the index says what is there, and what is there includes a broken file.

        Returns:
            Number of rows written — one per document, readable or not
        """
        rows = []
        for path in self.state_files():
            try:
                envelope = CarryOverEnvelope.model_validate_json(path.read_bytes())
                payload = ColdStartPayload.model_validate(envelope.snapshot)
            except (ValidationError, OSError, ValueError):
                rows.append({
                    'profile': '', 'symbol': '', 'file': path.name, 'saved_at_utc': '',
                    'written_by_run_id': '', 'session_keys': 0,
                    'highest_position_counter': 0, 'status': 'unreadable',
                    'modified_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
                })
                continue
            rows.append({
                'profile': envelope.profile,
                'symbol': envelope.symbol,
                'file': path.name,
                'saved_at_utc': envelope.saved_at_utc,
                'written_by_run_id': envelope.written_by_run_id or '',
                'session_keys': len(payload.session_keys),
                'highest_position_counter': payload.highest_position_counter,
                'status': 'ok',
                'modified_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
            })

        self.write(pd.DataFrame(rows, columns=self.COLUMNS))
        return len(rows)
