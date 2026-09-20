"""
FiniexTestingIDE - Data Origin Types

What one file's provenance resolved to. The registry answers with all three at once, because
the class alone is not the answer: `production` from a producer's own stamp and `production`
from a claim we recorded are the same word and a different fact.
"""

from dataclasses import dataclass
from typing import List, Optional

from python.framework.types.config_types.data_origin_config_types import (
    OriginClass,
    OriginEvidence,
)
from python.framework.utils.version_utils import parse_version

# The format version from which a producer states its own identity, so a file at or above it
# that carries no `origin` block is a DEFECTIVE PRODUCER rather than grandfathered history.
# Both producers step 1.5.0 -> 1.7.0 in one deployment and 1.6.0 never reaches production, so
# the boundary sits above every file an attestation may legitimately claim and below every
# file that must speak for itself. A development file at 1.6.0 exists and is deliberately
# caught by neither: the boundary fails closed rather than widening quietly.
ORIGIN_BLOCK_REQUIRED_FROM = '1.7.0'


@dataclass
class OriginResolution:
    """
    One file's resolved provenance, as it is stamped into the file's own metadata.

    Args:
        instance_id: The identity the producer stated, or None when it stated none
        origin_class: What this consumer's registry says that identity means
        evidence: How well that is known — stamped, attested or unknown
    """

    instance_id: Optional[str]
    origin_class: OriginClass
    evidence: OriginEvidence

    @property
    def is_admissible_for_measurement(self) -> bool:
        """
        Whether a run that must be comparable may read this file.

        Returns:
            True when the class is production AND the producer stamped it itself
        """
        return is_admissible_for_measurement(
            self.origin_class.value, self.evidence.value)


def joined_distinct(values: List[str]) -> str:
    """
    The one encoding provenance uses when a list of files becomes a single recorded value.

    Written once because two surfaces record the same answer at different grains — the ledger
    per RUN, the scenario detail row per SCENARIO — and the whole point of having both is that
    a reader can compare them. Two encodings of "which classes did this read" would make that
    comparison a parsing exercise.

    Sorted, so the value is stable: the same set of files must produce the same string whatever
    order they were read in, or two identical runs would look different in the ledger.

    Args:
        values: The per-file values to collapse

    Returns:
        The distinct values, sorted, comma-joined; empty for an empty input
    """
    return ','.join(sorted(set(values)))


def is_admissible_for_measurement(origin_class: str, evidence: str) -> bool:
    """
    The one rule that decides whether a file may enter a comparability measurement.

    Only a producer's own stamp qualifies. A recorded claim is honest enough to explore with
    and not enough to measure against — which is the entire reason the two grades are kept
    apart rather than collapsed into one word.

    It takes STRINGS because its second caller reads them back off an index, where a value
    that is not one of the known words is possible and must not raise: a post-run report that
    crashes on a surprising string tells the operator nothing, while one that counts it as not
    admissible tells them exactly the truth. Anything unrecognised is therefore not admissible,
    which is also the only safe direction for this particular question.

    Args:
        origin_class: The resolved class, as it is stored
        evidence: The resolved evidence grade, as it is stored

    Returns:
        True when the class is production AND the producer stamped it itself
    """
    return (origin_class == OriginClass.PRODUCTION.value
            and evidence == OriginEvidence.STAMPED.value)


def origin_block_is_required(format_version: str) -> bool:
    """
    Whether a file of this format version must carry an `origin` block.

    An unparseable or empty version answers False. That is the safe direction here and the
    opposite of the admissibility rule's: refusing a file over a version string we could not
    read would reject real data for our own parsing, where letting it through costs only the
    grade it already earns — `unknown`, which is not admissible anyway.

    Args:
        format_version: The `data_format_version` the file declares

    Returns:
        True when the version is at or above the boundary
    """
    if not format_version:
        return False
    try:
        return parse_version(format_version) >= parse_version(ORIGIN_BLOCK_REQUIRED_FROM)
    except (ValueError, TypeError, AttributeError):
        return False
