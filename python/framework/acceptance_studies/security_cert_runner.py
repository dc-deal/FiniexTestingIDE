"""
FiniexTestingIDE - Security Study Runner (#358)

Runs the security study's stages one after another, each as an AutoTrader session of its own, and
writes the certificate once every stage ran. The order matters — stage 3 reads the baseline stage
2 wrote, stage 5 takes over the lot stage 4 kept — so a stage that fails stops the study: what
would follow it measures nothing, and a real-money stage after a failed one only costs.

A stage is judged by its own capture as soon as it ends; the certificate later judges all of them
together, against each other.
"""

import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.autotrader.broker_config_fetcher_factory import BrokerConfigFetcherFactory
from python.framework.acceptance_studies.security_cert_stages import (
    SECURITY_CERT_PROFILE_DIR,
    SECURITY_STAGES,
    stage_profile_path,
)
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.logging.global_logger import GlobalLogger
from python.framework.reporting.certificates.security_cert_certificate import (
    DEFAULT_REPORTS_DIR,
    SecurityCertCertificate,
)
from python.framework.types.acceptance_study_types.security_cert_types import SecurityStageId
from python.framework.types.trading_env_types.broker_types import BrokerType

# The AutoTrader entry point each stage runs through — the same one an operator starts by hand
_AUTOTRADER_CLI = 'python/cli/autotrader_cli.py'


def run_security_study(
    release_version: str = 'dev',
    comment: str = '',
    allow_dirty: bool = False,
    from_stage: Optional[SecurityStageId] = None,
    profile_dir: Path = SECURITY_CERT_PROFILE_DIR,
    run_root: Optional[str] = None,
    reports_dir: str = DEFAULT_REPORTS_DIR,
) -> int:
    """
    Run every stage in order, stop at the first that fails, and write the certificate.

    Args:
        release_version: Version the certificate names (or 'dev')
        comment: Optional free-text note for the certificate
        allow_dirty: Pass `--allow-dirty` to each stage — real orders from uncommitted code, which
            each run records; the certificate then fails on purpose
        from_stage: Resume at this stage — the ones before it are taken from their latest runs
        profile_dir: Where the stage profiles are read from (the mock rehearsal's are elsewhere)
        run_root: The AutoTrader run root the captures are found under; None resolves config
        reports_dir: Where the certificate is written

    Returns:
        0 when every stage passed and the certificate was written, 1 otherwise
    """
    stages = list(SECURITY_STAGES)
    start = 0
    if from_stage is not None:
        start = next(i for i, s in enumerate(stages) if s.stage_id == from_stage)

    # Checked before anything is sent, so an account the study cannot use costs nothing. Not on a
    # resume at the last stage: that one starts from the lot the stage before it kept on purpose.
    if stages[start].stage_id != SecurityStageId.HARD_RESTART:
        refusals = _account_refusals(stage_profile_path(stages[start], profile_dir))
        if refusals:
            _print_refusal(refusals)
            return 1

    for index, stage in enumerate(stages[start:], start=start):
        profile = stage_profile_path(stage, profile_dir)
        print(f"\n{'=' * 68}\n  🛡️  Security study — stage {index + 1}/{len(stages)}: "
              f"{stage.stage_id.value}\n     {profile}\n{'=' * 68}")
        started = time.time()
        command = [sys.executable, _AUTOTRADER_CLI, 'run', '--config', str(profile)]
        if allow_dirty:
            command.append('--allow-dirty')
        # The exit code is not the verdict: the hard-stop stages end their sessions on purpose,
        # and an emergency end is graded as one. The stage's own capture says what happened.
        subprocess.run(command, check=False)

        capture = _capture_since(stage.stage_id, started, profile_dir, run_root)
        if capture is None:
            print(f'  ❌ Stage {stage.stage_id.value} wrote no capture — the study stops here.')
            return 1
        failures = SecurityCertCertificate.stage_failures(stage.stage_id, capture)
        if failures:
            print(f'  ❌ Stage {stage.stage_id.value} failed — the study stops here:')
            for failure in failures:
                print(f'     · {failure}')
            for hint in _hints(stage.stage_id, failures):
                print(f'     → {hint}')
            return 1
        print(f'  ✅ Stage {stage.stage_id.value} passed → {capture}')

    captures = SecurityCertCertificate.find_latest_captures(run_root, profile_dir)
    certificate = SecurityCertCertificate.generate(
        captures, release_version=release_version, comment=comment, reports_dir=reports_dir)
    print(f'  📜 Certificate → {certificate}')
    return 0


def _capture_since(
    stage_id: SecurityStageId,
    started: float,
    profile_dir: Path,
    run_root: Optional[str],
) -> Optional[Path]:
    """
    The capture the stage just wrote — its newest, and only if it is newer than the stage's start.

    Args:
        stage_id: The stage
        started: When the runner started it (epoch seconds)
        profile_dir: Where its profile was read from
        run_root: The AutoTrader run root; None resolves config

    Returns:
        The capture file, or None when the stage wrote none
    """
    captures: Dict[SecurityStageId, Path] = SecurityCertCertificate.find_latest_captures(
        run_root, profile_dir)
    capture = captures.get(stage_id)
    if capture is None or capture.stat().st_mtime < started:
        return None
    return capture


def _account_refusals(profile: Path) -> List[str]:
    """
    What about the account would make the study measure nothing — read from the venue, free.

    A held base moves the account value the breaker judges with every tick, and against limits
    this small it would trip them at a moment nobody chose. A base the venue does not report at
    all would refuse the session at its start. Only a live profile is checked; a mock rehearsal
    has no venue to ask.

    Args:
        profile: The profile of the first stage that will run

    Returns:
        One sentence per problem, each naming what was found and what to do; empty when the
        account is as the study needs it
    """
    config = load_autotrader_config(str(profile))
    if config.adapter_type != 'live':
        return []
    fetcher = BrokerConfigFetcherFactory.create(
        broker_type=config.broker_type, logger=GlobalLogger('SecurityStudy'))
    spec = BrokerConfigFactory.from_serialized_dict(
        BrokerType(config.broker_type),
        fetcher.fetch_broker_config_with_cache(symbol=config.symbol,
                                               broker_type=config.broker_type),
    ).get_symbol_specification(config.symbol)
    base_ccy, quote_ccy = spec.base_currency, spec.quote_currency
    base = fetcher.fetch_account_balance(base_ccy)
    quote = fetcher.fetch_account_balance(quote_ccy)

    refusals: List[str] = []
    if base is None:
        refusals.append(
            f'The venue reports no {base_ccy} balance for this account, so a session on '
            f'{config.symbol} would refuse to start. → Run the study on a pair whose base the '
            f'venue lists for the account.')
    elif base >= spec.volume_min:
        refusals.append(
            f'The account holds {base:.8f} {base_ccy}. The study judges the account value of '
            f'{config.symbol}, and a held {base_ccy} moves it with every tick — limits this small '
            f'would trip at a moment nobody chose. → Sell the {base_ccy} first, or run the study '
            f'on a pair whose base the account does not hold.')
    if quote is None or quote <= 0.0:
        refusals.append(
            f'The account holds no {quote_ccy}. The study buys the smallest {config.symbol} order '
            f'({spec.volume_min:g} {base_ccy}) and pays in {quote_ccy}. → Fund the account in '
            f'{quote_ccy}.')
    return refusals


def _print_refusal(refusals: List[str]) -> None:
    """
    Say, loudly, why the study will not start.

    Args:
        refusals: One sentence per problem
    """
    print(f"\n{'=' * 68}\n  ❌ SECURITY STUDY REFUSED — the account is not as the study needs it"
          f"\n{'=' * 68}")
    for refusal in refusals:
        print(f'  · {refusal}')
    print('  Nothing was sent. No stage ran.\n')


def _hints(stage_id: SecurityStageId, failures: List[str]) -> List[str]:
    """
    What to do after a failed stage — the failures say what happened, this says what next.

    Args:
        stage_id: The stage that failed
        failures: Its failed checks

    Returns:
        One sentence per hint
    """
    hints: List[str] = []
    if any('denied (insufficient_funds), expected filled' in f for f in failures):
        hints.append('The account cannot pay for the smallest order — fund it in the quote '
                     'currency.')
    if stage_id in (SecurityStageId.HARD_KEPT, SecurityStageId.HARD_RESTART):
        hints.append('The account may still hold the lot the hard stop kept. Resume with '
                     '`--from-stage hard_restart` to have the stop sell it, or sell it by hand '
                     'before the study runs again.')
    hints.append(f'Fix the cause and resume with `--from-stage {stage_id.value}`; the stages '
                 f'before it count from their latest runs.')
    return hints


def stage_ids() -> List[str]:
    """
    The stage ids in order, for a command line that offers them.

    Returns:
        Stage ids as their values
    """
    return [stage.stage_id.value for stage in SECURITY_STAGES]
