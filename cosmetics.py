"""Scope-based cosmetic entitlements and Storage path authorization."""
from __future__ import annotations

from typing import Optional

# Legacy bucket folders remain available with their original scope rules.
SCOPE_PREFIXES: dict[str, Optional[tuple[str, ...]]] = {
    "all": None,
    "default": ("default/",),
    "builds": ("default/", "builds/"),
    "sweat": ("default/", "sweat/"),
    "vip": ("default/", "builds/", "sweat/", "vip/"),
}

WOOD_LOOKS = (
    "rainbow", "natural", "charcoal", "whitewash", "gold",
)
PICKAXES = ("star-wand", "fncs-champion")
SCOPE_COSMETICS: dict[str, dict[str, tuple[str, ...]]] = {
    "default": {
        "pickaxes": ("star-wand",),
        "wood": ("rainbow",),
    },
    "builds": {
        "pickaxes": (),
        "wood": WOOD_LOOKS,
    },
    "sweat": {
        "pickaxes": ("star-wand",),
        "wood": ("rainbow", "natural", "charcoal", "whitewash"),
    },
    "vip": {
        "pickaxes": ("star-wand", "fncs-champion"),
        "wood": WOOD_LOOKS,
    },
    "all": {
        "pickaxes": PICKAXES,
        "wood": WOOD_LOOKS,
    },
}


def scope_allows(scope: str, path: str) -> bool:
    """Check legacy folder access or an explicitly granted cosmetic path.

    Cosmetic paths use `cosmetics/pickaxes/{id}/...` or
    `cosmetics/wood/{look}/...`. Matching is by complete path component so a
    similarly prefixed ungranted ID cannot inherit access.
    """
    if scope not in SCOPE_PREFIXES:
        return False
    parts = path.split("/")
    if parts[0] == "cosmetics":
        grants = SCOPE_COSMETICS.get(scope)
        if grants is None or len(parts) != 4:
            return False
        category, cosmetic = parts[1], parts[2]
        if category == "pickaxes":
            return (
                cosmetic in grants["pickaxes"]
                and parts[3] == "6e813a3edd180fe07ca4e438849b67b5.bundle"
            )
        if category == "wood":
            return (
                cosmetic in grants["wood"]
                and parts[3] == "cdbbb83c0ef9757d51e8dedb4979dcc3.data"
            )
        return False
    prefixes = SCOPE_PREFIXES[scope]
    return prefixes is None or path.startswith(prefixes)


def cosmetics_for_scope(scope: str) -> dict[str, list[str]]:
    """Return the exact cosmetic IDs granted to a known key scope."""
    grants = SCOPE_COSMETICS.get(scope)
    if grants is None:
        return {"pickaxes": [], "wood": []}
    return {"pickaxes": list(grants["pickaxes"]), "wood": list(grants["wood"])}
