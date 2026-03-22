"""Background Redis ingest workers for telemetry batches."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict, List

from app.database import SessionLocal
from app.redis_client import REDIS_AVAILABLE, TELEMETRY_BATCHES_QUEUE, redis_client
from app.routes.telemetry import BatchEventsRequest, persist_batch_events


logger = logging.getLogger(__name__)

TELEMETRY_QUEUE_BATCH_SIZE = max(
    int(os.getenv("TELEMETRY_QUEUE_BATCH_SIZE", "20")),
    1,
)
TELEMETRY_EVENT_BATCH_LIMIT = max(
    int(os.getenv("TELEMETRY_EVENT_BATCH_LIMIT", "1000")),
    1,
)
INGEST_QUEUE_POP_TIMEOUT = max(
    int(os.getenv("INGEST_QUEUE_POP_TIMEOUT", "5")),
    1,
)

INGEST_TASKS: Dict[str, asyncio.Task] = {}


def _chunk_list(items: List[dict], size: int) -> List[List[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _telemetry_batch_worker() -> None:
    """Drain telemetry batch queue into Postgres in merged chunks."""
    while True:
        first = await redis_client.blocking_pop_json(
            TELEMETRY_BATCHES_QUEUE,
            timeout=INGEST_QUEUE_POP_TIMEOUT,
        )
        if first is None:
            await asyncio.sleep(0)
            continue

        batch_payloads = [first]
        batch_payloads.extend(
            await redis_client.pop_many_json(
                TELEMETRY_BATCHES_QUEUE,
                TELEMETRY_QUEUE_BATCH_SIZE - 1,
            )
        )

        events: List[dict] = []
        for payload in batch_payloads:
            batch_events = payload.get("events") if isinstance(payload, dict) else None
            if isinstance(batch_events, list):
                events.extend(batch_events)

        if not events:
            continue

        for chunk in _chunk_list(events, TELEMETRY_EVENT_BATCH_LIMIT):
            db = SessionLocal()
            try:
                request = BatchEventsRequest.model_validate({"events": chunk})
                await persist_batch_events(request, db=db)
            except Exception as exc:
                logger.error("telemetry_batch_worker failed chunk persist: %s", exc)
            finally:
                db.close()
            await asyncio.sleep(0)


def start_ingest_workers() -> Dict[str, bool]:
    """Start background ingest workers when Redis is available."""
    summary = {"telemetry_worker": False}
    if not REDIS_AVAILABLE:
        logger.warning("Redis not available; ingest workers disabled")
        return summary

    if "telemetry_worker" not in INGEST_TASKS or INGEST_TASKS["telemetry_worker"].done():
        INGEST_TASKS["telemetry_worker"] = asyncio.create_task(_telemetry_batch_worker())
        summary["telemetry_worker"] = True

    return summary


async def stop_ingest_workers() -> int:
    """Stop background ingest workers and close Redis connection."""
    tasks = list(INGEST_TASKS.values())
    INGEST_TASKS.clear()
    for task in tasks:
        task.cancel()
    cancelled = 0
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            cancelled += 1
        except Exception as exc:  # pragma: no cover
            logger.warning("ingest worker shutdown raised: %s", exc)
    await redis_client.close()
    return cancelled
