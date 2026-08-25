"""
FiniexTestingIDE - Live Signal Feed Certificate Configuration (#466)

Reads the producer ONCE per session and writes the certificate when the session ends.
Every test then asserts against that single reading — a fixture per test would call the
producer once per assertion, and a certificate whose checks each looked at a different
envelope would not describe one observation of anything.

Usage:
    FINIEX_CONFIG_ISOLATION=0 pytest tests/live_signal_feed/ -v -m live_signal_feed \\
        --release-version 1.4

The env var is not decoration. tests/conftest.py sets config isolation so no personal
workspace override can decide a test outcome, and it is right to do so for every other
suite — but the production endpoint and its token live exactly there, so this one suite
must see them. Without it the run would resolve the DEVELOPMENT endpoint from the tracked
default and produce a certificate that passes and certifies nothing, which is the single
failure mode this whole gate exists to prevent. It therefore fails loudly instead.
"""

import pytest

from python.configuration.sentiment_config_manager import SentimentConfigManager
from python.framework.reporting.signal_feed_certificate import (
    DEFAULT_REPORTS_DIR,
    SignalFeedCertificate,
)
from python.framework.signal_data.signal_feed_observer import SignalFeedObserver
from python.framework.types.signal_certificate_types import SignalFeedAssessment
from python.framework.utils.config_merge_utils import is_config_isolation_active

# Where the session parks its one reading, so pytest_sessionfinish can write it out.
_ASSESSMENT_ATTRIBUTE = '_signal_feed_assessment'
# Whether this session will take a live reading at all, decided at COLLECTION time.
_PROBE_PLANNED_ATTRIBUTE = '_signal_feed_probe_planned'
# Fixture whose presence marks a test as part of the certification run.
_ASSESSMENT_FIXTURE = 'assessment'
# Release version meaning "nobody declared one", i.e. this run was not aimed at a release.
_UNDECLARED_RELEASE = 'dev'


def pytest_addoption(parser):
    """Add the certificate options for this suite."""
    parser.addoption(
        '--release-version', action='store', default='dev',
        help='Release version this certificate covers (e.g. 1.4). Defaults to "dev".')
    parser.addoption(
        '--comment', action='store', default='',
        help='Optional free-text note recorded in the certificate')
    parser.addoption(
        '--reports-dir', action='store', default=DEFAULT_REPORTS_DIR,
        help='Where certificates are written and where the previous one is read from')
    parser.addoption(
        '--observations', action='store', type=int, default=2,
        help='Envelopes to read. Two is the minimum that can say anything about a series.')
    parser.addoption(
        '--observation-gap-s', action='store', type=float, default=15.0,
        help='Pause between reads. A gap longer than the producer cadence additionally '
             'samples the cadence itself; the default only proves the series held.')


def pytest_collection_modifyitems(config, items):
    """
    Record up front whether this session includes the certification run.

    Decided at collection rather than from the fixture, because the read-back test may run
    BEFORE the probe — pytest orders by file name, and `test_signal_feed_certificate.py`
    sorts first. A flag set by the fixture would therefore still be unset when the
    read-back asks, and the read-back would silently validate the previous certificate
    while appearing to describe this run.
    """
    setattr(config, _PROBE_PLANNED_ATTRIBUTE, any(
        _ASSESSMENT_FIXTURE in item.fixturenames for item in items))


@pytest.fixture(scope='session')
def assessment(request) -> SignalFeedAssessment:
    """
    The one reading of the producer, already judged.

    Returns:
        Every verdict and recorded value for this session
    """
    release_version = request.config.getoption('release_version')
    if is_config_isolation_active():
        # Isolation means user_configs/sentiment_config.json is not merged, so the run
        # would resolve the DEVELOPMENT endpoint from the tracked default — and a
        # certificate signed against that would pass and certify nothing.
        #
        # Skip or fail is decided by DECLARED INTENT, not by the misconfiguration itself.
        # A sweep like `pytest tests/` collects this suite incidentally and should not
        # produce a wall of red from a release gate. But an operator who named a release
        # version meant to certify, and a release gate that quietly skips is the same
        # failure family as one that exits 0 on failure (#372) — so that one fails loudly.
        detail = ('FINIEX_CONFIG_ISOLATION is active, so the production endpoint in '
                  'user_configs/ is invisible and this run would target the DEVELOPMENT '
                  'producer. Re-run with FINIEX_CONFIG_ISOLATION=0.')
        if release_version != _UNDECLARED_RELEASE:
            pytest.fail(f'certifying {release_version} is not possible: {detail}')
        pytest.skip(f'not a certification run: {detail}')

    manager = SentimentConfigManager()
    config = manager.get_config()
    producer = manager.resolve_active_producer()
    pipeline_id = config.poll.pipeline_id
    if not pipeline_id:
        pytest.fail(
            'sentiment_config.json names no poll.pipeline_id, so there is no source to '
            'certify. Set it in the user override.')

    source = config.get_source(pipeline_id)
    gap_seconds = request.config.getoption('observation_gap_s')
    probe = SignalFeedObserver(
        producer=producer,
        pipeline_id=pipeline_id,
        timeout_s=config.poll.request_timeout_s,
    ).observe(
        observation_count=request.config.getoption('observations'),
        gap_seconds=gap_seconds)

    result = SignalFeedCertificate.assess(
        probe=probe,
        cadence_minutes_configured=source.cadence_minutes if source else None,
        release_version=release_version,
        observation_gap_s=gap_seconds,
        reports_dir=request.config.getoption('reports_dir'))
    setattr(request.config, _ASSESSMENT_ATTRIBUTE, result)
    return result


def pytest_sessionfinish(session, exitstatus):
    """Write the certificate for the reading this session took, if it took one."""
    result = getattr(session.config, _ASSESSMENT_ATTRIBUTE, None)
    if result is None:
        return
    SignalFeedCertificate.generate(
        assessment=result,
        release_version=session.config.getoption('release_version'),
        comment=session.config.getoption('comment'),
        reports_dir=session.config.getoption('reports_dir'))


def probe_planned(config) -> bool:
    """
    Whether this session takes a live reading.

    Args:
        config: The pytest config

    Returns:
        True when the certification run is part of this session, so a read-back of the
        committed artifact would be describing the PREVIOUS run rather than this one
    """
    return bool(getattr(config, _PROBE_PLANNED_ATTRIBUTE, False))
