"""Audio keepalive for `/audio_stream`.

While the LLM is still cooking the first sentence, the device's MP3 player
sees no bytes and trips its 30s read-timeout, aborting the stream.
We side-step that by yielding pre-encoded MP3 bytes — a "heartbeat" stream
— while we wait for the first real chunk.

Two assets, both loaded once at startup
---------------------------------------
- **content**: the on-disk `assets/heartbeat.mp3` (whatever the operator
  dropped in — silence, soft "嗯", breath, …) re-encoded to match the
  live TTS frame format.
- **silence**: a tiny (~250 ms) clip of pure silence at the same format,
  generated from `anullsrc` via ffmpeg.

Why the silence asset matters
-----------------------------
Android's `MediaPlayer` (used on-device through `audioplayers`) does
adaptive pre-buffering: if the byte arrival rate stays below the audio
playback rate, the player keeps buffering and never starts emitting
sound. Sending one ~1 s heartbeat clip every 10 s is well below the
playback rate (~10%), so the device piles up 20 s of audio and only
starts playing once the real TTS bursts in faster than realtime —
producing the observed "all heartbeats and reply collapsed at the end"
artefact.

The fix is to keep the byte stream flowing slightly *faster* than
playback rate. We do that by yielding the small silence clip every
`silence_duration * 0.9` seconds and only swapping in the audible
content clip every `heartbeat_interval_s` seconds. Network cost is
modest (~16 kB/s = the playback bitrate) and the device starts emitting
sound within the first few hundred ms.

Both clips have ID3v2 / Xing headers stripped at encode time, and the
TTS chunks are likewise stripped on the way out, so the entire HTTP
body is a flat sequence of MP3 frames at one consistent format —
required because `MediaPlayer` locks onto the format parameters of the
very first frame it parses and silently drops anything that disagrees.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from utils.mlogging import Logger

_logger = Logger.build("Heartbeat")


@dataclass(frozen=True)
class HeartbeatAssets:
    """Pre-encoded MP3 assets used by `audio_stream` while waiting for
    the first real TTS chunk."""

    content_mp3: bytes
    content_duration_s: float
    silence_mp3: bytes
    silence_duration_s: float

    @property
    def enabled(self) -> bool:
        return bool(self.content_mp3) and bool(self.silence_mp3)


def strip_id3v2(data: bytes) -> bytes:
    """Drop the leading ID3v2 tag (if any) from a chunk of MP3 bytes.

    We splice multiple MP3 segments together inside one HTTP audio
    response. Each segment that comes from the TTS engine (CosyVoice)
    starts with its own ID3v2 tag — leaving them in place puts metadata
    blocks at random offsets in the live stream, which Android's
    `MediaPlayer` handles inconsistently (occasional pop / brief stall).
    Stripping is cheap and makes the stream a flat sequence of MP3
    frames at the negotiated sample-rate / channel layout.

    Bytes that do not start with "ID3" are returned unchanged.
    """
    if len(data) < 10 or data[:3] != b"ID3":
        return data
    flags = data[5]
    size_bytes = data[6:10]
    size = (
        ((size_bytes[0] & 0x7F) << 21)
        | ((size_bytes[1] & 0x7F) << 14)
        | ((size_bytes[2] & 0x7F) << 7)
        | (size_bytes[3] & 0x7F)
    )
    # ID3v2.4 may append a 10-byte footer (flags bit 4). v2.3 cannot.
    footer_len = 10 if (flags & 0x10) else 0
    return data[10 + size + footer_len:]


def _bitrate_kbps(target_bitrate: str) -> int:
    """Parse a libmp3lame `-b:a` value like '128k' / '64k' to int kbps."""
    s = target_bitrate.strip().lower().rstrip("k")
    return int(s)


def _cbr_duration_s(byte_count: int, bitrate_kbps: int) -> float:
    """Approximate MP3 duration assuming CBR. Close enough for our use."""
    if bitrate_kbps <= 0:
        return 0.0
    return byte_count * 8.0 / (bitrate_kbps * 1000.0)


def _transcode_mp3(
    src_path: Path,
    sample_rate: int,
    channels: int,
    bitrate: str,
) -> bytes:
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-y",
        "-i", str(src_path),
        "-vn",
        "-codec:a", "libmp3lame",
        "-ar", str(sample_rate),
        "-ac", str(channels),
        "-b:a", bitrate,
        "-id3v2_version", "0",
        "-write_xing", "0",
        "-f", "mp3",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True)
    return proc.stdout


def _generate_silence_mp3(
    duration_s: float,
    sample_rate: int,
    channels: int,
    bitrate: str,
) -> bytes:
    layout = "mono" if channels == 1 else "stereo"
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl={layout}",
        "-t", f"{duration_s}",
        "-codec:a", "libmp3lame",
        "-b:a", bitrate,
        "-id3v2_version", "0",
        "-write_xing", "0",
        "-f", "mp3",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, check=True, capture_output=True)
    return proc.stdout


# Padding clip target duration. ~250 ms is a good balance: short enough
# to react quickly when the first real TTS chunk arrives, long enough to
# keep encoding overhead negligible (~4 kB per clip @ 128 kbps).
_SILENCE_TARGET_DURATION_S = 0.25


def load_heartbeat_assets(
    mp3_path: Path,
    target_sample_rate: int,
    target_channels: int,
    target_bitrate: str,
) -> HeartbeatAssets:
    """Load the on-disk heartbeat asset and produce the pair of MP3
    byte-blobs the audio stream uses as keepalive.

    Returns a disabled `HeartbeatAssets` (empty bytes) if the on-disk
    asset is missing or `ffmpeg` is unavailable; callers should treat
    `assets.enabled is False` as "skip the keepalive entirely".
    """
    if not mp3_path.is_file():
        _logger.warning(
            f"heartbeat asset missing at {mp3_path}; heartbeats disabled"
        )
        return HeartbeatAssets(b"", 0.0, b"", 0.0)

    if shutil.which("ffmpeg") is None:
        _logger.warning("ffmpeg not on PATH; heartbeats disabled")
        return HeartbeatAssets(b"", 0.0, b"", 0.0)

    try:
        kbps = _bitrate_kbps(target_bitrate)
    except ValueError:
        _logger.error(
            f"invalid heartbeat_target_bitrate {target_bitrate!r}; "
            "heartbeats disabled"
        )
        return HeartbeatAssets(b"", 0.0, b"", 0.0)

    try:
        content = _transcode_mp3(
            mp3_path, target_sample_rate, target_channels, target_bitrate
        )
        silence = _generate_silence_mp3(
            _SILENCE_TARGET_DURATION_S,
            target_sample_rate,
            target_channels,
            target_bitrate,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="ignore").strip()
        _logger.error(f"ffmpeg failed building heartbeat assets: {stderr}")
        return HeartbeatAssets(b"", 0.0, b"", 0.0)

    content_dur = _cbr_duration_s(len(content), kbps)
    silence_dur = _cbr_duration_s(len(silence), kbps)
    _logger.info(
        f"heartbeat assets loaded: "
        f"content={len(content)}B (~{content_dur:.2f}s), "
        f"silence={len(silence)}B (~{silence_dur:.3f}s) "
        f"@ {target_sample_rate}Hz/{target_channels}ch/{target_bitrate} "
        f"(source={mp3_path.name})"
    )
    return HeartbeatAssets(
        content_mp3=content,
        content_duration_s=content_dur,
        silence_mp3=silence,
        silence_duration_s=silence_dur,
    )
