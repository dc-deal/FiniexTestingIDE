"""
FiniexTestingIDE - Carry-Over Continuity Checks (#358)

What the boot found in this bot's carry-over document, held against what the profile declares —
before the session writes anything, and before its first order.

The identity check (`carry_over_identity_validator`) answers whether two profiles may share one
document. This answers what THIS session may do with the document it just read, and the reason
both exist is a store that reads, modifies and writes back: whatever the boot ignores on read,
its first write replaces. Four rules, each closing one route by which a session would trade
against a state that is not its own or erase one that is:

  - a profile that declares `carry_over_from` must actually READ a carry-over: its cold start is
    on, and it is not an acceptance study that skips the adoption step
  - a declared continuation must FIND what it continues — its predecessor's document, or its own
    from an earlier run. Absent, unreadable or written by somebody else, it is a contradiction and
    not a first run: the later stage would otherwise start fresh, and a restart stage that starts
    fresh trades with no risk limit armed
  - a document its declared partner wrote is taken only from a run that COMPLETED — reached its
    ledger row. A run with a header and no ledger row is either still going or died, and the two
    look the same; either way two sessions must not manage one book
  - a document another profile wrote that still holds an open position or a session key is
    never replaced. The usual cause is a profile renamed while its `bot_id` stayed, and the
    write would leave the venue holding something no bot knows about
"""

from typing import Optional

from python.framework.exceptions.persistence_errors import (
    CarryOverContinuationError,
    CarryOverForeignClaimsError,
)
from python.framework.reporting.store.run_completion_audit import run_completed
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.persistence_types import CarryOverLoad, CarryOverLoadOutcome

# What each outcome a declared continuation cannot accept means to the operator
_NOT_FOUND = {
    CarryOverLoadOutcome.ABSENT: 'there is no carry-over document at all',
    CarryOverLoadOutcome.UNREADABLE: 'the carry-over document cannot be read',
    CarryOverLoadOutcome.OTHER_SCHEMA: 'the carry-over document is in another format',
    CarryOverLoadOutcome.FOREIGN: 'the carry-over document was written by another profile',
}


def validate_carry_over_continuity(
    profile_name: str,
    carry_over_from: str,
    cold_start_enabled: bool,
    adoption_skipped_by: Optional[str],
    load: Optional[CarryOverLoad],
    ledger: RunResultsLedger,
) -> None:
    """
    Refuse to start when the carry-over this session read cannot be used as its profile declares.

    Args:
        profile_name: This profile's run unit name — what the stores compare a writer against
        carry_over_from: The profile this one declares it continues, or empty
        cold_start_enabled: Whether this session's cold start runs at all
        adoption_skipped_by: The acceptance study that skips the adoption step, by its name;
            None when adoption runs
        load: What the boot's read of the document found; None when nothing was read
        ledger: The run ledger — asked only whether the run that wrote a partner's document
            completed

    Returns:
        None — raises CarryOverContinuationError when a declared continuation cannot be honoured,
        and CarryOverForeignClaimsError when another profile's document still speaks for the venue
    """
    if carry_over_from:
        _refuse_a_continuation_nothing_reads(
            profile_name, carry_over_from, cold_start_enabled, adoption_skipped_by)
    if load is None:
        return
    if load.outcome == CarryOverLoadOutcome.FOREIGN and load.holds_venue_claims():
        _refuse_foreign_claims(profile_name, load)
    if carry_over_from and load.outcome != CarryOverLoadOutcome.ACCEPTED:
        _refuse_missing_predecessor(profile_name, carry_over_from, load)
    if load.outcome == CarryOverLoadOutcome.ACCEPTED and load.writer_profile != profile_name:
        _refuse_an_unfinished_partner(profile_name, load, ledger)


def _refuse_a_continuation_nothing_reads(
    profile_name: str,
    carry_over_from: str,
    cold_start_enabled: bool,
    adoption_skipped_by: Optional[str],
) -> None:
    """
    Refuse a declared continuation this session would never read.

    The continuation is carried by the cold-start document, and only the cold-start step reads
    it — so a session without one would declare a predecessor and start as though it had none.

    Args:
        profile_name: This profile's run unit name
        carry_over_from: The profile it declares it continues
        cold_start_enabled: Whether its cold start runs
        adoption_skipped_by: The acceptance study skipping adoption, or None

    Returns:
        None — raises CarryOverContinuationError when nothing would read the carry-over
    """
    if not cold_start_enabled:
        raise CarryOverContinuationError(
            f"The profile '{profile_name}' declares `carry_over_from: '{carry_over_from}'` but "
            f'disables its cold start.\n'
            f'    The carry-over it continues is read by the cold-start step alone, so this '
            f'session would\n'
            f'    start as though nothing came before it. Enable `cold_start`, or remove the '
            f'declaration.')
    if adoption_skipped_by is not None:
        raise CarryOverContinuationError(
            f"The profile '{profile_name}' declares `carry_over_from: '{carry_over_from}'`, but "
            f'the {adoption_skipped_by}\n'
            f'    skips the cold-start adoption — the step that reads the carry-over it '
            f'continues.')


def _refuse_foreign_claims(profile_name: str, load: CarryOverLoad) -> None:
    """
    Refuse to start over another profile's document that still speaks for the venue.

    Args:
        profile_name: This profile's run unit name
        load: What the read found — a FOREIGN document holding venue claims

    Returns:
        None — always raises CarryOverForeignClaimsError
    """
    raise CarryOverForeignClaimsError(
        f"This bot's carry-over was last written by the profile '{load.writer_profile}' "
        f'(run {load.writer_run_id}),\n'
        f"    not by '{profile_name}', and it still holds {load.open_positions} open position(s) "
        f'and {load.session_keys} session key(s).\n'
        f'    Starting would replace it, and what it describes would stay at the venue with no '
        f'bot that\n'
        f'    knows about it. File: {load.path}\n'
        f'\n'
        f"    - Was this profile called '{load.writer_profile}' before? Then the name changed "
        f'while the `bot_id`\n'
        f"      stayed: restore `profile_name: '{load.writer_profile}'`.\n"
        f"    - Is '{load.writer_profile}' another bot? Then give this profile its own `bot_id`.")


def _refuse_missing_predecessor(
    profile_name: str,
    carry_over_from: str,
    load: CarryOverLoad,
) -> None:
    """
    Refuse a declared continuation that found nothing it may continue.

    Args:
        profile_name: This profile's run unit name
        carry_over_from: The profile it declares it continues
        load: What the read found — anything but an accepted document

    Returns:
        None — always raises CarryOverContinuationError
    """
    found = _NOT_FOUND.get(load.outcome, load.outcome.value)
    writer = (f" (last written by '{load.writer_profile}')"
              if load.outcome == CarryOverLoadOutcome.FOREIGN else '')
    raise CarryOverContinuationError(
        f"The profile '{profile_name}' continues '{carry_over_from}', but {found}{writer}.\n"
        f'    File: {load.path}\n'
        f"    Run '{carry_over_from}' first: this session is a later stage and starts from what "
        f'that one\n    leaves. Starting fresh would run it against a state it was not built for.')


def _refuse_an_unfinished_partner(
    profile_name: str,
    load: CarryOverLoad,
    ledger: RunResultsLedger,
) -> None:
    """
    Refuse a partner's document whose writing run never reached its ledger row.

    A run still going and a run that died look the same here — a header and no ledger row — so
    the message says "not completed" and never "aborted".

    Args:
        profile_name: This profile's run unit name
        load: What the read found — a document the declared partner wrote
        ledger: The run ledger

    Returns:
        None — raises CarryOverContinuationError when the writing run has not completed
    """
    if load.writer_run_id is not None and run_completed(ledger, load.writer_run_id):
        return
    run = f'run {load.writer_run_id}' if load.writer_run_id else 'a run that recorded no id'
    raise CarryOverContinuationError(
        f"This bot's carry-over was last written by '{load.writer_profile}' in {run}, which has "
        f'not completed —\n'
        f'    it has no ledger row. It may still be running, or it ended without its close.\n'
        f"    Two sessions must not manage one book: let it end, or run '{load.writer_profile}' "
        f'again so it\n'
        f"    completes, before starting '{profile_name}'. File: {load.path}")
