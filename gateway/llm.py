import json
import os
from typing import AsyncGenerator
from utils.mlogging import LoggingMixin
from contextlib import asynccontextmanager
import httpx


def parse_nnecloud(chunk: bytes):
    data = json.loads(chunk)
    return data.get("message", {}).get("content", "")

def parse_hunyuan(chunk: bytes):
    if not chunk: return None
    try:
        data = json.loads(chunk[len('data: '):])
    except json.JSONDecodeError:
        return None
    if delta := data.get("choices", [{}])[0].get("delta", {}):
        return delta.get("content", delta.get("reasoning_content", ""))
    return None

def parse_zhiyuan(chunk: bytes):
    if not chunk: return None
    try:
        data = json.loads(chunk[len('data: '):])
    except json.JSONDecodeError:
        return None
    if delta := data.get("choices", [{}])[0].get("delta", {}):
        return delta.get("content", delta.get("reasoning_content", ""))
    return None

parser_map = {
    "NNECLOUD": parse_nnecloud,
    "HUNYUAN": parse_hunyuan,
    "ZHIYUAN": parse_zhiyuan,
}

class LLM(LoggingMixin):

    def __init__(
        self,
        api_url: str,
        model_name: str,
        temperature: float,
        system_prompt: str,
        timeout: float,
        char_separators: str,
        char_batch_size: int
    ):
        super().__init__()
        self.model_name = model_name
        self.api_url = api_url
        self.temperature = temperature
        self.system_prompt = system_prompt
        self.timeout = timeout
        self.char_separators = set(char_separators)
        self.char_batch_size = char_batch_size

        if not self.api_url.startswith('http'):
            self._parse_response = parser_map[self.api_url.upper()]
            API_URL = self.api_url.upper() + "_API_URL"
            API_KEY = self.api_url.upper() + "_API_KEY"
            self.api_url = os.environ[API_URL]
            self.api_key = os.environ[API_KEY]
        else:
            self._parse_response = parser_map["NNECLOUD"]

        self.logger.info(f"LLM initialized: {self.model_name} at {self.api_url}")

    @asynccontextmanager
    async def generate(
        self,
        messages: list[dict[str, str]],
        think: bool = False,
    ) -> AsyncGenerator[httpx.Response, None]:
        """Stream a chat completion. `messages` must include the system
        prompt and the full prior conversation; the caller (typically
        `ConversationStage`) builds it from a `Conversation`.
        """
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": True,
            "think": think,
            "options": {"temperature": self.temperature}
        }
        headers = {}
        if hasattr(self, 'api_key'):
            headers['Authorization'] = f'Bearer {self.api_key}'
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", self.api_url, json=payload, headers=headers) as response:  
                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode("utf-8", errors="replace")
                    self.logger.error(f"Chat failed ({response.status_code}): {error_text}")
                    raise RuntimeError(f"LLM chat failed ({response.status_code}): {error_text}")
                yield response
    
    async def parse_response(self, response: httpx.Response) -> AsyncGenerator[str, None]:
        async for chunk in response.aiter_lines():
            if not chunk: continue
            try:
                token: str = self._parse_response(chunk)
                if not token: continue
                for char in token:
                    yield char
            except Exception as e:
                self.logger.error(f"JSON stream parsing exception: {e}")
                self.logger.error(f"chunk content: {chunk}")

    async def generate_text(
        self,
        messages: list[dict[str, str]],
        think: bool = False,
    ) -> str:
        text = ""
        async with self.generate(messages, think=think) as response:
            async for chunk in self.parse_response(response):
                text += chunk
        return text
