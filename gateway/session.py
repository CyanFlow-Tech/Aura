"""Session layer: a single running instance of a Pipeline + process-wide
registry.

Session does not need to know which channels exist or their names; it just
takes a `PipelineBundle` and runs. Adding new Stages or Pipelines requires
no changes to this file.

Two lifecycle signals:
- `producers_done: asyncio.Event` -- set when the TaskGroup exits
- `active_consumers: int`         -- number of HTTP stream endpoints
                                     currently attached
Release condition: `producers_done.is_set() and active_consumers == 0`.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from channels import BroadcastChannel, ReceiveChannel
from pipeline import PipelineBundle
from stages import Stage
from utils.mlogging import Logger, LoggingMixin

_logger = Logger.build("Session")


@dataclass
class Session:
    session_id: str
    stages: list[Stage]
    channels: list[Any]
    endpoints: dict[str, Any]
    producers_done: asyncio.Event = field(default_factory=asyncio.Event)
    active_consumers: int = 0
    _runner: asyncio.Task | None = None

    def start(self) -> None:
        self._runner = asyncio.create_task(
            self._run(), name=f"session:{self.session_id}"
        )

    async def _run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tg:
                for stage in self.stages:
                    tg.create_task(stage.run(), name=type(stage).__name__)
        except* Exception as eg:
            for exc in eg.exceptions:
                _logger.error(
                    f"Session {self.session_id} stage failed: {exc!r}"
                )
        finally:
            # Safety net: even if a Stage forgets to close its output
            # channel, downstream stream endpoints will not hang forever
            # on `async for`.
            for ch in self.channels:
                close = getattr(ch, "close", None)
                if close is not None:
                    try:
                        await close()
                    except Exception:
                        pass
            self.producers_done.set()

    def subscribe(self, name: str) -> ReceiveChannel:
        """Return a receive-side for the endpoint channel registered under
        `name`. For BroadcastChannel this opens a fresh subscription."""
        ch = self.endpoints[name]
        if isinstance(ch, BroadcastChannel):
            return ch.subscribe()
        return ch

    def can_release(self) -> bool:
        return self.producers_done.is_set() and self.active_consumers == 0


class SessionManager(LoggingMixin):
    """Process-wide Session registry."""

    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, Session] = {}

    def new_session(self, bundle: PipelineBundle) -> Session:
        session = Session(
            session_id=uuid.uuid4().hex,
            stages=bundle.stages,
            channels=bundle.channels,
            endpoints=bundle.endpoints,
        )
        self._sessions[session.session_id] = session
        session.start()
        assert session._runner is not None
        session._runner.add_done_callback(
            lambda _t, sid=session.session_id: self._maybe_release(sid)
        )
        self.logger.info(f"Session {session.session_id} started")
        return session

    def get_session(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=404, detail="session_id not found or already released"
            )
        return session

    def _maybe_release(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None or not session.can_release():
            return
        self._sessions.pop(session_id, None)
        self.logger.info(f"Session {session_id} released")

    async def stream(self, session: Session, channel_name: str):
        """Wrap a consumer iteration: auto-manage active_consumers and
        attempt release when the generator finishes."""
        session.active_consumers += 1
        try:
            async for item in session.subscribe(channel_name):
                yield item
        finally:
            session.active_consumers -= 1
            self._maybe_release(session.session_id)
