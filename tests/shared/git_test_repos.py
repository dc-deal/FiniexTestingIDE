"""
FiniexTestingIDE - Temporary Git Repositories for Tests

Plain helpers that build real repositories in a test's temporary directory, for the suites that
pin how a run's code is identified (#551). Real repositories rather than a scripted `git`: the
subject of those tests is what git ANSWERS — under a user's configuration, for an ignored file, in
a SHA-256 repository — and a stand-in would only answer what its author expected.

Convention: not pytest fixtures — call them from the test or from a suite's own fixture. The
helpers' OWN git calls ignore the developer's global and system configuration and carry their own
identity, so a `core.autocrlf=true` checkout or a missing `user.name` cannot fail a test. The code
under test is left alone: that it answers the same under any configuration is what is tested.
"""

import os
import subprocess
from pathlib import Path
from typing import Dict, Optional, Union

# The identity every test commit is made under — a missing global `user.name` must not fail one.
_IDENTITY = ('-c', 'user.name=test', '-c', 'user.email=test@test')


def run_git(repo: Path, *args: str) -> str:
    """
    Run one git command in a test repository and insist that it succeeds.

    Args:
        repo: The directory git is pointed at
        args: The git arguments

    Returns:
        The command's standard output, stripped
    """
    result = subprocess.run(['git', '-C', str(repo), *_IDENTITY, *args],
                            capture_output=True, text=True, check=True, env=_isolated_env())
    return result.stdout.strip()


def _isolated_env() -> Dict[str, str]:
    """
    The environment for a helper's own git call: no global and no system configuration.

    Returns:
        A copy of the current environment with both configuration layers switched off
    """
    return dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')


def write_files(root: Path, files: Dict[str, Union[str, bytes]]) -> None:
    """
    Write files below a root, creating directories as needed.

    Args:
        root: The directory the paths are relative to
        files: Relative path → text (str) or raw content (bytes)
    """
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding='utf-8')


def make_repo(path: Path, files: Dict[str, Union[str, bytes]],
              object_format: Optional[str] = None) -> Path:
    """
    Create a repository holding one commit of the given files.

    Args:
        path: Where the repository is created
        files: The committed files
        object_format: 'sha256' for a SHA-256 repository, None for git's default

    Returns:
        The repository's top-level directory, as git reports it
    """
    path.mkdir(parents=True)
    init = ['init', '-q'] + ([f'--object-format={object_format}'] if object_format else [])
    run_git(path, *init)
    write_files(path, files)
    run_git(path, 'add', '-A')
    run_git(path, 'commit', '-q', '-m', 'initial')
    return Path(run_git(path, 'rev-parse', '--show-toplevel'))


def commit_all(repo: Path, message: str = 'change') -> None:
    """
    Stage everything and commit it.

    Args:
        repo: The repository
        message: The commit message
    """
    run_git(repo, 'add', '-A')
    run_git(repo, 'commit', '-q', '-m', message)


def working_tree(root: Path) -> Dict[str, bytes]:
    """
    Every file of a working tree except git's own and bytecode caches.

    Args:
        root: The working tree

    Returns:
        Relative path → bytes
    """
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob('*'))
        if path.is_file() and '.git' not in path.parts and '__pycache__' not in path.parts
    }


def restore_on_fresh_clone(source: Path, commit: Optional[str], patch: bytes,
                           target: Path) -> Path:
    """
    Put a recorded tree back the way a reader of a run header would: clone, check out the
    recorded commit, apply the stored patch with `git apply --binary`.

    Args:
        source: The repository the run ran from
        commit: The recorded commit, None for a repository that had none yet — the patch is then
            applied onto an empty repository of the same object format
        patch: The stored patch bytes
        target: Where the fresh checkout is created

    Returns:
        The restored working tree
    """
    if commit is None:
        object_format = run_git(source, 'rev-parse', '--show-object-format')
        target.mkdir(parents=True)
        run_git(target, 'init', '-q', f'--object-format={object_format}')
    else:
        subprocess.run(['git', 'clone', '-q', str(source), str(target)], check=True,
                       capture_output=True, env=_isolated_env())
        run_git(target, 'checkout', '-q', commit)
    patch_file = target.parent / f'{target.name}.patch'
    patch_file.write_bytes(patch)
    if patch:
        run_git(target, 'apply', '--binary', str(patch_file))
    return target


def sha256_repositories_supported(scratch: Path) -> bool:
    """
    Whether the installed git can create a SHA-256 repository at all.

    Args:
        scratch: A directory the probe repository may be created in

    Returns:
        True when `git init --object-format=sha256` succeeds
    """
    probe = scratch / 'sha256_probe'
    probe.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['git', '-C', str(probe), 'init', '-q', '--object-format=sha256'],
                            capture_output=True, env=_isolated_env())
    return result.returncode == 0
