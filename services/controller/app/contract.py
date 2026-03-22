"""Environment Contract Models and Validator.

This module defines the environment contract schema and provides validation
capabilities to ensure environments implement the required API.
"""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ContractVersion(str, Enum):
    """Environment contract versions."""

    V1 = "v1.0.0"


class EndpointDefinition(BaseModel):
    """Definition of an API endpoint."""

    path: str = Field(..., description="URL path (e.g., /health)")
    method: str = Field(..., pattern="^(GET|POST|PUT|DELETE|PATCH)$", description="HTTP method")
    description: str = Field(default="", description="What this endpoint does")
    required_params: List[str] = Field(
        default_factory=list, description="Required query/body parameters"
    )
    response_schema: Optional[Dict[str, Any]] = Field(
        default=None, description="Expected response structure"
    )


class Capability(str, Enum):
    """Environment capabilities that can be declared."""

    HEALTH_CHECK = "health_check"
    CONTRACT_DISCOVERY = "contract_discovery"
    SKILL_DOCUMENTATION = "skill_documentation"
    RUN_LIFECYCLE = "run_lifecycle"
    AGENT_MANAGEMENT = "agent_management"
    ACTION_API = "action_api"
    EVENT_EXPORT = "event_export"
    STATE_SNAPSHOT = "state_snapshot"
    METRICS = "metrics"


class EnvironmentContract(BaseModel):
    """Environment contract definition.

    Every environment must implement this contract to be compatible
    with the MASE runtime/environment/run platform.
    """

    contract_version: ContractVersion = Field(
        default=ContractVersion.V1, description="Version of this contract specification"
    )
    environment_name: str = Field(..., description="Name of the environment")
    environment_version: str = Field(..., description="Version of the environment implementation")

    # Required endpoints
    endpoints: List[EndpointDefinition] = Field(
        ..., description="List of all implemented endpoints"
    )

    # Capabilities
    capabilities: List[str] = Field(
        default_factory=list, description="Supported capabilities"
    )

    # Schema information
    event_schema_version: str = Field(default="v1", description="Version of the event schema used")

    # Optional metadata
    description: str = Field(default="", description="Environment description")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")

    model_config = {
        "json_schema_extra": {
            "example": {
                "contract_version": "v1.0.0",
                "environment_name": "reference-social-env",
                "environment_version": "1.0.0",
                "endpoints": [
                    {"path": "/health", "method": "GET", "description": "Health check endpoint"},
                    {"path": "/contract", "method": "GET", "description": "Return this contract"},
                ],
                "capabilities": ["health_check", "contract_discovery", "skill_documentation"],
                "event_schema_version": "v1",
                "description": "Reference environment implementing the v1 contract",
            }
        }
    }


class ContractValidationResult(BaseModel):
    """Result of contract validation."""

    valid: bool = Field(..., description="Whether the contract is valid")
    environment_name: Optional[str] = Field(default=None, description="Detected environment name")
    environment_version: Optional[str] = Field(
        default=None, description="Detected environment version"
    )
    missing_required_endpoints: List[str] = Field(
        default_factory=list, description="List of missing required endpoint names"
    )
    missing_capabilities: List[str] = Field(
        default_factory=list, description="List of missing required capabilities"
    )
    errors: List[str] = Field(default_factory=list, description="Validation error messages")
    warnings: List[str] = Field(default_factory=list, description="Validation warnings")

    def is_compatible(self) -> bool:
        """Check if environment is compatible (valid + has required endpoints)."""
        return self.valid and len(self.missing_required_endpoints) == 0


# Required endpoints for v1 contract
REQUIRED_ENDPOINTS_V1 = [
    ("GET", "/health"),
    ("GET", "/contract"),
    ("GET", "/skill.md"),
]

# Required capabilities for v1
REQUIRED_CAPABILITIES_V1 = [
    Capability.HEALTH_CHECK.value,
    Capability.CONTRACT_DISCOVERY.value,
]

# Nice-to-have endpoints (will warn but not block)
NICE_TO_HAVE_ENDPOINTS_V1 = [
    ("POST", "/run/init"),
    ("POST", "/run/reset"),
    ("POST", "/run/stop"),
    ("POST", "/agents/register"),
    ("POST", "/agents/unregister"),
    ("GET", "/state/snapshot"),
    ("GET", "/metrics"),
]

# Nice-to-have capabilities
NICE_TO_HAVE_CAPABILITIES_V1 = [
    Capability.RUN_LIFECYCLE.value,
    Capability.AGENT_MANAGEMENT.value,
    Capability.STATE_SNAPSHOT.value,
    Capability.METRICS.value,
]
