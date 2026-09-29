"""Centralized runtime configuration for App Platform + local dev (SIABUMDES backend)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

READ_LEVEL = ("admin", "direktur", "bendahara", "pengawas", "penasihat")
REPORT_READ_LEVEL = READ_LEVEL + ("pengelola",)
WRITE_LEVEL = ("admin", "direktur", "bendahara", "pengelola")
ADMIN_LEVEL = ("admin",)
READONLY_ROLES = ("pengawas", "penasihat")

API_PREFIX = "/api"
APP_TITLE = os.getenv("APP_TITLE", "SIABUMDES API")

PUBLIC_ROLES = (
    "admin",
    "direktur",
    "bendahara",
    "pengelola",
    "pengawas",
    "penasihat",
)
ROLE_ALIASES_TO_PUBLIC = {
    "unit_manager": "pengelola",
    "unit-manager": "pengelola",
    "manager": "pengelola",
    "pengelola_unit": "pengelola",
}


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [part.strip().rstrip("/") for part in raw.split(",") if part.strip()]


CORS_ORIGINS = list(dict.fromkeys(_csv("CORS_ORIGINS", "http://localhost:3000")))
CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX", "").strip() or None

JWT_SECRET = os.getenv("JWT_SECRET", "")
# Optional tenant-specific secret (B1). When unset, jwt_secret_for() falls back
# to JWT_SECRET so existing App Platform env keeps working after deploy.
# Rotate by setting JWT_SECRET_BUMDES (can start equal to JWT_SECRET, then
# change after the re-login window).
JWT_SECRET_BUMDES = os.getenv("JWT_SECRET_BUMDES", "").strip()
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", str(24 * 7)))
JWT_COOKIE_NAME = os.getenv("JWT_COOKIE_NAME", "bumdes_token")

# Stable tenant id used as JWT aud (and in iss = "sm85:<tenant>"). Kept
# identical to sm85-arch so tokens issued there stay valid here when the same
# secret is configured.
JWT_TENANT_BUMDES = "bumdes"


def jwt_issuer_for(tenant: str) -> str:
    return f"sm85:{tenant}"

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").strip().lower() in {"1", "true", "yes"}
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "none").strip().lower() or "none"
COOKIE_PATH = os.getenv("COOKIE_PATH", "/")

POSTGRES_SSL = os.getenv("POSTGRES_SSL", "true").strip().lower() in {"1", "true", "yes"}


def public_role(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    return ROLE_ALIASES_TO_PUBLIC.get(value, value)


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    normalized = origin.strip().rstrip("/")
    if normalized in CORS_ORIGINS:
        return True
    if CORS_ORIGIN_REGEX:
        try:
            return re.match(CORS_ORIGIN_REGEX, origin) is not None
        except re.error:
            return False
    return False
