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
from conversation import Conversation
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
    """Drives one turn of the multi-turn dialog.

    Reads one user_text from the input channel, appends it to the shared
    `Conversation`, asks the LLM (with the *full* prior history), streams
    sentences out, and finally appends the accumulated assistant reply
    back into the conversation so the NEXT turn (which reuses the same
    `Conversation` instance) sees this exchange.
    """

    def __init__(
        self,
        llm: LLM,
        conversation: Conversation,
        inp: ReceiveChannel[str],
        out: SendChannel[str],
    ):
        super().__init__()
        self._llm = llm
        self._conversation = conversation
        self._inp = inp
        self._out = out

    async def run(self) -> None:
        full_reply = ""
        appended_user = False
        try:
            user_text = await self._inp.receive()
            self._conversation.append_user(user_text)
            appended_user = True
            messages = self._conversation.messages(self._llm.system_prompt)

            sentence = ""
            async with self._llm.generate(messages, think=False) as response:
                async for char in self._llm.parse_response(response):
                    sentence += char
                    full_reply += char
                    if (
                        char in self._llm.char_separators
                        and len(sentence) >= self._llm.char_batch_size
                    ):
                        await self._out.send(sentence)
                        sentence = ""
                if sentence:
                    await self._out.send(sentence)
        finally:
            # Persist whatever we managed to generate, even on partial
            # output (e.g. interrupted mid-turn). This keeps the next
            # turn's context faithful to what the user actually heard,
            # rather than dropping the half-said reply entirely.
            if appended_user and full_reply:
                self._conversation.append_assistant(full_reply)
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
