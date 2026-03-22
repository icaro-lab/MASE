"""Stack manager for environment lifecycle management."""

import asyncio
import os
import logging
import sqlite3
import json
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, asdict
from pathlib import Path
import httpx

from app.config import settings
from app.docker_client import DockerClient, DockerClientError, docker_client

logger = logging.getLogger(__name__)


def _runtime_namespace() -> str:
    raw = str(
        os.getenv("COMPOSE_PROJECT_NAME")
        or os.getenv("MASE_NETWORK_NAME")
        or "mase"
    ).strip()
    if raw.endswith("-network"):
        raw = raw[: -len("-network")]
    return raw or "mase"


def _run_project_name(run_id: str) -> str:
    return f"{_runtime_namespace()}-run-{run_id}"


def _environment_project_name(name: str) -> str:
    return f"{_runtime_namespace()}-{name}"


@dataclass
class StackInfo:
    """Information about a running stack."""
    name: str
    compose_path: str
    status: str  # starting, running, unhealthy, stopped, error
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    services: List[str] = None
    urls: Dict[str, str] = None
    env_vars: Dict[str, str] = None
    health_url: Optional[str] = None
    error_message: Optional[str] = None
    project_name: Optional[str] = None
    
    def __post_init__(self):
        if self.services is None:
            self.services = []
        if self.urls is None:
            self.urls = {}
        if self.env_vars is None:
            self.env_vars = {}
        if not self.project_name:
            self.project_name = _environment_project_name(self.name)


class StackManagerError(Exception):
    """Custom exception for stack manager errors."""
    pass


@dataclass
class RunStackInfo:
    """Information about a run-scoped stack."""
    run_id: str
    status: str  # starting, running, stopped, error
    compose_files: List[str] = None
    project_name: Optional[str] = None
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    env_vars: Dict[str, str] = None
    error_message: Optional[str] = None
    services: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.compose_files is None:
            self.compose_files = []
        if self.env_vars is None:
            self.env_vars = {}
        if self.services is None:
            self.services = {}
        if not self.project_name:
            self.project_name = _run_project_name(self.run_id)


class StackManager:
    """Manages environment lifecycle and state tracking."""
    
    def __init__(self, state_db_path: Optional[str] = None):
        """
        Initialize stack manager.
        
        Args:
            state_db_path: Path to SQLite database for persistent state
        """
        self.docker = docker_client
        self.running_stacks: Dict[str, StackInfo] = {}
        self._health_check_tasks: Dict[str, asyncio.Task] = {}
        self._log_callbacks: Dict[str, List[Callable]] = {}
        
        # Initialize SQLite for persistent state
        self.db_path = state_db_path or "/tmp/orchestrator_state.db"
        self._init_database()
        
        # Load previous state
        self._load_state()
    
    def _init_database(self):
        """Initialize SQLite database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stacks (
                name TEXT PRIMARY KEY,
                compose_path TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                stopped_at TEXT,
                services TEXT,
                urls TEXT,
                env_vars TEXT,
                health_url TEXT,
                error_message TEXT,
                project_name TEXT
            )
        ''')
        conn.commit()
        conn.close()
    
    def _save_state(self, stack_info: StackInfo):
        """Save stack state to database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO stacks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            stack_info.name,
            stack_info.compose_path,
            stack_info.status,
            stack_info.started_at,
            stack_info.stopped_at,
            json.dumps(stack_info.services),
            json.dumps(stack_info.urls),
            json.dumps(stack_info.env_vars),
            stack_info.health_url,
            stack_info.error_message,
            stack_info.project_name
        ))
        conn.commit()
        conn.close()
    
    def _load_state(self):
        """Load stack states from database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM stacks')
        rows = cursor.fetchall()
        conn.close()
        
        for row in rows:
            stack = StackInfo(
                name=row[0],
                compose_path=row[1],
                status=row[2],
                started_at=row[3],
                stopped_at=row[4],
                services=json.loads(row[5]) if row[5] else [],
                urls=json.loads(row[6]) if row[6] else {},
                env_vars=json.loads(row[7]) if row[7] else {},
                health_url=row[8],
                error_message=row[9],
                project_name=row[10]
            )
            # Mark previously running stacks as stopped (they won't survive restart)
            if stack.status in ('starting', 'running'):
                stack.status = 'stopped'
                stack.stopped_at = datetime.utcnow().isoformat()
                self._save_state(stack)
            self.running_stacks[stack.name] = stack
    
    def _delete_state(self, name: str):
        """Delete stack state from database."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM stacks WHERE name = ?', (name,))
        conn.commit()
        conn.close()
    
    async def start_environment(
        self,
        name: str,
        compose_path: str,
        env_vars: Optional[Dict[str, str]] = None,
        health_url: Optional[str] = None,
        urls: Optional[Dict[str, str]] = None
    ) -> StackInfo:
        """
        Start an environment compose stack.
        
        Args:
            name: Environment name
            compose_path: Path to docker-compose.yml
            env_vars: Environment variables
            health_url: URL for health checks
            urls: Additional URLs to expose
            
        Returns:
            StackInfo object
        """
        if name in self.running_stacks and self.running_stacks[name].status == 'running':
            logger.info(f"Environment '{name}' is already running, returning existing stack")
            return self.running_stacks[name]
        
        try:
            # Validate compose file and get services
            services = self.docker.get_services(compose_path)
            ports = self.docker.get_service_ports(compose_path)
            
            # Create stack info
            stack_info = StackInfo(
                name=name,
                compose_path=compose_path,
                status='starting',
                started_at=datetime.utcnow().isoformat(),
                services=services,
                urls=urls or {},
                env_vars=env_vars or {},
                health_url=health_url,
                project_name=_environment_project_name(name)
            )
            
            # Add service URLs based on ports
            for service, port_list in ports.items():
                for port in port_list:
                    if port['host_port']:
                        stack_info.urls[f"{service}_port_{port['host_port']}"] = \
                            f"http://localhost:{port['host_port']}"
            
            self.running_stacks[name] = stack_info
            self._save_state(stack_info)
            
            # Start compose stack
            logger.info(f"Starting environment '{name}' from {compose_path}")
            self.docker.compose_up(
                compose_path=compose_path,
                env_vars=env_vars,
                project_name=stack_info.project_name
            )
            
            # Start health check polling if health URL provided
            if health_url:
                stack_info.status = 'starting'
                self._save_state(stack_info)
                
                health_passed = await self._wait_for_health(health_url)
                if health_passed:
                    stack_info.status = 'running'
                else:
                    stack_info.status = 'unhealthy'
                    stack_info.error_message = "Health check failed"
            else:
                # No health check, assume running after start
                await asyncio.sleep(2)  # Brief wait for containers to start
                stack_info.status = 'running'
            
            self._save_state(stack_info)
            logger.info(f"Environment '{name}' started with status: {stack_info.status}")
            return stack_info
            
        except DockerClientError as e:
            logger.error(f"Failed to start environment '{name}': {e}")
            if name in self.running_stacks:
                self.running_stacks[name].status = 'error'
                self.running_stacks[name].error_message = str(e)
                self._save_state(self.running_stacks[name])
            raise StackManagerError(f"Failed to start environment: {e}")
        except Exception as e:
            logger.error(f"Unexpected error starting environment '{name}': {e}")
            if name in self.running_stacks:
                self.running_stacks[name].status = 'error'
                self.running_stacks[name].error_message = str(e)
                self._save_state(self.running_stacks[name])
            raise StackManagerError(f"Unexpected error: {e}")
    
    async def _wait_for_health(self, health_url: str) -> bool:
        """
        Wait for health check to pass.
        
        Args:
            health_url: URL to check
            
        Returns:
            True if health check passed, False otherwise
        """
        timeout = settings.health_check_timeout
        interval = settings.health_check_interval
        retries = settings.health_check_retries
        
        logger.info(f"Waiting for health check at {health_url}")
        
        for attempt in range(retries):
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    response = await client.get(health_url)
                    if response.status_code == 200:
                        logger.info(f"Health check passed at {health_url}")
                        return True
            except httpx.RequestError as e:
                logger.debug(f"Health check attempt {attempt + 1}/{retries} failed: {e}")
            
            if attempt < retries - 1:
                await asyncio.sleep(interval)
        
        logger.error(f"Health check failed after {retries} attempts")
        return False
    
    async def stop_environment(self, name: str) -> StackInfo:
        """
        Stop and remove an environment stack.
        
        Args:
            name: Environment name
            
        Returns:
            StackInfo object
        """
        if name not in self.running_stacks:
            raise StackManagerError(f"Environment '{name}' not found")
        
        stack_info = self.running_stacks[name]
        
        try:
            logger.info(f"Stopping environment '{name}'")
            
            # Cancel any pending health check
            if name in self._health_check_tasks:
                self._health_check_tasks[name].cancel()
                del self._health_check_tasks[name]
            
            # Stop compose stack
            self.docker.compose_down(
                compose_path=stack_info.compose_path,
                project_name=stack_info.project_name,
                remove_volumes=True
            )
            
            stack_info.status = 'stopped'
            stack_info.stopped_at = datetime.utcnow().isoformat()
            self._save_state(stack_info)
            
            logger.info(f"Environment '{name}' stopped")
            return stack_info
            
        except DockerClientError as e:
            logger.error(f"Failed to stop environment '{name}': {e}")
            raise StackManagerError(f"Failed to stop environment: {e}")
    
    def get_stack_status(self, name: str) -> Optional[StackInfo]:
        """
        Get status of a stack.
        
        Args:
            name: Environment name
            
        Returns:
            StackInfo object or None
        """
        if name not in self.running_stacks:
            return None
        
        stack_info = self.running_stacks[name]
        
        # If running, check container health
        if stack_info.status == 'running':
            try:
                containers = self.docker.get_stack_containers(stack_info.project_name)
                if not containers:
                    stack_info.status = 'stopped'
                    self._save_state(stack_info)
                else:
                    # Check if all containers are healthy
                    unhealthy = []
                    for container in containers:
                        health = self.docker.get_container_health(container.id)
                        if health == 'unhealthy':
                            unhealthy.append(container.name)
                    
                    if unhealthy:
                        stack_info.status = 'unhealthy'
                        stack_info.error_message = f"Unhealthy containers: {', '.join(unhealthy)}"
                        self._save_state(stack_info)
            except DockerClientError as e:
                logger.error(f"Failed to get container status: {e}")
        
        return stack_info
    
    def list_environments(self) -> List[StackInfo]:
        """
        List all managed environments.
        
        Returns:
            List of StackInfo objects
        """
        return list(self.running_stacks.values())
    
    def get_logs(
        self,
        name: str,
        service: Optional[str] = None,
        lines: int = 100,
        follow: bool = False
    ) -> Dict[str, str]:
        """
        Get logs from environment services.
        
        Args:
            name: Environment name
            service: Specific service name (optional)
            lines: Number of lines to return
            follow: Whether to stream logs (not implemented)
            
        Returns:
            Dictionary mapping service names to logs
        """
        if name not in self.running_stacks:
            raise StackManagerError(f"Environment '{name}' not found")
        
        stack_info = self.running_stacks[name]
        
        try:
            containers = self.docker.get_stack_containers(stack_info.project_name)
            logs = {}
            
            for container in containers:
                container_service = container.labels.get('com.docker.compose.service', 'unknown')
                
                if service and container_service != service:
                    continue
                
                try:
                    container_logs = self.docker.get_container_logs(
                        container.id,
                        tail=lines,
                        follow=False
                    )
                    logs[container_service] = container_logs
                except DockerClientError as e:
                    logs[container_service] = f"Error getting logs: {e}"
            
            return logs
            
        except DockerClientError as e:
            raise StackManagerError(f"Failed to get logs: {e}")
    
    async def stream_logs(self, name: str, service: Optional[str] = None):
        """
        Stream logs from environment services.
        
        Args:
            name: Environment name
            service: Specific service name (optional)
            
        Yields:
            Log lines
        """
        if name not in self.running_stacks:
            raise StackManagerError(f"Environment '{name}' not found")
        
        stack_info = self.running_stacks[name]
        
        try:
            containers = self.docker.get_stack_containers(stack_info.project_name)
            
            for container in containers:
                container_service = container.labels.get('com.docker.compose.service', 'unknown')
                
                if service and container_service != service:
                    continue
                
                # Get initial logs
                logs = self.docker.get_container_logs(container.id, tail=100, follow=False)
                for line in logs.split('\n'):
                    if line.strip():
                        yield f"[{container_service}] {line}"
                
                # Note: Real-time streaming would require async subprocess
                # For now, we return static logs
                
        except DockerClientError as e:
            raise StackManagerError(f"Failed to stream logs: {e}")
    
    def get_service_health(self, name: str) -> Dict[str, Any]:
        """
        Get detailed health information for all services.
        
        Args:
            name: Environment name
            
        Returns:
            Dictionary with service health details
        """
        if name not in self.running_stacks:
            raise StackManagerError(f"Environment '{name}' not found")
        
        stack_info = self.running_stacks[name]
        
        try:
            containers = self.docker.get_stack_containers(stack_info.project_name)
            services = {}
            
            for container in containers:
                container_service = container.labels.get('com.docker.compose.service', 'unknown')
                
                services[container_service] = {
                    'id': container.id[:12],
                    'name': container.name,
                    'status': container.status,
                    'health': self.docker.get_container_health(container.id),
                    'image': container.image.tags[0] if container.image.tags else container.image.id[:12],
                    'ports': [
                        {
                            'internal': p.get('PrivatePort'),
                            'external': p.get('PublicPort'),
                            'type': p.get('Type')
                        }
                        for p in container.ports.values() if p
                    ] if container.ports else []
                }
            
            return {
                'environment': name,
                'status': stack_info.status,
                'services': services
            }
            
        except DockerClientError as e:
            raise StackManagerError(f"Failed to get service health: {e}")
    
    # Run-scoped stack operations
    
    async def start_run_stack(
        self,
        run_id: str,
        compose_files: List[str],
        env_vars: Optional[Dict[str, str]] = None
    ) -> RunStackInfo:
        """
        Start a run-scoped Docker stack.
        
        Args:
            run_id: Unique run identifier
            compose_files: List of compose file paths
            env_vars: Environment variables
            
        Returns:
            RunStackInfo object
        """
        if run_id in self.running_stacks and self.running_stacks[run_id].status == 'running':
            logger.info(f"Run stack '{run_id}' is already running")
            return self.running_stacks[run_id]
        
        try:
            project_name = _run_project_name(run_id)
            run_labels = {'mase.run_id': run_id, 'mase.stack_type': 'run'}
            
            run_info = RunStackInfo(
                run_id=run_id,
                status='starting',
                compose_files=compose_files,
                project_name=project_name,
                started_at=datetime.utcnow().isoformat(),
                env_vars=env_vars or {}
            )
            
            self.running_stacks[run_id] = run_info
            
            # Start compose stack with run labels
            logger.info(f"Starting run stack '{run_id}' with {len(compose_files)} compose files")
            self.docker.compose_up(
                compose_files=compose_files,
                env_vars=env_vars,
                project_name=project_name,
                labels=run_labels
            )
            
            # Wait briefly for containers to start
            await asyncio.sleep(2)
            
            # Get service info
            containers = self.get_run_containers(run_id)
            for container in containers:
                service_name = container.labels.get('com.docker.compose.service', 'unknown')
                run_info.services[service_name] = {
                    'id': container.id[:12],
                    'name': container.name,
                    'status': container.status,
                    'health': self.docker.get_container_health(container.id)
                }
            
            run_info.status = 'running'
            logger.info(f"Run stack '{run_id}' started successfully")
            return run_info
            
        except DockerClientError as e:
            logger.error(f"Failed to start run stack '{run_id}': {e}")
            if run_id in self.running_stacks:
                self.running_stacks[run_id].status = 'error'
                self.running_stacks[run_id].error_message = str(e)
            raise StackManagerError(f"Failed to start run stack: {e}")
    
    async def stop_run_stack(self, run_id: str) -> RunStackInfo:
        """
        Stop a run-scoped Docker stack.
        
        Args:
            run_id: Run identifier
            
        Returns:
            RunStackInfo object
        """
        if run_id not in self.running_stacks:
            # Recover from orchestrator memory loss by reconstructing from compose project.
            project_name = _run_project_name(run_id)
            containers = self.docker.get_stack_containers(project_name)
            if not containers:
                raise StackManagerError(f"Run stack '{run_id}' not found")
            recovered = RunStackInfo(
                run_id=run_id,
                status="running",
                project_name=project_name,
                compose_files=[],
            )
            for container in containers:
                service_name = container.labels.get("com.docker.compose.service", "unknown")
                recovered.services[service_name] = {
                    "id": container.id[:12],
                    "name": container.name,
                    "status": container.status,
                    "health": self.docker.get_container_health(container.id),
                }
            self.running_stacks[run_id] = recovered
        
        run_info = self.running_stacks[run_id]
        
        try:
            logger.info(
                f"Stopping run stack '{run_id}' (containers will be removed; volumes preserved)"
            )

            # Operator stop should tear down all run-scoped containers while leaving
            # named volumes intact so follow-up delete/reconciliation paths remain safe.
            self.docker.compose_down(
                project_name=run_info.project_name,
                remove_volumes=False,
            )
            
            run_info.status = 'stopped'
            run_info.stopped_at = datetime.utcnow().isoformat()
            
            logger.info(
                f"Run stack '{run_id}' stopped and containers removed"
            )
            return run_info
            
        except DockerClientError as e:
            logger.error(f"Failed to stop run stack '{run_id}': {e}")
            raise StackManagerError(f"Failed to stop run stack: {e}")
    
    async def delete_run_stack(self, run_id: str) -> RunStackInfo:
        """
        Delete a run-scoped Docker stack and remove all containers.
        
        Args:
            run_id: Run identifier
            
        Returns:
            RunStackInfo object
        """
        if run_id not in self.running_stacks:
            # Try to find and delete by label anyway
            try:
                containers = self.docker.get_containers_by_labels({'mase.run_id': run_id})
                if containers:
                    project_name = _run_project_name(run_id)
                    self.docker.compose_down(
                        project_name=project_name,
                        remove_volumes=True
                    )
                    logger.info(f"Deleted run stack '{run_id}' by label")
                    
                    # Create a temp run_info for response
                    run_info = RunStackInfo(
                        run_id=run_id,
                        status='deleted',
                        project_name=project_name,
                        compose_files=[],
                        stopped_at=datetime.utcnow().isoformat()
                    )
                    return run_info
            except Exception as e:
                logger.warning(f"Could not delete run stack '{run_id}': {e}")
            raise StackManagerError(f"Run stack '{run_id}' not found")
        
        run_info = self.running_stacks[run_id]
        
        try:
            logger.info(f"Deleting run stack '{run_id}' (containers will be removed)")
            
            # Use compose_down with project_name only - don't rely on compose files
            # since the override file may have been cleaned up
            self.docker.compose_down(
                project_name=run_info.project_name,
                remove_volumes=True
            )
            
            run_info.status = 'deleted'
            run_info.stopped_at = datetime.utcnow().isoformat()
            
            # Remove from running_stacks
            del self.running_stacks[run_id]
            
            logger.info(f"Run stack '{run_id}' deleted")
            return run_info
            
        except DockerClientError as e:
            logger.error(f"Failed to delete run stack '{run_id}': {e}")
            raise StackManagerError(f"Failed to delete run stack: {e}")
    
    def get_run_containers(self, run_id: str) -> List[Any]:
        """
        Get containers for a run using Docker labels.
        
        Args:
            run_id: Run identifier
            
        Returns:
            List of container objects
        """
        try:
            containers = self.docker.get_containers_by_labels({"mase.run_id": run_id})
            if containers:
                return containers
            # Fallback: use compose project label (always present for compose-managed containers).
            return self.docker.get_stack_containers(_run_project_name(run_id))
        except DockerClientError as e:
            logger.error(f"Failed to get run containers for '{run_id}': {e}")
            return []
    
    def get_run_stack_status(self, run_id: str) -> Optional[RunStackInfo]:
        """
        Get status of a run stack.
        
        Args:
            run_id: Run identifier
            
        Returns:
            RunStackInfo object or None
        """
        if run_id in self.running_stacks:
            return self.running_stacks[run_id]
        
        # Check if containers exist with run_id label
        containers = self.get_run_containers(run_id)
        if containers:
            # Reconstruct run info from containers
            run_info = RunStackInfo(
                run_id=run_id,
                status='running',
                project_name=_run_project_name(run_id)
            )
            for container in containers:
                service_name = container.labels.get('com.docker.compose.service', 'unknown')
                run_info.services[service_name] = {
                    'id': container.id[:12],
                    'name': container.name,
                    'status': container.status
                }
            return run_info
        
        return None


# Singleton instance
stack_manager = StackManager()
