"""
FiniexTestingIDE - Release Certificates CLI (#640)

Operator entry point: whether the four certificates describe this release and this code, asked once
before the tag. Parameter reception only — the logic lives in ReleaseCertificateCheck.

Usage:
    python python/cli/release_certificates_cli.py check --release-version 1.5.0
"""

import argparse

from python.framework.reporting.certificates.release_certificate_check import (
    ReleaseCertificateCheck,
)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands."""
    parser = argparse.ArgumentParser(description='The release certificates, asked once (#640)')
    subparsers = parser.add_subparsers(dest='command', required=True)

    check = subparsers.add_parser(
        'check', help='Exit non-zero unless all four describe this release and this code')
    check.add_argument('--release-version', required=True,
                       help='The release about to be tagged (e.g. 1.5.0)')
    return parser


def main() -> None:
    """Parse arguments and delegate to ReleaseCertificateCheck."""
    args = _build_parser().parse_args()

    if args.command == 'check':
        check = ReleaseCertificateCheck()
        result = check.run(args.release_version)
        check.print_result(result)
        raise SystemExit(0 if result.passed() else 1)


if __name__ == '__main__':
    main()
