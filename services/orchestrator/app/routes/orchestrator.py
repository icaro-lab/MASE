"""API routes for the Environment Orchestrator service."""

import logging
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

from app.stack_manager import StackManagerError, stack_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orchestrator", tags=["orchestrator"])


# Request/Response Models

class StartEnvironmentRequest(BaseModel):
    """Request model for starting an environment."""
    compose_path: str = Field(..., description="Path to docker-compose.yml file")
    env_vars: Optional[Dict[str, str]] = Field(default_factory=dict, description="Environment variables")
    health_url: Optional[str] = Field(None, description="Health check URL")
    urls: Optional[Dict[str, str]] = Field(default_factory=dict, description="Additional URLs")


class StartEnvironmentResponse(BaseModel):
    """Response model for starting an environment."""
    status: str
    name: str
    services: List[str]
    urls: Dict[str, str]
    started_at: Optional[str] = None


class StopEnvironmentResponse(BaseModel):
    """Response model for stopping an environment."""
    status: str
    name: str
    stopped_at: Optional[str] = None


class EnvironmentStatusResponse(BaseModel):
    """Response model for environment status."""
    name: str
    status: str
    compose_path: str
    services: List[str]
    urls: Dict[str, str]
    started_at: Optional[str] = None
    health_url: Optional[str] = None
    error_message: Optional[str] = None


class ServiceHealth(BaseModel):
    """Health information for a service."""
    id: str
    name: str
    status: str
    health: str
    image: str
    ports: List[Dict[str, Any]]


class DetailedStatusResponse(BaseModel):
    """Detailed status response with service health."""
    environment: str
    status: str
    services: Dict[str, ServiceHealth]


class EnvironmentListResponse(BaseModel):
    """Response model for listing environments."""
    environments: List[EnvironmentStatusResponse]
    count: int


class LogsResponse(BaseModel):
    """Response model for logs."""
    environment: str
    service: Optional[str]
    logs: Dict[str, str]


class StartRunRequest(BaseModel):
    """Request model for starting a run stack."""
    compose_files: List[str] = Field(..., description="List of docker-compose file paths")
    env_vars: Optional[Dict[str, str]] = Field(default_factory=dict, description="Environment variables")


class StartRunResponse(BaseModel):
    """Response model for starting a run stack."""
    run_id: str
    status: str
    project_name: str
    started_at: str
    services: Dict[str, Any]


class StopRunResponse(BaseModel):
    """Response model for stopping a run stack."""
    run_id: str
    status: str
    stopped_at: str


class RunStatusResponse(BaseModel):
    """Response model for run stack status."""
    run_id: str
    status: str
    project_name: str
    services: Dict[str, Any]


class RunServiceInfo(BaseModel):
    """Service information for run stack."""
    id: str
    name: str
    status: str
    health: str
    ports: List[Dict[str, Any]]


class RunServicesResponse(BaseModel):
    """Response model for run services discovery."""
    run_id: str
    status: str
    services: Dict[str, RunServiceInfo]


# API Endpoints

@router.post(
    "/environments/{name}/start",
    response_model=StartEnvironmentResponse,
    summary="Start an environment",
    description="Start a Docker Compose stack for the specified environment"
)
async def start_environment(
    name: str,
    request: StartEnvironmentRequest
) -> StartEnvironmentResponse:
    """
    Start an environment compose stack.
    
    - **name**: Unique name for the environment
    - **compose_path**: Path to docker-compose.yml file
    - **env_vars**: Optional environment variables
    - **health_url**: Optional health check URL to wait for
    - **urls**: Optional additional URLs to expose
    
    Waits for health check to pass if health_url is provided.
    """
    try:
        stack_info = await stack_manager.start_environment(
            name=name,
            compose_path=request.compose_path,
            env_vars=request.env_vars,
            health_url=request.health_url,
            urls=request.urls
        )
        
        return StartEnvironmentResponse(
            status=stack_info.status,
            name=stack_info.name,
            services=stack_info.services,
            urls=stack_info.urls,
            started_at=stack_info.started_at
        )
        
    except StackManagerError as e:
        logger.error(f"Failed to start environment '{name}': {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error starting environment '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.post(
    "/environments/{name}/stop",
    response_model=StopEnvironmentResponse,
    summary="Stop an environment",
    description="Stop and remove a Docker Compose stack"
)
async def stop_environment(name: str) -> StopEnvironmentResponse:
    """
    Stop and remove an environment stack.
    
    - **name**: Name of the environment to stop
    """
    try:
        stack_info = await stack_manager.stop_environment(name)
        
        return StopEnvironmentResponse(
            status=stack_info.status,
            name=stack_info.name,
            stopped_at=stack_info.stopped_at
        )
        
    except StackManagerError as e:
        logger.error(f"Failed to stop environment '{name}': {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error stopping environment '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get(
    "/environments/{name}/status",
    response_model=EnvironmentStatusResponse,
    summary="Get environment status",
    description="Get the current status of an environment"
)
async def get_environment_status(name: str) -> EnvironmentStatusResponse:
    """
    Get status of an environment.
    
    - **name**: Name of the environment
    
    Returns stack status (running, stopped, unhealthy, error) and basic info.
    """
    stack_info = stack_manager.get_stack_status(name)
    
    if not stack_info:
        raise HTTPException(status_code=404, detail=f"Environment '{name}' not found")
    
    return EnvironmentStatusResponse(
        name=stack_info.name,
        status=stack_info.status,
        compose_path=stack_info.compose_path,
        services=stack_info.services,
        urls=stack_info.urls,
        started_at=stack_info.started_at,
        health_url=stack_info.health_url,
        error_message=stack_info.error_message
    )


@router.get(
    "/environments/{name}/health",
    response_model=DetailedStatusResponse,
    summary="Get detailed health status",
    description="Get detailed health information for all services in an environment"
)
async def get_detailed_health(name: str) -> DetailedStatusResponse:
    """
    Get detailed health status for all services.
    
    - **name**: Name of the environment
    
    Returns container status, health checks, and port mappings.
    """
    try:
        health_info = stack_manager.get_service_health(name)
        
        # Convert to Pydantic models
        services = {
            name: ServiceHealth(**data)
            for name, data in health_info['services'].items()
        }
        
        return DetailedStatusResponse(
            environment=health_info['environment'],
            status=health_info['status'],
            services=services
        )
        
    except StackManagerError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting health for '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get(
    "/environments/{name}/logs",
    response_model=LogsResponse,
    summary="Get environment logs",
    description="Get logs from environment services"
)
async def get_environment_logs(
    name: str,
    service: Optional[str] = Query(None, description="Filter by service name"),
    lines: int = Query(100, ge=1, le=10000, description="Number of log lines"),
    follow: bool = Query(False, description="Stream logs (not implemented)")
) -> LogsResponse:
    """
    Get logs from environment services.
    
    - **name**: Name of the environment
    - **service**: Optional service name filter
    - **lines**: Number of lines to return (1-10000)
    - **follow**: Stream logs (returns initial logs only)
    """
    try:
        if follow:
            # Return streaming response
            async def log_generator():
                async for line in stack_manager.stream_logs(name, service):
                    yield line + "\n"
            
            return StreamingResponse(
                log_generator(),
                media_type="text/plain"
            )
        else:
            # Return JSON response
            logs = stack_manager.get_logs(name, service, lines, follow)
            return LogsResponse(
                environment=name,
                service=service,
                logs=logs
            )
            
    except StackManagerError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting logs for '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get(
    "/environments",
    response_model=EnvironmentListResponse,
    summary="List all environments",
    description="List all managed environments and their status"
)
async def list_environments() -> EnvironmentListResponse:
    """
    List all managed environments and their status.
    """
    try:
        environments = stack_manager.list_environments()
        
        env_list = [
            EnvironmentStatusResponse(
                name=env.name,
                status=env.status,
                compose_path=env.compose_path,
                services=env.services,
                urls=env.urls,
                started_at=env.started_at,
                health_url=env.health_url,
                error_message=env.error_message
            )
            for env in environments
        ]
        
        return EnvironmentListResponse(
            environments=env_list,
            count=len(env_list)
        )
        
    except Exception as e:
        logger.error(f"Error listing environments: {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


# Run-scoped stack endpoints

@router.post(
    "/runs/{run_id}/start",
    response_model=StartRunResponse,
    summary="Start a run-scoped stack",
    description="Start Docker Compose stack(s) for a specific run with run_id labeling"
)
async def start_run_stack(
    run_id: str,
    request: StartRunRequest
) -> StartRunResponse:
    """
    Start a run-scoped Docker stack.
    
    - **run_id**: Unique run identifier
    - **compose_files**: List of compose file paths to apply in order
    - **env_vars**: Optional environment variables
    
    Containers are labeled with `mase.run_id` for tracking.
    """
    try:
        run_info = await stack_manager.start_run_stack(
            run_id=run_id,
            compose_files=request.compose_files,
            env_vars=request.env_vars
        )
        
        return StartRunResponse(
            run_id=run_info.run_id,
            status=run_info.status,
            project_name=run_info.project_name,
            started_at=run_info.started_at,
            services=run_info.services
        )
        
    except StackManagerError as e:
        logger.error(f"Failed to start run stack '{run_id}': {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error starting run stack '{run_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.post(
    "/runs/{run_id}/stop",
    response_model=StopRunResponse,
    summary="Stop a run-scoped stack",
    description="Stop and remove Docker containers for a specific run"
)
async def stop_run_stack(run_id: str) -> StopRunResponse:
    """
    Stop a run-scoped Docker stack.
    
    - **run_id**: Run identifier to stop
    
    Stops all containers labeled with the run_id.
    """
    try:
        run_info = await stack_manager.stop_run_stack(run_id)
        
        return StopRunResponse(
            run_id=run_info.run_id,
            status=run_info.status,
            stopped_at=run_info.stopped_at
        )
        
    except StackManagerError as e:
        logger.error(f"Failed to stop run stack '{run_id}': {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error stopping run stack '{run_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.delete(
    "/runs/{run_id}",
    response_model=StopRunResponse,
    summary="Delete a run-scoped stack",
    description="Stop and completely remove Docker containers for a specific run"
)
async def delete_run_stack(run_id: str) -> StopRunResponse:
    """
    Delete a run-scoped Docker stack.
    
    - **run_id**: Run identifier to delete
    
    Stops and removes all containers labeled with the run_id.
    """
    try:
        run_info = await stack_manager.delete_run_stack(run_id)
        
        return StopRunResponse(
            run_id=run_info.run_id,
            status=run_info.status,
            stopped_at=run_info.stopped_at
        )
        
    except StackManagerError as e:
        logger.error(f"Failed to delete run stack '{run_id}': {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error deleting run stack '{run_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.get(
    "/runs/{run_id}/status",
    response_model=RunStatusResponse,
    summary="Get run stack status",
    description="Get current status of a run-scoped stack"
)
async def get_run_status(run_id: str) -> RunStatusResponse:
    """
    Get status of a run stack.
    
    - **run_id**: Run identifier
    
    Returns stack status and service information.
    """
    run_info = stack_manager.get_run_stack_status(run_id)
    
    if not run_info:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    
    return RunStatusResponse(
        run_id=run_info.run_id,
        status=run_info.status,
        project_name=run_info.project_name,
        services=run_info.services
    )


@router.get(
    "/runs/{run_id}/services",
    response_model=RunServicesResponse,
    summary="Get run services",
    description="Get detailed service discovery information for a run"
)
async def get_run_services(run_id: str) -> RunServicesResponse:
    """
    Get service discovery information for a run.
    
    - **run_id**: Run identifier
    
    Returns detailed information about all services in the run stack
    including container IDs, ports, and health status.
    """
    try:
        containers = stack_manager.get_run_containers(run_id)
        
        if not containers:
            raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found or no containers")
        
        services = {}
        for container in containers:
            service_name = container.labels.get('com.docker.compose.service', 'unknown')
            
            # Extract port mappings
            ports = []
            if container.ports:
                for port_key, port_bindings in container.ports.items():
                    if port_bindings:
                        for binding in port_bindings:
                            ports.append({
                                'internal': binding.get('HostPort'),
                                'external': binding.get('HostPort'),
                                'type': binding.get('Type', 'tcp')
                            })
            
            services[service_name] = RunServiceInfo(
                id=container.id[:12],
                name=container.name,
                status=container.status,
                health=stack_manager.docker.get_container_health(container.id),
                ports=ports
            )
        
        return RunServicesResponse(
            run_id=run_id,
            status='running' if containers else 'stopped',
            services=services
        )
        
    except Exception as e:
        logger.error(f"Error getting run services for '{run_id}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")


@router.delete(
    "/environments/{name}",
    summary="Delete an environment",
    description="Remove an environment from management (stops if running)"
)
async def delete_environment(name: str):
    """
    Delete an environment from management.
    
    - **name**: Name of the environment to delete
    
    Stops the environment if running and removes it from state tracking.
    """
    try:
        stack_info = stack_manager.get_stack_status(name)
        
        if not stack_info:
            raise HTTPException(status_code=404, detail=f"Environment '{name}' not found")
        
        # Stop if running
        if stack_info.status in ('running', 'starting', 'unhealthy'):
            await stack_manager.stop_environment(name)
        
        # Remove from state
        del stack_manager.running_stacks[name]
        stack_manager._delete_state(name)
        
        return JSONResponse(
            content={"message": f"Environment '{name}' deleted"},
            status_code=200
        )
        
    except StackManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting environment '{name}': {e}")
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
