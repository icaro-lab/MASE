"""Main FastAPI application for the Agent Launcher service."""

import os
from fastapi import FastAPI
from contextlib import asynccontextmanager

from .config import settings
from .routes import launcher, scheduler as scheduler_routes
from .scheduler import heartbeat_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    os.makedirs(settings.agents_base_path, exist_ok=True)
    
    # Initialize heartbeat scheduler with environment configuration
    heartbeat_scheduler.initialize(
        interval=os.getenv("HEARTBEAT_INTERVAL", settings.heartbeat_interval),
        timeout=os.getenv("HEARTBEAT_TIMEOUT", settings.heartbeat_timeout),
        max_parallel=int(os.getenv("MAX_PARALLEL_AGENTS", str(settings.max_parallel_agents))),
        retry_count=int(os.getenv("HEARTBEAT_RETRY_COUNT", "3")),
        retry_delay=os.getenv("HEARTBEAT_RETRY_DELAY", "10s"),
        jitter=os.getenv("HEARTBEAT_JITTER", "5s"),
        environment_url=os.getenv("HEARTBEAT_ENVIRONMENT_URL", settings.environment_url),
        environment_name=os.getenv("HEARTBEAT_ENVIRONMENT_NAME"),
    )
    
    # Auto-start scheduler on container startup only when explicitly enabled.
    auto_start = os.getenv("HEARTBEAT_AUTO_START", "false").lower() == "true"
    if auto_start:
        await heartbeat_scheduler.start()
        print("Heartbeat scheduler auto-started on container startup")
    else:
        print("Heartbeat scheduler auto-start disabled; waiting for runtime start config")
    
    yield
    
    # Shutdown
    if heartbeat_scheduler._state.name != "STOPPED":
        await heartbeat_scheduler.stop()
        print("Heartbeat scheduler stopped on container shutdown")


app = FastAPI(
    title="MASE Agent Launcher",
    description="Service for invoking LLM agents based on filesystem-based identity",
    version="0.1.0",
    lifespan=lifespan,
)

# Include routers
app.include_router(launcher.router)
app.include_router(scheduler_routes.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "service": "MASE Agent Launcher",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "service": "agent-launcher"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
