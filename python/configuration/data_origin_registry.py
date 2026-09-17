"""
FiniexTestingIDE - Data Origin Registry

Where a producing instance's identity is turned into a judgement. The producer states an
identity it cannot falsify; this is the one place that decides what that identity means, and
it lives on the consumer's side on purpose — the meaning is an opinion about the data, never a
property of it.
"""

import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

from pydantic import ValidationError

from python.framework.types.config_types.data_origin_config_types import (
    DataOriginConfig,
    OriginClass,
    OriginEvidence,
)
from python.framework.types.data_origin_types import OriginResolution
from python.framework.utils.config_merge_utils import (
    deep_merge,
    is_config_isolation_active,
)
from python.framework.utils.version_utils import parse_version

_CONFIG_PATH = 'configs/data_origins.json'
_USER_CONFIG_PATH = 'user_configs/data_origins.json'

# The key of the origin block inside a source file's metadata, and the fields this side reads
# from it. Everything else in the block is forensic and deliberately never decided on.
ORIGIN_METADATA_KEY = 'origin'
ORIGIN_INSTANCE_FIELD = 'instance_id'


class DataOriginRegistry:
    """
    Resolves a file's stated identity into a class and an evidence grade.

    Two ways a file can be judged, and they are tried in that order. A file that STATES an
    identity is judged by that identity alone: a hit gives the registry's class with evidence
    `stamped`, a miss gives `unknown` — and a miss is deliberately not fallen back on the
    attestation, because a stated identity nobody registered is a gap in this file, not a
    legacy file. A file that states nothing may be covered by an attestation, which is a dated
    claim about everything an archive wrote before its producer could state anything.

    Anything else is `unknown`, which is refused for a measurement run. There is no setting for
    that: it is the one answer that must not be configurable, because the alternative somebody
    would eventually write is the one that quietly admits the wrong data.
    """

    _config: Optional[DataOriginConfig] = None
    _lock = Lock()

    def __init__(self, config_path: Optional[str] = None,
                 user_config_path: Optional[str] = None):
        """
        The paths are resolved from the module constants at CALL time rather than bound as
        signature defaults, so a test can point the registry somewhere else without reaching
        into the instance.

        Args:
            config_path: The tracked registry, carrying the schema and development entries
            user_config_path: The workspace override, carrying production entries
        """
        self._config_path = config_path or _CONFIG_PATH
        self._user_config_path = user_config_path or _USER_CONFIG_PATH

    def resolve(self, instance_id: Optional[str], format_version: str, *,
                broker_type: str = '', pipeline_id: str = '') -> OriginResolution:
        """
        Judge one file from the identity it stated and the archive it belongs to.

        The identity is passed IN rather than parsed here, because every producer states it in
        its own shape — the collector nests it in an `origin` block, the signal producer carries
        it top-level beside `data_origin` — and reading a shape is not deciding a meaning. This
        class owns the meaning; each importer owns its own producer's wire format.

        Args:
            instance_id: The identity the file stated, or None when it stated none
            format_version: The version the producer declared, in that archive's own terms
            broker_type: The tick archive this file belongs to, when it is one
            pipeline_id: The signal archive this file belongs to, when it is one

        Returns:
            The identity it stated, the class that identity means here, and how well it is known
        """
        config = self._load()

        if instance_id:
            entry = config.origins.get(instance_id)
            if entry is None:
                return OriginResolution(instance_id, OriginClass.UNKNOWN,
                                        OriginEvidence.UNKNOWN)
            return OriginResolution(instance_id, entry.origin_class,
                                    OriginEvidence.STAMPED)

        attested = self._match_attestation(
            config, format_version, broker_type=broker_type, pipeline_id=pipeline_id)
        if attested is not None:
            return OriginResolution(None, attested, OriginEvidence.ATTESTED)

        return OriginResolution(None, OriginClass.UNKNOWN, OriginEvidence.UNKNOWN)

    def describe(self, instance_id: Optional[str]) -> str:
        """
        One readable phrase naming an identity, for a refusal message.

        Args:
            instance_id: The identity to describe, or None when the file stated none

        Returns:
            The identity with its registered name, or a phrase saying it is not registered
        """
        if not instance_id:
            return 'no identity stated'
        entry = self._load().origins.get(instance_id)
        if entry is None:
            return f'{instance_id} (not registered)'
        return f'{instance_id} ({entry.name})' if entry.name else instance_id

    @classmethod
    def reload(cls) -> None:
        """Drop the cached registry so the next resolve reads the files again."""
        with cls._lock:
            cls._config = None

    @staticmethod
    def read_nested_instance_id(source_metadata: Dict[str, Any]) -> Optional[str]:
        """
        Read the identity out of a NESTED origin block — the tick collectors' shape.

        The signal producer states its identity top-level instead, beside `data_origin`, and
        deliberately so: that envelope already uses the word origin for whether the data is live
        or synthetic, and two neighbouring fields called origin with different meanings is a pair
        that gets misread once and stays misread. So there is no second reader here — the signal
        importer reads its own field and hands the value to `resolve`.

        Args:
            source_metadata: The file's own metadata

        Returns:
            The identity, or None when the file carries no origin block
        """
        block = source_metadata.get(ORIGIN_METADATA_KEY)
        if not isinstance(block, dict):
            return None
        instance_id = block.get(ORIGIN_INSTANCE_FIELD)
        return instance_id if isinstance(instance_id, str) and instance_id else None

    @staticmethod
    def _match_attestation(config: DataOriginConfig, format_version: str, *,
                           broker_type: str = '',
                           pipeline_id: str = '') -> Optional[OriginClass]:
        """
        Find the claim covering a file that stated no identity.

        A scope names exactly one archive, so it is compared against exactly one of the two
        keys — and an empty key never matches, which is what keeps a tick claim from reaching a
        signal file and back.

        An unparseable version matches nothing. That is the honest answer rather than an
        inconvenience: a claim is bounded by a version, and a file whose version cannot be read
        cannot be shown to fall inside that bound.

        Args:
            config: The loaded registry
            format_version: The version the producer declared, in that archive's own terms
            broker_type: The tick archive the file belongs to, when it is one
            pipeline_id: The signal archive the file belongs to, when it is one

        Returns:
            The claimed class, or None when no claim covers this file
        """
        version = parse_version(format_version)
        if version is None:
            return None

        for attestation in config.attestations:
            scope = attestation.scope
            key = scope.broker_type or scope.pipeline_id
            against = broker_type if scope.broker_type else pipeline_id
            if not against or key != against:
                continue
            boundary = parse_version(scope.up_to_format)
            if boundary is not None and version <= boundary:
                return attestation.origin_class
        return None

    def _load(self) -> DataOriginConfig:
        """
        Read the registry once, through the usual cascade.

        Checked twice on purpose. `resolve()` is called per FILE — 5690 of them on a full
        reimport — and after the first read the lock would be guarding nothing but a read of an
        attribute that is already set. The unlocked check takes it off that path; the locked one
        behind it is what still makes the first read happen exactly once.

        Returns:
            The merged registry; an empty one when no file exists at all
        """
        config = DataOriginRegistry._config
        if config is not None:
            return config

        with DataOriginRegistry._lock:
            if DataOriginRegistry._config is None:
                DataOriginRegistry._config = self._read()
            return DataOriginRegistry._config

    def _read(self) -> DataOriginConfig:
        """
        Load and merge the tracked registry with the workspace override.

        The workspace file ADDS origins rather than replacing them, so the tracked development
        entries need no second copy. Its attestation list replaces the tracked one outright,
        which is right: the tracked list is an example and the real claims are the operator's.

        Returns:
            The validated registry
        """
        raw = self._read_json(Path(self._config_path))

        user_path = Path(self._user_config_path)
        if user_path.exists() and not is_config_isolation_active():
            raw = deep_merge(raw, self._read_json(user_path))

        try:
            return DataOriginConfig(**raw)
        except ValidationError as error:
            raise ValueError(
                f'Invalid data origin registry ({self._config_path}'
                f'{" + " + self._user_config_path if user_path.exists() else ""}).\n{error}'
            )

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        """
        Read one registry file.

        Args:
            path: The file to read

        Returns:
            Its contents, or an empty mapping when the file does not exist
        """
        if not path.exists():
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                return json.load(handle)
        except json.JSONDecodeError as error:
            raise ValueError(
                f'Invalid JSON in {path}\n{error}\nFix the syntax or remove the file.'
            )
