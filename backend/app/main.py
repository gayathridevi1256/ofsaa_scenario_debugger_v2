"""Scenario Debugger — FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import APP_NAME, APP_VERSION, CORS_ORIGINS, create_app_directories, setup_logging, print_config_summary
from app.database.schema import init_db
from app.database.user_repo import ensure_admin_exists
from app.routers.health import router as health_router
from app.routers.auth import router as auth_router
from app.routers.upload import router as upload_router
from app.routers.jobs import router as jobs_router
from app.routers.websocket import router as ws_router
from app.routers.admin.users import router as admin_users_router
from app.routers.admin.audit import router as admin_audit_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_app_directories()
    setup_logging()
    init_db()
    ensure_admin_exists()
    print_config_summary()
    logger.info("Application startup complete")
    yield
    logger.info("Application shutting down")


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(upload_router)
app.include_router(jobs_router)
app.include_router(ws_router)
app.include_router(admin_users_router)
app.include_router(admin_audit_router)
