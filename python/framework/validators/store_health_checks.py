"""
FiniexTestingIDE - Store Health Checks

Two dated claims this installation holds whose validity decays rather than ending at a stroke: a
release gate's certificate, and a broker's declared fee structure. Each check is a pure function
over its raw inputs that RETURNS findings; `check_store_health` gathers the inputs from the stores
and runs both. The store catalog shows the result beside the stores, and because the findings are
data rather than printed lines, any other surface can serve them the same way.

Both are advisories. Neither rejects anything: a release checks its gates on its own, and only the
venue can say whether a declared fee rate is actually wrong — a live-adapter session asks it on
every start and warns on divergence. What these checks add is the question for someone who runs
only backtests and therefore never sees that warning.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.reporting.certificates.certificate_index import CertificateIndex
from python.framework.store.store_registrations import CERTIFICATES_ROOT
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
)

CERTIFICATE_EXPIRED_CHECK = 'certificate_expired'
FEE_STRUCTURE_FROZEN_LONG_CHECK = 'fee_structure_frozen_long'

# The same window a release certificate gets: both are dated claims whose validity decays rather
# than expiring at a stroke.
FEE_FREEZE_WINDOW_DAYS = 90


def check_store_health(now: Optional[datetime] = None) -> List[ValidationFinding]:
    """
    Both checks over the installation's own stores.

    Args:
        now: The instant to measure against; current UTC when not given

    Returns:
        Every finding, certificates first
    """
    return (check_expired_certificates(CertificateIndex(CERTIFICATES_ROOT).expired_families(now))
            + check_fee_freeze_ages(_frozen_fee_ages(now)))


def check_expired_certificates(expired: List[Tuple[str, str, str]]) -> List[ValidationFinding]:
    """
    One finding per release gate whose NEWEST certificate has expired.

    Only the newest counts, and the certificate index has already chosen it: an old certificate
    expiring is what old certificates do, and listing every one would turn a release gate into
    permanent noise. What a release asks is whether the certificate that WOULD be presented
    still holds.

    Args:
        expired: (family, release version, valid until) per expired gate, as the certificate
            index returns them

    Returns:
        One advisory per gate, scoped to the gate's family
    """
    return [ValidationFinding(
        severity=Severity.WARNING, check=CERTIFICATE_EXPIRED_CHECK,
        domain=ValidationDomain.RELEASE, scope=family,
        message=f'the newest certificate ({version}) was valid until {until[:10]}')
        for family, version, until in expired]


def check_fee_freeze_ages(
    ages: Dict[str, Optional[Tuple[str, int]]],
    window_days: int = FEE_FREEZE_WINDOW_DAYS,
) -> List[ValidationFinding]:
    """
    One finding per broker whose fee structure was frozen longer ago than the window.

    A fee rate is a declared assumption, not a fetched fact, so the seed records the date it was
    frozen. The finding states an AGE, not that the rate is wrong: the venue is the only authority
    on that. A broker whose file records no freeze date is not flagged — an absence is not an age.

    Args:
        ages: Per broker type, (freeze date as written, whole days since) or None when the file
            records no freeze
        window_days: How old a freeze may be before it is flagged

    Returns:
        One advisory per broker past the window, scoped to the broker type
    """
    return [ValidationFinding(
        severity=Severity.WARNING, check=FEE_STRUCTURE_FROZEN_LONG_CHECK,
        domain=ValidationDomain.BROKER, scope=broker_type,
        message=f'frozen {age[0]}, {age[1]} days ago — past the {window_days}-day window. '
                f"Re-freeze it from a live-adapter session's divergence warning, or confirm it still "
                f'holds')
        for broker_type, age in ages.items() if age and age[1] > window_days]


def _frozen_fee_ages(now: Optional[datetime]) -> Dict[str, Optional[Tuple[str, int]]]:
    """
    How old each configured broker's fee freeze is.

    Args:
        now: The instant to measure against; current UTC when not given

    Returns:
        Per broker type, (freeze date as written, whole days since) or None
    """
    manager = MarketConfigManager()
    return {broker_type: BrokerConfigFactory.frozen_fee_age_days(
                manager.get_broker_config_path(broker_type), now)
            for broker_type in manager.get_all_broker_types()}
