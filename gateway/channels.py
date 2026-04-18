"""Transport layer: channel abstractions.

Stages only depend on the `SendChannel` / `ReceiveChannel` protocols defined
here; they never touch `asyncio.Queue` directly. Two implementations are
provided:

- `QueueChannel`: buffered 1->1 (or N->1) stream.
- `BroadcastChannel`: 1->N fan-out with **late-subscriber replay**. Late
  subscribers receive the full history emitted so far. This is essential
  when HTTP stream endpoints attach after the producer has already started.

Once a channel is `close()`d, consumers iterating with `async for` will
terminate naturally and `receive()` raises `ChannelClosed`. This avoids
scattering `[DONE]` sentinels throughout business code.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
T_contra = TypeVar("T_contra", contravariant=True)


class ChannelClosed(Exception):
    """Raised when send/receive is attempted on a closed channel."""


@runtime_checkable
class SendChannel(Protocol[T_contra]):
    async def send(self, item: T_contra) -> None: ...
    async def close(self) -> None: ...


@runtime_checkable
class ReceiveChannel(Protocol[T_co]):
    async def receive(self) -> T_co: ...
    def __aiter__(self) -> AsyncIterator[T_co]: ...


class QueueChannel(Generic[T]):
    """Buffered single-stream channel. Supports N producers -> 1 consumer.

    `maxsize=0` means unbounded. `close()` is idempotent.
    """

    _SENTINEL = object()

    def __init__(self, maxsize: int = 0) -> None:
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    async def send(self, item: T) -> None:
        if self._closed:
            raise ChannelClosed("send on closed channel")
        await self._q.put(item)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._q.put(self._SENTINEL)

    async def receive(self) -> T:
        item = await self._q.get()
        if item is self._SENTINEL:
            # Put the sentinel back so other potential consumers also wake up.
            await self._q.put(self._SENTINEL)
            raise ChannelClosed("channel closed")
        return item

    def __aiter__(self) -> "QueueChannel[T]":
        return self

    async def __anext__(self) -> T:
        try:
            return await self.receive()
        except ChannelClosed:
            raise StopAsyncIteration


class BroadcastChannel(Generic[T]):
    """1-producer -> N-consumer fan-out channel.

    - `subscribe()` returns an independent `QueueChannel`; every subscriber
      receives every message sent by the producer.
    - To accommodate the case where HTTP endpoints attach after the producer
      starts, this implementation **buffers the full history**. New
      subscribers get a replay first, then live messages. Suitable for
      streams with bounded total volume (e.g. a single conversation turn).
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._history: list[T] = []
        self._subs: list[QueueChannel[T]] = []
        self._closed = False
        self._maxsize = maxsize

    def subscribe(self) -> QueueChannel[T]:
        sub: QueueChannel[T] = QueueChannel(self._maxsize)
        for item in self._history:
            sub._q.put_nowait(item)
        if self._closed:
            sub._closed = True
            sub._q.put_nowait(QueueChannel._SENTINEL)
        else:
            self._subs.append(sub)
        return sub

    async def send(self, item: T) -> None:
        if self._closed:
            raise ChannelClosed("send on closed channel")
        self._history.append(item)
        # Snapshot the subscriber list: if a new subscriber joins while we
        # await inside the loop, we don't want it to receive `item` twice
        # (once via `_history` replay, once via this live send).
        for sub in list(self._subs):
            await sub.send(item)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for sub in list(self._subs):
            await sub.close()
