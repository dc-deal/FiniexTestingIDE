"""
Certificate index (#486) — the read path over the four release-gate certificate families.

The certificates are the only data store this project COMMITS, and until now the least
reachable: each family's tests carried their own "find the newest" glob, and the four did not
agree — one sorted by the timestamp inside the document, another by the file name. This index
gives them one answer, and answers the release checklist's own question without opening four
directories: which certificate is newest per family, and has it expired.

Derived and gitignored like every other index: the committed certificates are the truth.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from python.framework.store.abstract_store_index import (
    AbstractStoreIndex,
    store_index_filename,
)
from python.framework.types.store_types import StoreId
from python.framework.utils.time_utils import parse_datetime

CERTIFICATE_INDEX_FILE = store_index_filename(StoreId.CERTIFICATES)
REPORTS_SUBDIR = 'reports'


class CertificateIndex(AbstractStoreIndex):
    """
    One row per committed certificate, across all families.

    Args:
        root: The tests tree the certificate families live under
    """

    COLUMNS: List[str] = [
        'family', 'file', 'path', 'release_version', 'timestamp', 'valid_until', 'git_commit',
    ]
    LOGIC_VERSION: int = 1

    def __init__(self, root: Path):
        super().__init__(Path(root) / CERTIFICATE_INDEX_FILE)
        self._root = Path(root)

    def certificate_files(self) -> List[Path]:
        """
        Every committed certificate below the root.

        Returns:
            Sorted certificate paths; empty when the root does not exist
        """
        if not self._root.exists():
            return []
        return sorted(self._root.glob(f'**/{REPORTS_SUBDIR}/*_report_*.json'))

    def rebuild(self) -> int:
        """
        Rebuild the index by reading every certificate.

        The FAMILY is derived from where the file lies, never from what the document claims:
        certificates written before `record_kind` existed carry no family of their own, and a
        store that could not index its own older entries would be answering a different question
        than the one asked.

        Returns:
            How many certificates were indexed
        """
        rows = []
        for path in self.certificate_files():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                # A certificate that cannot be parsed is still evidence that a file is there.
                # It is indexed with empty fields rather than dropped — an index that silently
                # omits entries is the failure mode this whole model exists to end.
                data = {}
            rows.append({
                'family': path.parent.parent.name,
                'file': path.name,
                'path': str(path),
                'release_version': data.get('release_version', ''),
                'timestamp': data.get('timestamp', ''),
                'valid_until': data.get('valid_until', ''),
                'git_commit': data.get('git_commit', ''),
            })
        self.write(pd.DataFrame(rows, columns=self.COLUMNS))
        return len(rows)

    def staleness_reason(self) -> Optional[str]:
        """
        Why the index may not be served.

        Returns:
            The reason, or None when it is current and newer than every certificate
        """
        code = super().staleness_reason()
        if code is not None:
            return code
        files = self.certificate_files()
        if not files:
            return None
        if self.get_path().stat().st_mtime_ns < max(f.stat().st_mtime_ns for f in files):
            return f'{len(files)} certificate(s) on disk, some newer than the index'
        return None

    def refreshed(self) -> pd.DataFrame:
        """
        The index contents, rebuilt first when they are not current.

        Correctness before speed, deliberately: this store holds seventeen documents read a
        handful of times per release, so the freshness check costs nothing worth saving — while
        a stale answer about which certificate is newest is exactly the answer a release gate
        must not get.

        Returns:
            The current index table
        """
        if not self.is_valid():
            self.rebuild()
        return self.read()

    def find_latest(self, family: str) -> Optional[Path]:
        """
        The newest certificate of one family.

        Ordered by the timestamp INSIDE the document rather than by the file name: the name is a
        convention, the field is the record. Where a certificate carries no timestamp it sorts
        last, which is the honest place for a document that cannot say when it was taken.

        Args:
            family: The family directory's name (e.g. 'live_field_study', 'benchmark')

        Returns:
            Path of the newest certificate, or None when the family has none
        """
        frame = self.refreshed()
        if frame.empty:
            return None
        hits = frame[frame['family'] == family]
        if hits.empty:
            return None
        hits = hits.sort_values(['timestamp', 'file'])
        return Path(hits.iloc[-1]['path'])

    def expired_families(self, now: Optional[datetime] = None) -> List[Tuple[str, str, str]]:
        """
        Families whose NEWEST certificate is past its validity window.

        Only the newest matters. An old certificate expiring is not a finding — it is what old
        certificates do, and listing them turns a release gate into four permanent lines of
        noise. The question a release actually asks is whether the certificate that WOULD be
        presented is still valid, and which version it names.

        Args:
            now: The instant to compare against; current UTC when not given

        Returns:
            (family, release_version, valid_until) per expired family, sorted by family
        """
        moment = now or datetime.now(timezone.utc)
        frame = self.refreshed()
        if frame.empty:
            return []
        rows = []
        for family in sorted(frame['family'].unique()):
            newest = self.find_latest(family)
            if newest is None:
                continue
            hit = frame[frame['path'] == str(newest)].iloc[0]
            if not hit['valid_until']:
                continue
            if parse_datetime(hit['valid_until']) < moment:
                rows.append((family, hit['release_version'] or '?', hit['valid_until']))
        return rows
