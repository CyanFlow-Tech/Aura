#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


DEFAULT_GATEWAY_BASE = "http://127.0.0.1:8000/api/aura"
DEFAULT_TTS_URL = "http://127.0.0.1:50000/api/tts"
DEFAULT_SPEAKER = "中文女"
DEFAULT_SAMPLE_RATE = 16000
DEFAULT_CHANNELS = 1
DEFAULT_SAMPLE_WIDTH = 2
DEFAULT_OUTPUT_DIR = str(
    Path(__file__).resolve().parents[1] / "tmp" / "client_outputs"
)


@dataclass
class StreamResult:
    text: str
    audio_path: Path
    session_id: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Quick Aura gateway client: synthesize speech, upload PCM, consume text/audio streams."
    )
    parser.add_argument(
        "--text",
        required=True,
        help="Text to synthesize through the local TTS service before uploading to gateway.",
    )
    parser.add_argument(
        "--gateway-base",
        default=DEFAULT_GATEWAY_BASE,
        help=f"Gateway API base URL. Default: {DEFAULT_GATEWAY_BASE}",
    )
    parser.add_argument(
        "--tts-url",
        default=DEFAULT_TTS_URL,
        help=f"Local TTS URL. Default: {DEFAULT_TTS_URL}",
    )
    parser.add_argument(
        "--speaker",
        default=DEFAULT_SPEAKER,
        help=f"TTS speaker name. Default: {DEFAULT_SPEAKER}",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("AURA_API_KEY", ""),
        help="Aura API token. Defaults to env AURA_API_KEY.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Reuse an existing Aura session_id for multi-turn testing.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where synthesized/uploaded/streamed audio artifacts are written.",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Optional output filename prefix. Defaults to a timestamp.",
    )
    parser.add_argument(
        "--complete-session",
        action="store_true",
        help="Call /session_complete after the turn finishes.",
    )
    parser.add_argument(
        "--save-tts-audio",
        action="store_true",
        help="Also save the raw TTS response bytes before PCM conversion.",
    )
    return parser.parse_args()


def require_token(token: str) -> None:
    if token:
        return
    raise SystemExit(
        "Missing Aura token. Pass --token or set AURA_API_KEY before running."
    )


def build_headers(token: str) -> dict[str, str]:
    return {"X-Aura-Token": token}


def make_prefix(user_prefix: str | None) -> str:
    if user_prefix:
        return user_prefix
    return datetime.now().strftime("%Y%m%d_%H%M%S")


async def synthesize_tts_audio(
    client: httpx.AsyncClient,
    tts_url: str,
    text: str,
    speaker: str,
) -> bytes:
    payload = {
        "text": text,
        "speaker": speaker,
        "stream": True,
    }
    chunks: list[bytes] = []
    async with client.stream("POST", tts_url, json=payload) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            if chunk:
                chunks.append(chunk)
    audio = b"".join(chunks)
    if not audio:
        raise RuntimeError("TTS returned empty audio")
    return audio


def transcode_to_pcm_s16le_mono_16k(audio_bytes: bytes) -> bytes:
    cmd = [
        "ffmpeg",
        "-loglevel",
        "error",
        "-y",
        "-i",
        "pipe:0",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(DEFAULT_SAMPLE_RATE),
        "-ac",
        str(DEFAULT_CHANNELS),
        "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        input=audio_bytes,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="ignore").strip()
        raise RuntimeError(f"ffmpeg transcoding failed: {stderr or proc.returncode}")
    if not proc.stdout:
        raise RuntimeError("ffmpeg produced empty PCM output")
    return proc.stdout


async def upload_audio(
    client: httpx.AsyncClient,
    gateway_base: str,
    token: str,
    pcm_bytes: bytes,
    session_id: str | None,
) -> str:
    data: dict[str, str] = {}
    if session_id:
        data["session_id"] = session_id
    files = {
        "audio_file": ("command.pcm", pcm_bytes, "application/octet-stream"),
    }
    response = await client.post(
        f"{gateway_base}/upload",
        headers=build_headers(token),
        data=data,
        files=files,
    )
    response.raise_for_status()
    payload = response.json()
    returned_session_id = payload.get("session_id")
    if not returned_session_id:
        raise RuntimeError(f"upload response missing session_id: {payload}")
    return returned_session_id


async def consume_text_stream(
    client: httpx.AsyncClient,
    gateway_base: str,
    token: str,
    session_id: str,
) -> str:
    parts: list[str] = []
    headers = {
        **build_headers(token),
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    async with client.stream(
        "GET",
        f"{gateway_base}/text_stream/{session_id}",
        headers=headers,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            content = line[6:].strip()
            if not content:
                continue
            if content == "[DONE]":
                break
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
            token_text = payload.get("token")
            if not isinstance(token_text, str):
                continue
            parts.append(token_text)
            print(f"[text] {token_text}", flush=True)
    return "".join(parts)


async def consume_audio_stream(
    client: httpx.AsyncClient,
    gateway_base: str,
    token: str,
    session_id: str,
    output_path: Path,
) -> Path:
    url = f"{gateway_base}/audio_stream/{session_id}.mp3"
    params = {"token": token}
    with output_path.open("wb") as handle:
        async with client.stream("GET", url, params=params) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    handle.write(chunk)
    return output_path


async def complete_session(
    client: httpx.AsyncClient,
    gateway_base: str,
    token: str,
    session_id: str,
) -> None:
    response = await client.post(
        f"{gateway_base}/session_complete",
        headers=build_headers(token),
        params={"session_id": session_id},
    )
    response.raise_for_status()


async def run_turn(args: argparse.Namespace) -> StreamResult:
    require_token(args.token)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = make_prefix(args.prefix)
    tts_audio_path = output_dir / f"{prefix}_tts_audio.bin"
    pcm_path = output_dir / f"{prefix}_upload.pcm"
    stream_audio_path = output_dir / f"{prefix}_stream.mp3"

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        print(f"[step] synthesize via {args.tts_url}", flush=True)
        tts_audio = await synthesize_tts_audio(
            client=client,
            tts_url=args.tts_url,
            text=args.text,
            speaker=args.speaker,
        )
        if args.save_tts_audio:
            tts_audio_path.write_bytes(tts_audio)
            print(f"[file] saved raw TTS audio to {tts_audio_path}", flush=True)

        print("[step] transcode TTS audio to 16k/mono/16-bit PCM", flush=True)
        pcm_bytes = transcode_to_pcm_s16le_mono_16k(tts_audio)
        pcm_path.write_bytes(pcm_bytes)
        seconds = len(pcm_bytes) / (
            DEFAULT_SAMPLE_RATE * DEFAULT_CHANNELS * DEFAULT_SAMPLE_WIDTH
        )
        print(f"[file] saved upload PCM to {pcm_path} ({seconds:.2f}s)", flush=True)

        print("[step] upload PCM to gateway", flush=True)
        session_id = await upload_audio(
            client=client,
            gateway_base=args.gateway_base.rstrip("/"),
            token=args.token,
            pcm_bytes=pcm_bytes,
            session_id=args.session_id,
        )
        print(f"[session] {session_id}", flush=True)

        print("[step] consume text_stream and audio_stream", flush=True)
        text_task = asyncio.create_task(
            consume_text_stream(
                client=client,
                gateway_base=args.gateway_base.rstrip("/"),
                token=args.token,
                session_id=session_id,
            )
        )
        audio_task = asyncio.create_task(
            consume_audio_stream(
                client=client,
                gateway_base=args.gateway_base.rstrip("/"),
                token=args.token,
                session_id=session_id,
                output_path=stream_audio_path,
            )
        )
        text, audio_path = await asyncio.gather(text_task, audio_task)

        if args.complete_session:
            print("[step] complete session", flush=True)
            await complete_session(
                client=client,
                gateway_base=args.gateway_base.rstrip("/"),
                token=args.token,
                session_id=session_id,
            )

    return StreamResult(text=text, audio_path=audio_path, session_id=session_id)


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(run_turn(args))
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except httpx.HTTPStatusError as exc:
        body = exc.response.text
        print(
            f"HTTP error {exc.response.status_code} for {exc.request.method} {exc.request.url}\n{body}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print("\n=== Result ===")
    print(f"session_id: {result.session_id}")
    print(f"text: {result.text}")
    print(f"audio_stream saved to: {result.audio_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
