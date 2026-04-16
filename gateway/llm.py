import json
from typing import AsyncGenerator
from utils.mlogging import LoggingMixin
from contextlib import asynccontextmanager
import httpx


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
        self.logger.info(f"LLM initialized: {self.model_name}")

    @asynccontextmanager
    async def generate(self, user_text: str, think: bool = False) -> AsyncGenerator[httpx.Response, None]:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_text}
            ],
            "stream": True,
            "think": think,
            "options": {"temperature": self.temperature}
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", self.api_url, json=payload) as response:  
                if response.status_code != 200:
                    error_body = await response.aread()
                    self.logger.error(f"Chat failed ({response.status_code}): {error_body.decode('utf-8')}")
                else:
                    yield response
    
    async def parse_response(self, response: httpx.Response) -> AsyncGenerator[str, None]:
        async for chunk in response.aiter_lines():
            if not chunk: continue
            try:
                data = json.loads(chunk)
                token: str = data.get("message", {}).get("content", "")
                if not token: continue
                for char in token:
                    yield char
            except Exception as e:
                self.logger.error(f"JSON stream parsing exception: {e}")
                self.logger.error(f"chunk content: {chunk}")
