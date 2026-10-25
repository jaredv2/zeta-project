"""Key format, scopes and the key generator.

Key format:  ZETA-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-ATEZ
  - "ZETA" and "ATEZ" are fixed.
  - Six XXXX groups are random, drawn from a 32-character alphabet
    (A-Z and 2-9 without the look-alikes 0/O/1/I) = 32^24 possible keys.
  - Existing three-group keys remain valid during the transition.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets

from config import dprint

KEY_PREFIX = "ZETA"
KEY_SUFFIX = "ATEZ"
GROUP_COUNT = 6
GROUP_LEN = 4

# No 0/O and 1/I so a key read aloud or typed from a screenshot is not ambiguous.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

SCOPES = ("all", "default", "builds", "sweat", "vip", "pickaxes")
DEFAULT_SCOPE = "default"

# Built from the constants above so the format can never drift from the generator.
_KEY_RE = re.compile(
    "^" + re.escape(KEY_PREFIX)
    + r"(?:(?:-[A-Z0-9]{%d}){3}|(?:-[A-Z0-9]{%d}){%d})-" %
    (GROUP_LEN, GROUP_LEN, GROUP_COUNT)
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
    """True for current six-group keys and compatible legacy three-group keys."""
    return bool(_KEY_RE.match(key))


def mask_key(key: str) -> str:
    """Return a log-safe key hint without revealing its complete credential."""
    parts = key.split("-")
    if len(parts) in (GROUP_COUNT + 2, 5):
        return "-".join([parts[0], parts[1], *["****"] * (len(parts) - 3), parts[-1]])
    return "****"


def key_verifier(key: str, pepper: str) -> str:
    """Return a deterministic server-only HMAC verifier for a high-entropy key."""
    if len(pepper.encode("utf-8")) < 32:
        raise ValueError("ZETA_KEY_PEPPER must contain at least 32 bytes.")
    return hmac.new(pepper.encode("utf-8"), key.encode("utf-8"), hashlib.sha256).hexdigest()


def configured_key_pepper() -> str:
    """Load the shared server-side key pepper, failing clearly if it is missing."""
    pepper = os.getenv("ZETA_KEY_PEPPER", "")
    if len(pepper.encode("utf-8")) < 32:
        raise RuntimeError("ZETA_KEY_PEPPER must be configured with at least 32 random bytes.")
    return pepper


if __name__ == "__main__":
    # Self-check: python keygen.py
    sample = generate_key()
    print(f"[DEBUG] generated valid key={is_valid_key(sample)} hint={mask_key(sample)}")
    assert is_valid_key(sample), "generator produced an invalid key"
    assert is_valid_key("ZETA-AAAA-AAAA-AAAA-ATEZ"), "validator rejected a legacy key"
    assert not is_valid_key("ZETA-AAAA-AAAA-AAAA-BBBB-ATEZ"), "validator accepted an invalid key length"
    assert not is_valid_key("ZETA-AAAA-AAAA-AAAA"), "validator accepted a short key"
    assert normalize_key("  `zeta-abcd-efgh-jklm-atez`\n") == "ZETA-ABCD-EFGH-JKLM-ATEZ"
    print("[DEBUG] keygen self-check passed")
