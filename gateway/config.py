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
    text_stream: str = "/api/aura/text_stream/{session_id}"
    audio_stream: str = "/api/aura/audio_stream/{session_id}.mp3"
    upload: str = "/api/aura/upload"
    interrupt: str = "/api/aura/interrupt/{session_id}"
    session_complete: str = "/api/aura/session_complete"
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
    # /audio_stream keepalive while waiting for the first real TTS chunk.
    # Sent every `heartbeat_interval_s` seconds; stops automatically once
    # any real audio arrives. Must stay well below the device-side player
    # timeout (currently 30s). Set to 0 to disable.
    heartbeat_interval_s: float = 10.0
    # Path (relative to gateway/) of a pre-encoded MP3 file used as the
    # heartbeat frame. Drop in any short MP3 clip (silence, soft "嗯",
    # room tone, …) and restart to swap.
    heartbeat_path: str = "assets/heartbeat.mp3"
    # The heartbeat is re-encoded once at startup to match the live TTS
    # output's frame format exactly. The Android `MediaPlayer` locks onto
    # the format parameters of the very first MP3 header it parses; any
    # later frames whose params disagree are dropped silently. Defaults
    # below match the current CosyVoice output (22050 Hz / mono / 128k
    # CBR). If you swap TTS engines, run `ffprobe` on a sample and update.
    heartbeat_target_sample_rate: int = 22050
    heartbeat_target_channels: int = 1
    heartbeat_target_bitrate: str = "128k"


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
