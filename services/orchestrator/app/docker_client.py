"""Docker client interface for managing containers and compose stacks."""

import os
import re
import subprocess
import logging
from typing import Optional, List, Dict, Any
from pathlib import Path

import docker
from docker.errors import DockerException, NotFound, APIError
import yaml

from app.config import settings

logger = logging.getLogger(__name__)


class DockerClientError(Exception):
    """Custom exception for Docker client errors."""
    pass


class ComposeValidationError(Exception):
    """Exception for compose file validation errors."""
    pass


class DockerClient:
    """Interface to Docker daemon for managing compose stacks."""
    
    def __init__(self):
        """Initialize Docker client."""
        self.client = None
        self._connect()
    
    def _connect(self):
        """Connect to Docker daemon."""
        try:
            if settings.docker_host:
                self.client = docker.DockerClient(base_url=settings.docker_host)
            else:
                self.client = docker.from_env()
            # Test connection
            self.client.ping()
            logger.info("Connected to Docker daemon")
        except DockerException as e:
            logger.error(f"Failed to connect to Docker: {e}")
            raise DockerClientError(f"Failed to connect to Docker: {e}")
    
    def validate_compose_path(self, compose_path: str) -> str:
        """
        Validate compose file path to prevent directory traversal attacks.
        
        Args:
            compose_path: Path to docker-compose.yml file
            
        Returns:
            Resolved absolute path
            
        Raises:
            ComposeValidationError: If path is invalid or outside allowed directories
        """
        # Resolve to absolute path
        path = Path(compose_path).resolve()
        
        # Check for directory traversal patterns
        if settings.prevent_directory_traversal:
            # Normalize and check for suspicious patterns
            path_str = str(path)
            
            # Check for path traversal attempts
            if ".." in compose_path:
                raise ComposeValidationError(f"Path traversal detected in: {compose_path}")
            
            # Check if path is within allowed directories
            allowed = False
            allowed_dirs = [d.strip() for d in settings.allowed_compose_dirs.split(',')]
            for allowed_dir in allowed_dirs:
                allowed_path = Path(allowed_dir).resolve()
                try:
                    path.relative_to(allowed_path)
                    allowed = True
                    break
                except ValueError:
                    continue
            
            if not allowed:
                raise ComposeValidationError(
                    f"Compose path '{compose_path}' is outside allowed directories"
                )
        
        # Check if file exists
        if not path.exists():
            raise ComposeValidationError(f"Compose file not found: {path}")
        
        if not path.is_file():
            raise ComposeValidationError(f"Path is not a file: {path}")
        
        return str(path)
    
    def parse_compose_file(self, compose_path: str) -> Dict[str, Any]:
        """
        Parse docker-compose.yml file.
        
        Args:
            compose_path: Path to compose file
            
        Returns:
            Dictionary containing compose configuration
        """
        try:
            with open(compose_path, 'r') as f:
                return yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ComposeValidationError(f"Invalid YAML in compose file: {e}")
        except Exception as e:
            raise ComposeValidationError(f"Failed to parse compose file: {e}")
    
    def get_services(self, compose_path: str) -> List[str]:
        """
        Get list of services defined in compose file.
        
        Args:
            compose_path: Path to compose file
            
        Returns:
            List of service names
        """
        compose = self.parse_compose_file(compose_path)
        services = compose.get('services', {})
        return list(services.keys())
    
    def get_service_ports(self, compose_path: str) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get port mappings for all services.
        
        Args:
            compose_path: Path to compose file
            
        Returns:
            Dictionary mapping service names to port configurations
        """
        compose = self.parse_compose_file(compose_path)
        services = compose.get('services', {})
        ports_map = {}
        
        for service_name, config in services.items():
            ports = config.get('ports', [])
            ports_map[service_name] = []
            for port in ports:
                if isinstance(port, str):
                    # Parse "host:container" or "host:container/protocol" format
                    parts = port.split('/')
                    protocol = parts[1] if len(parts) > 1 else 'tcp'
                    host_port, container_port = parts[0].split(':')
                    ports_map[service_name].append({
                        'host_port': int(host_port),
                        'container_port': container_port,
                        'protocol': protocol
                    })
                elif isinstance(port, dict):
                    ports_map[service_name].append({
                        'host_port': port.get('published'),
                        'container_port': port.get('target'),
                        'protocol': port.get('protocol', 'tcp')
                    })
        
        return ports_map
    
    def _get_host_path(self, container_path: str) -> tuple[str, str]:
        """
        Convert container path to host path for docker-compose.
        
        When running inside a container, docker-compose executes on the host
        Docker daemon which needs paths that exist on the host filesystem.
        
        Uses HOST_PROJECT_ROOT environment variable to construct proper paths.
        
        Args:
            container_path: Absolute path inside the container
            
        Returns:
            Tuple of (host_path, working_dir) for subprocess
        """
        from app.config import settings
        
        host_root = settings.host_project_root
        
        # If path starts with /app/environments/, convert to host path
        if container_path.startswith('/app/environments/'):
            # Extract the path after /app/environments/
            subpath = container_path[18:]  # Remove '/app/environments/' prefix
            # Construct host path: ${HOST_PROJECT_ROOT}/environments/...
            host_path = os.path.join(host_root, "environments", subpath)
            # Working directory should be the project root on host
            return host_path, host_root
        
        # If path starts with /app/, convert using host_project_root
        if container_path.startswith('/app/'):
            subpath = container_path[5:]  # Remove '/app/' prefix
            host_path = os.path.join(host_root, subpath)
            return host_path, host_root
        
        # If already a relative path or doesn't start with /app/, use as-is
        if not container_path.startswith('/'):
            return container_path, host_root
        
        # For other absolute paths, use as-is with its directory as cwd
        return container_path, os.path.dirname(container_path)
    
    def compose_up(
        self,
        compose_path: Optional[str] = None,
        compose_files: Optional[List[str]] = None,
        env_vars: Optional[Dict[str, str]] = None,
        project_name: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None
    ) -> subprocess.CompletedProcess:
        """
        Start a docker-compose stack.
        
        Args:
            compose_path: Path to docker-compose.yml (single file mode, optional if compose_files provided)
            compose_files: List of compose file paths (multi-file mode)
            env_vars: Environment variables to set
            project_name: Docker Compose project name
            labels: Additional labels to apply to containers
            
        Returns:
            CompletedProcess result
        """
        # Validate that at least one compose source is provided
        if not compose_files and not compose_path:
            raise DockerClientError("Either compose_path or compose_files must be provided")
        
        # Multi-file mode
        if compose_files:
            cmd = ['docker-compose']
            for f in compose_files:
                validated = self.validate_compose_path(f)
                host_path, work_dir = self._get_host_path(validated)
                cmd.extend(['-f', host_path])
            work_dir = settings.host_project_root
        else:
            # Single file mode (backward compatibility)
            validated_path = self.validate_compose_path(compose_path)
            host_path, work_dir = self._get_host_path(validated_path)
            cmd = ['docker-compose', '-f', host_path]
        
        if project_name:
            cmd.extend(['-p', project_name])
        cmd.extend(['up', '-d', '--remove-orphans'])
        
        # Note: --label flag is not supported in all docker-compose versions
        # Labels are applied via the docker-compose.yml file instead
        
        env = os.environ.copy()
        if env_vars:
            env.update(env_vars)
        
        logger.info(f"Running: {' '.join(cmd)} in {work_dir}")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                env=env,
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Compose up successful for {project_name or compose_path}")
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Compose up failed: {e.stderr}")
            raise DockerClientError(f"Failed to start compose stack: {e.stderr}")
    
    def compose_down(
        self,
        compose_path: Optional[str] = None,
        compose_files: Optional[List[str]] = None,
        project_name: Optional[str] = None,
        remove_volumes: bool = True
    ) -> subprocess.CompletedProcess:
        """
        Stop and remove a docker-compose stack.
        
        Args:
            compose_path: Path to docker-compose.yml (single file mode)
            compose_files: List of compose file paths (multi-file mode)
            project_name: Docker Compose project name
            remove_volumes: Whether to remove volumes
            
        Returns:
            CompletedProcess result
        """
        from app.config import settings
        
        work_dir = settings.host_project_root
        
        # Multi-file mode
        if compose_files:
            cmd = ['docker-compose']
            for f in compose_files:
                validated = self.validate_compose_path(f)
                host_path, _ = self._get_host_path(validated)
                cmd.extend(['-f', host_path])
        elif compose_path:
            # Single file mode (backward compatibility)
            validated_path = self.validate_compose_path(compose_path)
            host_path, work_dir = self._get_host_path(validated_path)
            cmd = ['docker-compose', '-f', host_path]
        else:
            # Project name only mode
            cmd = ['docker-compose']
        
        if project_name:
            cmd.extend(['-p', project_name])
        cmd.extend(['down'])
        if remove_volumes:
            cmd.append('-v')
        
        logger.info(f"Running: {' '.join(cmd)} in {work_dir}")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Compose down successful for {project_name or compose_path}")
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Compose down failed: {e.stderr}")
            raise DockerClientError(f"Failed to stop compose stack: {e.stderr}")
    
    def compose_stop(
        self,
        compose_path: Optional[str] = None,
        compose_files: Optional[List[str]] = None,
        project_name: Optional[str] = None,
        timeout: int = 10,
        services: Optional[List[str]] = None
    ) -> subprocess.CompletedProcess:
        """
        Stop a docker-compose stack without removing containers.
        
        Args:
            compose_path: Path to docker-compose.yml (single file mode)
            compose_files: List of compose file paths (multi-file mode)
            project_name: Docker Compose project name
            timeout: Timeout in seconds for stopping containers
            services: Optional service names to stop (defaults to all services)
            
        Returns:
            CompletedProcess result
        """
        from app.config import settings
        
        work_dir = settings.host_project_root
        
        # Multi-file mode
        if compose_files:
            cmd = ['docker-compose']
            for f in compose_files:
                validated = self.validate_compose_path(f)
                host_path, _ = self._get_host_path(validated)
                cmd.extend(['-f', host_path])
        elif compose_path:
            # Single file mode (backward compatibility)
            validated_path = self.validate_compose_path(compose_path)
            host_path, work_dir = self._get_host_path(validated_path)
            cmd = ['docker-compose', '-f', host_path]
        else:
            # Project name only mode
            cmd = ['docker-compose']
        
        if project_name:
            cmd.extend(['-p', project_name])
        cmd.extend(['stop', '-t', str(timeout)])
        if services:
            cmd.extend(services)
        
        logger.info(f"Running: {' '.join(cmd)} in {work_dir}")
        
        try:
            result = subprocess.run(
                cmd,
                cwd=work_dir,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=True
            )
            logger.info(f"Compose stop successful for {project_name or compose_path}")
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Compose stop failed: {e.stderr}")
            raise DockerClientError(f"Failed to stop compose stack: {e.stderr}")
    
    def get_stack_containers(
        self,
        project_name: str
    ) -> List[docker.models.containers.Container]:
        """
        Get containers for a compose project.
        
        Args:
            project_name: Docker Compose project name
            
        Returns:
            List of container objects
        """
        try:
            filters = {'label': f'com.docker.compose.project={project_name}'}
            return self.client.containers.list(filters=filters, all=True)
        except APIError as e:
            logger.error(f"Failed to list containers: {e}")
            raise DockerClientError(f"Failed to list containers: {e}")
    
    def get_containers_by_labels(
        self,
        labels: Dict[str, str],
        all_containers: bool = True
    ) -> List[docker.models.containers.Container]:
        """
        Get containers matching multiple labels.
        
        Args:
            labels: Dictionary of label key-value pairs to filter by
            all_containers: Include stopped containers
            
        Returns:
            List of container objects matching all labels
        """
        try:
            # Build label filters - multiple filters are ANDed together
            label_filters = [f"{k}={v}" for k, v in labels.items()]
            filters = {'label': label_filters}
            return self.client.containers.list(filters=filters, all=all_containers)
        except APIError as e:
            logger.error(f"Failed to list containers by labels: {e}")
            raise DockerClientError(f"Failed to list containers by labels: {e}")
    
    def get_container_health(self, container_id: str) -> str:
        """
        Get health status of a container.
        
        Args:
            container_id: Container ID or name
            
        Returns:
            Health status: healthy, unhealthy, starting, or unknown
        """
        try:
            container = self.client.containers.get(container_id)
            state = container.attrs.get('State', {})
            health = state.get('Health', {})
            return health.get('Status', 'unknown')
        except NotFound:
            return 'not_found'
        except Exception as e:
            logger.error(f"Failed to get container health: {e}")
            return 'unknown'
    
    def get_container_logs(
        self,
        container_id: str,
        tail: int = 100,
        follow: bool = False,
        since: Optional[int] = None
    ) -> str:
        """
        Get logs from a container.
        
        Args:
            container_id: Container ID or name
            tail: Number of lines to return from end
            follow: Whether to stream logs
            since: Unix timestamp to get logs since
            
        Returns:
            Container logs as string
        """
        try:
            container = self.client.containers.get(container_id)
            logs = container.logs(
                tail=tail,
                follow=follow,
                since=since,
                timestamps=True,
                stdout=True,
                stderr=True
            )
            return logs.decode('utf-8', errors='replace')
        except NotFound:
            raise DockerClientError(f"Container not found: {container_id}")
        except Exception as e:
            raise DockerClientError(f"Failed to get container logs: {e}")
    
    def list_running_stacks(self) -> List[Dict[str, Any]]:
        """
        List all running compose projects.
        
        Returns:
            List of project information dictionaries
        """
        try:
            containers = self.client.containers.list()
            projects = {}
            
            for container in containers:
                labels = container.labels
                project = labels.get('com.docker.compose.project')
                service = labels.get('com.docker.compose.service')
                
                if project:
                    if project not in projects:
                        projects[project] = {
                            'name': project,
                            'services': [],
                            'containers': []
                        }
                    projects[project]['services'].append(service)
                    projects[project]['containers'].append({
                        'id': container.id[:12],
                        'name': container.name,
                        'service': service,
                        'status': container.status,
                        'health': self.get_container_health(container.id)
                    })
            
            return list(projects.values())
        except APIError as e:
            logger.error(f"Failed to list running stacks: {e}")
            raise DockerClientError(f"Failed to list running stacks: {e}")
    
    def container_exists(self, name: str) -> bool:
        """Check if a container exists."""
        try:
            self.client.containers.get(name)
            return True
        except NotFound:
            return False
    
    def get_network_info(self, network_name: str) -> Optional[Dict[str, Any]]:
        """Get network information."""
        try:
            network = self.client.networks.get(network_name)
            return {
                'id': network.id,
                'name': network.name,
                'driver': network.attrs.get('Driver'),
                'scope': network.attrs.get('Scope'),
                'containers': list(network.attrs.get('Containers', {}).keys())
            }
        except NotFound:
            return None
        except Exception as e:
            logger.error(f"Failed to get network info: {e}")
            return None


# Singleton instance
docker_client = DockerClient()
