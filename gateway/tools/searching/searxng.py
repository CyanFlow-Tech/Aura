from typing import Annotated

import httpx

from utils.mlogging import LoggingMixin

from .searching import SearchResult, Searching


@Searching.register_impl()
class SearXNG(Searching, LoggingMixin):

    def __init__(
        self,
        api_url: Annotated[str, "SearXNG JSON API endpoint"] = "http://127.0.0.1:8888/search",
        user_agent: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        ),
        accept_language: str = "zh-CN,zh;q=0.9,en;q=0.8",
        timeout: float = 10.0,
        connect_timeout: float = 5.0,
    ):
        super().__init__()
        self.api_url = api_url
        self.user_agent = user_agent
        self.accept_language = accept_language
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.logger.info(f"SearXNG initialized: {self.api_url}")

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        params = {"q": query, "format": "json"}
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": self.accept_language,
            # Pretend the request came from a local client; SearXNG is
            # typically bound to localhost and rejects non-local origins.
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
        }
        timeout = httpx.Timeout(self.timeout, connect=self.connect_timeout)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(self.api_url, params=params, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as e:
            self.logger.error(f"SearXNG request failed: {e!r}")
            return []

        raw_results = data.get("results", [])[:limit]
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
            )
            for r in raw_results
        ]
