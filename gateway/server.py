"""
API layer: HTTP / SSE / audio encoding plus Pipeline dispatch.

The cloud and the device speak in terms of `session_id` only — there is no
separate "task id". One `session_id` covers the entire device-side dialog
(state0→state6); each `/upload` runs a fresh Turn against the same Session
so the LLM sees the full prior context. See `session.py` for the lifecycle
contract.
"""

from __future__ import annotations

import asyncio
import io
import json
import wave
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, Security, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from auth import get_api_key
from config import config
from conversation import Conversation
from core import Aura
from heartbeat import load_heartbeat_assets, strip_id3v2
from pipeline import CHANNEL_TTS_OUT, build_voice_chat_pipeline
from session import SessionManager
from utils.mlogging import Logger

logger = Logger.build("AuraServer", config.app.logging_level)

aura = Aura(config)
session_manager = SessionManager()
app = FastAPI(title=config.app.title)

# Pre-encoded MP3 assets for the audio_stream keepalive. Resolved once
# at startup; `assets.enabled is False` means the asset / ffmpeg are
# unavailable and we silently fall back to no-keepalive behaviour.
_HEARTBEAT = load_heartbeat_assets(
    Path(__file__).resolve().parent / config.streaming.heartbeat_path,
    target_sample_rate=config.streaming.heartbeat_target_sample_rate,
    target_channels=config.streaming.heartbeat_target_channels,
    target_bitrate=config.streaming.heartbeat_target_bitrate,
)


def _pcm_to_wav(pcm_data: bytes) -> io.BytesIO:
    wav_buffer = io.BytesIO()
    audio_cfg = config.audio
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(audio_cfg.channels)
        wav_file.setsampwidth(audio_cfg.sample_width)
        wav_file.setframerate(audio_cfg.frame_rate)
        wav_file.writeframes(pcm_data)
    wav_buffer.seek(0)
    return wav_buffer


@app.post(config.api.upload, dependencies=[Security(get_api_key)])
async def upload(
    audio_file: UploadFile = File(...),
    session_id: str | None = Form(None),
):
    """Start one Turn of a dialog.

    - First turn of a dialog: device omits `session_id`; the cloud mints
      a new Session and returns its id.
    - Subsequent turns: device sends the same `session_id` it received
      previously; the cloud reuses that Session (preserving conversation
      memory) and runs a new Turn against it.

    Always returns the `session_id` to use going forward.
    """
    pcm_data = await audio_file.read()
    wav_buffer = _pcm_to_wav(pcm_data)

    def build(conversation: Conversation):
        return build_voice_chat_pipeline(aura, wav_buffer, conversation)

    session = await session_manager.start_turn(session_id, build)
    return {"status": "success", "session_id": session.session_id}


@app.get(config.api.text_stream, dependencies=[Security(get_api_key)])
async def text_stream(session_id: str):
    """SSE token stream for the current Turn of `session_id`."""
    session = session_manager.get_session(session_id)

    async def gen():
        async for sentence, _audio in session_manager.stream(session, CHANNEL_TTS_OUT):
            payload = json.dumps({"token": sentence}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get(config.api.audio_stream, dependencies=[Security(get_api_key)])
async def audio_stream(session_id: str):
    """Streaming MP3 audio for the current Turn of `session_id`.

    While the LLM is still cooking the first sentence, the producer side
    of the channel is silent. To keep the device-side `MediaPlayer` both
    (a) connected (its socket-read deadline is ~30 s) and (b) actually
    *playing* (it pre-buffers indefinitely if byte arrival rate < audio
    playback rate), we synthesize a continuous MP3 byte stream made of:

    - the audible heartbeat clip (`assets.content_mp3`), emitted once
      every `heartbeat_interval_s` seconds, so the user hears the chosen
      cue (silence / "嗯" / …) at a sparse cadence;
    - short silence frames (`assets.silence_mp3`) emitted on every
      pre-first-chunk tick to keep the byte stream slightly ahead of the
      device's playback rate.

    As soon as the first real TTS chunk lands the synthesized stream
    stops and we forward producer output verbatim (with each chunk's
    leading ID3v2 tag stripped) until the Turn ends.
    """
    session = session_manager.get_session(session_id)

    async def gen():
        producer = session_manager.stream(session, CHANNEL_TTS_OUT).__aiter__()
        assets = _HEARTBEAT
        interval = config.streaming.heartbeat_interval_s
        keepalive_enabled = assets.enabled and interval > 0

        # Tick faster than the silence clip's own playback duration so
        # cumulative bytes/sec exceed the player's drain rate; the
        # device starts playing within a few hundred ms instead of
        # buffering indefinitely.
        padding_tick = (
            assets.silence_duration_s * 0.9 if keepalive_enabled else 0.0
        )

        async def _next():
            return await producer.__anext__()

        first_real_received = False
        pending: asyncio.Task | None = None
        loop = asyncio.get_running_loop()
        last_content_emit_at: float | None = None

        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(_next())

                if first_real_received or not keepalive_enabled:
                    try:
                        _sentence, audio = await pending
                    except StopAsyncIteration:
                        return
                    pending = None
                    yield strip_id3v2(audio)
                    continue

                # Pre-first-chunk path: race the producer against a
                # short padding tick. `shield` is critical — without it
                # `wait_for`'s timeout would cancel `pending` and the
                # underlying async generator would be left half-consumed.
                try:
                    _sentence, audio = await asyncio.wait_for(
                        asyncio.shield(pending), timeout=padding_tick
                    )
                except asyncio.TimeoutError:
                    now = loop.time()
                    if (
                        last_content_emit_at is None
                        or (now - last_content_emit_at) >= interval
                    ):
                        last_content_emit_at = now
                        yield assets.content_mp3
                    else:
                        yield assets.silence_mp3
                    continue
                except StopAsyncIteration:
                    return
                pending = None
                first_real_received = True
                yield strip_id3v2(audio)
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                try:
                    await pending
                except BaseException:
                    pass

    return StreamingResponse(gen(), media_type="audio/mpeg")


@app.post(config.api.interrupt, dependencies=[Security(get_api_key)])
async def interrupt(session_id: str):
    """Device-side state-4: stop streaming the current Turn for
    `session_id`, but keep the Session and its conversation memory alive
    so the next /upload can continue the dialog. Idempotent."""
    ok = await session_manager.interrupt_session(session_id)
    return JSONResponse(
        {"status": "success" if ok else "noop", "session_id": session_id}
    )


@app.post(config.api.session_complete, dependencies=[Security(get_api_key)])
async def session_complete(session_id: str | None = None):
    """Device-side state-6: end the multi-turn dialog. Cancels any
    in-flight Turn AND drops the Session from the registry. Always 200 —
    a missing session_id is treated as a no-op."""
    ok = await session_manager.complete_session(session_id)
    return JSONResponse(
        {"status": "success" if ok else "noop", "session_id": session_id}
    )


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=config.app.host,
        port=config.app.port,
        loop=config.app.loop,
    )
