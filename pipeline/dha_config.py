"""Shared runtime config for the Python stages.

Import this first; it loads the repo's `.env` into os.environ (without
overriding vars already set) and exposes the resolved paths + branding so no
stage hardcodes a machine-specific path, token, domain, or brand string.

    from dha_config import env, SITE_DIR, OUTPUT_DIR, branding
"""
from __future__ import annotations

import os
from pathlib import Path

# Repo root = parent of the directory holding this file (works whether this
# lives in pipeline/ or site/, both one level below the root).
DHA_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Load DHA_ROOT/.env into os.environ; existing env vars win."""
    dotenv = DHA_ROOT / ".env"
    if not dotenv.is_file():
        return
    for raw in dotenv.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = val.strip()


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _path(name: str, default: Path) -> Path:
    val = os.environ.get(name)
    return Path(val).expanduser() if val else default


OUTPUT_DIR = _path("OUTPUT_DIR", DHA_ROOT / "daily")
SITE_DIR = _path("SITE_DIR", DHA_ROOT / "site")


def branding() -> dict:
    """User-facing brand strings, all overridable via .env."""
    return {
        "brand": env("SITE_BRAND", "Daily Harness Analysis"),
        "tagline": env("SITE_TAGLINE", ""),
        "domain": env("SITE_DOMAIN", "http://localhost:8000").rstrip("/"),
        "repo_url": env("SITE_REPO_URL",
                        "https://github.com/nmhjklnm/daily-harness-analysis"),
        "home_url": env("SITE_HOME_URL", "") or "/",
    }
