"""HTTP client for the environment orchestrator service."""

import asyncio
from typing import Optional
import httpx

from app.config import settings


class OrchestratorClient:
    """Client for interacting with the environment orchestrator."""
    
    def __init__(self, base_url: Optional[str] = None):
        """Initialize the client with settings."""
        self.base_url = (base_url or settings.orchestrator_url).rstrip('/')
        self.timeout = httpx.Timeout(60.0)  # Longer timeout for env operations
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
    
    async def start_environment(self, name: str, compose_path: str, 
                                health_url: Optional[str] = None,
                                env_vars: Optional[dict] = None) -> dict:
        """Start an environment stack via the orchestrator."""
        client = await self._get_client()
        
        request = {
            "compose_path": compose_path,
            "env_vars": env_vars or {},
            "health_url": health_url,
        }
        
        try:
            response = await client.post(
                f"{self.base_url}/orchestrator/environments/{name}/start",
                json=request,
                timeout=120.0  # Environment startup can take time
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Failed to start environment: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Orchestrator service unavailable")
        except httpx.TimeoutException:
            raise RuntimeError("Environment startup timed out")
    
    async def stop_environment(self, name: str) -> dict:
        """Stop an environment stack."""
        client = await self._get_client()
        
        try:
            response = await client.post(
                f"{self.base_url}/orchestrator/environments/{name}/stop"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"Failed to stop environment: {e.response.text}")
        except httpx.ConnectError:
            raise RuntimeError("Orchestrator service unavailable")
    
    async def get_environment_status(self, name: str) -> dict:
        """Check if an environment is running."""
        client = await self._get_client()
        
        try:
            response = await client.get(
                f"{self.base_url}/orchestrator/environments/{name}/status"
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"name": name, "status": "stopped", "running": False}
            raise RuntimeError(f"Failed to get environment status: {e.response.text}")
        except httpx.ConnectError:
            return {"name": name, "status": "unknown", "running": False}
