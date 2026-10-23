"""Configuration loading and the debug-print helper for the ZETA bot.

Everything the bot reads from the environment is parsed here, once, so a
missing or malformed variable stops the bot at startup with a clear
message instead of showing up later as a confusing runtime error.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

# Read .env (if present) into os.environ. Real environment variables win,
# so hosting dashboards (Railway, Render, ...) override the file.
load_dotenv()

_TRUE = {"1", "true", "yes", "on", "y"}
_FALSE = {"0", "false", "no", "off", "n"}
_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _env_bool(name: str, default: bool, problems: list[str]) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    problems.append(f"{name} must be true or false (got {raw!r})")
    return default


def _env_int(name: str, problems: list[str]) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw.strip())
    except ValueError:
        problems.append(f"{name} must be a whole number (got {raw!r})")
        return None


def _env_float(name: str, default: float, minimum: float, problems: list[str]) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        problems.append(f"{name} must be a number (got {raw!r})")
        return default
    if value < minimum:
        problems.append(f"{name} must be >= {minimum} (got {value})")
        return default
    return value


def _env_id_set(name: str, problems: list[str]) -> frozenset[int]:
    raw = os.getenv(name, "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            problems.append(f"{name} must be comma-separated role ids (bad value {part!r})")
    return frozenset(ids)


# --- Debug helper ------------------------------------------------------
# One switch (DEBUG_PRINTS=false) silences every [DEBUG] line in production
# without touching any code. Parsed with a throwaway problems list because
# the full settings are loaded later.
DEBUG_PRINTS = _env_bool("DEBUG_PRINTS", True, [])


def dprint(message: str) -> None:
    """Print a [DEBUG] line (flush=True so hosting log viewers show it live)."""
    if DEBUG_PRINTS:
        print(f"[DEBUG] {message}", flush=True)


def redact_dsn(dsn: str) -> str:
    """Hide the password in a connection string so it is safe to print."""
    try:
        parts = urlsplit(dsn)
        if parts.password:
            netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
            return urlunsplit(parts._replace(netloc=netloc))
        return dsn
    except ValueError:
        return "<unparseable database url>"


@dataclass(frozen=True)
class Settings:
    token: str
    database_url: str
    key_pepper: str
    db_ssl: str
    db_pool_max: int
    dev_guild_id: Optional[int]
    auto_migrate: bool
    sync_commands: bool
    require_verified: bool
    regen_cooldown_hours: float
    admin_role_ids: frozenset[int]
    log_level: str


def load_settings() -> Settings:
    """Read, validate and return all settings (raises one RuntimeError listing every problem)."""
    problems: list[str] = []

    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        problems.append("DISCORD_TOKEN is missing")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        problems.append("DATABASE_URL is missing")
    elif not database_url.startswith(("postgres://", "postgresql://")):
        problems.append("DATABASE_URL must start with postgresql:// (copy it from Supabase > Connect)")

    key_pepper = os.getenv("ZETA_KEY_PEPPER", "").strip()
    if len(key_pepper.encode("utf-8")) < 32:
        problems.append("ZETA_KEY_PEPPER must contain at least 32 random bytes")

    db_ssl = os.getenv("DB_SSL", "require").strip().lower() or "require"
    if db_ssl not in _SSL_MODES:
        problems.append(f"DB_SSL must be one of {sorted(_SSL_MODES)} (got {db_ssl!r})")

    db_pool_max = _env_int("DB_POOL_MAX", problems) or 5
    if db_pool_max < 1:
        problems.append("DB_POOL_MAX must be at least 1")
        db_pool_max = 5

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in _LOG_LEVELS:
        problems.append(f"LOG_LEVEL must be one of {sorted(_LOG_LEVELS)} (got {log_level!r})")
        log_level = "INFO"

    settings = Settings(
        token=token,
        database_url=database_url,
        key_pepper=key_pepper,
        db_ssl=db_ssl,
        db_pool_max=db_pool_max,
        dev_guild_id=_env_int("DEV_GUILD_ID", problems),
        auto_migrate=_env_bool("AUTO_MIGRATE", True, problems),
        sync_commands=_env_bool("SYNC_COMMANDS", True, problems),
        require_verified=_env_bool("REQUIRE_VERIFIED", True, problems),
        regen_cooldown_hours=_env_float("REGEN_COOLDOWN_HOURS", 24.0, 0.0, problems),
        admin_role_ids=_env_id_set("ADMIN_ROLE_IDS", problems),
        log_level=log_level,
    )

    if problems:
        joined = "\n  - ".join(problems)
        raise RuntimeError(f"Invalid configuration:\n  - {joined}")

    dprint(
        "settings loaded: "
        f"token={token[:6]}..., db={redact_dsn(database_url)}, ssl={db_ssl}, "
        f"pool_max={db_pool_max}, dev_guild={settings.dev_guild_id}, "
        f"auto_migrate={settings.auto_migrate}, sync={settings.sync_commands}, "
        f"require_verified={settings.require_verified}, "
        f"regen_cooldown_h={settings.regen_cooldown_hours}, "
        f"admin_roles={sorted(settings.admin_role_ids)}"
    )
    return settings
