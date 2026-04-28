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
import json
from dataclasses import dataclass, field
from typing import Protocol

from channels import ReceiveChannel, SendChannel
from conversation import Conversation
from llm import LLM
from tools.searching import SearchResult, Searching
from tools.speech_to_text import SpeechToText
from tools.text_to_speech import TextToSpeech
from utils.mlogging import LoggingMixin


class Stage(Protocol):
    async def run(self) -> None: ...


def _is_decimal_point_boundary(sentence: str, char: str) -> bool:
    """Return True when the current boundary looks like a decimal point."""
    return char == "." and len(sentence) >= 2 and sentence[-2].isdigit()


@dataclass
class UserTurnContext:
    user_text: str
    should_search: bool = False
    search_query: str | None = None
    search_results: list[SearchResult] = field(default_factory=list)


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
                        and not _is_decimal_point_boundary(sentence, char)
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


class SearchIntentStage(LoggingMixin):
    def __init__(
        self,
        llm: LLM,
        conversation: Conversation,
        inp: ReceiveChannel[str],
        out: SendChannel[UserTurnContext],
        history_messages: int = 6,
    ):
        super().__init__()
        self._llm = llm
        self._conversation = conversation
        self._inp = inp
        self._out = out
        self._history_messages = history_messages

    async def run(self) -> None:
        try:
            user_text = await self._inp.receive()
            context = UserTurnContext(user_text=user_text)
            try:
                context.should_search, context.search_query = await self._plan(user_text)
            except Exception as exc:
                self.logger.warning(f"Search intent planning failed: {exc!r}")
            await self._out.send(context)
        finally:
            await self._out.close()

    async def _plan(self, user_text: str) -> tuple[bool, str | None]:
        planner_messages = [
            {
                "role": "system",
                "content": (
                    "你是检索路由器。判断当前用户问题是否需要先做联网检索再回答。"
                    "用户的输入可能有错别字或者模糊音，请更正后再进行判断。"
                    "对以下情况倾向返回 should_search=true：用户明确要求搜索；"
                    "问题涉及最新、今天、当前、实时、新闻、价格、天气、股价、汇率、比分、"
                    "政策变动、版本更新等时效性信息；或需要外部事实核验。"
                    "对闲聊、改写、翻译、总结、纯推理、稳定常识问题返回 should_search=false。"
                    "只输出一个 JSON 对象，不要输出任何额外文字。"
                    "格式必须是："
                    "{\"should_search\": true, \"query\": \"适合搜索引擎的查询词\"}。"
                    "如果不需要检索，query 置为空字符串。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "recent_history": self._conversation.recent_history(
                            self._history_messages
                        ),
                        "current_user_text": user_text,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        raw = await self._llm.generate_text(planner_messages, think=False)
        data = json.loads(self._extract_json_object(raw))
        should_search = bool(data.get("should_search"))
        query = str(data.get("query") or "").strip()
        if should_search and not query:
            query = user_text.strip()
        self.logger.info(
            "Search intent: should_search=%s query=%s",
            should_search,
            query or "<empty>",
        )
        return should_search, query or None

    @staticmethod
    def _extract_json_object(text: str) -> str:
        text = text.strip()
        if text.startswith("{") and text.endswith("}"):
            return text
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"Planner did not return JSON: {text!r}")
        return text[start : end + 1]


class SearchStage(LoggingMixin):
    def __init__(
        self,
        searching: Searching,
        inp: ReceiveChannel[UserTurnContext],
        out: SendChannel[UserTurnContext],
        limit: int = 5,
    ):
        super().__init__()
        self._searching = searching
        self._inp = inp
        self._out = out
        self._limit = limit

    async def run(self) -> None:
        try:
            context = await self._inp.receive()
            if context.should_search and context.search_query:
                try:
                    context.search_results = await self._searching.search(
                        context.search_query, limit=self._limit
                    )
                    self.logger.info(
                        "Search executed: query=%s results=%d",
                        context.search_query,
                        len(context.search_results),
                    )
                except Exception as exc:
                    self.logger.warning(f"Search execution failed: {exc!r}")
            await self._out.send(context)
        finally:
            await self._out.close()


class SearchAugmentedConversationStage(LoggingMixin):
    """Conversation stage that can consume optional retrieval context."""

    def __init__(
        self,
        llm: LLM,
        conversation: Conversation,
        inp: ReceiveChannel[UserTurnContext],
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
            turn = await self._inp.receive()
            self._conversation.append_user(turn.user_text)
            appended_user = True

            extra_system_messages: list[str] = []
            retrieval_context = self._format_retrieval_context(turn)
            if retrieval_context:
                extra_system_messages.append(retrieval_context)

            messages = self._conversation.messages(
                self._llm.system_prompt,
                extra_system_messages=extra_system_messages,
            )

            sentence = ""
            async with self._llm.generate(messages, think=False) as response:
                async for char in self._llm.parse_response(response):
                    sentence += char
                    full_reply += char
                    if (
                        char in self._llm.char_separators
                        and len(sentence) >= self._llm.char_batch_size
                        and not _is_decimal_point_boundary(sentence, char)
                    ):
                        await self._out.send(sentence)
                        sentence = ""
                if sentence:
                    await self._out.send(sentence)
        finally:
            if appended_user and full_reply:
                self._conversation.append_assistant(full_reply)
            await self._out.close()

    @staticmethod
    def _format_retrieval_context(turn: UserTurnContext) -> str:
        if turn.search_results:
            lines = [
                "以下是联网检索结果，请优先参考与问题直接相关且彼此一致的信息作答。"
                "如果结果不足以支持明确结论，请直接说明不确定，不要编造。"
            ]
            for idx, item in enumerate(turn.search_results, start=1):
                lines.append(
                    f"[{idx}] 标题: {item.title}\n链接: {item.url}\n摘要: {item.content}"
                )
            return "\n\n".join(lines)
        if turn.should_search:
            return (
                "已尝试联网检索，但没有拿到有效结果。回答时请保持谨慎，"
                "不要把不确定的最新事实说成确定事实。"
            )
        return ""


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
