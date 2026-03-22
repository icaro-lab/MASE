"""API endpoints for editable admin platform settings."""

from fastapi import APIRouter, HTTPException

from ..models import OpenRouterSettingsPayload, OpenRouterSettingsResponse
from ..settings_store import describe_openrouter_fallback, mask_openrouter_key, update_openrouter_fallback_key

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/openrouter")
async def get_openrouter_settings() -> OpenRouterSettingsResponse:
    """Return configured OpenRouter API key status for run fallback behavior."""
    return OpenRouterSettingsResponse(**describe_openrouter_fallback())


@router.put("/openrouter")
async def update_openrouter_settings(payload: OpenRouterSettingsPayload) -> OpenRouterSettingsResponse:
    """Persist or clear baseline OpenRouter API key for future runs.

    Provide an empty string to clear the configured key and fall back to environment variable.
    """
    try:
        resolved_key, source = update_openrouter_fallback_key(payload.openrouter_api_key)
    except OSError:
        raise HTTPException(status_code=500, detail="Failed to persist OpenRouter key settings")

    return OpenRouterSettingsResponse(
        openrouter_api_key_configured=bool(resolved_key),
        openrouter_api_key_masked=mask_openrouter_key(resolved_key) if resolved_key else None,
        source=source,
    )
