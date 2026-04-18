import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator
from fastapi import HTTPException
from utils.mlogging import LoggingMixin
import uuid


@dataclass
class Session:
    session_id: str
    q_llm_input: asyncio.Queue = field(default_factory=asyncio.Queue)
    q_tts_input: asyncio.Queue = field(default_factory=asyncio.Queue)
    q_sse_input: asyncio.Queue = field(default_factory=asyncio.Queue)
    tasks: set[asyncio.Task] = field(default_factory=set)
    consumers: int = 0

class SessionManager(LoggingMixin):

    sessions: dict[str, Session] = {}

    def __init__(self):
        super().__init__()

    def new_session(self) -> Session:
        session_id = uuid.uuid4().hex
        session = Session(session_id=session_id)
        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session_id not found or already released")
        return session

    def _del_session_if_possible(self, session_id: str):
        session = self.sessions.get(session_id)
        if session is None:
            return
        if session.consumers <= 0 and all(t.done() for t in session.tasks):
            self.sessions.pop(session_id, None)
            self.logger.info(f"Session {session_id} released")
    
    def del_session_if_possible(self, session_id: str):
        session = self.get_session(session_id)
        session.consumers -= 1
        self._del_session_if_possible(session_id)

    def add_session_tasks(self, session_id: str, tasks: set[asyncio.Task]):
        session = self.get_session(session_id)
        session.tasks.update(tasks)
        for t in tasks:
            t.add_done_callback(
                lambda _t, tid=session_id: 
                    self._del_session_if_possible(tid)
            )
        session.consumers += len(tasks)
    
    def stream_session(self, stream: AsyncGenerator[Any, None], session: Session):
        async def get_stream():
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                self.del_session_if_possible(session.session_id)
        return get_stream()
    