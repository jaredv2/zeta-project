"""Scope-based entitlement checks for cosmetic assets and game config files."""
from __future__ import annotations

from typing import Optional

# Legacy bucket folders remain available with their original scope rules.
SCOPE_PREFIXES: dict[str, Optional[tuple[str, ...]]] = {
    "all": None,
    "default": ("default/",),
    "builds": ("default/", "builds/"),
    "pickaxes": (),
    "sweat": ("default/", "sweat/"),
    "vip": ("default/", "builds/", "sweat/", "vip/"),
}

WOOD_LOOKS = (
    "rainbow", "natural", "charcoal", "whitewash", "gold", "checker", "blue",
)
PICKAXE_BUNDLES = (
    "6e813a3edd180fe07ca4e438849b67b5.bundle",
    "2aa55d46c4def6847148e4e9af03de6d.bundle",
)
GAME_CONFIG_FILES = frozenset({
    "game/buildnow-gg/225/Build/StreamingAssets/aa/catalog.json",
    "game/buildnow-gg/225/Build/StreamingAssets/aa/settings.json",
})
PICKAXES = (
    "star-wand",
    "fncs-champion",
    "candy-axe",
    "minecraft-sword",
    "leviathan-axe",
)
SCOPE_COSMETICS: dict[str, dict[str, tuple[str, ...]]] = {
    "default": {
        "pickaxes": ("star-wand",),
        "wood": ("rainbow", "natural"),
    },
    "builds": {
        "pickaxes": (),
        "wood": WOOD_LOOKS,
    },
    "pickaxes": {
        "pickaxes": PICKAXES,
        "wood": (),
    },
    "sweat": {
        "pickaxes": ("star-wand",),
        "wood": ("rainbow", "natural", "charcoal", "whitewash"),
    },
    "vip": {
        "pickaxes": PICKAXES,
        "wood": ("rainbow", "natural", "gold", "charcoal"),
    },
    "all": {
        "pickaxes": PICKAXES,
        "wood": WOOD_LOOKS,
    },
}


def scope_allows(scope: str, path: str) -> bool:
    """Check legacy folder access or an explicitly granted asset path.

    Cosmetic paths use `cosmetics/pickaxes/{id}/...` or
    `cosmetics/wood/{look}/...`; game config access is limited to the exact
    catalog and settings paths. Matching is by complete path component so a
    similarly prefixed ungranted ID cannot inherit access.
    """
    if scope not in SCOPE_PREFIXES:
        return False
    if path in GAME_CONFIG_FILES:
        return True
    parts = path.split("/")
    if parts[0] == "cosmetics":
        grants = SCOPE_COSMETICS.get(scope)
        if grants is None or len(parts) < 3:
            return False
        category, cosmetic = parts[1], parts[2]
        if category == "pickaxes":
            return len(parts) == 4 and (
                cosmetic in grants["pickaxes"]
                and parts[3] in PICKAXE_BUNDLES
            )
        if category == "wood":
            if cosmetic not in grants["wood"]:
                return False
            if len(parts) == 4:
                return parts[3] == "cdbbb83c0ef9757d51e8dedb4979dcc3.data"
            return (
                len(parts) == 5
                and parts[3] == "default-background"
                and parts[4] == "cdbbb83c0ef9757d51e8dedb4979dcc3.data"
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
