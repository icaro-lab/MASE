"""Main FastAPI application for the MASE Admin Dashboard."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes import health, environments, settings, v1, stream


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    app_settings = get_settings()
    print(f"Starting {app_settings.app_name}")
    print(f"Orchestrator URL: {app_settings.orchestrator_url}")
    print(f"Controller URL: {app_settings.controller_url}")
    yield
    # Shutdown
    print("Shutting down admin dashboard")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app_settings = get_settings()
    
    app = FastAPI(
        title=app_settings.app_name,
        description="Admin Dashboard API for the MASE (Multi-Agent Simulation Environment) platform",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include routers with /api prefix
    app.include_router(environments.router, prefix="/api")
    app.include_router(health.router, prefix="/api")
    app.include_router(v1.router, prefix="/api")
    app.include_router(stream.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")

    return app


# Create the application instance
app = create_app()


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "MASE Admin Dashboard API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }
