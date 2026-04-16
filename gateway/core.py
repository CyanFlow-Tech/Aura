import asyncio
import io
import json
from utils.mlogging import LoggingMixin
from tools.speech_to_text import SpeechToText
from tools.text_to_speech import TextToSpeech
from llm import LLM
from dataclasses import asdict


class Aura(LoggingMixin):

    def __init__(self, config):
        super().__init__()
        self.tts: TextToSpeech = TextToSpeech.build(config)
        self.stt: SpeechToText = SpeechToText.build(config)
        self.llm: LLM = LLM(**asdict(config.llm))

    async def speech_to_text(self, audio_buffer: io.BytesIO, llm_input_queue: asyncio.Queue):
        text = self.stt.speech_to_text(audio_buffer)
        await llm_input_queue.put(text)

    async def conversation(
        self, 
        llm_input_queue: asyncio.Queue,
        tts_input_queue: asyncio.Queue,
    ):
        sentence = ""
        user_text = await llm_input_queue.get()
        async with self.llm.generate(user_text, think=False) as response:
            async for char in self.llm.parse_response(response):
                sentence += char
                if char in self.llm.char_separators and len(sentence) >= self.llm.char_batch_size:
                    await tts_input_queue.put(sentence)
                    sentence = ""
            if sentence:
                await tts_input_queue.put(sentence)
            await tts_input_queue.put("[DONE]")

    async def get_text_stream(self, text_queue: asyncio.Queue):
        while True:
            token = await text_queue.get()
            data = json.dumps({'token': token}, ensure_ascii=False) if token != "[DONE]" else "[DONE]"
            yield f"data: {data}\n\n"

    async def get_audio_stream(self, tts_input_queue: asyncio.Queue, sse_input_queue: asyncio.Queue):
        sentence = await tts_input_queue.get()
        while sentence != "[DONE]":
            if audio_bytes := await self.tts.text_to_speech(sentence):
                self.logger.info(f"Generating audio for sentence: {sentence}")
                await sse_input_queue.put(sentence)
                yield audio_bytes
            sentence = await tts_input_queue.get()
        await sse_input_queue.put("[DONE]")
