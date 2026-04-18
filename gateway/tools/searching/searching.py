from dataclasses import dataclass

from utils.config_tool import AutoConfigMixin
from utils.polymorphic import FactoryMixin


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    content: str


class Searching(AutoConfigMixin, FactoryMixin):

    implement = 'SearXNG'

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        raise NotImplementedError("Subclasses must implement this method")
