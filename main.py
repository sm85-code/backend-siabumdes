"""FastAPI application entrypoint — SIABUMDES backend."""
from __future__ import annotations

import logging
import os
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from modules.siabumdes.adapters.api.v1 import siabumdes_router, uu05_inventory_router
from modules.siabumdes.adapters.api.v1.inventory_documents_router import router as inventory_documents_router
from modules.siabumdes.adapters.api.v1.admin_control_router import router as admin_control_router
from modules.siabumdes.adapters.api.v1.audit_log_router import router as audit_log_router
from modules.siabumdes.adapters.api.v1.auth_router import router as auth_router
from modules.siabumdes.adapters.api.v1.io_router import router as io_router
from modules.siabumdes.adapters.api.v1.yield_router import router as yield_router
from modules.siabumdes.adapters.api.v1.master_data_router import router as master_data_router
from modules.siabumdes.adapters.api.v1.org_profile_router import router as org_profile_router
from modules.siabumdes.adapters.api.v1.period_close_router import router as period_close_router
from modules.siabumdes.adapters.api.v1.public_router import router as public_router
from modules.siabumdes.adapters.api.v1.reports_router import router as reports_router
from modules.siabumdes.adapters.api.v1.transaction_router import router as transaction_router
from shared.config import APP_TITLE, CORS_ORIGIN_REGEX, CORS_ORIGINS, origin_allowed
from shared.database import engine
from modules.siabumdes.schema import ensure_schema
from modules.siabumdes.seed import seed_if_needed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("sm85.audit")


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await ensure_schema()
        await seed_if_needed()
    except Exception:
        logger.exception("startup schema/seed failed — app continues")
    yield


# Public API docs (Swagger UI, ReDoc, OpenAPI schema) are disabled by default
# so the production API surface is not advertised. Set ENABLE_API_DOCS=true
# (e.g. locally) to serve /docs, /redoc and /openapi.json again.
ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "").strip().lower() in {"1", "true", "yes"}

app = FastAPI(
    title=APP_TITLE,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)
_login_attempts: dict[str, deque[float]] = defaultdict(deque)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Requested-With"],
    expose_headers=["Content-Disposition"],
)

app.include_router(public_router)
app.include_router(auth_router)
app.include_router(period_close_router)
app.include_router(admin_control_router)
app.include_router(audit_log_router)
app.include_router(master_data_router)
app.include_router(yield_router)
app.include_router(org_profile_router)
app.include_router(transaction_router)
app.include_router(reports_router)
app.include_router(io_router)
app.include_router(siabumdes_router.router)
app.include_router(inventory_documents_router)
app.include_router(uu05_inventory_router.router)


def _apply_cors(response, origin: str | None) -> None:
    if origin and origin_allowed(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"


class CsrfOriginMiddleware(BaseHTTPMiddleware):
    """Reject unknown-origin mutations; rate-limit login. Never block CORS preflight.

    Class-based (BaseHTTPMiddleware) instead of the @app.middleware("http")
    decorator: that decorator is deprecated and removed in Starlette 1.0, and
    this middleware guards the cookie-based auth the SIABUMDES frontend
    depends on -- it must keep working across a starlette bump.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.url.path.endswith("/auth/login"):
                now = monotonic()
                host = request.client.host if request.client else "unknown"
                attempts = _login_attempts[host]
                while attempts and now - attempts[0] > 60:
                    attempts.popleft()
                if len(attempts) >= 10:
                    response = JSONResponse(
                        status_code=429,
                        content={"detail": "Terlalu banyak percobaan login"},
                    )
                    _apply_cors(response, request.headers.get("origin"))
                    return response
                attempts.append(now)
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            source = origin or (referer and "/".join(referer.split("/")[:3]))
            if source and CORS_ORIGINS and not origin_allowed(source):
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Permintaan lintas situs ditolak"},
                )
                return response
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception("unhandled method=%s path=%s", request.method, request.url.path)
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "Terjadi kesalahan internal pada server",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:500],
                    "path": request.url.path,
                },
            )
            _apply_cors(response, request.headers.get("origin"))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            logger.info(
                "mutation method=%s path=%s status=%s",
                request.method,
                request.url.path,
                response.status_code,
            )
        return response


app.add_middleware(CsrfOriginMiddleware)


@app.get("/health")
async def health():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as exc:
        logger.exception("database health check failed")
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "database": "unavailable",
                "error_type": type(exc).__name__,
            },
        )


@app.get("/")
async def root():
    return {"app": APP_TITLE, "version": "1.0.0"}
