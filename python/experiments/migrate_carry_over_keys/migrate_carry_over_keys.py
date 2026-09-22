"""
FiniexTestingIDE - Carry-over key migration (#538, single-use)

Rename every carry-over document to the key the RESERVED separator produces.

Until 2026-09-22 both halves of `<profile>_<symbol>` were sanitised to `[a-z0-9_]`, so the
underscore occurred inside the halves as well as between them and two different bots could
resolve to one document. The halves now sanitise to `[a-z0-9-]`; the separator appears exactly
once, and the composition is injective.

**Every existing document is therefore orphaned until this runs.** A bot whose document it cannot
find reads its own holding as flat — at spot a holding is a balance the venue cannot describe as
a position, so the position book survives a restart only because we wrote it down. Run this before
the next live session, never during one.

Single-use by §27: nothing imports it, and it goes when the migration is done.

    python python/experiments/migrate_carry_over_keys/migrate_carry_over_keys.py --dry-run
    python python/experiments/migrate_carry_over_keys/migrate_carry_over_keys.py --apply
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from python.framework.persistence.carry_over_identity import carry_over_key  # noqa: E402

_ROOTS = (
    Path('data/runtime/cold_start_state'),
    Path('data/runtime/session_state'),
)


def planned_renames(root: Path) -> List[Tuple[Path, Path]]:
    """
    Which documents below one root carry a name the current rule would not produce.

    The identity is read from the document's OWN envelope rather than parsed back out of its
    file name — the file name is exactly what cannot be parsed back, which is the defect being
    migrated.

    Args:
        root: A carry-over store directory

    Returns:
        (old path, new path) for every document whose name has to change
    """
    if not root.exists():
        return []

    renames = []
    for path in sorted(root.glob('*.json')):
        try:
            envelope = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            print(f'  ⚠️  unreadable, left alone: {path.name}')
            continue

        profile, symbol = envelope.get('profile'), envelope.get('symbol')
        if not profile or not symbol:
            print(f'  ⚠️  no identity in the envelope, left alone: {path.name}')
            continue

        target = path.with_name(f'{carry_over_key(profile, symbol)}.json')
        if target != path:
            renames.append((path, target))
    return renames


def main() -> int:
    """
    Plan or apply the migration.

    Returns:
        Process exit code — 1 when a target name is already taken
    """
    parser = argparse.ArgumentParser(description='Rename carry-over documents to the new key')
    parser.add_argument('--apply', action='store_true', help='Perform the renames')
    parser.add_argument('--dry-run', action='store_true', help='Show them and change nothing')
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        parser.error('choose --dry-run or --apply')

    total = 0
    for root in _ROOTS:
        renames = planned_renames(root)
        print(f'\n{root}: {len(renames)} document(s) to rename')
        for old, new in renames:
            if new.exists():
                # Two documents mapping to one name is the very collision this fixes; refusing
                # is the only safe answer, because either file could be the live one.
                print(f'  ❌ {old.name} -> {new.name}  ALREADY EXISTS — resolve by hand')
                return 1
            print(f'  {old.name}  ->  {new.name}')
            if args.apply:
                old.rename(new)
        total += len(renames)

    print(f'\n{"renamed" if args.apply else "would rename"} {total} document(s)')
    if not args.apply:
        print('nothing was changed — re-run with --apply')
    return 0


if __name__ == '__main__':
    sys.exit(main())
