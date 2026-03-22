"""API routes for system health checks."""
from datetime import datetime
from fastapi import APIRouter, Depends

from ..config import Settings, get_settings
from ..models import ServiceHealth, SystemHealth
from ..orchestrator_client import ControllerClient, OrchestratorClient


router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=SystemHealth)
async def get_system_health(
    settings: Settings = Depends(get_settings)
) -> SystemHealth:
    """Get overall system health status."""
    services = []
    active_runs = 0
    
    # Check Orchestrator
    orchestrator = OrchestratorClient(settings)
    try:
        orch_health = await orchestrator.health_check()
        services.append(ServiceHealth(
            name="orchestrator",
            status=orch_health.get("status", "unknown"),
            details=orch_health.get("details")
        ))
    except Exception as e:
        services.append(ServiceHealth(
            name="orchestrator",
            status="unhealthy",
            error=str(e)
        ))
    finally:
        await orchestrator.close()
    
    # Check controller
    controller = ControllerClient(settings)
    try:
        ctrl_health = await controller.health_check()
        services.append(ServiceHealth(
            name="controller",
            status=ctrl_health.get("status", "unknown"),
            details=ctrl_health.get("details")
        ))
    except Exception as e:
        services.append(ServiceHealth(
            name="controller",
            status="unhealthy",
            error=str(e)
        ))
    finally:
        await controller.close()
    
    # Calculate overall status
    unhealthy_count = sum(1 for s in services if s.status == "unhealthy")
    degraded_count = sum(1 for s in services if s.status == "degraded")
    
    if unhealthy_count == len(services):
        overall_status = "unhealthy"
    elif unhealthy_count > 0 or degraded_count > 0:
        overall_status = "degraded"
    else:
        overall_status = "healthy"
    
    return SystemHealth(
        status=overall_status,
        timestamp=datetime.utcnow(),
        services=services,
        active_runs=active_runs
    )


@router.get("/live")
async def liveness_probe() -> dict:
    """Kubernetes liveness probe endpoint."""
    return {"status": "alive"}


@router.get("/ready")
async def readiness_probe(
    settings: Settings = Depends(get_settings)
) -> dict:
    """Kubernetes readiness probe endpoint."""
    # Check if we can connect to required services
    orchestrator = OrchestratorClient(settings)
    controller = ControllerClient(settings)
    
    ready = True
    errors = []
    
    try:
        orch_health = await orchestrator.health_check()
        if orch_health.get("status") == "unhealthy":
            ready = False
            errors.append("Orchestrator unavailable")
    except Exception as e:
        ready = False
        errors.append(f"Orchestrator error: {str(e)}")
    finally:
        await orchestrator.close()
    
    try:
        ctrl_health = await controller.health_check()
        if ctrl_health.get("status") == "unhealthy":
            ready = False
            errors.append("Controller unavailable")
    except Exception as e:
        ready = False
        errors.append(f"Controller error: {str(e)}")
    finally:
        await controller.close()
    
    if ready:
        return {"status": "ready"}
    else:
        return {"status": "not_ready", "errors": errors}
