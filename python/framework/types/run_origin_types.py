"""
FiniexTestingIDE - Run Origin & Code Identity Types (#551)

What a run header says about WHERE a run came from and WHICH code it ran. Pydantic, because both
travel inside `RunHeader`, which is serialised into every run's `header.json`.

Two questions, deliberately two models. The origin says who or what started the run, on whose
behalf and on which installation — the identity a write surface will need before it can exist.
The code identity says which code ran: this repository's state, the state of every other
repository a strategy was loaded from, and one entry per component the run resolved. A run that
records only this repository's commit cannot say which STRATEGY ran, because a strategy may live
in a repository this one ignores.
"""

from enum import StrEnum
from typing import List, Optional

from pydantic import BaseModel, Field


class RunChannel(StrEnum):
    """
    How a run was started — DECLARED by the entry point, never inferred.

    Detecting pytest from the environment would be a heuristic, and a heuristic in a provenance
    field is a guess recorded as a fact. So every entry point states its channel, and whatever
    constructs a run in code without stating one is `DIRECT`.
    """
    CLI = 'cli'
    SWEEP = 'sweep'
    DIRECT = 'direct'
    API = 'api'


# The console operator — a principal of its own, and never an account the API can bind a token to.
OPERATOR_PERSON = 'operator'
# The client that is not a token: somebody at the terminal.
CONSOLE_CLIENT = 'console'


class RunOrigin(BaseModel):
    """
    Who or what started a run, for whom, and where (#551).

    Args:
        channel: How it was started — declared by the entry point
        client: The caller: `console` at a terminal, the API consumer name for a token
        person: The account the client acts for — `operator` at the console
        host: The installation's minted identity (`user_configs/host_identity.json`)
        allow_dirty: Whether the operator explicitly allowed real orders from uncommitted code
            (`--allow-dirty`); recorded because an override nobody can see afterwards is not one
    """
    channel: RunChannel
    client: str
    person: str
    host: str
    allow_dirty: bool = False


class RepositoryState(BaseModel):
    """
    One repository's contribution to a run's code (#551).

    Args:
        root: The repository's top-level directory, or the directory a component was loaded from
            when it lies in no repository (or when git could not say)
        in_repository: True inside a repository, False when that directory is under no version
            control — or IGNORED by the repository around it, which no commit can contain either —
            and None when git itself could not run, so nobody knows. Only True can be a commit
        commit: Short commit hash, None when unknown
        branch: The branch checked out AT CAPTURE, read with the commit — so a ledger row written
            at the end of a thirty-day session never pairs the start's commit with the branch
            that happens to be checked out at its end
        dirty: Whether the working tree differs from the commit (untracked files count, and so
            does an index entry flagged assume-unchanged or skip-worktree)
        uncommitted_count: How many porcelain entries made it dirty
        changes: The first porcelain lines, so a refusal can say WHAT is uncommitted
        diff_hash: SHA256 over the CONTENT of every changed path — path, executable bit and bytes,
            or a deletion marker — in path order. Taken over content and never over git's
            rendering of a diff, whose bytes move with configuration, git version and object
            count; equal delta, equal digest. None on a clean tree
        patch_ref: Where the patch restoring that delta was stored (`run_patches/`), None when
            nothing was stored
        patch_excluded: Changed paths deliberately left out of the patch — credential homes. Their
            content is not copied into the patch store, and the diff hash records only that they
            changed
        restorable: Whether this repository's code can be put back exactly: a clean commit, or a
            dirty tree whose complete patch was stored. False for anything else, including a
            patch that could not cover a nested repository
    """
    root: str
    in_repository: Optional[bool] = True
    commit: Optional[str] = None
    branch: Optional[str] = None
    dirty: bool = False
    uncommitted_count: int = 0
    changes: List[str] = Field(default_factory=list)
    diff_hash: Optional[str] = None
    patch_ref: Optional[str] = None
    patch_excluded: List[str] = Field(default_factory=list)
    restorable: bool = False

    def is_committed(self) -> bool:
        """
        Whether this repository's code is exactly one known commit.

        Returns:
            False when the tree is dirty, lies in no repository, or git could not read it
        """
        return not self.dirty and self.in_repository is True and self.commit is not None


class ComponentRole(StrEnum):
    """What a resolved component is in the run — the closed vocabulary both writer and reader use."""
    DECISION = 'decision'
    WORKER = 'worker'


class ComponentIdentity(BaseModel):
    """
    One component the run resolved — a decision logic or a worker instance (#551).

    Args:
        role: A decision logic or a worker instance
        name: The worker instance name; the decision logic's type for the decision
        type: The type string the configuration named (`CORE/rsi` or a file path)
        version: The version the component declares (§39) — the author's INTENT, which the
            digest below does not depend on
        source_path: The file the class was loaded from
        repository: The root of the repository it belongs to, None when it lies in none
        package_digest: SHA256 over the package directory's files (USER components only). Taken
            over content and not over git's tree id on purpose: the same code yields the same
            digest whether or not it is committed, so two runs can be compared by it directly.
            A component whose file sits directly in a repository's root is digested alone, since
            that repository as a whole is not its package
    """
    role: ComponentRole
    name: str
    type: str
    version: str = ''
    source_path: Optional[str] = None
    repository: Optional[str] = None
    package_digest: Optional[str] = None


class CodeIdentity(BaseModel):
    """
    Which code a run ran, captured at its START (#551).

    Args:
        framework: This repository
        repositories: Every OTHER repository a component was loaded from, once each
        components: One entry per resolved decision logic and worker instance
    """
    framework: Optional[RepositoryState] = None
    repositories: List[RepositoryState] = Field(default_factory=list)
    components: List[ComponentIdentity] = Field(default_factory=list)

    def is_dirty(self) -> bool:
        """
        Whether the code that ran cannot be reproduced from commits alone.

        An UNKNOWN framework state counts: when git could not answer, nothing says the tree was
        clean, and a guard reading "not dirty" there would pass exactly the run it exists to stop.

        Returns:
            True when any repository is uncommitted, unversioned or could not be read
        """
        if self.framework is None:
            return True
        states = [self.framework] + list(self.repositories)
        return any(not state.is_committed() for state in states)
