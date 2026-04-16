import os
from dataclasses import dataclass, field
from pathlib import Path
from utils.config_tool import assemble_config
from tools import tools
from dotenv import load_dotenv
load_dotenv()


@dataclass
class AppSettings:
    title: str = "Aura Server"
    logging_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000
    loop: str = "asyncio"
    cache_dir: str = "./tmp_audio"


@dataclass
class APISettings:
    test: str = "/api/aura/test"
    text_stream: str = "/api/aura/text_stream/{task_id}"
    audio_stream: str = "/api/aura/audio_stream/{task_id}.mp3"
    upload: str = "/api/aura/upload"
    auth_header: str = "X-Aura-Token"
    auth_token: str = os.getenv("AURA_API_KEY", "")


@dataclass
class AudioSettings:
    channels: int = 1
    sample_width: int = 2
    frame_rate: int = 16000


@dataclass
class LLMSettings:
    api_url: str = "http://192.168.1.172:11434/api/chat"
    model_name: str = "gemma4:31b"
    temperature: float = 0.3
    system_prompt: str = (
        "你是 Aura，请简明扼要作答，口语化，不要废话。"
        "输出结果使用普通文本，不要使用 Markdown 格式。"
    )
    timeout: float = 60.0
    char_separators: str = "，。！？；,.!?;"
    char_batch_size: int = 10

@dataclass
class StreamingSettings:
    stop_flags: str = "，。！？；,.!?;"
    test_min_sentence: int = 10
    audio_min_sentence: int = 20
    fallback_text: str = "模型未响应，请检查服务是否正常。"


@dataclass
class AppConfig:
    app: AppSettings = field(default_factory=AppSettings)
    api: APISettings = field(default_factory=APISettings)
    audio: AudioSettings = field(default_factory=AudioSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    streaming: StreamingSettings = field(default_factory=StreamingSettings)


config = assemble_config(
    AppConfig(), tools=tools,
    override_yaml=Path(__file__).resolve().parent / "config.yaml",
)
