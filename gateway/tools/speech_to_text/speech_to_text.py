import io
from utils.config_tool import AutoConfigMixin
from utils.polymorphic import FactoryMixin


class SpeechToText(AutoConfigMixin, FactoryMixin):

    implement = 'Whisper'
    
    def speech_to_text(self, audio_buffer: io.BytesIO) -> str:
        raise NotImplementedError("Subclasses must implement this method")
