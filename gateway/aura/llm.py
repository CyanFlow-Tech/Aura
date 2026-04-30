import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator, Callable

import httpx

from .utils.mlogging import LoggingMixin


def _parse_json_message(chunk: str) -> str | None:
    data = json.loads(chunk)
    return data.get("message", {}).get("content", "")


def _parse_sse_choice_delta(chunk: str) -> str | None:
    if not chunk:
        return None
    if not chunk.startswith("data: "):
        return None
    try:
        data = json.loads(chunk[len("data: "):])
    except json.JSONDecodeError:
        return None
    if delta := data.get("choices", [{}])[0].get("delta", {}):
        return delta.get("content", delta.get("reasoning_content", ""))
    return None


@dataclass(frozen=True)
class LLMProviderSpec:
    name: str
    parser: Callable[[str], str | None]
    api_url_env: str | None = None
    api_key_env: str | None = None


PROVIDER_SPECS = {
    "NNECLOUD": LLMProviderSpec(name="NNECLOUD", parser=_parse_json_message),
    "HUNYUAN": LLMProviderSpec(
        name="HUNYUAN",
        parser=_parse_sse_choice_delta,
        api_url_env="HUNYUAN_API_URL",
        api_key_env="HUNYUAN_API_KEY",
    ),
    "ZHIYUAN": LLMProviderSpec(
        name="ZHIYUAN",
        parser=_parse_sse_choice_delta,
        api_url_env="ZHIYUAN_API_URL",
        api_key_env="ZHIYUAN_API_KEY",
    ),
}


class LLM(LoggingMixin):

    def __init__(
        self,
        provider: str,
        api_url: str,
        model_name: str,
        temperature: float,
        system_prompt: str,
        timeout: float,
        char_separators: str,
        char_batch_size: int
    ):
        super().__init__()
        provider_name, normalized_api_url = self._normalize_provider_config(
            provider, api_url
        )
        provider_spec = self._resolve_provider_spec(provider_name)

        self.model_name = model_name
        self.provider = provider_spec.name
        self.api_url = self._resolve_api_url(provider_spec, normalized_api_url)
        self.temperature = temperature
        self.system_prompt = system_prompt
        self.timeout = timeout
        self.char_separators = set(char_separators)
        self.char_batch_size = char_batch_size
        self._parse_response = provider_spec.parser
        self._auth_headers = self._build_auth_headers(provider_spec)

        self.logger.info(
            f"LLM initialized: provider={self.provider} "
            f"model={self.model_name} url={self.api_url}"
        )

    @staticmethod
    def _normalize_provider_config(
        provider: str,
        api_url: str,
    ) -> tuple[str, str]:
        provider_name = (provider or "NNECLOUD").upper()
        api_url_value = api_url.strip()
        compatibility_alias = api_url_value.upper()
        if (
            compatibility_alias in PROVIDER_SPECS
            and not api_url_value.startswith("http")
            and provider_name == "NNECLOUD"
        ):
            provider_name = compatibility_alias
            api_url_value = ""
        return provider_name, api_url_value

    @staticmethod
    def _resolve_provider_spec(provider: str) -> LLMProviderSpec:
        try:
            return PROVIDER_SPECS[provider.upper()]
        except KeyError as exc:
            available = ", ".join(sorted(PROVIDER_SPECS))
            raise ValueError(
                f"Unsupported LLM provider {provider!r}. Available: {available}"
            ) from exc

    @staticmethod
    def _resolve_api_url(provider: LLMProviderSpec, api_url: str) -> str:
        if api_url.startswith("http"):
            return api_url
        if provider.api_url_env is None:
            raise ValueError(
                f"LLM provider {provider.name} requires an explicit HTTP api_url"
            )
        resolved = os.getenv(provider.api_url_env, "").strip()
        if not resolved:
            raise ValueError(
                f"Environment variable {provider.api_url_env} is required "
                f"for LLM provider {provider.name}"
            )
        return resolved

    @staticmethod
    def _build_auth_headers(provider: LLMProviderSpec) -> dict[str, str]:
        if provider.api_key_env is None:
            return {}
        api_key = os.getenv(provider.api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                f"Environment variable {provider.api_key_env} is required "
                f"for LLM provider {provider.name}"
            )
        return {"Authorization": f"Bearer {api_key}"}

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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                self.api_url,
                json=payload,
                headers=self._auth_headers,
            ) as response:
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
                token = self._parse_response(chunk)
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
