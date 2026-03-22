"""SSE streaming routes for admin real-time telemetry.

Implements contracts.md §5.3 Admin Stream:
- GET /api/v1/runs/{run_id}/stream
- Real-time event streaming via SSE
- Support filtering by run/agent/action
"""

import asyncio
import json
import logging
from typing import Optional, Set
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import get_settings
from ..orchestrator_client import ControllerClient

# Import controller Redis client
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../controller'))

try:
    from app.redis_client import redis_client, get_run_channel, get_agent_channel
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis_client = None
    get_run_channel = lambda x: f"run:{x}:events"
    get_agent_channel = lambda x: f"agent:{x}:events"

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["streaming"])


# ============================================================================
# Pydantic Models
# ============================================================================

class StreamFilter(BaseModel):
    """Filter parameters for SSE stream."""
    
    agent_id: Optional[str] = Field(default=None, description="Filter by agent")
    source: Optional[str] = Field(default=None, description="Filter by source")
    action_category: Optional[str] = Field(default=None, description="Filter by category")
    action_type: Optional[str] = Field(default=None, description="Filter by action type")
    skill_name: Optional[str] = Field(default=None, description="Filter by skill")
    success_only: bool = Field(default=False, description="Only successful events")
    
    def matches(self, event: dict) -> bool:
        """Check if event matches filter criteria."""
        if self.agent_id and event.get("agent_id") != self.agent_id:
            return False
        if self.source and event.get("source") != self.source:
            return False
        if self.action_category and event.get("action_category") != self.action_category:
            return False
        if self.action_type and event.get("action_type") != self.action_type:
            return False
        if self.skill_name and event.get("skill_name") != self.skill_name:
            return False
        if self.success_only and not event.get("success", True):
            return False
        return True


# ============================================================================
# SSE Utilities
# ============================================================================

def format_sse_event(event_id: str, data: dict, event_type: str = "message") -> str:
    """Format data as SSE event.
    
    Args:
        event_id: Event ID
        data: Event data dictionary
        event_type: SSE event type
        
    Returns:
        Formatted SSE string
    """
    lines = [
        f"id: {event_id}",
        f"event: {event_type}",
        f"data: {json.dumps(data)}",
        "",  # Empty line to end event
    ]
    return "\n".join(lines) + "\n"


async def event_generator(
    run_id: str,
    filter_params: StreamFilter,
    history_count: int = 0
):
    """Generate SSE events from Redis pub/sub.
    
    Args:
        run_id: Run identifier
        filter_params: Filter parameters
        history_count: Number of historical messages to send first
        
    Yields:
        SSE formatted strings
    """
    if not REDIS_AVAILABLE or not redis_client:
        logger.warning("Redis not available for SSE stream; using controller polling fallback")
        settings = get_settings()
        controller = ControllerClient(settings)
        seen_event_ids: Set[str] = set()

        def normalize_event_id(event_data: dict, idx: int) -> str:
            direct = event_data.get("event_id") or event_data.get("id")
            if direct:
                return str(direct)
            return (
                f"{event_data.get('timestamp', '')}-"
                f"{event_data.get('agent_id', '')}-"
                f"{event_data.get('action_type', '')}-{idx}"
            )

        try:
            if history_count > 0:
                try:
                    history_result = await controller.get_run_events(
                        run_id=run_id,
                        limit=min(history_count, 1000),
                        offset=0,
                    )
                    history_events = history_result.get("events", [])
                    for idx, event_data in enumerate(reversed(history_events)):
                        if filter_params.matches(event_data):
                            event_id = normalize_event_id(event_data, idx)
                            seen_event_ids.add(event_id)
                            yield format_sse_event(event_id, event_data, "history")
                except Exception as e:
                    logger.warning(f"Polling fallback history fetch failed: {e}")

            yield format_sse_event(
                "conn-0",
                {
                    "type": "connected",
                    "run_id": run_id,
                    "timestamp": datetime.utcnow().isoformat(),
                    "transport": "polling_fallback",
                    "filter": filter_params.model_dump(exclude_none=True),
                },
                "connected",
            )

            while True:
                try:
                    latest_result = await controller.get_run_events(
                        run_id=run_id,
                        limit=50,
                        offset=0,
                    )
                    latest_events = latest_result.get("events", [])
                    for idx, event_data in enumerate(reversed(latest_events)):
                        if not filter_params.matches(event_data):
                            continue
                        event_id = normalize_event_id(event_data, idx)
                        if event_id in seen_event_ids:
                            continue
                        seen_event_ids.add(event_id)
                        yield format_sse_event(event_id, event_data, "telemetry")
                except Exception as e:
                    logger.warning(f"Polling fallback stream fetch failed: {e}")

                yield format_sse_event(
                    f"hb-{datetime.utcnow().timestamp()}",
                    {"type": "heartbeat", "timestamp": datetime.utcnow().isoformat()},
                    "heartbeat",
                )
                await asyncio.sleep(2.0)
        finally:
            await controller.close()
        return
    
    # Get channel name
    channel = get_run_channel(run_id)
    
    # Send historical events if requested
    if history_count > 0:
        try:
            history = await redis_client.get_history(channel, history_count)
            for idx, msg in enumerate(reversed(history)):
                try:
                    event_data = json.loads(msg)
                    if filter_params.matches(event_data):
                        yield format_sse_event(
                            f"hist-{idx}",
                            event_data,
                            "history"
                        )
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            logger.error(f"Error fetching history: {e}")
    
    # Send connection established event
    yield format_sse_event(
        "conn-0",
        {
            "type": "connected",
            "run_id": run_id,
            "timestamp": datetime.utcnow().isoformat(),
            "filter": filter_params.model_dump(exclude_none=True)
        },
        "connected"
    )
    
    # Subscribe to Redis channel
    try:
        async with redis_client.subscription(channel) as pubsub:
            while True:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=30.0
                    )
                    
                    if message and message.get("type") == "message":
                        try:
                            event_data = json.loads(message["data"])
                            
                            # Apply filters
                            if filter_params.matches(event_data):
                                event_id = event_data.get("event_id", str(datetime.utcnow().timestamp()))
                                yield format_sse_event(
                                    event_id,
                                    event_data,
                                    "telemetry"
                                )
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to decode message: {message['data']}")
                            continue
                    
                except asyncio.TimeoutError:
                    # Send heartbeat to keep connection alive
                    yield format_sse_event(
                        f"hb-{datetime.utcnow().timestamp()}",
                        {"type": "heartbeat", "timestamp": datetime.utcnow().isoformat()},
                        "heartbeat"
                    )
                    
    except Exception as e:
        logger.error(f"Error in event generator: {e}")
        yield format_sse_event(
            "error",
            {"error": str(e), "type": "stream_error"},
            "error"
        )


# ============================================================================
# API Endpoints
# ============================================================================

@router.get("/runs/{run_id}/stream")
async def stream_run_events(
    run_id: str,
    agent_id: Optional[str] = Query(None, description="Filter by agent"),
    source: Optional[str] = Query(None, description="Filter by source"),
    action_category: Optional[str] = Query(None, description="Filter by category"),
    action_type: Optional[str] = Query(None, description="Filter by action type"),
    skill_name: Optional[str] = Query(None, description="Filter by skill"),
    success_only: bool = Query(False, description="Only successful events"),
    history: int = Query(0, ge=0, le=1000, description="Include N historical events"),
):
    """Stream real-time telemetry events for a run via SSE.
    
    Implements contracts.md §5.3 Admin Stream.
    
    Args:
        run_id: Run identifier
        agent_id: Filter by agent ID
        source: Filter by source (agent, system)
        action_category: Filter by category (Self, Environmental, System, Institutional)
        action_type: Filter by action type
        skill_name: Filter by skill name
        success_only: Only return successful events
        history: Number of historical events to include (0-1000)
        
    Returns:
        SSE stream of telemetry events
        
    Example:
        ```
        GET /api/v1/runs/{run_id}/stream?agent_id=agent_001&success_only=true
        
        Event stream:
        id: conn-0
        event: connected
        data: {"type":"connected","run_id":"...","timestamp":"..."}
        
        id: evt_123
        event: telemetry
        data: {"event_id":"evt_123","agent_id":"agent_001",...}
        
        id: hb-1234567890
        event: heartbeat
        data: {"type":"heartbeat","timestamp":"..."}
        ```
    """
    # Build filter
    filter_params = StreamFilter(
        agent_id=agent_id,
        source=source,
        action_category=action_category,
        action_type=action_type,
        skill_name=skill_name,
        success_only=success_only
    )
    
    return StreamingResponse(
        event_generator(run_id, filter_params, history),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


@router.get("/agents/{agent_id}/stream")
async def stream_agent_events(
    agent_id: str,
    source: Optional[str] = Query(None, description="Filter by source"),
    action_category: Optional[str] = Query(None, description="Filter by category"),
    action_type: Optional[str] = Query(None, description="Filter by action type"),
    skill_name: Optional[str] = Query(None, description="Filter by skill"),
    success_only: bool = Query(False, description="Only successful events"),
    history: int = Query(0, ge=0, le=1000, description="Include N historical events"),
):
    """Stream real-time telemetry events for an agent via SSE.
    
    Args:
        agent_id: Agent identifier
        source: Filter by source
        action_category: Filter by category
        action_type: Filter by action type
        skill_name: Filter by skill name
        success_only: Only return successful events
        history: Number of historical events to include
        
    Returns:
        SSE stream of telemetry events
    """
    # Build filter
    filter_params = StreamFilter(
        agent_id=agent_id,
        source=source,
        action_category=action_category,
        action_type=action_type,
        skill_name=skill_name,
        success_only=success_only
    )
    
    # Use agent channel
    channel = get_agent_channel(agent_id)
    
    async def agent_event_generator():
        """Generate SSE events for agent channel."""
        if not REDIS_AVAILABLE or not redis_client:
            yield format_sse_event(
                "error",
                {"error": "Redis not available"},
                "error"
            )
            return
        
        # Send historical events if requested
        if history > 0:
            try:
                history_messages = await redis_client.get_history(channel, history)
                for idx, msg in enumerate(reversed(history_messages)):
                    try:
                        event_data = json.loads(msg)
                        if filter_params.matches(event_data):
                            yield format_sse_event(
                                f"hist-{idx}",
                                event_data,
                                "history"
                            )
                    except json.JSONDecodeError:
                        continue
            except Exception as e:
                logger.error(f"Error fetching history: {e}")
        
        # Send connection established event
        yield format_sse_event(
            "conn-0",
            {
                "type": "connected",
                "agent_id": agent_id,
                "timestamp": datetime.utcnow().isoformat(),
                "filter": filter_params.model_dump(exclude_none=True)
            },
            "connected"
        )
        
        # Subscribe to Redis channel
        try:
            async with redis_client.subscription(channel) as pubsub:
                while True:
                    try:
                        message = await asyncio.wait_for(
                            pubsub.get_message(ignore_subscribe_messages=True),
                            timeout=30.0
                        )
                        
                        if message and message.get("type") == "message":
                            try:
                                event_data = json.loads(message["data"])
                                
                                if filter_params.matches(event_data):
                                    event_id = event_data.get("event_id", str(datetime.utcnow().timestamp()))
                                    yield format_sse_event(
                                        event_id,
                                        event_data,
                                        "telemetry"
                                    )
                            except json.JSONDecodeError:
                                continue
                        
                    except asyncio.TimeoutError:
                        yield format_sse_event(
                            f"hb-{datetime.utcnow().timestamp()}",
                            {"type": "heartbeat", "timestamp": datetime.utcnow().isoformat()},
                            "heartbeat"
                        )
                        
        except Exception as e:
            logger.error(f"Error in agent event generator: {e}")
            yield format_sse_event(
                "error",
                {"error": str(e), "type": "stream_error"},
                "error"
            )
    
    return StreamingResponse(
        agent_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
