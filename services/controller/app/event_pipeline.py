"""Event pipeline for run telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session


class RunEvent(BaseModel):
    """Telemetry event emitted by a run."""

    event_id: str = Field(default_factory=lambda: str(uuid4()), description="Unique event ID.")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event timestamp.")
    run_id: str = Field(..., description="Parent run identifier.")
    environment_id: str = Field(..., description="Environment identifier.")
    agent_id: Optional[str] = Field(default=None, description="Agent identifier when applicable.")
    event_type: str = Field(..., description="Event category.")
    action_name: str = Field(..., description="Action performed.")
    outcome: str = Field(..., description="Action outcome.")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Event data.")
    trace_id: Optional[str] = Field(default=None, description="Distributed trace ID.")


class EventStore:
    """Storage backend for run events."""

    def __init__(self, db_session: Optional[Session] = None):
        self._db = db_session
        self._events: Dict[str, List[RunEvent]] = {}
        self._use_db = db_session is not None

    def store(self, event: RunEvent, db: Optional[Session] = None) -> None:
        session = db or self._db
        if session and self._use_db:
            from app.database import Event as EventDB

            session.add(
                EventDB(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    environment_id=event.environment_id,
                    agent_id=event.agent_id,
                    event_type=event.event_type,
                    action_name=event.action_name,
                    outcome=event.outcome,
                    payload=event.payload,
                    trace_id=event.trace_id,
                    timestamp=event.timestamp,
                )
            )
            session.commit()
            return

        self._events.setdefault(event.run_id, []).append(event)

    def store_many(self, events: List[RunEvent], db: Optional[Session] = None) -> None:
        session = db or self._db
        if session and self._use_db:
            from app.database import Event as EventDB

            session.add_all(
                [
                    EventDB(
                        event_id=event.event_id,
                        run_id=event.run_id,
                        environment_id=event.environment_id,
                        agent_id=event.agent_id,
                        event_type=event.event_type,
                        action_name=event.action_name,
                        outcome=event.outcome,
                        payload=event.payload,
                        trace_id=event.trace_id,
                        timestamp=event.timestamp,
                    )
                    for event in events
                ]
            )
            session.commit()
            return

        for event in events:
            self.store(event, db)

    def get_events(
        self,
        run_id: str,
        event_type: Optional[str] = None,
        agent_id: Optional[str] = None,
        limit: int = 1000,
        db: Optional[Session] = None,
    ) -> List[RunEvent]:
        session = db or self._db
        if session and self._use_db:
            from app.database import Event as EventDB

            query = session.query(EventDB).filter(EventDB.run_id == run_id)
            if event_type:
                query = query.filter(EventDB.event_type == event_type)
            if agent_id:
                query = query.filter(EventDB.agent_id == agent_id)
            events = query.order_by(EventDB.timestamp.desc()).limit(limit).all()
            return [
                RunEvent(
                    event_id=event.event_id,
                    timestamp=event.timestamp,
                    run_id=event.run_id,
                    environment_id=event.environment_id,
                    agent_id=event.agent_id,
                    event_type=event.event_type,
                    action_name=event.action_name,
                    outcome=event.outcome,
                    payload=event.payload or {},
                    trace_id=event.trace_id,
                )
                for event in events
            ]

        events = self._events.get(run_id, [])
        if event_type:
            events = [event for event in events if event.event_type == event_type]
        if agent_id:
            events = [event for event in events if event.agent_id == agent_id]
        return sorted(events, key=lambda event: event.timestamp)[-limit:]

    def get_event_count(self, run_id: str, db: Optional[Session] = None) -> int:
        session = db or self._db
        if session and self._use_db:
            from app.database import Event as EventDB

            return session.query(EventDB).filter(EventDB.run_id == run_id).count()
        return len(self._events.get(run_id, []))

    def export_run_events(self, run_id: str, db: Optional[Session] = None) -> List[Dict[str, Any]]:
        return [event.model_dump() for event in self.get_events(run_id, limit=10000, db=db)]

    def clear_run(self, run_id: str, db: Optional[Session] = None) -> None:
        session = db or self._db
        if session and self._use_db:
            from app.database import Event as EventDB

            session.query(EventDB).filter(EventDB.run_id == run_id).delete()
            session.commit()
            return
        self._events.pop(run_id, None)
