"""
FiniexTestingIDE - Host Identity Types (#551)

The shape of `user_configs/host_identity.json` — the identity an installation mints once and
states in every run header it writes. Minted rather than read from the hostname, because the
container hostname changes on every rebuild while this file lives on the bind mount and survives
it (the pattern is systemd's `machine-id`).

Deliberately not called "instance": that word already names a data producer's identity (#518).
"""

import re
import string

from pydantic import AwareDatetime, Field

from python.framework.types.config_types.strict_config_model import StrictConfigModel

# A minted id is the prefix plus a short random tail, e.g. `h_7k2m9q` — short enough to read in
# a table and compare by eye, random enough that two installations do not meet by accident.
# The pattern spells the alphabet as a range because it is what a refusal message shows the
# operator; a test holds the two to the same set of characters.
HOST_ID_PREFIX = 'h_'
HOST_ID_ALPHABET = string.ascii_lowercase + string.digits
HOST_ID_RANDOM_LENGTH = 6
HOST_ID_PATTERN = f'^{re.escape(HOST_ID_PREFIX)}[a-z0-9]{{{HOST_ID_RANDOM_LENGTH}}}$'

# Where the installation's identity lives: the workspace, which the `/app` bind mount carries across
# a container rebuild, and which is never tracked — a tracked id would give every clone the same one.
HOST_IDENTITY_FILE = 'user_configs/host_identity.json'

# The identity stated under config isolation. It deliberately does NOT match the minted shape,
# so a record written by a test can never be mistaken for one written by a real installation.
TEST_HOST_ID = 'test'


class HostIdentityRecord(StrictConfigModel):
    """
    The minted identity file.

    Strict on purpose: the file is written by the application and read back as an identity, so
    a key nobody reads or a value outside the minted shape means the file is not what it claims
    to be — and the manager refuses it rather than guessing.

    Args:
        host_id: The installation's identity, `h_` plus six characters of [a-z0-9]
        minted_at: When it was minted — a provenance stamp nothing decides on (§9)
    """
    host_id: str = Field(pattern=HOST_ID_PATTERN)
    minted_at: AwareDatetime
