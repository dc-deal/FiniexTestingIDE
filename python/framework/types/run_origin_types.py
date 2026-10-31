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

The code stand is the ledger's short form of the code identity: what one session's row keeps of it,
so that two sessions of a deployment can be compared without opening their headers.
"""

from enum import StrEnum
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

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
OPERATOR_PRINCIPAL = 'operator'
# The client that is not a token: somebody at the terminal.
CONSOLE_CLIENT = 'console'


class RunOrigin(BaseModel):
    """
    Who or what started a run, for whom, and where (#551).

    Args:
        channel: How it was started — declared by the entry point
        client: The caller: `console` at a terminal, the API consumer name for a token
        principal: On whose behalf the run was started — `operator`, the console's own
            principal, or the account a token acts for, which may be a person or a service
        host: The installation's minted identity (`user_configs/host_identity.json`)
        allow_dirty: Whether the operator explicitly allowed real orders from uncommitted code
            (`--allow-dirty`); recorded because an override nobody can see afterwards is not one
        first_start: Whether the operator declared this the bot's first start (`--first-start`) —
            the one way a session trading real money starts without a carry-over (#598)
        accept_composite_change: The composite the operator let this one start continue what the
            venue still held under (`--accept-composite-change <key>`, #626) — recorded as typed,
            None when not given; the session's own record says whether it let anything through
    """
    channel: RunChannel
    client: str
    principal: str
    host: str
    allow_dirty: bool = False
    first_start: bool = False
    accept_composite_change: Optional[str] = None


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
        patch_ref: Where the patch restoring that delta was kept, None when nothing was: under
            `run_patches/` for this repository, INSIDE the repository itself for any other
            (`.finiex_run_patches/<sha256>.patch`), so private strategy code never leaves its own
            repository. A relative reference resolves against `root`; an absolute one stands for
            itself
        patch_excluded: Changed paths deliberately left out of the patch — credential homes. Their
            content is not copied into the patch store, and the diff hash records only that they
            changed
        restorable: Whether this repository's code can be put back exactly: a clean commit, or a
            dirty tree whose complete patch was stored. False for anything else, including a
            patch that could not cover a nested repository
        commit_subject: The commit's subject line (#559). None when it was not read, because only
            a session with a deployment reads it, or could not be
        recent_commits: The last commits on the first-parent line, newest first, each a 12-character
            prefix of its full hash; None on the same terms as the subject. A shallow clone lists
            only the commits it holds, so a commit missing here may still be an older one
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
    commit_subject: Optional[str] = None
    recent_commits: Optional[List[str]] = None

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

    def labelled_commits(self) -> List[Tuple[str, Optional[str]]]:
        """
        Each repository's commit under the word a reader knows it by.

        Returns:
            Label and commit per repository, this repository first; the commit is None where it is
            unknown
        """
        labels = repository_labels(state.root for state in self.repositories)
        framework_commit = self.framework.commit if self.framework is not None else None
        return [(FRAMEWORK_REPOSITORY, framework_commit),
                *((labels[state.root], state.commit) for state in self.repositories)]


# How a code stand names THIS repository. Its root says nothing: a deploy starts the next session
# from a new code tree, and the same repository would read as another one.
FRAMEWORK_REPOSITORY = 'framework'


def repository_labels(repositories: Iterable[str]) -> Dict[str, str]:
    """
    The word a reader knows each repository by.

    Args:
        repositories: The names a code stand gives them, this repository's among them or not

    Returns:
        Name → label: `framework`; `strategy` when one other repository is in view; `strategy` and
        its directory's name when several are. Never the whole root: it says nothing on another
        machine, and no path of the machine a run ran on is served
    """
    others = {name for name in repositories if name != FRAMEWORK_REPOSITORY}
    labels = {FRAMEWORK_REPOSITORY: FRAMEWORK_REPOSITORY}
    for name in others:
        labels[name] = ('strategy' if len(others) == 1
                        else f'strategy {PurePosixPath(name).name}')
    return labels


class RepositoryStand(BaseModel):
    """
    One repository's code as a session started on it, the way a ledger row keeps it (#559).

    Args:
        repository: `framework` for this repository, the root for any other one
        commit: A 12-character prefix of the commit where the history was read, the recorded short
            hash where it was not; two lengths of one commit, so a reader compares them by shared
            prefix. None when it could not be read, which is never the same as another stand
        dirty: Whether this is anything but exactly one known commit: uncommitted changes, no
            repository, or a state nobody could read
        diff_hash: The digest of what the working tree adds to the commit, None on a clean tree
        subject: The commit's subject line, None where it was not read
        recent_commits: The last commits on the first-parent line, newest first, None where they
            were not read. The position of an older commit in this list is how far the code moved
    """
    repository: str
    commit: Optional[str] = None
    dirty: bool
    diff_hash: Optional[str] = None
    subject: Optional[str] = None
    recent_commits: Optional[List[str]] = None


class CodeStand(BaseModel):
    """
    Which code a session started on, per repository: the ledger's `code_stand` (#559).

    Args:
        repositories: This repository first, then every other repository a component came from
    """
    repositories: List[RepositoryStand] = Field(default_factory=list)

    def is_unread(self) -> bool:
        """
        Whether this stand names nothing, because nobody could read the session's code identity.

        Returns:
            True for this repository alone with no commit, which is what such a session records;
            its other repositories are then unknown, not absent
        """
        return len(self.repositories) == 1 and self.repositories[0].commit is None
