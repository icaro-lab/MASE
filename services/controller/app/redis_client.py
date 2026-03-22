"""Redis client for telemetry pub/sub.

Implements Redis channels per contracts.md §5.2:
- run:{run_id}:events
- agent:{agent_id}:events

With 24-hour TTL on messages.
"""

import os
import json
import logging
from typing import Optional, AsyncGenerator, Callable, Any
from contextlib import asynccontextmanager

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    aioredis = None

logger = logging.getLogger(__name__)


# Redis configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_TTL_SECONDS = int(os.getenv("REDIS_TTL_SECONDS", "86400"))  # 24 hours
HEARTBEAT_REPORTS_QUEUE = os.getenv("HEARTBEAT_REPORTS_QUEUE", "ingest:heartbeat_reports:v1")
TELEMETRY_BATCHES_QUEUE = os.getenv("TELEMETRY_BATCHES_QUEUE", "ingest:telemetry_batches:v1")

# Channel patterns (contracts.md §5.2)
RUN_CHANNEL_PATTERN = "run:{run_id}:events"
AGENT_CHANNEL_PATTERN = "agent:{agent_id}:events"


def get_run_channel(run_id: str) -> str:
    """Get Redis channel name for a run.
    
    Args:
        run_id: Run identifier
        
    Returns:
        Channel name (run:{run_id}:events)
    """
    return RUN_CHANNEL_PATTERN.format(run_id=run_id)


def get_agent_channel(agent_id: str) -> str:
    """Get Redis channel name for an agent.
    
    Args:
        agent_id: Agent identifier
        
    Returns:
        Channel name (agent:{agent_id}:events)
    """
    return AGENT_CHANNEL_PATTERN.format(agent_id=agent_id)


class RedisClient:
    """Redis client wrapper for telemetry pub/sub.
    
    Provides publish/subscribe functionality with automatic reconnection.
    """
    
    def __init__(self, redis_url: str = REDIS_URL):
        """Initialize Redis client.
        
        Args:
            redis_url: Redis connection URL
        """
        self._redis_url = redis_url
        self._client: Optional[Any] = None
        self._pubsub: Optional[Any] = None
        
        if not REDIS_AVAILABLE:
            logger.warning("redis-py not installed. Redis features disabled.")
    
    async def _get_client(self) -> Optional[Any]:
        """Get or create Redis client."""
        if not REDIS_AVAILABLE:
            return None
            
        if self._client is None:
            try:
                self._client = aioredis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True
                )
                logger.info(f"Connected to Redis at {self._redis_url}")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                return None
        
        return self._client
    
    async def publish(self, channel: str, message: str) -> bool:
        """Publish message to Redis channel.
        
        Args:
            channel: Channel name
            message: Message to publish
            
        Returns:
            True if published successfully
        """
        client = await self._get_client()
        if not client:
            return False
        
        try:
            # Publish with TTL
            await client.publish(channel, message)
            
            # Also store in list with TTL for historical access
            list_key = f"{channel}:history"
            await client.lpush(list_key, message)
            await client.expire(list_key, REDIS_TTL_SECONDS)
            
            # Trim to prevent unbounded growth
            await client.ltrim(list_key, 0, 9999)
            
            return True
        except Exception as e:
            logger.error(f"Failed to publish to {channel}: {e}")
            return False
    
    async def subscribe(self, *channels: str) -> Optional[Any]:
        """Subscribe to Redis channels.
        
        Args:
            *channels: Channel names to subscribe to
            
        Returns:
            Pub/Sub object or None if failed
        """
        client = await self._get_client()
        if not client:
            return None
        
        try:
            self._pubsub = client.pubsub()
            await self._pubsub.subscribe(*channels)
            logger.info(f"Subscribed to channels: {channels}")
            return self._pubsub
        except Exception as e:
            logger.error(f"Failed to subscribe to channels: {e}")
            return None
    
    async def unsubscribe(self, *channels: str) -> bool:
        """Unsubscribe from Redis channels.
        
        Args:
            *channels: Channel names to unsubscribe from
            
        Returns:
            True if unsubscribed successfully
        """
        if not self._pubsub:
            return True
        
        try:
            await self._pubsub.unsubscribe(*channels)
            return True
        except Exception as e:
            logger.error(f"Failed to unsubscribe: {e}")
            return False
    
    async def get_history(self, channel: str, count: int = 100) -> list:
        """Get recent message history for a channel.
        
        Args:
            channel: Channel name
            count: Number of messages to retrieve
            
        Returns:
            List of messages
        """
        client = await self._get_client()
        if not client:
            return []
        
        try:
            list_key = f"{channel}:history"
            messages = await client.lrange(list_key, 0, count - 1)
            return messages
        except Exception as e:
            logger.error(f"Failed to get history for {channel}: {e}")
            return []

    async def enqueue_json(self, queue_name: str, payload: dict) -> bool:
        """Enqueue a JSON payload on a Redis list."""
        client = await self._get_client()
        if not client:
            return False
        try:
            await client.rpush(queue_name, json.dumps(payload, ensure_ascii=True))
            return True
        except Exception as e:
            logger.error(f"Failed to enqueue on {queue_name}: {e}")
            return False

    async def blocking_pop_json(self, queue_name: str, timeout: int = 5) -> Optional[dict]:
        """Pop one JSON payload from a Redis list, blocking briefly."""
        client = await self._get_client()
        if not client:
            return None
        try:
            item = await client.blpop(queue_name, timeout=timeout)
            if not item or len(item) < 2:
                return None
            return json.loads(item[1])
        except Exception as e:
            logger.error(f"Failed to blocking-pop from {queue_name}: {e}")
            return None

    async def pop_many_json(self, queue_name: str, count: int) -> list[dict]:
        """Pop up to count JSON payloads without blocking."""
        client = await self._get_client()
        if not client or count <= 0:
            return []
        rows: list[dict] = []
        try:
            while len(rows) < count:
                raw = await client.lpop(queue_name)
                if raw is None:
                    break
                rows.append(json.loads(raw))
        except Exception as e:
            logger.error(f"Failed to pop many from {queue_name}: {e}")
        return rows
    
    async def close(self):
        """Close Redis connection."""
        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None
        
        if self._client:
            await self._client.close()
            self._client = None
    
    @asynccontextmanager
    async def subscription(self, *channels: str):
        """Context manager for subscriptions.
        
        Args:
            *channels: Channels to subscribe to
            
        Yields:
            Pub/Sub object
        """
        pubsub = await self.subscribe(*channels)
        try:
            yield pubsub
        finally:
            await self.unsubscribe(*channels)


# Global Redis client instance
redis_client = RedisClient()


async def publish_event(run_id: str, agent_id: Optional[str], event_data: dict) -> bool:
    """Convenience function to publish an event to Redis.
    
    Args:
        run_id: Run identifier
        agent_id: Optional agent identifier
        event_data: Event data dictionary
        
    Returns:
        True if published successfully
    """
    try:
        message = json.dumps(event_data)
        
        # Publish to run channel
        run_channel = get_run_channel(run_id)
        await redis_client.publish(run_channel, message)
        
        # Publish to agent channel if agent_id present
        if agent_id:
            agent_channel = get_agent_channel(agent_id)
            await redis_client.publish(agent_channel, message)
        
        return True
    except Exception as e:
        logger.error(f"Failed to publish event: {e}")
        return False


async def get_channel_history(run_id: Optional[str] = None, agent_id: Optional[str] = None, count: int = 100) -> list:
    """Get message history for a channel.
    
    Args:
        run_id: Optional run identifier
        agent_id: Optional agent identifier
        count: Number of messages to retrieve
        
    Returns:
        List of messages
    """
    if agent_id:
        channel = get_agent_channel(agent_id)
    elif run_id:
        channel = get_run_channel(run_id)
    else:
        return []
    
    return await redis_client.get_history(channel, count)
