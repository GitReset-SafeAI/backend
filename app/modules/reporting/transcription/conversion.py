"""Bounded local media decoding; only canonical PCM audio leaves this adapter."""

import asyncio
import json
import math
import os
import shutil
import subprocess
import tempfile
import wave
from io import BytesIO
from pathlib import Path
from threading import Lock

from app.modules.reporting.transcription.service import (
    AudioTooLarge,
    InvalidAudio,
    TranscriptionError,
    UnsupportedAudio,
    validate_wav,
)

SAMPLE_RATE = 16000
MAX_PROBE_BYTES = 16384
CONTAINERS = {
    "audio/mp4": ("mov", "aac"),
    "audio/x-m4a": ("mov", "aac"),
    "video/mp4": ("mov", "aac"),
    "audio/mpeg": ("mp3", "mp3"),
}


class _ProcessState:
    """Let async cancellation terminate a synchronous, cross-platform child."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._process: subprocess.Popen | None = None
        self._cancelled = False

    def attach(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            if self._cancelled:
                self._kill()

    def detach(self) -> None:
        with self._lock:
            self._process = None

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            self._kill()

    def _kill(self) -> None:
        if self._process is not None and self._process.poll() is None:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass  # Child exited between poll and kill.


def _run(
    arguments: list[str], directory: Path, state: _ProcessState, limit: int, *, decoding=False
) -> bytes:
    # Do not inherit provider secrets, FFREPORT, proxy configuration, or loader overrides.
    environment = {
        name: os.environ[name] for name in ("SYSTEMROOT", "WINDIR") if name in os.environ
    }
    environment["AV_LOG_FORCE_NOCOLOR"] = "1"
    with subprocess.Popen(
        arguments,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=directory,
        env=environment,
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) as process:
        state.attach(process)
        try:
            output = bytearray()
            while chunk := process.stdout.read(65536):
                if len(chunk) > limit - len(output):
                    if decoding:
                        raise AudioTooLarge
                    raise InvalidAudio
                output.extend(chunk)
            if process.wait() != 0:
                raise InvalidAudio
            return bytes(output)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            state.detach()


def _probe_result(body: bytes, container: str, codec: str, max_duration: float) -> None:
    try:
        result = json.loads(body)
        actual = result["format"]["format_name"].split(",")
        if container not in actual:
            raise UnsupportedAudio
        streams = result["streams"]
        if len(streams) != 1 or streams[0]["codec_type"] != "audio":
            raise InvalidAudio
        stream = streams[0]
        if stream["codec_name"] != codec:
            raise UnsupportedAudio
        if int(stream["channels"]) not in (1, 2) or not 8000 <= int(stream["sample_rate"]) <= 48000:
            raise UnsupportedAudio
        # The selected audio stream's duration is a useful early rejection, not authority.
        # Container duration can describe unselected video or other audio streams.
        duration = stream.get("duration")
        if duration not in (None, "N/A"):
            duration = float(duration)
            if not math.isfinite(duration) or duration < 0:
                raise InvalidAudio
            if duration > max_duration:
                raise AudioTooLarge
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise InvalidAudio from None


class FFmpegAudioConverter:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self._timeout_seconds = timeout_seconds

    async def convert(
        self, audio: bytes | bytearray, media_type: str, max_duration: float
    ) -> bytes:
        if media_type not in CONTAINERS:
            raise UnsupportedAudio
        if not audio:
            raise InvalidAudio
        if len(audio) > 10485760 or not 0 < max_duration <= 60:
            raise AudioTooLarge
        if not math.isfinite(self._timeout_seconds) or not 0 < self._timeout_seconds <= 30:
            raise TranscriptionError
        ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise TranscriptionError

        state = _ProcessState()
        # Popen in a worker also works with Windows Uvicorn's Selector event loop.
        # Shield the worker so cancellation cannot release its slot or delete input
        # before the child is killed, reaped, and the private directory is removed.
        task = asyncio.create_task(
            asyncio.to_thread(
                self._convert, audio, media_type, max_duration, ffmpeg, ffprobe, state
            )
        )
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await asyncio.shield(task)
        except BaseException as error:
            state.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue  # Repeated cancellation must not abandon a running child.
                except Exception:
                    break
            if not task.cancelled():
                task.exception()  # Consume a worker error without replacing cancellation.
            if isinstance(error, TimeoutError):
                raise TranscriptionError from None
            raise

    @staticmethod
    def _convert(audio, media_type, max_duration, ffmpeg, ffprobe, state) -> bytes:
        try:
            container, codec = CONTAINERS[media_type]
            with tempfile.TemporaryDirectory(prefix="saferoute-audio-") as temporary:
                directory = Path(temporary)
                source = directory / "input.media"
                source.write_bytes(audio)
                # Only these demuxers/protocols can be opened. MOV external
                # data references and absolute reference paths are explicitly disabled.
                options = [
                    "-max_alloc",
                    "16777216",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    container,
                    "-probesize",
                    "10485760",
                    "-analyzeduration",
                    "5000000",
                    "-threads",
                    "1",
                ]
                if container == "mov":
                    # MOV headers contain the required audio metadata. Skipping
                    # stream-info decoding prevents video frame decoding in the probe.
                    options += [
                        "-enable_drefs",
                        "0",
                        "-use_absolute_path",
                        "0",
                        "-nofind_stream_info",
                    ]
                probe = _run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        *options,
                        "-select_streams",
                        "a:0",
                        "-show_entries",
                        "stream=codec_name,codec_type,sample_rate,channels,duration:format=format_name",
                        "-of",
                        "json",
                        str(source),
                    ],
                    directory,
                    state,
                    MAX_PROBE_BYTES,
                )
                _probe_result(probe, container, codec, max_duration)
                max_pcm = math.floor(max_duration * SAMPLE_RATE) * 2
                pcm = _run(
                    [
                        ffmpeg,
                        "-nostdin",
                        "-hide_banner",
                        "-v",
                        "error",
                        "-xerror",
                        *options,
                        "-codec_whitelist",
                        "mp3,mp3float" if codec == "mp3" else codec,
                        "-vn",
                        "-sn",
                        "-dn",
                        "-i",
                        str(source),
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-sn",
                        "-dn",
                        "-map_metadata",
                        "-1",
                        "-map_chapters",
                        "-1",
                        "-filter_threads",
                        "1",
                        "-ac",
                        "1",
                        "-ar",
                        str(SAMPLE_RATE),
                        "-c:a",
                        "pcm_s16le",
                        "-threads",
                        "1",
                        "-t",
                        str(max_duration + 1),
                        "-f",
                        "s16le",
                        "pipe:1",
                    ],
                    directory,
                    state,
                    max_pcm,
                    decoding=True,
                )
                if not pcm or len(pcm) % 2:
                    raise InvalidAudio
                # A seekable in-memory WAV writer creates accurate RIFF sizes, unlike
                # FFmpeg's nonseekable WAV stdout. The decoded sample count is bounded
                # independently of container metadata; overlong output is rejected.
                output = BytesIO()
                with wave.open(output, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(SAMPLE_RATE)
                    wav.writeframes(pcm)
                return validate_wav(output.getvalue(), max_pcm + 44, max_duration)
        except (OSError, subprocess.SubprocessError):
            raise TranscriptionError from None
