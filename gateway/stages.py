"""Stage layer: pure business coroutines.

**Extension contract** (everything, and only, needed to add a new Stage):

1. Define a class whose constructor receives its dependencies and channel
   references (`ReceiveChannel` / `SendChannel`).
2. Implement `async def run(self) -> None`.
3. When `run` exits (including on exceptions), close its own output
   channel(s). Downstream stages terminate naturally via channel close;
   do not emit `[DONE]`-style sentinels.

The only thing Session requires from a Stage is `run()`. No other
interface.
"""

from __future__ import annotations

import io
from typing import Protocol

from channels import ReceiveChannel, SendChannel
from llm import LLM
from tools.speech_to_text import SpeechToText
from tools.text_to_speech import TextToSpeech
from utils.mlogging import LoggingMixin


class Stage(Protocol):
    async def run(self) -> None: ...


class STTStage(LoggingMixin):
    def __init__(
        self,
        stt: SpeechToText,
        audio_buffer: io.BytesIO,
        out: SendChannel[str],
    ):
        super().__init__()
        self._stt = stt
        self._buffer = audio_buffer
        self._out = out

    async def run(self) -> None:
        try:
            text = self._stt.speech_to_text(self._buffer)
            self.logger.info(f"STT: {text}")
            await self._out.send(text)
        finally:
            await self._out.close()


class ConversationStage(LoggingMixin):
    def __init__(
        self,
        llm: LLM,
        inp: ReceiveChannel[str],
        out: SendChannel[str],
    ):
        super().__init__()
        self._llm = llm
        self._inp = inp
        self._out = out

    async def run(self) -> None:
        try:
            user_text = await self._inp.receive()
            sentence = ""
            async with self._llm.generate(user_text, think=False) as response:
                async for char in self._llm.parse_response(response):
                    sentence += char
                    if (
                        char in self._llm.char_separators
                        and len(sentence) >= self._llm.char_batch_size
                    ):
                        await self._out.send(sentence)
                        sentence = ""
                if sentence:
                    await self._out.send(sentence)
        finally:
            await self._out.close()


class TTSStage(LoggingMixin):
    def __init__(
        self,
        tts: TextToSpeech,
        inp: ReceiveChannel[str],
        out: SendChannel[tuple[str, bytes]],
    ):
        super().__init__()
        self._tts = tts
        self._inp = inp
        self._out = out

    async def run(self) -> None:
        try:
            async for sentence in self._inp:
                audio = await self._tts.text_to_speech(sentence)
                if not audio:
                    continue
                self.logger.info(f"TTS ok: {sentence}")
                await self._out.send((sentence, audio))
        finally:
            await self._out.close()
