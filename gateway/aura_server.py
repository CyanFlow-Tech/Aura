import uuid
import uvicorn
import wave
import os
from fastapi import FastAPI, Response, UploadFile, File
from fastapi.responses import StreamingResponse
from speech_to_text import STTClient
from text_to_speech import TTSClient
from llm import AudioStreamLLM, LLMClient
import asyncio
from utils.cache import LRUFileCache
from utils.mlogging import Logger

logger = Logger.build("AuraServer", "INFO")

# from utils.runtime_tool import inject_envs, Envs
# inject_envs(dict([Envs.HF_ENDPOINT]))

cache = LRUFileCache(cache_dir="./tmp_audio")
tts_client = TTSClient.build("edge_tts")
stt_client = STTClient.build("whisper")
llm_client = LLMClient(
    api_url="http://192.168.1.172:11434/api/chat", 
    model_name="gemma4:31b", temperature=0.3
)
audio_stream_llm = asyncio.run(AudioStreamLLM.build(
    llm_client, tts_client, "模型未响应，请检查服务是否正常。"))

app = FastAPI(title="Aura Server")

@app.post("/api/aura/upload")
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

@app.get("/api/aura/stream/{task_id}.mp3")
async def get_response(task_id: str):
    save_path = cache.path(f"command_{task_id}.wav")
    
    if not os.path.exists(save_path):
        logger.error(f"File not found: {save_path}")
        return Response(status_code=404)
    try:
        user_text = stt_client.speech_to_text(save_path)
        logger.info(f"User text: {user_text}")

        if user_text:    
            return StreamingResponse(
                audio_stream_llm.generate_answer_stream(user_text), 
                media_type="audio/mpeg"
            )
        else:
            return Response(status_code=400)
    except Exception as e:
        logger.error(f"Speech to text failed: {e}")
        return Response(status_code=500)
    

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, loop="asyncio")