"""
FiniexTestingIDE - Deployment Resolver (#497)

Whether an AutoTrader session joins a deployment, starts one, or stands alone — answered once,
before the run header that carries the answer is written. Beside `dry_run_resolver.py`, because
both answer the same kind of question: what the profile declares, narrowed by the command line.
"""

from datetime import datetime

from python.framework.exceptions.live_execution_errors import OneOffInsideDeploymentError
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.persistence_types import ColdStartPayload
from python.framework.utils.run_id_utils import mint_run_id


def resolve_deployment(
    config: AutoTraderConfig,
    carried: ColdStartPayload,
    one_off: bool,
    new_deployment: bool,
    run_timestamp: datetime,
) -> str:
    """
    Decide whether this session joins a deployment, starts one, or stands alone (#497).

    The profile DECLARES; the command line may only narrow. That asymmetry is the whole
    design and it follows from one case: an unattended restart re-executes a command nobody
    typed, so a deployment declared on the command line would fragment at exactly the
    restarts it exists to span — silently, because a missing flag looks like a one-off.
    The flag whose absence is expensive lives in the profile; the flags whose absence is
    harmless live on the command line.

    `--new-deployment` is safe to forget for the same reason in reverse: forgetting it
    continues the existing deployment, which is the recoverable direction. `--one-off` on a
    bot that already has a deployment is refused with OneOffInsideDeploymentError — before the
    run header is written, so the refusal reaches the command line as a refused start.

    Args:
        config: The resolved profile — its `deployment` block
        carried: What the predecessor left in the carry-over — its deployment identity
        one_off: The `--one-off` flag
        new_deployment: The `--new-deployment` flag
        run_timestamp: This session's start, which names a newly minted deployment

    Returns:
        The deployment identity, or '' for a session that stands alone
    """
    if not config.deployment.continuous:
        return ''
    if one_off:
        # The intended ORDER is one-off first, continuous afterwards: a probe day, then
        # the deployment. Reversed, the flag stops meaning "do not record this" and starts
        # meaning "trade this account without appearing in its history".
        if carried.deployment_id:
            raise OneOffInsideDeploymentError(
                f'--one-off refused: this bot already belongs to deployment '
                f'{carried.deployment_id}.\n'
                f'A one-off start would still trade this account with real money — its '
                f'P&L would be MISSING from the deployment history while its drawdown '
                f'keeps running inside it, so two columns of one table would describe '
                f'different periods.\n'
                f'  • starting over on purpose?  --new-deployment\n'
                f'  • just trying something?     copy the profile and give it its own `bot_id`\n'
                f'  • only probing before you deploy? that is what --one-off is for, and '
                f"it belongs BEFORE the deployment's first start")
        return ''
    if new_deployment:
        return _mint_deployment_id(run_timestamp)
    return carried.deployment_id or _mint_deployment_id(run_timestamp)


def _mint_deployment_id(run_timestamp: datetime) -> str:
    """
    Mint the identity that groups a bot's sessions into one continuous deployment (#497).

    Minted at the FIRST session of a deployment and carried forward by every successor, so N
    session records become one history. It is an opaque KEY and must never be parsed: the
    timestamp is in it for a human reading a directory listing, exactly as a sweep id carries
    one, and anything that needs the start reads `curve_started_utc` instead.

    A profile name cannot do this job. The same bot deployed in July, stopped for a month and
    redeployed in September is two deployments, and from the outside the two look identical —
    which is why the key has to be written WHILE the sessions run rather than derived later.

    It carries a random half, minted the way a run id's is and for the same reason: the
    deployment history groups by this key alone, so two bots whose first sessions start in the
    same second — a supervisor bringing several up at once — would otherwise share one history.

    Args:
        run_timestamp: The first session's start (UTC)

    Returns:
        A deployment identity of the shape `deploy_<YYYYmmdd>_<HHMMSS>_<8 hex>`
    """
    return f'deploy_{mint_run_id(run_timestamp)}'
