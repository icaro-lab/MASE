"""API routes for environment management."""
from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from ..config import Settings, get_settings
from ..models import EnvironmentInfo, EnvironmentStatus
from ..orchestrator_client import ControllerClient, OrchestratorClient


router = APIRouter(prefix="/environments", tags=["environments"])


def get_controller(settings: Settings = Depends(get_settings)) -> ControllerClient:
    """Get controller client instance."""
    return ControllerClient(settings)


def get_orchestrator(settings: Settings = Depends(get_settings)) -> OrchestratorClient:
    """Get orchestrator client instance."""
    return OrchestratorClient(settings)


@router.get("", response_model=List[EnvironmentInfo])
async def list_environments(
    controller: ControllerClient = Depends(get_controller)
) -> List[EnvironmentInfo]:
    """List all available environments from the registry."""
    try:
        envs = await controller.list_environments()
        return [
            EnvironmentInfo(
                name=env.get("id") or env.get("name", ""),
                description=env.get("description", ""),
                version=env.get("version", "unknown"),
                tags=env.get("tags", []),
                requirements=env.get("requirements", {}),
            )
            for env in envs
        ]
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list environments: {str(e)}")
    finally:
        await controller.close()


@router.get("/{name}/status", response_model=EnvironmentStatus)
async def get_environment_status(
    name: str,
    orchestrator: OrchestratorClient = Depends(get_orchestrator)
) -> EnvironmentStatus:
    """Get the status of a specific environment."""
    try:
        status = await orchestrator.get_environment_status(name)
        return EnvironmentStatus(
            name=name,
            status=status.get("status", "unknown"),
            services=status.get("services", []),
            urls=status.get("urls", {}),
            health=status.get("health", {})
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get environment status: {str(e)}")
    finally:
        await orchestrator.close()


@router.post("/{name}/start")
async def start_environment(
    name: str,
    orchestrator: OrchestratorClient = Depends(get_orchestrator)
) -> dict:
    """Start an environment."""
    try:
        result = await orchestrator.start_environment(name)
        return {
            "message": f"Environment '{name}' started",
            "name": name,
            "status": "started",
            "details": result
        }
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start environment: {str(e)}")
    finally:
        await orchestrator.close()


@router.post("/{name}/stop")
async def stop_environment(
    name: str,
    orchestrator: OrchestratorClient = Depends(get_orchestrator)
) -> dict:
    """Stop an environment."""
    try:
        result = await orchestrator.stop_environment(name)
        return {
            "message": f"Environment '{name}' stopped",
            "name": name,
            "status": "stopped",
            "details": result
        }
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop environment: {str(e)}")
    finally:
        await orchestrator.close()
