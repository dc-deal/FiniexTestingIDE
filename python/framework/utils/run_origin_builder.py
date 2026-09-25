"""
FiniexTestingIDE - Run Origin Builder (#551)

Builds the two blocks a run header states about where a run came from: its ORIGIN — who or what
started it, for whom, on which installation — and its CODE IDENTITY — which code it ran.

One unit for both header sites, the scenario set and the live session, so the two pipelines
cannot answer the same question two ways. The types these fill live in `run_origin_types.py`;
the git reads and the component resolution behind the code identity live in
`code_identity_builder.py`. What is decided HERE is only the wiring: which host, which client,
and where a dirty tree's patch is kept.
"""

from pathlib import Path
from typing import Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.host_identity_manager import HostIdentityManager
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.store.run_patch_store import RunPatchStore
from python.framework.types.run_origin_types import (
    CONSOLE_CLIENT,
    OPERATOR_PERSON,
    CodeIdentity,
    RunChannel,
    RunOrigin,
)
from python.framework.utils.code_identity_builder import PatchSink, build_code_identity


def build_run_origin(channel: RunChannel, allow_dirty: bool = False) -> RunOrigin:
    """
    State who or what started a run, for whom, and on which installation.

    Every channel that exists today starts at a terminal, so the client is `console` and the
    person is `operator` — never empty, and never an API account: the console operator is a
    principal of its own. The `api` channel, where a consumer token names its client and the
    account it is bound to, is future work: nothing constructs a run from a request until the
    first write route exists (#552), so nothing here reads a token yet.

    The host is the installation's minted identity. A broken identity file refuses here, before
    the header that would state it is written — a run must not record an identity nobody can
    trust.

    Args:
        channel: How the run was started, as the entry point DECLARES it
        allow_dirty: Whether the operator allowed real orders from uncommitted code
            (`--allow-dirty`); only a live session can carry it

    Returns:
        The run's origin
    """
    return RunOrigin(
        channel=channel,
        client=CONSOLE_CLIENT,
        person=OPERATOR_PERSON,
        host=HostIdentityManager().get_host_id(),
        allow_dirty=allow_dirty,
    )


def capture_code_identity(strategy_configs: List[Dict]) -> CodeIdentity:
    """
    Capture which code a run is about to run, keeping a dirty tree's patch in `run_patches/`.

    Called at the START, before the header is written. The git reads behind it are cached per
    process and per repository (§42), so the ledger's own git read at the end of the run reuses
    them instead of paying for them a second time.

    Args:
        strategy_configs: Every strategy_config the run will execute

    Returns:
        The run's code identity
    """
    store = RunPatchStore(Path(AppConfigManager().get_run_patches_path()))
    return build_code_identity(strategy_configs, patch_sink=_patch_sink(store))


def _patch_sink(store: RunPatchStore) -> PatchSink:
    """
    A sink that keeps a patch in the store and never stops the run for it.

    Never fatal, for the same reason registering a run's configuration is not: a patch that could
    not be kept costs its restorability and nothing else. The diff hash is still recorded, so the
    run stays identifiable, and a header whose `patch_ref` is empty says honestly that the patch
    was not kept.

    Args:
        store: The run-patch store

    Returns:
        The sink the code identity builder calls for each dirty repository
    """
    def keep(patch_hash: str, patch: bytes) -> Optional[str]:
        """
        Keep one patch.

        Args:
            patch_hash: The SHA256 of the patch bytes — the store's key, which is not the
                header's `diff_hash` (a digest of the changed content, not of this rendering)
            patch: The patch bytes

        Returns:
            Where the patch was kept, or None when it could not be
        """
        try:
            return store.put(patch_hash, patch)
        except OSError as error:
            get_global_logger().warning(
                f'⚠️ Run patch {patch_hash[:12]} could not be kept ({error}) — the run records '
                f'its diff hash, but its uncommitted code cannot be restored from the store')
            return None

    return keep
