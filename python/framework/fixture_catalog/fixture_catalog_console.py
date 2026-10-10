"""
The fixture catalog on the console (#576) — what the catalog command prints.
"""

from pathlib import Path
from typing import Dict, List, Optional

from python.framework.fixture_catalog.fixture_catalog import (
    DECLARED_RUN_COUNT,
    DECLARED_RUN_COUNT_SENTENCE,
)
from python.framework.types.fixture_catalog_types import FixtureEntry, FixtureProduction


def render_catalog(entries: List[FixtureEntry], current: Dict[str, FixtureProduction],
                   record: Path) -> None:
    """
    Print every entry with its current production.

    Args:
        entries: The catalog
        current: Entry id → its current production
        record: Where the production record lives
    """
    print('\n' + '=' * 80)
    print('🧷 Fixture Catalog — the runs a consumer pins')
    print('=' * 80)
    for entry in entries:
        production = current.get(entry.entry_id)
        state = (f'current since {production.produced_at[:19]}Z' if production
                 else 'no verified production yet')
        print(f'\n  {entry.entry_id}  ({entry.producer})  {state}')
        print(f'    {entry.title}')
        print(f'    from {entry.source} · for {", ".join(entry.consumers)}')
        if production:
            _print_ids(production)
    print(f'\n  Record: {record}\n')


def render_production_start(entry: FixtureEntry) -> None:
    """
    Announce that an entry is being produced.

    Args:
        entry: The catalog entry
    """
    print(f'\n🔄 Producing {entry.entry_id} — {entry.title}')


def render_production(entry: FixtureEntry, production: FixtureProduction,
                      superseded: Optional[FixtureProduction]) -> None:
    """
    Print what one production made and whether it carried every property.

    Args:
        entry: The catalog entry
        production: The production just recorded
        superseded: The production that was current before it, when there was one
    """
    mark = '✅' if production.verified else '❌'
    print(f'\n{mark} {entry.entry_id}: {len(production.run_ids)} run(s)')
    _print_ids(production)
    _print_failures(entry, production.failed_properties)
    if production.verified and superseded is not None:
        print(f'   Supersedes the production of {superseded.produced_at[:19]}Z — its runs stay '
              f'until the consumer has re-pinned; tell them the new ids in one message.')
    if not production.verified:
        _print_session_outcomes(production.session_outcomes)
        print('   Not current: the previous production, if any, stays the current one.')


def render_verification(entry: FixtureEntry, production: Optional[FixtureProduction],
                        failed: List[str]) -> None:
    """
    Print whether an entry's current production still carries every property.

    Args:
        entry: The catalog entry
        production: Its current production, None when it has none
        failed: The properties that no longer hold
    """
    if production is None:
        print(f'\n⚠️  {entry.entry_id}: no verified production to check — produce it first.')
        return
    mark = '✅' if not failed else '❌'
    print(f'\n{mark} {entry.entry_id}: the production of {production.produced_at[:19]}Z')
    _print_ids(production)
    _print_failures(entry, failed)


def _print_ids(production: FixtureProduction) -> None:
    """
    Print the identities a production made — what a consumer pins.

    Args:
        production: The production
    """
    for label, ids in (('runs', production.run_ids), ('deployments', production.deployment_ids),
                       ('sweeps', production.sweep_ids)):
        if ids:
            print(f'    {label:12} {", ".join(ids)}')


def _print_failures(entry: FixtureEntry, failed: List[str]) -> None:
    """
    Print each property that did not hold, with what it asserts.

    Args:
        entry: The catalog entry
        failed: The ids of the properties that did not hold
    """
    sentences = {prop.property_id: prop.sentence for prop in entry.properties}
    sentences[DECLARED_RUN_COUNT] = DECLARED_RUN_COUNT_SENTENCE
    for property_id in failed:
        print(f'    ✗ {property_id}: {sentences.get(property_id, "")}')


def _print_session_outcomes(outcomes: List[str]) -> None:
    """
    Print how each AutoTrader session of a production ended — the reason a session was killed,
    or could not be.

    Args:
        outcomes: One line per session
    """
    for outcome in outcomes:
        print(f'    · {outcome}')
