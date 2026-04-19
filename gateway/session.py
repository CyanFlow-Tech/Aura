"""Session layer: multi-turn dialog container + per-turn pipeline runner.

A `Session` corresponds 1:1 to a device-side conversation (one
`session_id`). It owns the persistent dialog memory (`Conversation`) and
exactly one in-flight `Turn` at a time. Each device upload starts a new
Turn against the same Session, so the LLM sees the full prior context.

Lifecycle:
- `/upload` without `session_id` → mint a new Session, start its first Turn.
- `/upload` with a known `session_id` → reuse that Session; if a previous
  Turn is somehow still running, it is cancelled before the new one starts.
- `/interrupt/{session_id}` → cancel the current Turn only; Session and its
  conversation memory live on, ready for the next upload.
- `/session_complete?session_id=...` → cancel any in-flight Turn AND drop
  the Session from the registry.

A `Turn` mirrors what the previous single-turn `Session` used to do: run a
TaskGroup over the pipeline stages, close all channels in `finally` so HTTP
stream consumers iterating with `async for` always terminate cleanly.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import HTTPException

from channels import BroadcastChannel, ReceiveChannel
from conversation import Conversation
from pipeline import PipelineBundle
from stages import Stage
from utils.mlogging import Logger, LoggingMixin

_logger = Logger.build("Session")


@dataclass
class Turn:
    """One pipeline run for a single device upload."""

    stages: list[Stage]
    channels: list[Any]
    endpoints: dict[str, Any]
    producers_done: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False
    _runner: asyncio.Task | None = None

    def start(self, name: str) -> None:
        self._runner = asyncio.create_task(self._run(), name=name)

    async def _run(self) -> None:
        try:
            async with asyncio.TaskGroup() as tg:
                for stage in self.stages:
                    tg.create_task(stage.run(), name=type(stage).__name__)
        except* asyncio.CancelledError:
            _logger.info("Turn cancelled")
        except* Exception as eg:
            for exc in eg.exceptions:
                _logger.error(f"Turn stage failed: {exc!r}")
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

    async def cancel(self) -> None:
        """Idempotent: stop the runner; channels close in `_run`'s
        finally; HTTP consumers iterating on them unwind naturally."""
        if self.cancelled:
            return
        self.cancelled = True
        runner = self._runner
        if runner is None or runner.done():
            return
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, Exception):
            pass

    def subscribe(self, name: str) -> ReceiveChannel:
        """Return a receive-side for the endpoint channel `name`. For
        BroadcastChannel this opens a fresh subscription with full
        history replay."""
        ch = self.endpoints[name]
        if isinstance(ch, BroadcastChannel):
            return ch.subscribe()
        return ch


class Session:
    """Multi-turn container, identified by a stable `session_id`."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.conversation = Conversation(session_id=session_id)
        self._current_turn: Turn | None = None
        self._lock = asyncio.Lock()

    @property
    def current_turn(self) -> Turn | None:
        return self._current_turn

    async def start_turn(self, bundle: PipelineBundle) -> Turn:
        """Replace the current Turn with a fresh one. If a previous Turn
        is still running (defensive: shouldn't happen under the device
        FSM), cancel it first so its channels close cleanly before we
        swap in the new one."""
        async with self._lock:
            previous = self._current_turn
            if previous is not None:
                await previous.cancel()
            turn = Turn(
                stages=bundle.stages,
                channels=bundle.channels,
                endpoints=bundle.endpoints,
            )
            turn.start(name=f"turn:{self.session_id}")
            self._current_turn = turn
            return turn

    async def interrupt_current_turn(self) -> bool:
        """Cancel the in-flight Turn (if any). Returns True regardless,
        as long as the Session itself still exists. Conversation memory
        is preserved.
        """
        turn = self._current_turn
        if turn is None:
            return True
        await turn.cancel()
        return True

    async def release(self) -> None:
        """Tear down everything tied to this Session. Called by
        `SessionManager.complete_session` only."""
        await self.interrupt_current_turn()
        self._current_turn = None


class SessionManager(LoggingMixin):
    """Process-wide registry of multi-turn `Session`s, keyed by `session_id`."""

    def __init__(self) -> None:
        super().__init__()
        self._sessions: dict[str, Session] = {}

    def get_session(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail="session_id not found or already released",
            )
        return session

    async def start_turn(
        self,
        session_id: str | None,
        build: Callable[[Conversation], PipelineBundle],
    ) -> Session:
        """Get-or-create the Session, then run a fresh Turn against it.

        - `session_id is None`  → first turn of a new dialog; mint a new id.
        - `session_id` matches  → reuse; new Turn replaces the old one.
        - `session_id` unknown  → log a warning and mint a fresh id; the
          response carries the new id so the device adopts it. We refuse
          to silently create a Session at the client-supplied id, because
          that would mask client/server-restart bugs and risk id
          collisions.
        """
        session: Session
        if session_id is None:
            session = self._mint_session()
        else:
            existing = self._sessions.get(session_id)
            if existing is None:
                self.logger.warning(
                    f"Unknown session_id {session_id} on /upload; allocating fresh"
                )
                session = self._mint_session()
            else:
                session = existing

        bundle = build(session.conversation)
        await session.start_turn(bundle)
        self.logger.info(
            f"Session {session.session_id} turn started "
            f"(history len={len(session.conversation.history)})"
        )
        return session

    def _mint_session(self) -> Session:
        new_id = uuid.uuid4().hex
        session = Session(session_id=new_id)
        self._sessions[new_id] = session
        self.logger.info(f"Session {new_id} created")
        return session

    async def interrupt_session(self, session_id: str) -> bool:
        """Device state-4: stop streaming the current Turn but keep the
        Session alive so the next /upload can continue the conversation."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        await session.interrupt_current_turn()
        self.logger.info(f"Session {session_id} current turn interrupted")
        return True

    async def complete_session(self, session_id: str | None) -> bool:
        """Device state-6: end the multi-turn dialog. Cancel any in-flight
        Turn AND drop the Session from the registry, releasing its
        conversation memory."""
        if session_id is None:
            return False
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.release()
        self.logger.info(f"Session {session_id} completed and released")
        return True

    async def stream(self, session: Session, channel_name: str):
        """Subscribe to the current Turn's endpoint channel.

        The device only fires the GET text_stream / audio_stream calls
        AFTER /upload returns, so by then `current_turn` is the freshly
        started one. BroadcastChannel's history replay covers the small
        race where producer-side data has already started flowing before
        the subscriber attaches.
        """
        turn = session.current_turn
        if turn is None:
            raise HTTPException(
                status_code=409,
                detail="session has no active turn; upload first",
            )
        async for item in turn.subscribe(channel_name):
            yield item
