"""`Aura`: plain dependency container.

The coroutines that used to live here have been moved to `stages.py`.
This module only instantiates the three underlying models (STT / TTS / LLM)
from config; stages obtain them from `Aura` instead. Business flow is no
longer `Aura`'s concern.
"""

from __future__ import annotations

from dataclasses import asdict

from llm import LLM
from tools.searching import Searching
from tools.speech_to_text import SpeechToText
from tools.text_to_speech import TextToSpeech
from utils.mlogging import LoggingMixin


class Aura(LoggingMixin):
    def __init__(self, config):
        super().__init__()
        self.tts: TextToSpeech = TextToSpeech.build(config)
        self.stt: SpeechToText = SpeechToText.build(config)
        self.searching: Searching = Searching.build(config)
        self.llm: LLM = LLM(**asdict(config.llm))
