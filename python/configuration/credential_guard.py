"""
FiniexTestingIDE - Credential Guard
Refuses a real broker call that would run on the tracked default credentials.

The file under `configs/credentials/` is a committed placeholder by design (§29); the real
key belongs in `user_configs/credentials/`, which takes precedence in the cascade. Without
this check the two are indistinguishable until the broker answers — minutes into a session,
with a message that reads as the broker's fault rather than as our configuration.

**The rule is the ANSWERING FILE, not the value.** That is deliberate and catches two
hazards with one check: a placeholder reaching a live path, and a real key committed into
the tracked default — which is itself a §29 violation and the more expensive of the two.
Matching literals would catch only the first, and only until someone renames them.
"""

from pathlib import Path

# Home of the committed placeholder credentials, as the two directory names that identify
# it. Compared part by part and NOT as a substring, because 'configs/credentials' is a
# substring of 'user_configs/credentials' — a substring test refuses the real key too, and
# would have failed every live run.
TRACKED_CREDENTIALS_PARENT = 'configs'
CREDENTIALS_DIR_NAME = 'credentials'


def assert_real_credential(credential_path: Path, purpose: str) -> None:
    """
    Refuse a credential that was read from the tracked default.

    Args:
        credential_path: File the credential was actually read from
        purpose: What was about to happen, named in the error so the operator does not
            have to work out which call tripped
    """
    path = Path(credential_path)
    parent = path.parent
    if not (parent.name == CREDENTIALS_DIR_NAME
            and parent.parent.name == TRACKED_CREDENTIALS_PARENT):
        return

    raise ValueError(
        f'{purpose} would run on the tracked default credentials at {path}, which is a '
        f'committed placeholder by design.\n'
        f'  Put the real key in user_configs/credentials/{path.name} — it takes '
        f'precedence in the cascade.\n'
        f'  If a real key IS in the tracked file: remove it. That file is committed.'
    )
