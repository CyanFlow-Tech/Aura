"""
API layer: HTTP / SSE / audio encoding plus Pipeline dispatch.
"""

from __future__ import annotations

import io
import json
import wave

import uvicorn
from fastapi import FastAPI, File, Security, UploadFile
from fastapi.responses import StreamingResponse

from auth import get_api_key
from config import config
from core import Aura
from pipeline import CHANNEL_TTS_OUT, build_voice_chat_pipeline
from session import SessionManager
from utils.mlogging import Logger

logger = Logger.build("AuraServer", config.app.logging_level)

aura = Aura(config)
session_manager = SessionManager()
app = FastAPI(title=config.app.title)


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
async def upload(audio_file: UploadFile = File(...)):
    pcm_data = await audio_file.read()
    wav_buffer = _pcm_to_wav(pcm_data)

    bundle = build_voice_chat_pipeline(aura, wav_buffer)
    session = session_manager.new_session(bundle)
    return {"status": "success", "task_id": session.session_id}


@app.get(config.api.text_stream, dependencies=[Security(get_api_key)])
async def text_stream(task_id: str):
    session = session_manager.get_session(task_id)

    async def gen():
        async for sentence, _audio in session_manager.stream(session, CHANNEL_TTS_OUT):
            payload = json.dumps({"token": sentence}, ensure_ascii=False)
            yield f"data: {payload}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get(config.api.audio_stream, dependencies=[Security(get_api_key)])
async def audio_stream(task_id: str):
    session = session_manager.get_session(task_id)

    async def gen():
        async for _sentence, audio in session_manager.stream(session, CHANNEL_TTS_OUT):
            yield audio

    return StreamingResponse(gen(), media_type="audio/mpeg")


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=config.app.host,
        port=config.app.port,
        loop=config.app.loop,
    )
