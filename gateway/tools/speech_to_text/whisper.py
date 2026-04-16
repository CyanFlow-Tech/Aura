import io
from .speech_to_text import SpeechToText
from typing import Literal
from utils.mlogging import LoggingMixin


@SpeechToText.register_impl()
class Whisper(SpeechToText, LoggingMixin):

    def __init__(
        self, 
        model_name: str = "large-v3-turbo", 
        device: Literal['cuda', 'cpu', 'auto'] = 'cuda',
        compute_type: Literal['float16', 'float32', 'int8'] = 'float16',
        language: str = "zh",
        prompt: str = "以下是一段简体中文。",
    ):
        super().__init__()
        from utils.runtime_tool import inject_libs, Libs
        inject_libs([Libs.CUBLAS, Libs.CUDNN])
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self.language = language
        self.prompt = prompt
        self.logger.info(f"Whisper initialized: {model_name}, {device}, {compute_type}, {language}, {prompt}")

    def speech_to_text(self, audio_buffer: io.BytesIO) -> str:
        segments, info = self.model.transcribe(
            audio_buffer, language=self.language, beam_size=5, initial_prompt=self.prompt
        )
        user_text = "".join([segment.text for segment in segments]).strip()
        return user_text