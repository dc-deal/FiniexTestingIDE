"""
FiniexTestingIDE - Data Origin Config Types

What an instance identity MEANS, as this consumer declares it. A producer states an identity it
cannot falsify and says nothing about its meaning; every judgement about that identity lives
here, on the side that has to act on it.
"""

import re
from enum import Enum
from typing import Dict, List

from pydantic import ConfigDict, Field, model_validator

from python.framework.types.config_types.strict_config_model import StrictConfigModel


class OriginClass(str, Enum):
    """
    What a producing instance is, as far as this consumer is concerned.

    A CLOSED vocabulary, and deliberately not the producer's to set. It stays at three values:
    a fourth is how a deployment label creeps back into a judgement that belongs here, and a
    file written under it would then carry a word this side never agreed to.
    """

    PRODUCTION = 'production'
    DEVELOPMENT = 'development'
    UNKNOWN = 'unknown'


class OriginEvidence(str, Enum):
    """
    How well the class above is KNOWN — which is a different question from what it is.

    STAMPED means the producer wrote its identity into the file and we resolved that.
    ATTESTED means we recorded a dated claim about files written before it could.
    UNKNOWN means nobody has said anything, which is the honest reading of an unmapped
    identity and of everything written before the contract existed.
    """

    STAMPED = 'stamped'
    ATTESTED = 'attested'
    UNKNOWN = 'unknown'


class OriginEntry(StrictConfigModel):
    """
    One instance identity and what it means here.

    Only `class` is read by code. `role`, `name`, `note` and `registered_at` are for people,
    and nothing branches on them — which is what makes a standby's promotion to primary a
    one-line edit that reinterprets every historical file correctly, because no role was ever
    written into the data.
    """

    model_config = ConfigDict(populate_by_name=True)

    origin_class: OriginClass = Field(alias='class')
    role: str = ''
    name: str = ''
    note: str = ''
    registered_at: str = ''


class AttestationScope(StrictConfigModel):
    """
    Which files an attestation covers — a PREDICATE, never a list.

    A list would be stale the moment a producer writes another file, and the archive keeps
    growing until that producer ships the block. Keyed on the archive plus the last format
    version written without an identity, the scope closes itself: once the block ships, every
    new file carries an identity and none of them can fall under this.

    It keys on the archive rather than on the producer NAME, and that is not a detail: a file
    without an origin block has no producer field either — it is the whole reason the claim
    exists. The scope has to be checkable against what such a file actually carries.

    Which is why there are TWO keys and a scope sets exactly one. That is the same rule applied
    to two archives that answer it differently: a tick file carries its BROKER, while a signal
    envelope carries neither a broker nor a `data_format_version` — it carries its PIPELINE and
    calls its version `schema_version`. A single key would have covered one archive and silently
    matched nothing in the other.

    Args:
        broker_type: The tick archive this claim covers, as the file names it
        pipeline_id: The signal archive this claim covers, as the envelope names it
        up_to_format: The highest version this claim covers, inclusive
    """

    broker_type: str = ''
    pipeline_id: str = ''
    up_to_format: str

    @model_validator(mode='after')
    def _names_exactly_one_archive(self) -> 'AttestationScope':
        """
        Refuse a scope that names no archive or both.

        Neither leaves a claim that can never match, which is the silent failure this whole
        file is built to avoid; both would let one claim reach across two archives that have
        no version line in common.

        Returns:
            The validated scope
        """
        if bool(self.broker_type) == bool(self.pipeline_id):
            raise ValueError(
                'An attestation scope names exactly one archive: `broker_type` for a tick '
                'archive or `pipeline_id` for a signal archive. A scope with neither can '
                'never match a file, and one with both claims across two archives whose '
                'version numbers mean different things.')
        return self


class Attestation(StrictConfigModel):
    """
    A dated claim about files written before their producer could state an identity.

    It carries no evidence grade, on purpose: an attestation can only ever BE attested, and a
    field that accepts one value is a field somebody can eventually use to write a lie. The
    resolver assigns the grade, so a claim can never present itself as a measurement.
    """

    model_config = ConfigDict(populate_by_name=True)

    scope: AttestationScope
    origin_class: OriginClass = Field(alias='class')
    attested_by: str
    attested_at: str
    basis: str


# What a minted identity looks like on the wire: 12 lowercase hex, the shape `journal_id`,
# `config_fingerprint` and `prompt_hash` already use across the three projects. Case matters —
# one registry holding three producers' identities cannot afford a case-insensitive key.
INSTANCE_ID_PATTERN = re.compile(r'^[0-9a-f]{12}$')


class DataOriginConfig(StrictConfigModel):
    """
    The registry file as a whole.

    There is no setting for what an UNMAPPED identity resolves to. That is a rule rather than a
    choice — an identity nobody has registered is unknown, and the one alternative anybody
    would ever configure is the one that must not be possible.
    """

    origins: Dict[str, OriginEntry] = Field(default_factory=dict)
    attestations: List[Attestation] = Field(default_factory=list)

    @model_validator(mode='after')
    def _keys_must_be_matchable(self) -> 'DataOriginConfig':
        """
        Refuse an identity key that no file could ever carry.

        A registry is only ever consulted by exact match, so a key in the wrong shape — an
        uppercase digit, a truncated id, a name where an identity belongs — is not a typo that
        misfires. It is an entry that can never match anything, and its files stay `unknown`
        for good with no error anywhere. The entry looks present and is not.

        Returns:
            The validated registry
        """
        wrong = [key for key in self.origins if not INSTANCE_ID_PATTERN.fullmatch(key)]
        if wrong:
            raise ValueError(
                f'Identity keys must be 12 lowercase hex characters: {", ".join(sorted(wrong))}. '
                f'A key in any other shape can never match a file and would leave its data '
                f'`unknown` without saying so.')
        return self
