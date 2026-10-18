"""
FiniexTestingIDE - Security Study CLI (#358)

Operator entry point for the security study: run its stages in order and certify them, or certify
the stages' latest runs again. Parameter reception only — the logic lives in the study runner and
in SecurityCertCertificate.

Usage:
    python python/cli/security_cert_cli.py run --release-version 1.5.0
    python python/cli/security_cert_cli.py run --from-stage restart
    python python/cli/security_cert_cli.py generate --latest --release-version 1.5.0
"""

import argparse
from pathlib import Path

from python.framework.acceptance_studies.security_cert_runner import (
    run_security_study,
    stage_ids,
)
from python.framework.acceptance_studies.security_cert_stages import SECURITY_CERT_PROFILE_DIR
from python.framework.reporting.certificates.security_cert_certificate import (
    DEFAULT_REPORTS_DIR,
    SecurityCertCertificate,
)
from python.framework.types.acceptance_study_types.security_cert_types import SecurityStageId


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands."""
    parser = argparse.ArgumentParser(description='Security study and its certificate (#358)')
    subparsers = parser.add_subparsers(dest='command', required=True)

    run = subparsers.add_parser(
        'run', help='Run every stage in order — REAL MONEY — and write the certificate')
    run.add_argument('--release-version', default='dev',
                     help="Release version (e.g. 1.5.0). Defaults to 'dev'.")
    run.add_argument('--comment', default='', help='Optional free-text note')
    run.add_argument(
        '--allow-dirty', action='store_true',
        help='Pass --allow-dirty to each stage: real orders from uncommitted code, recorded by '
             'each run. The certificate then fails on purpose — it is evidence only from a commit.')
    run.add_argument(
        '--from-stage', choices=stage_ids(),
        help='Resume at this stage; the stages before it are taken from their latest runs')
    run.add_argument(
        '--profile-dir', default=str(SECURITY_CERT_PROFILE_DIR),
        help='Where the stage profiles are read from — the mock rehearsal has its own')
    run.add_argument('--reports-dir', default=DEFAULT_REPORTS_DIR,
                     help='Target directory for the certificate')

    generate = subparsers.add_parser(
        'generate', help="Certify the stages' latest runs again — no trade")
    generate.add_argument(
        '--latest', action='store_true', required=True,
        help='Use the newest capture of every stage under the AutoTrader run root')
    generate.add_argument('--release-version', default='dev',
                          help="Release version (e.g. 1.5.0). Defaults to 'dev'.")
    generate.add_argument('--comment', default='', help='Optional free-text note')
    generate.add_argument(
        '--profile-dir', default=str(SECURITY_CERT_PROFILE_DIR),
        help='Where the stage profiles are read from — their names find the runs')
    generate.add_argument('--reports-dir', default=DEFAULT_REPORTS_DIR,
                          help='Target directory for the certificate')
    return parser


def main() -> None:
    """Parse arguments and delegate to the study runner or the certificate."""
    args = _build_parser().parse_args()

    if args.command == 'run':
        raise SystemExit(run_security_study(
            release_version=args.release_version,
            comment=args.comment,
            allow_dirty=args.allow_dirty,
            from_stage=SecurityStageId(args.from_stage) if args.from_stage else None,
            profile_dir=Path(args.profile_dir),
            reports_dir=args.reports_dir,
        ))

    if args.command == 'generate':
        captures = SecurityCertCertificate.find_latest_captures(
            profile_dir=Path(args.profile_dir))
        if not captures:
            raise SystemExit('No security study capture found under the AutoTrader run root '
                             '(file_logging.run_logs.autotrader)')
        SecurityCertCertificate.generate(
            captures,
            release_version=args.release_version,
            comment=args.comment,
            reports_dir=args.reports_dir,
        )


if __name__ == '__main__':
    main()
