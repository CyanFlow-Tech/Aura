import importlib
from typing import Tuple, Type
import httpx
import re


class TTSPreprocessor:
    
    @classmethod
    def process(cls, text: str) -> str:
        text = re.sub(r'[*#`_~]', '', text).strip()
        return text


class TTSClient:

    _implements = {}
    
    @staticmethod
    def register(name: str):
        def decorator(cls: Type[TTSClient]) -> Type[TTSClient]:
            if not issubclass(cls, TTSClient):
                raise TypeError(f"{cls.__name__} must be a subclass of TTSClient")
            TTSClient._implements[name] = cls
            return cls
        return decorator

    @staticmethod
    def build(name: str, **kwargs) -> 'TTSClient':
        cls = TTSClient._implements[name]
        return cls(**kwargs)

    async def text_to_speech(self, text: str) -> bytes:
        if not text: return b""
        text = TTSPreprocessor.process(text)
        return await self._text_to_speech(text)
    
    async def _text_to_speech(self, text: str) -> bytes:
        raise NotImplementedError("Subclasses must implement this method")


@TTSClient.register("edge_tts")
class EdgeTTSClient(TTSClient):

    def __init__(self, voice: str = "zh-CN-YunxiNeural"):
        self.voice = voice
        self.api = importlib.import_module('edge_tts')


    async def _text_to_speech(self, text: str) -> bytes:
        communicate = self.api.Communicate(text, self.voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk.get("data", b"")
        return audio_data


@TTSClient.register("cosyvoice")
class CosyVoiceClient(TTSClient):
    
    def __init__(
        self, 
        host: Tuple[str, int] = ("localhost", 50000), 
        api: str = "/api/tts",
        voice: str = "中文女_温柔",
    ):
        self.host = host
        self.api = api
        self.voice = voice
        self.url = f"http://{host[0]}:{host[1]}{api}"

    async def _text_to_speech(self, text: str) -> bytes:
        payload = {
            "text": text,
            "speaker": self.voice,
            "stream": True
        }
        audio_data = b""
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", self.url, json=payload) as response:
                async for chunk in response.aiter_bytes():
                    if chunk: audio_data += chunk
        return audio_data