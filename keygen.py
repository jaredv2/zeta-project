"""Key format, scopes and the key generator.

Key format:  ZETA-XXXX-XXXX-XXXX-ATEZ
  - "ZETA" and "ATEZ" are fixed.
  - The three XXXX groups are random, drawn from a 32-character alphabet
    (A-Z and 2-9 without the look-alikes 0/O/1/I) = 32^12 ~ 1.15e18 keys.
"""
from __future__ import annotations

import re
import secrets

from config import dprint

KEY_PREFIX = "ZETA"
KEY_SUFFIX = "ATEZ"
GROUP_COUNT = 3
GROUP_LEN = 4

# No 0/O and 1/I so a key read aloud or typed from a screenshot is not ambiguous.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

SCOPES = ("all", "default", "builds", "sweat", 'vip')
DEFAULT_SCOPE = "default"

# Built from the constants above so the format can never drift from the generator.
_KEY_RE = re.compile(
    "^" + re.escape(KEY_PREFIX)
    + r"(?:-[A-Z0-9]{%d}){%d}-" % (GROUP_LEN, GROUP_COUNT)
    + re.escape(KEY_SUFFIX) + "$"
)


def generate_key() -> str:
    """Return a fresh random key.

    `secrets` (not `random`) is used because keys are credentials: the
    stdlib `random` module is predictable, `secrets` uses the OS CSPRNG.
    """
    groups = [
        "".join(secrets.choice(ALPHABET) for _ in range(GROUP_LEN))
        for _ in range(GROUP_COUNT)
    ]
    key = "-".join([KEY_PREFIX, *groups, KEY_SUFFIX])
    dprint(f"generated key {mask_key(key)}")
    return key


def normalize_key(raw: str) -> str:
    """Clean user input: trim whitespace/backticks (copy-paste from code blocks) and uppercase."""
    return raw.strip().strip("`").strip().upper()


def is_valid_key(key: str) -> bool:
    """True if `key` matches ZETA-XXXX-XXXX-XXXX-ATEZ exactly."""
    return bool(_KEY_RE.match(key))


def mask_key(key: str) -> str:
    """Return a log-safe version: ZETA-AB3K-****-****-ATEZ (never log full keys)."""
    parts = key.split("-")
    if len(parts) == GROUP_COUNT + 2:
        return f"{parts[0]}-{parts[1]}-****-****-{parts[-1]}"
    return "****"


if __name__ == "__main__":
    # Self-check: python keygen.py
    sample = generate_key()
    print(f"[DEBUG] sample={sample} valid={is_valid_key(sample)} masked={mask_key(sample)}")
    assert is_valid_key(sample), "generator produced an invalid key"
    assert not is_valid_key("ZETA-AAAA-AAAA-AAAA"), "validator accepted a short key"
    assert normalize_key("  `zeta-abcd-efgh-jklm-atez`\n") == "ZETA-ABCD-EFGH-JKLM-ATEZ"
    print("[DEBUG] keygen self-check passed")
