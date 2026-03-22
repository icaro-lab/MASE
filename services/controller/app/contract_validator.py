"""Environment Contract Validator.

Validates that environments implement the required contract before
allowing runs to start.
"""

import httpx
from typing import Optional
from app.contract import (
    EnvironmentContract,
    ContractValidationResult,
    REQUIRED_ENDPOINTS_V1,
    REQUIRED_CAPABILITIES_V1,
    NICE_TO_HAVE_ENDPOINTS_V1,
    NICE_TO_HAVE_CAPABILITIES_V1,
)


class ContractValidator:
    """Validates environment contracts."""

    def __init__(self, timeout: float = 10.0):
        """Initialize validator.

        Args:
            timeout: HTTP request timeout in seconds
        """
        self.timeout = timeout

    async def validate_environment(
        self, environment_url: str, strict: bool = True
    ) -> ContractValidationResult:
        """Validate an environment's contract.

        Args:
            environment_url: Base URL of the environment
            strict: If True, missing nice-to-have endpoints are errors

        Returns:
            ContractValidationResult with validation details
        """
        result = ContractValidationResult(valid=False)

        try:
            # Try to fetch the contract
            contract = await self._fetch_contract(environment_url)

            if contract is None:
                result.errors.append(f"Could not fetch contract from {environment_url}/contract")
                return result

            result.environment_name = contract.environment_name
            result.environment_version = contract.environment_version

            # Validate required endpoints
            missing_required = self._check_required_endpoints(contract)
            result.missing_required_endpoints = missing_required

            # Validate required capabilities
            missing_caps = self._check_required_capabilities(contract)
            result.missing_capabilities = missing_caps

            # Check nice-to-have endpoints
            missing_nice = self._check_nice_to_have_endpoints(contract)
            if missing_nice:
                warning_msg = f"Missing nice-to-have endpoints: {', '.join(missing_nice)}"
                if strict:
                    result.errors.append(warning_msg)
                else:
                    result.warnings.append(warning_msg)

            # Check nice-to-have capabilities
            missing_nice_caps = self._check_nice_to_have_capabilities(contract)
            if missing_nice_caps:
                warning_msg = f"Missing nice-to-have capabilities: {', '.join(missing_nice_caps)}"
                if strict:
                    result.errors.append(warning_msg)
                else:
                    result.warnings.append(warning_msg)

            # Check if we can actually reach required endpoints
            unreachable = await self._check_endpoint_reachability(environment_url, contract)
            if unreachable:
                result.errors.extend([f"Endpoint unreachable: {ep}" for ep in unreachable])

            # Set valid if no errors and all required endpoints present
            result.valid = len(result.errors) == 0 and len(missing_required) == 0

        except Exception as e:
            result.errors.append(f"Validation error: {str(e)}")

        return result

    async def _fetch_contract(self, environment_url: str) -> Optional[EnvironmentContract]:
        """Fetch contract from environment.

        Args:
            environment_url: Base URL of environment

        Returns:
            EnvironmentContract if successful, None otherwise
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{environment_url}/contract")

                if response.status_code == 200:
                    return EnvironmentContract(**response.json())
                else:
                    return None

        except Exception:
            return None

    def _check_required_endpoints(self, contract: EnvironmentContract) -> list:
        """Check which required endpoints are missing.

        Args:
            contract: The environment contract

        Returns:
            List of missing endpoint descriptions
        """
        implemented = set((ep.method.upper(), ep.path) for ep in contract.endpoints)
        missing = []

        for method, path in REQUIRED_ENDPOINTS_V1:
            if (method.upper(), path) not in implemented:
                missing.append(f"{method} {path}")

        return missing

    def _check_required_capabilities(self, contract: EnvironmentContract) -> list:
        """Check which required capabilities are missing.

        Args:
            contract: The environment contract

        Returns:
            List of missing capability names
        """
        implemented = {str(cap).strip() for cap in contract.capabilities}
        missing = []

        for cap in REQUIRED_CAPABILITIES_V1:
            if cap not in implemented:
                missing.append(cap)

        return missing

    def _check_nice_to_have_endpoints(self, contract: EnvironmentContract) -> list:
        """Check which nice-to-have endpoints are missing.

        Args:
            contract: The environment contract

        Returns:
            List of missing endpoint descriptions
        """
        implemented = set((ep.method.upper(), ep.path) for ep in contract.endpoints)
        missing = []

        for method, path in NICE_TO_HAVE_ENDPOINTS_V1:
            if (method.upper(), path) not in implemented:
                missing.append(f"{method} {path}")

        return missing

    def _check_nice_to_have_capabilities(self, contract: EnvironmentContract) -> list:
        """Check which nice-to-have capabilities are missing.

        Args:
            contract: The environment contract

        Returns:
            List of missing capability names
        """
        implemented = {str(cap).strip() for cap in contract.capabilities}
        missing = []

        for cap in NICE_TO_HAVE_CAPABILITIES_V1:
            if cap not in implemented:
                missing.append(cap)

        return missing

    async def _check_endpoint_reachability(
        self, environment_url: str, contract: EnvironmentContract
    ) -> list:
        """Check if required endpoints are actually reachable.

        Args:
            environment_url: Base URL of environment
            contract: The environment contract

        Returns:
            List of unreachable endpoint descriptions
        """
        unreachable = []

        # Only check required endpoints
        required_paths = set(path for _, path in REQUIRED_ENDPOINTS_V1)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for endpoint in contract.endpoints:
                if endpoint.path not in required_paths:
                    continue

                try:
                    url = f"{environment_url}{endpoint.path}"
                    response = await client.request(endpoint.method, url)

                    if response.status_code >= 500:
                        unreachable.append(f"{endpoint.method} {endpoint.path} (server error)")

                except Exception as e:
                    unreachable.append(f"{endpoint.method} {endpoint.path} ({str(e)})")

        return unreachable


# Global validator instance
contract_validator = ContractValidator()
