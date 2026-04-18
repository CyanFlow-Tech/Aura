"""Pipeline factories: declare which stages, channels, and endpoint channels
make up a given business flow.

**Extension contract** (adding a new business scenario): write a
`build_xxx(...)` function that instantiates channels, instantiates stages,
and returns them wrapped in a `PipelineBundle`. Session / SessionManager
stay untouched; server.py only needs a new endpoint.
"""

from __future__ import annotations

import io
from typing import Any, NamedTuple

from channels import BroadcastChannel, QueueChannel
from core import Aura
from stages import ConversationStage, Stage, STTStage, TTSStage


class PipelineBundle(NamedTuple):
    stages: list[Stage]
    # All channel references. Session closes them on teardown as a safety
    # net in case a Stage forgets to close its own output.
    channels: list[Any]
    # Channels exposed to HTTP endpoints, keyed by stable name.
    # Usually BroadcastChannel instances.
    endpoints: dict[str, Any]


# ---- Stable endpoint channel names (consumed by server.py) -----------------
CHANNEL_TTS_OUT = "tts_out"


def build_voice_chat_pipeline(aura: Aura, audio_buffer: io.BytesIO) -> PipelineBundle:
    """
    wav_buffer --> STT --> [user_text] --> Conversation --> [sentences]
                                                              |
                                                              v
                                                            TTS --> [tts_out]*
                                                                        |
                            text_stream endpoint  <-- subscribe --------|
                            audio_stream endpoint <-- subscribe --------'

    (*) broadcast channel, payload is (sentence, audio_bytes)
    """
    user_text: QueueChannel[str] = QueueChannel()
    sentences: QueueChannel[str] = QueueChannel()
    tts_out: BroadcastChannel[tuple[str, bytes]] = BroadcastChannel()

    stages: list[Stage] = [
        STTStage(aura.stt, audio_buffer, user_text),
        ConversationStage(aura.llm, user_text, sentences),
        TTSStage(aura.tts, sentences, tts_out),
    ]
    return PipelineBundle(
        stages=stages,
        channels=[user_text, sentences, tts_out],
        endpoints={CHANNEL_TTS_OUT: tts_out},
    )
