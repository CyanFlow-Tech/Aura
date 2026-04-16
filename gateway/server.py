import io
import uuid
import uvicorn
import wave
from fastapi import FastAPI, UploadFile, File
from fastapi import Security, HTTPException, status, Query
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import StreamingResponse
import asyncio
from config import config
from core import Aura
from utils.mlogging import Logger
logger = Logger.build("AuraServer", config.app.logging_level)


aura = Aura(config)

api_key_header = APIKeyHeader(
    name=config.api.auth_header,
    auto_error=False,
)

q_llm_input: dict[str, asyncio.Queue] = {}
q_tts_input: dict[str, asyncio.Queue] = {}
q_sse_input: dict[str, asyncio.Queue] = {}


app = FastAPI(title=config.app.title)

async def get_api_key(
    api_key_header: str = Security(api_key_header),
    token: str = Query(None)
):
    if api_key_header == config.api.auth_token:
        return api_key_header
    if token == config.api.auth_token:
        return token
        
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="无权访问"
    )


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

    task_id = uuid.uuid4().hex
    if task_id not in q_llm_input:
        q_llm_input[task_id] = asyncio.Queue()
        q_tts_input[task_id] = asyncio.Queue()
        q_sse_input[task_id] = asyncio.Queue()
    asyncio.create_task(aura.speech_to_text(wav_buffer, q_llm_input[task_id]))
    asyncio.create_task(aura.conversation(q_llm_input[task_id], q_tts_input[task_id]))
    return {"status": "success", "task_id": task_id}

# @app.get(config.api.test, dependencies=[Security(get_api_key)])
# async def test(text: str):
#     text_queue = asyncio.Queue()
#     punctuations = set(streaming_config.stop_flags)
#     async def produce_text():
#         sentence = ""
#         try:
#             async with llm_client.chat(text, think=False) as response:
#                 async for char in llm_client.parse_response(response):
#                     sentence += char
#                     if char in punctuations and len(sentence) >= streaming_config.test_min_sentence:
#                         await text_queue.put(sentence)
#                         sentence = ""
#             if sentence:
#                 await text_queue.put(sentence)
#         finally:
#             await text_queue.put(None)

#     producer_task = asyncio.create_task(produce_text())

#     async def event_generator():
#         try:
#             while True:
#                 token = await text_queue.get()
#                 if token is None:
#                     break
#                 yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
#         finally:
#             if not producer_task.done():
#                 producer_task.cancel()
#                 with contextlib.suppress(asyncio.CancelledError):
#                     await producer_task
        
#     return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get(config.api.text_stream, dependencies=[Security(get_api_key)])
async def text_stream(task_id: str):
    return StreamingResponse(
        aura.get_text_stream(q_sse_input[task_id]), 
        media_type="text/event-stream"
    )

@app.get(config.api.audio_stream, dependencies=[Security(get_api_key)])
async def audio_stream(task_id: str):
    return StreamingResponse(
        aura.get_audio_stream(q_tts_input[task_id], q_sse_input[task_id]), 
        media_type="audio/mpeg"
    )
    
if __name__ == "__main__":
    uvicorn.run(
        app,
        host=config.app.host,
        port=config.app.port,
        loop=config.app.loop,
    )