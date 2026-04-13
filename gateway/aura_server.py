import uuid
import uvicorn
import wave
import os
import json
from fastapi import FastAPI, Response, UploadFile, File
from fastapi import Security, HTTPException, status, Query
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import StreamingResponse
from speech_to_text import STTClient
from text_to_speech import TTSClient
from llm import AudioStreamLLM, LLMClient
import asyncio
from utils.cache import LRUFileCache
from utils.mlogging import Logger
from dotenv import load_dotenv


logger = Logger.build("AuraServer", "INFO")

load_dotenv()
API_KEY = os.getenv('AURA_API_KEY')
api_key_header = APIKeyHeader(name="X-Aura-Token", auto_error=False)

cache = LRUFileCache(cache_dir="./tmp_audio")
# tts_client = TTSClient.build("edge_tts")
tts_client = TTSClient.build("cosyvoice")
stt_client = STTClient.build("whisper")
llm_client = LLMClient(
    api_url="http://192.168.1.172:11434/api/chat", 
    model_name="gemma4:31b", temperature=0.3
)
audio_stream_llm = asyncio.run(AudioStreamLLM.build(
    llm_client, tts_client, "模型未响应，请检查服务是否正常。"))

# Global task manager to coordinate between audio and text streams
# Maps task_id to its corresponding asyncio.Queue
task_queues: dict[str, asyncio.Queue] = {}

app = FastAPI(title="Aura Server")

async def get_api_key(
    api_key_header: str = Security(api_key_header),
    token: str = Query(None)
):
    if api_key_header == API_KEY:
        return api_key_header
    if token == API_KEY:
        return token
        
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="无权访问"
    )


@app.post("/api/aura/upload", dependencies=[Security(get_api_key)])
async def request(audio_file: UploadFile = File(...)):
    task_id = uuid.uuid4().hex
    save_path = cache.path(f"command_{task_id}.wav")
    pcm_data = await audio_file.read()
    with wave.open(save_path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm_data)
    cache.evict(save_path)
    return {"status": "success", "task_id": task_id}

@app.get("/api/aura/text_stream/{task_id}", dependencies=[Security(get_api_key)])
async def text_stream(task_id: str):
    """
    SSE endpoint to push LLM tokens to the Flutter app in real-time.
    """
    # Ensure a queue exists for this task even if audio stream hasn't started yet
    if task_id not in task_queues:
        task_queues[task_id] = asyncio.Queue()

    async def event_generator():
        try:
            while True:
                # Wait asynchronously for new tokens from the LLM generator
                token = await task_queues[task_id].get()
                
                # Check for the termination signal
                if token == "[DONE]":
                    yield "data: [DONE]\n\n"
                    break
                
                # Format the token as an SSE data line
                # ensure_ascii=False prevents Chinese characters from becoming unicode escapes
                yield f"data: {json.dumps({'token': token}, ensure_ascii=False)}\n\n"
        finally:
            # Clean up the queue to prevent memory leaks once the connection is closed
            if task_id in task_queues:
                del task_queues[task_id]

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/aura/audio_stream/{task_id}.mp3", dependencies=[Security(get_api_key)])
async def get_response(task_id: str):
    save_path = cache.path(f"command_{task_id}.wav")
    
    if not os.path.exists(save_path):
        logger.error(f"File not found: {save_path}")
        return Response(status_code=404)
    try:
        user_text = stt_client.speech_to_text(save_path)
        logger.info(f"User text: {user_text}")

        if user_text:
            # Bind the text queue for this specific task
            if task_id not in task_queues:
                task_queues[task_id] = asyncio.Queue()
                
            text_queue = task_queues[task_id]

            return StreamingResponse(
                # Pass the queue into the generator
                audio_stream_llm.generate_answer_stream(user_text, text_queue), 
                media_type="audio/mpeg"
            )
        else:
            return Response(status_code=400)
    except Exception as e:
        logger.error(f"Speech to text failed: {e}")
        return Response(status_code=500)
    

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, loop="asyncio")