"""
FiniexTestingIDE - Field Study Certificate CLI (#332)

Operator entry point: generate a PASS/FAIL acceptance certificate from a Field Study
JSONL run. Parameter reception only — all logic lives in FieldStudyCertificate.

Usage:
    python python/cli/field_study_certificate_cli.py generate --latest --release-version 1.3.0
    python python/cli/field_study_certificate_cli.py generate --jsonl <path> --comment "..."
"""

import argparse

from python.framework.reporting.field_study_certificate import (
    DEFAULT_REPORTS_DIR,
    FieldStudyCertificate,
)


def _build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        description='Field Study acceptance certificate (#332)'
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    generate = subparsers.add_parser(
        'generate',
        help='Generate a PASS/FAIL certificate from a Field Study JSONL run',
    )
    source = generate.add_mutually_exclusive_group(required=True)
    source.add_argument('--jsonl', help='Path to the run field_study.jsonl')
    source.add_argument(
        '--latest', action='store_true',
        help='Use the newest field_study.jsonl under logs/autotrader',
    )
    generate.add_argument(
        '--release-version', default='dev',
        help="Release version (e.g. 1.3.0). Defaults to 'dev'.",
    )
    generate.add_argument('--comment', default='', help='Optional free-text note')
    generate.add_argument(
        '--reports-dir', default=DEFAULT_REPORTS_DIR,
        help='Target directory for the certificate',
    )
    return parser


def main() -> None:
    """Parse arguments and delegate to FieldStudyCertificate."""
    args = _build_parser().parse_args()

    if args.command == 'generate':
        jsonl_path = args.jsonl
        if args.latest:
            latest = FieldStudyCertificate.find_latest_jsonl()
            if latest is None:
                raise SystemExit('No field_study.jsonl found under logs/autotrader')
            jsonl_path = str(latest)

        FieldStudyCertificate.generate(
            jsonl_path=jsonl_path,
            release_version=args.release_version,
            comment=args.comment,
            reports_dir=args.reports_dir,
        )


if __name__ == '__main__':
    main()
