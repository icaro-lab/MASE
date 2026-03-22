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
    evaluate_compass_gate,
    record_compass_event,
    record_event,
    resolve_authenticated_agent_id,
    router as platform_router,
    should_gate_mutating_request,
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
    header_agent_id = request.headers.get("x-agent-id")
    effective_agent_id = header_agent_id

    if should_gate_mutating_request(request.method, request.url.path):
        db_gen = get_db()
        db = next(db_gen)
        try:
            authenticated_agent_id = resolve_authenticated_agent_id(request, db)
            if authenticated_agent_id:
                effective_agent_id = authenticated_agent_id
            gate_payload = evaluate_compass_gate(
                db,
                run_id=run_id,
                agent_id=effective_agent_id,
            )
        finally:
            db_gen.close()

        if gate_payload.get("state") == "warning":
            record_compass_event(
                event_name="compass_due_warning",
                run_id=run_id,
                agent_id=effective_agent_id,
                details={
                    "path": request.url.path,
                    "method": request.method,
                    "due_at": (gate_payload.get("status") or {}).get("due_at"),
                    "grace_expires_at": (gate_payload.get("status") or {}).get("grace_expires_at"),
                },
            )

        if gate_payload.get("blocked"):
            blocked_payload = gate_payload.get("blocked_payload") or {
                "error": "compass_required",
                "code": "compass_gate_blocked",
                "reason": "compass_overdue",
                "next_action": "submit_compass",
            }
            record_compass_event(
                event_name="compass_gate_blocked",
                run_id=run_id,
                agent_id=effective_agent_id,
                status_code=409,
                details={
                    "path": request.url.path,
                    "method": request.method,
                    "due_at": blocked_payload.get("due_at"),
                    "grace_expires_at": blocked_payload.get("grace_expires_at"),
                },
            )
            duration_ms = int((time.perf_counter() - started_at) * 1000)
            record_event(
                method=request.method,
                path=request.url.path,
                status_code=409,
                run_id=run_id,
                agent_id=effective_agent_id,
                duration_ms=duration_ms,
            )
            return JSONResponse(status_code=409, content=blocked_payload)

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
