from typing import Annotated
from .text_to_speech import TextToSpeech
import httpx
from utils.mlogging import LoggingMixin


@TextToSpeech.register_impl()
class CosyVoice(TextToSpeech, LoggingMixin):
    
    def __init__(
        self, 
        api_url: Annotated[str, "The API URL for the CosyVoice API"] = "http://localhost:50000/api/tts",
        voice: str = "中文女",
    ):
        super().__init__()
        self.api_url = api_url
        self.voice = voice
        self.logger.info(f"CosyVoice initialized: {self.api_url}, {self.voice}")

    async def _text_to_speech(self, text: str) -> bytes:
        payload = {
            "text": text,
            "speaker": self.voice,
            "stream": True
        }
        audio_data = b""
        timeout = httpx.Timeout(20.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", self.api_url, json=payload) as response:
                async for chunk in response.aiter_bytes():
                    if chunk: audio_data += chunk
        return audio_data