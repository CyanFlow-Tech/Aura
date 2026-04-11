from typing import Literal, Type


class STTClient:

    _implements = {}
    
    @staticmethod
    def register(name: str):
        def decorator(cls: Type[STTClient]) -> Type[STTClient]:
            if not issubclass(cls, STTClient):
                raise TypeError(f"{cls.__name__} must be a subclass of STTClient")
            STTClient._implements[name] = cls
            return cls
        return decorator

    @staticmethod
    def build(name: str, **kwargs) -> 'STTClient':
        cls = STTClient._implements[name]
        return cls(**kwargs)

    def speech_to_text(self, audio_path: str) -> str:
        raise NotImplementedError("Subclasses must implement this method")


@STTClient.register("whisper")
class WhisperSTTClient(STTClient):

    def __init__(
        self, 
        model_name: str = "large-v3-turbo", 
        device: Literal['cuda', 'cpu', 'auto'] = 'cuda',
        compute_type: Literal['float16', 'float32', 'int8'] = 'float16',
        language: str = "zh",
        prompt: str = "以下是一段简体中文。",
    ):
        from utils.runtime_tool import inject_libs, Libs
        inject_libs([Libs.CUBLAS, Libs.CUDNN])
        from faster_whisper import WhisperModel
        self.model = WhisperModel(model_name, device=device, compute_type=compute_type)
        self.language = language
        self.prompt = prompt

    def speech_to_text(self, audio_path: str) -> str:
        segments, info = self.model.transcribe(
            audio_path, language=self.language, beam_size=5, initial_prompt=self.prompt
        )
        user_text = "".join([segment.text for segment in segments]).strip()
        return user_text