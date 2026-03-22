"""Redis-backed ingest queue helpers for large-run reporting.

These helpers let per-run agent-launchers enqueue heartbeat summaries and
telemetry batches without synchronously writing into the shared controller.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

try:
    import redis.asyncio as aioredis
    REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency/packaging fallback
    aioredis = None
    REDIS_AVAILABLE = False


logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
HEARTBEAT_REPORTS_QUEUE = os.getenv(
    "HEARTBEAT_REPORTS_QUEUE",
    "ingest:heartbeat_reports:v1",
)
TELEMETRY_BATCHES_QUEUE = os.getenv(
    "TELEMETRY_BATCHES_QUEUE",
    "ingest:telemetry_batches:v1",
)


class IngestQueueClient:
    """Very small async Redis client for enqueue-only workflows."""

    def __init__(self, redis_url: str = REDIS_URL) -> None:
        self._redis_url = redis_url
        self._client: Optional[Any] = None
        if not REDIS_AVAILABLE:
            logger.warning("redis-py not installed. Redis ingest queue disabled.")

    async def _get_client(self) -> Optional[Any]:
        if not REDIS_AVAILABLE:
            return None
        if self._client is None:
            try:
                self._client = aioredis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
                logger.info("Connected agent-launcher ingest queue to Redis at %s", self._redis_url)
            except Exception as exc:  # pragma: no cover - runtime fallback
                logger.error("Failed to connect agent-launcher ingest queue to Redis: %s", exc)
                return None
        return self._client

    async def enqueue_json(self, queue_name: str, payload: Dict[str, Any]) -> bool:
        client = await self._get_client()
        if client is None:
            return False
        try:
            await client.rpush(queue_name, json.dumps(payload, ensure_ascii=True))
            return True
        except Exception as exc:  # pragma: no cover - runtime fallback
            logger.error("Failed to enqueue payload on %s: %s", queue_name, exc)
            return False

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


ingest_queue_client = IngestQueueClient()


async def enqueue_heartbeat_report(payload: Dict[str, Any]) -> bool:
    """Enqueue a heartbeat-report payload for controller-side draining."""
    return await ingest_queue_client.enqueue_json(HEARTBEAT_REPORTS_QUEUE, payload)


async def enqueue_telemetry_batch(payload: Dict[str, Any]) -> bool:
    """Enqueue a telemetry batch payload for controller-side draining."""
    return await ingest_queue_client.enqueue_json(TELEMETRY_BATCHES_QUEUE, payload)
