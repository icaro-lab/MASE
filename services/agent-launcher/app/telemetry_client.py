"""Telemetry client for agent-launcher.

Implements event buffer and sender per contracts.md §5:
- In-memory batching
- Flush on: buffer size >= 50, time >= 5s, critical event
- Send to telemetry endpoint
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from enum import Enum

import httpx

from .ingest_queue import enqueue_telemetry_batch

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


# Configuration
TELEMETRY_ENDPOINT = os.getenv(
    "TELEMETRY_ENDPOINT", 
    "http://controller:8002/api/v1/telemetry/events:batch"
)
TELEMETRY_BUFFER_SIZE = int(os.getenv("TELEMETRY_BUFFER_SIZE", "50"))
TELEMETRY_FLUSH_INTERVAL_MS = int(os.getenv("TELEMETRY_FLUSH_INTERVAL_MS", "5000"))
TELEMETRY_TIMEOUT_SECONDS = int(os.getenv("TELEMETRY_TIMEOUT_SECONDS", "30"))


class ActionCategory(str, Enum):
    """Action categories per contracts.md §4.1."""
    SELF = "Self"
    ENVIRONMENTAL = "Environmental"
    SYSTEM = "System"


class EventSource(str, Enum):
    """Event sources per contracts.md §4.2."""
    AGENT = "agent"
    SYSTEM = "system"


@dataclass
class TelemetryEvent:
    """Telemetry event structure per contracts.md §4.2."""
    
    # Identity
    event_id: str = field(default_factory=lambda: f"evt_{_utc_now().timestamp()}")
    timestamp: datetime = field(default_factory=_utc_now)
    
    # Scoping
    run_id: str = ""
    agent_id: Optional[str] = None
    
    # Classification
    source: str = EventSource.AGENT.value
    action_category: str = ActionCategory.SYSTEM.value
    action_type: str = ""
    
    # Skill and intent
    skill_name: Optional[str] = None
    intent: Optional[str] = None
    parent_event_id: Optional[str] = None
    
    # Payload
    payload: Dict[str, Any] = field(default_factory=dict)
    
    # Outcome
    success: bool = True
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    
    # Costs
    llm_tokens_input: int = 0
    llm_tokens_output: int = 0
    llm_cost_usd: float = 0.0
    ia_cost_usd: float = 0.0
    ia_metadata: Dict[str, Any] = field(default_factory=dict)
    
    def is_critical(self) -> bool:
        """Check if this is a critical event requiring immediate flush."""
        # Critical events: errors and high-cost events
        if not self.success:
            return True
        if self.ia_cost_usd > 0:
            return True
        if self.llm_cost_usd > 0.01:  # Cost threshold
            return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data = asdict(self)
        # Convert datetime to ISO format
        if isinstance(data.get("timestamp"), datetime):
            data["timestamp"] = data["timestamp"].isoformat()
        return data


class TelemetryBuffer:
    """In-memory event buffer with automatic flushing.
    
    Implements contracts.md §5:
    - Flush on: buffer size >= 50, time >= 5s, critical event
    """
    
    def __init__(
        self,
        endpoint: str = TELEMETRY_ENDPOINT,
        max_size: int = TELEMETRY_BUFFER_SIZE,
        flush_interval_ms: int = TELEMETRY_FLUSH_INTERVAL_MS,
        timeout_seconds: int = TELEMETRY_TIMEOUT_SECONDS
    ):
        """Initialize telemetry buffer.
        
        Args:
            endpoint: Telemetry ingest endpoint URL
            max_size: Maximum buffer size before flush
            flush_interval_ms: Flush interval in milliseconds
            timeout_seconds: HTTP timeout for sending
        """
        self.endpoint = endpoint
        self.max_size = max_size
        self.flush_interval = timedelta(milliseconds=flush_interval_ms)
        self.timeout = timeout_seconds
        
        self._buffer: List[TelemetryEvent] = []
        self._lock = asyncio.Lock()
        self._last_flush = _utc_now()
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Statistics
        self.events_buffered = 0
        self.events_sent = 0
        self.events_dropped = 0
        self.flush_count = 0
    
    async def start(self):
        """Start the periodic flush task."""
        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())
        logger.info(f"Telemetry buffer started (endpoint: {self.endpoint})")
    
    async def stop(self):
        """Stop the buffer and flush remaining events."""
        self._running = False
        
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # Final flush
        await self.flush()
        logger.info("Telemetry buffer stopped")
    
    async def add_event(self, event: TelemetryEvent) -> bool:
        """Add event to buffer, flush if needed.
        
        Args:
            event: Telemetry event to add
            
        Returns:
            True if event was added successfully
        """
        async with self._lock:
            self._buffer.append(event)
            self.events_buffered += 1
            
            # Check if we need to flush
            should_flush = (
                len(self._buffer) >= self.max_size or  # Buffer full
                event.is_critical()  # Critical event
            )
        
        if should_flush:
            asyncio.create_task(self.flush())
            return True
        
        return True
    
    async def add_events(self, events: List[TelemetryEvent]) -> bool:
        """Add multiple events to buffer.
        
        Args:
            events: List of telemetry events
            
        Returns:
            True if all events were added
        """
        for event in events:
            await self.add_event(event)
        return True
    
    async def flush(self) -> bool:
        """Flush buffer to telemetry endpoint.
        
        Returns:
            True if flush was successful
        """
        async with self._lock:
            if not self._buffer:
                return True
            
            events_to_send = self._buffer.copy()
            self._buffer = []
            self._last_flush = _utc_now()
        
        if not events_to_send:
            return True
        
        try:
            success = await self._send_events(events_to_send)
            if success:
                self.events_sent += len(events_to_send)
                self.flush_count += 1
                logger.debug(f"Flushed {len(events_to_send)} events")
            else:
                self.events_dropped += len(events_to_send)
                logger.error(f"Failed to flush {len(events_to_send)} events")
            return success
        except Exception as e:
            self.events_dropped += len(events_to_send)
            logger.error(f"Error flushing events: {e}")
            return False
    
    async def _send_events(self, events: List[TelemetryEvent]) -> bool:
        """Send events to telemetry endpoint.
        
        Args:
            events: List of events to send
            
        Returns:
            True if successful
        """
        if not events:
            return True
        
        payload = {
            "events": [e.to_dict() for e in events]
        }

        # Preferred path at scale: enqueue for controller-side draining.
        if await enqueue_telemetry_batch(payload):
            return True
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get("status") == "success":
                        return True
                    else:
                        logger.warning(f"Partial success: {result}")
                        return result.get("accepted_count", 0) > 0
                else:
                    logger.error(f"HTTP {response.status_code}: {response.text}")
                    return False
                    
        except httpx.TimeoutException:
            logger.error("Timeout sending telemetry")
            return False
        except Exception as e:
            logger.error(f"Error sending telemetry: {e}")
            return False
    
    async def _periodic_flush(self):
        """Background task for periodic flushing."""
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval.total_seconds())
                
                # Check if we should flush based on time
                time_since_flush = _utc_now() - self._last_flush
                if time_since_flush >= self.flush_interval:
                    await self.flush()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in periodic flush: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get buffer statistics.
        
        Returns:
            Statistics dictionary
        """
        return {
            "buffered": len(self._buffer),
            "events_buffered": self.events_buffered,
            "events_sent": self.events_sent,
            "events_dropped": self.events_dropped,
            "flush_count": self.flush_count,
            "last_flush": self._last_flush.isoformat() if self._last_flush else None,
        }


# Global telemetry buffer instance
telemetry_buffer = TelemetryBuffer()


# Convenience functions for common use cases

async def record_action(
    run_id: str,
    agent_id: str,
    action_type: str,
    action_category: str = ActionCategory.SELF.value,
    success: bool = True,
    duration_ms: Optional[int] = None,
    payload: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    **kwargs
) -> bool:
    """Record an agent action event.
    Args:
        run_id: Run identifier
        agent_id: Agent identifier
        action_type: Type of action
        action_category: Action category
        success: Whether action succeeded
        duration_ms: Action duration
        payload: Additional data
        error_message: Error message if failed
        **kwargs: Additional event fields
        
    Returns:
        True if event was recorded
    """
    event = TelemetryEvent(
        run_id=run_id,
        agent_id=agent_id,
        action_type=action_type,
        action_category=action_category,
        source=EventSource.AGENT.value,
        success=success,
        duration_ms=duration_ms,
        payload=payload or {},
        error_message=error_message,
        **kwargs
    )
    
    return await telemetry_buffer.add_event(event)


async def record_llm_call(
    run_id: str,
    agent_id: str,
    success: bool,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    duration_ms: int,
    **kwargs
) -> bool:
    """Record an LLM call event.
    
    Args:
        run_id: Run identifier
        agent_id: Agent identifier
        success: Whether call succeeded
        tokens_input: Input tokens
        tokens_output: Output tokens
        cost_usd: Cost in USD
        duration_ms: Call duration
        **kwargs: Additional fields

    Returns:
        True if event was recorded
    """
    event = TelemetryEvent(
        run_id=run_id,
        agent_id=agent_id,
        action_type="llm_call",
        action_category=ActionCategory.SYSTEM.value,
        source=EventSource.AGENT.value,
        success=success,
        duration_ms=duration_ms,
        llm_tokens_input=tokens_input,
        llm_tokens_output=tokens_output,
        llm_cost_usd=cost_usd,
        **kwargs
    )

    return await telemetry_buffer.add_event(event)
