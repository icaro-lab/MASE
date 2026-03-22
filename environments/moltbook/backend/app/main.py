from contextlib import asynccontextmanager
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.deps import get_db
from app.core.config import settings
from app.core.database import init_db
from app.api import api_router
from app.api.platform import (
    record_event,
    router as platform_router,
)
from app.api.skill import router as skill_router


# Create database tables
init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("Starting up Moltbook API...")
    yield
    # Shutdown
    print("Shutting down Moltbook API...")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173", "http://127.0.0.1:3000", "http://frontend:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_journal_middleware(request: Request, call_next):
    started_at = time.perf_counter()
    run_id = request.headers.get("x-run-id")
    effective_agent_id = request.headers.get("x-agent-id")
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        record_event(
            method=request.method,
            path=request.url.path,
            status_code=500,
            run_id=run_id,
            agent_id=effective_agent_id,
            duration_ms=duration_ms,
        )
        raise

    duration_ms = int((time.perf_counter() - started_at) * 1000)
    record_event(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        run_id=run_id,
        agent_id=effective_agent_id,
        duration_ms=duration_ms,
    )
    return response


# Error handlers
@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": str(exc)}
    )


# Include API router
app.include_router(platform_router)
app.include_router(api_router, prefix=settings.API_V1_STR)
app.include_router(skill_router)


@app.get("/")
def root():
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "description": settings.DESCRIPTION,
        "docs": f"{settings.API_V1_STR}/docs"
    }


@app.get("/health")
def health_check():
    state_key = (os.getenv("ENV_STATE_KEY") or os.getenv("MOLTBOOK_STATE_KEY") or "").strip() or None
    state_path = (os.getenv("ENV_STATE_PATH") or os.getenv("MOLTBOOK_STATE_PATH") or "").strip() or None
    return {
        "status": "healthy",
        "version": settings.VERSION,
        "run_id": (os.getenv("RUN_ID") or "").strip() or None,
        "environment_id": (os.getenv("ENVIRONMENT_ID") or "").strip() or None,
        "state_key": state_key,
        "state_path": state_path,
        "database_url": (os.getenv("DATABASE_URL") or "").strip() or None,
    }
