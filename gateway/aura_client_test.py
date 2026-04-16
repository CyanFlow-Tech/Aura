import asyncio
import json
import os

import httpx
from dotenv import load_dotenv


load_dotenv()


async def main():
    api_key = os.getenv("AURA_API_KEY")
    if not api_key:
        raise RuntimeError("AURA_API_KEY is not set")

    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "GET",
            "http://localhost:8000/api/aura/test",
            headers={"X-Aura-Token": api_key},
            params={"text": "你好，我是Aura，一个智能助手。"},
        ) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue

                payload = line.removeprefix("data: ")
                data = json.loads(payload)
                token = data.get("token", "")
                if token:
                    print(token, end="", flush=True)

    print()


if __name__ == "__main__":
    asyncio.run(main())