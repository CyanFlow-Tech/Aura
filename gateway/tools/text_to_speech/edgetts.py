from .text_to_speech import TextToSpeech
import importlib
from typing import Annotated
from utils.mlogging import LoggingMixin


@TextToSpeech.register_impl()
class EdgeTTS(TextToSpeech, LoggingMixin):

    def __init__(
        self, 
        voice: Annotated[str, "The voice to use for the text to speech"] = "zh-CN-YunxiNeural"
    ):
        super().__init__()
        self.voice = voice
        self.api = importlib.import_module('edge_tts')
        self.logger.info(f"EdgeTTS initialized: {self.voice}")

    async def _text_to_speech(self, text: str) -> bytes:
        communicate = self.api.Communicate(text, self.voice)
        audio_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data += chunk.get("data", b"")
        return audio_data

