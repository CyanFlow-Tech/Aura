import asyncio
import io
import wave

from fastapi import FastAPI, File, Security, UploadFile
from fastapi.responses import StreamingResponse
import uvicorn

from session import SessionManager
from utils.mlogging import Logger
from auth import get_api_key
from config import config
from core import Aura

logger = Logger.build("AuraServer", config.app.logging_level)


aura = Aura(config)
session_manager = SessionManager()
app = FastAPI(title=config.app.title)


@app.post(config.api.upload, dependencies=[Security(get_api_key)])
async def request(audio_file: UploadFile = File(...)):
    wav_buffer = io.BytesIO()
    pcm_data = await audio_file.read()
    audio_config = config.audio
    with wave.open(wav_buffer, "wb") as wav_file:
        wav_file.setnchannels(audio_config.channels)
        wav_file.setsampwidth(audio_config.sample_width)
        wav_file.setframerate(audio_config.frame_rate)
        wav_file.writeframes(pcm_data)
    wav_buffer.seek(0)
    
    session = session_manager.new_session()
    stt_task = asyncio.create_task(
        aura.speech_to_text(wav_buffer, session.q_llm_input))
    conv_task = asyncio.create_task(
        aura.conversation(session.q_llm_input, session.q_tts_input))
    session_manager.add_session_tasks(session.session_id, {stt_task, conv_task})

    return {"status": "success", "task_id": session.session_id}

@app.get(config.api.text_stream, dependencies=[Security(get_api_key)])
async def text_stream(task_id: str):
    session = session_manager.get_session(task_id)
    stream = aura.get_text_stream(session.q_sse_input)
    return StreamingResponse(
        session_manager.stream_session(stream, session), 
        media_type="text/event-stream"
    )

@app.get(config.api.audio_stream, dependencies=[Security(get_api_key)])
async def audio_stream(task_id: str):
    session = session_manager.get_session(task_id)
    stream = aura.get_audio_stream(session.q_tts_input, session.q_sse_input)

    return StreamingResponse(
        session_manager.stream_session(stream, session), 
        media_type="audio/mpeg"
    )
    
if __name__ == "__main__":
    uvicorn.run(
        app,
        host=config.app.host,
        port=config.app.port,
        loop=config.app.loop,
    )