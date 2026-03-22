"""Main entry point for the Environment Orchestrator service."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routes import orchestrator

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),
    format=settings.log_format,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info(f"Starting {settings.service_name} v{settings.version}")
    logger.info(f"Docker socket: {settings.docker_socket_path}")
    logger.info(f"Health check timeout: {settings.health_check_timeout}s")
    
    # Startup: Check Docker connection
    try:
        from app.docker_client import docker_client
        docker_client.client.ping()
        logger.info("Connected to Docker daemon successfully")
    except Exception as e:
        logger.error(f"Failed to connect to Docker daemon: {e}")
        logger.warning("Service may not function correctly without Docker access")
    
    yield
    
    # Shutdown
    logger.info(f"Shutting down {settings.service_name}")
    
    # Stop any running stacks
    from app.stack_manager import stack_manager
    for name, stack_info in list(stack_manager.running_stacks.items()):
        if stack_info.status in ('running', 'starting'):
            logger.info(f"Stopping environment '{name}' during shutdown")
            try:
                import asyncio
                asyncio.create_task(stack_manager.stop_environment(name))
            except Exception as e:
                logger.error(f"Error stopping environment '{name}': {e}")


# Create FastAPI application
app = FastAPI(
    title="MASE Environment Orchestrator",
    description="""
    The Environment Orchestrator service manages Docker Compose stacks for MASE environments.
    
    ## Features
    
    - **Start/Stop Environments**: Manage Docker Compose stacks on demand
    - **Health Monitoring**: Poll health checks and track container status
    - **Log Streaming**: Access service logs from running environments
    - **State Tracking**: Persistent state management with SQLite
    
    ## Docker Access
    
    This service requires access to the Docker daemon. When running in a container,
    mount the Docker socket:
    ```
    -v /var/run/docker.sock:/var/run/docker.sock
    ```
    """,
    version=settings.version,
    lifespan=lifespan
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle unhandled exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


# Health check endpoint
@app.get("/health", tags=["health"])
async def health_check():
    """
    Health check endpoint.
    
    Returns service status and Docker connectivity.
    """
    try:
        from app.docker_client import docker_client
        docker_client.client.ping()
        docker_status = "connected"
    except Exception as e:
        docker_status = f"disconnected: {str(e)}"
    
    return {
        "status": "healthy",
        "service": settings.service_name,
        "version": settings.version,
        "docker": docker_status
    }


# Root endpoint
@app.get("/", tags=["root"])
async def root():
    """
    Root endpoint.
    
    Returns basic service information.
    """
    return {
        "service": settings.service_name,
        "version": settings.version,
        "description": "MASE Environment Orchestrator",
        "docs": "/docs"
    }


# Include routers
app.include_router(orchestrator.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level=settings.log_level.lower()
    )
