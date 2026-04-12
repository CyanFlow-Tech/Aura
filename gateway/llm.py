import json
from typing import AsyncGenerator
from text_to_speech import TTSClient
from utils.mlogging import LoggingMixin
from contextlib import asynccontextmanager
import httpx


class LLMClient(LoggingMixin):
    
    def __init__(self, api_url: str, model_name: str = "gemma4:31b", temperature: float = 0.3):
        super().__init__()
        self.model_name = model_name
        self.api_url = api_url
        self.temperature = temperature

    @asynccontextmanager
    async def chat(self, user_text: str, think: bool = False) -> AsyncGenerator[httpx.Response, None]:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": "你是 Aura，请简明扼要作答，口语化，不要废话。"},
                {"role": "user", "content": user_text}
            ],
            "stream": True,
            "think": think,
            "options": {"temperature": self.temperature}
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", self.api_url, json=payload) as response:  
                if response.status_code != 200:
                    error_body = await response.aread()
                    self.logger.error(f"Chat failed ({response.status_code}): {error_body.decode('utf-8')}")
                else:
                    yield response
    
    async def parse_response(self, response: httpx.Response) -> AsyncGenerator[str, None]:
        async for chunk in response.aiter_lines():
            if not chunk: continue
            try:
                data = json.loads(chunk)
                token: str = data.get("message", {}).get("content", "")
                if not token: continue
                for char in token:
                    yield char
            except Exception as e:
                self.logger.error(f"JSON stream parsing exception: {e}")
                self.logger.error(f"chunk content: {chunk}")


class AudioStreamLLM(LoggingMixin):

    def __init__(self, llm_client: LLMClient, tts_client: TTSClient, fallback_audio: bytes):
        super().__init__()
        self.llm_client = llm_client
        self.tts_client = tts_client
        self.fallback_audio = fallback_audio
        self.min_sentence = 20
        self.stop_flags = set("，。！？；,.!?;")
    
    @staticmethod
    async def build(llm_client: LLMClient, tts_client: TTSClient, fallback_text: str):
        fallback_audio = await tts_client.text_to_speech(fallback_text)
        return AudioStreamLLM(llm_client, tts_client, fallback_audio)

    async def generate_answer_stream(self, user_text: str):
        has_generated_audio = False 

        sentence = ""
        async with self.llm_client.chat(user_text, think=False) as response:
            async for char in self.llm_client.parse_response(response):
                sentence += char
                if char in self.stop_flags and len(sentence) >= self.min_sentence:
                    if audio_bytes := await self.tts_client.text_to_speech(sentence):
                        self.logger.info(f"Generating audio for sentence: {sentence}")
                        has_generated_audio = True
                        yield audio_bytes
                    sentence = ""
        if sentence and (audio_bytes := await self.tts_client.text_to_speech(sentence)):
            has_generated_audio = True
            yield audio_bytes

        if not has_generated_audio:
            self.logger.warning("No valid text generated, fallback to default audio")
            yield self.fallback_audio