import re
from utils.config_tool import AutoConfigMixin
from utils.polymorphic import FactoryMixin


class TTSPreprocessor:
    
    @classmethod
    def process(cls, text: str) -> str:
        text = re.sub(r'[*#`_~]', '', text).strip()
        return text

class TextToSpeech(AutoConfigMixin, FactoryMixin):

    implement = 'CosyVoice'

    async def text_to_speech(self, text: str) -> bytes:
        if not text: return b""
        text = TTSPreprocessor.process(text)
        return await self._text_to_speech(text)
    
    async def _text_to_speech(self, text: str) -> bytes:
        raise NotImplementedError("Subclasses must implement this method")
