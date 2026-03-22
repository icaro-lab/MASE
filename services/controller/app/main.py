"""Main FastAPI application for the runtime/environment/run control plane."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.ingest_workers import start_ingest_workers, stop_ingest_workers
from app import run_task_enforcement
from app.routes import (
    events,
    monitoring,
    telemetry,
    runtime_environment,
    runs,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    print(f"Starting {settings.service_name}...")

    # Ensure database schema is present on fresh stacks.
    init_db()
    
    # Ensure directories exist
    import os
    os.makedirs(settings.metrics_path, exist_ok=True)
    os.makedirs(settings.state_path, exist_ok=True)
    os.makedirs(settings.packages_path, exist_ok=True)

    # Rebuild runtime-limit enforcer tasks for active runs after restarts.
    try:
        recovery_summary = run_task_enforcement.recover_run_runtime_limit_tasks_on_startup()
        print(f"Recovered runtime-limit tasks: {recovery_summary}")
    except Exception as recovery_error:
        print(f"Runtime-limit task recovery failed: {recovery_error}")
    try:
        recovery_summary = run_task_enforcement.recover_run_max_tick_tasks_on_startup()
        print(f"Recovered max-tick tasks: {recovery_summary}")
    except Exception as recovery_error:
        print(f"Max-tick task recovery failed: {recovery_error}")
    try:
        recovery_summary = run_task_enforcement.recover_run_max_agent_heartbeat_tasks_on_startup()
        print(f"Recovered max-agent-heartbeat tasks: {recovery_summary}")
    except Exception as recovery_error:
        print(f"Max-agent-heartbeat task recovery failed: {recovery_error}")
    try:
        ingest_summary = start_ingest_workers()
        print(f"Started ingest workers: {ingest_summary}")
    except Exception as ingest_error:
        print(f"Ingest worker startup failed: {ingest_error}")
    
    yield

    # Shutdown
    print(f"Shutting down {settings.service_name}...")

    cancelled_timeout_tasks = run_task_enforcement.cancel_all_run_runtime_limit_tasks()
    print(f"Cancelled runtime-limit tasks: {cancelled_timeout_tasks}")
    cancelled_max_tick_tasks = run_task_enforcement.cancel_all_run_max_tick_tasks()
    print(f"Cancelled max-tick tasks: {cancelled_max_tick_tasks}")
    cancelled_max_agent_heartbeat_tasks = run_task_enforcement.cancel_all_run_max_agent_heartbeat_tasks()
    print(f"Cancelled max-agent-heartbeat tasks: {cancelled_max_agent_heartbeat_tasks}")
    cancelled_ingest_workers = await stop_ingest_workers()
    print(f"Cancelled ingest workers: {cancelled_ingest_workers}")


app = FastAPI(
    title="MASE Run Controller",
    description="Code-first experiment runtime control plane built around runtimes, environments, and runs.",
    version="0.3.0",
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(runs.router)
app.include_router(events.router)
app.include_router(monitoring.router)
app.include_router(telemetry.router)
app.include_router(runtime_environment.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": settings.service_name,
        "version": "0.3.0",
        "phase": "runtime-environment-run",
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": settings.service_name,
        "version": "0.3.0",
        "phase": "runtime-environment-run",
        "features": [
            "runtime-catalog",
            "environment-catalog",
            "run-control",
            "event-aggregation",
            "metrics-collection",
            "per-run-containers"
        ],
        "delegated_to_agent_launcher": [
            "heartbeat-management",
            "agent-scheduling",
            "workspace-materialization"
        ],
        "documentation": "/docs",
        "health": "/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )
