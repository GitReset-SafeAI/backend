"""Bounded WAV validation and draft transcription, without report persistence."""

import asyncio
import struct
import wave
from collections.abc import AsyncIterable
from io import BytesIO
from threading import BoundedSemaphore
from typing import Protocol

from app.modules.reporting.schemas import TranscriptionResponse
from app.schemas.report import LanguageHint

UPLOAD_TIMEOUT_SECONDS = 30.0
# A thread-safe, non-waiting limit works across event loops and dependency instances.
# Each process has its own two slots, covering upload, validation, and provider work.
_TRANSCRIPTION_SLOTS = BoundedSemaphore(2)


class TranscriptionError(Exception):
    status_code = 503
    detail = "Audio transcription is unavailable."


class InvalidAudio(TranscriptionError):
    status_code = 422
    detail = "Audio is invalid or contains no usable speech."


class AudioTooLarge(TranscriptionError):
    status_code = 413
    detail = "Audio exceeds the permitted size or duration."


class UnsupportedAudio(TranscriptionError):
    status_code = 415
    detail = "Only audio/wav with supported 16-bit PCM audio is accepted."


def validate_transcript(text: object) -> str:
    """Provider text is never logged, truncated, or coerced into a transcript."""
    if not isinstance(text, str) or len(text) > 5000:
        raise TranscriptionError
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise TranscriptionError from None
    if not text.strip():
        raise InvalidAudio
    return text


def validate_wav(audio: bytes, max_bytes: int, max_duration: float) -> bytes:
    """Check complete RIFF framing and PCM metadata, then strip unrelated chunks."""
    if len(audio) > max_bytes:
        raise AudioTooLarge
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise InvalidAudio
    if struct.unpack_from("<I", audio, 4)[0] + 8 != len(audio):
        raise InvalidAudio

    offset = 12
    parameters: tuple[int, int, int] | None = None
    samples: memoryview | None = None
    while offset < len(audio):
        if len(audio) - offset < 8:
            raise InvalidAudio
        chunk_id = audio[offset : offset + 4]
        size = struct.unpack_from("<I", audio, offset + 4)[0]
        start = offset + 8
        end = start + size
        next_offset = end + size % 2
        if next_offset > len(audio):
            raise InvalidAudio
        if chunk_id == b"fmt ":
            if parameters is not None or size < 16:
                raise InvalidAudio
            encoding, channels, rate, byte_rate, alignment, bits = struct.unpack_from(
                "<HHIIHH", audio, start
            )
            if encoding != 1 or bits != 16 or channels not in (1, 2) or not 8000 <= rate <= 48000:
                raise UnsupportedAudio
            if size not in (16, 18) or (
                size == 18 and struct.unpack_from("<H", audio, start + 16)[0]
            ):
                raise InvalidAudio
            if alignment != channels * 2 or byte_rate != rate * alignment:
                raise InvalidAudio
            parameters = (channels, rate, alignment)
        elif chunk_id == b"data":
            if parameters is None or samples is not None or size == 0 or size % parameters[2]:
                raise InvalidAudio
            samples = memoryview(audio)[start:end]
        offset = next_offset

    if parameters is None or samples is None:
        raise InvalidAudio
    channels, rate, alignment = parameters
    frames = len(samples) // alignment
    if frames > max_duration * rate:
        raise AudioTooLarge
    if not any(samples):
        raise InvalidAudio  # Digital silence; this is not a general speech detector.

    # Forward only a canonical header and validated samples, excluding uploaded metadata.
    output = BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples)
    return output.getvalue()


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, language_hint: LanguageHint) -> str: ...


class TranscriptionService:
    def __init__(self, provider: Transcriber, max_bytes: int, max_duration: float) -> None:
        self._provider = provider
        self.max_bytes = max_bytes
        self.max_duration = max_duration

    async def transcribe(
        self,
        chunks: AsyncIterable[bytes],
        language_hint: LanguageHint,
        declared_length: int | None = None,
    ) -> TranscriptionResponse:
        if not _TRANSCRIPTION_SLOTS.acquire(blocking=False):
            raise TranscriptionError
        try:
            if declared_length is not None and declared_length > self.max_bytes:
                raise AudioTooLarge
            buffer = bytearray()
            try:
                async with asyncio.timeout(UPLOAD_TIMEOUT_SECONDS):
                    async for chunk in chunks:
                        if len(chunk) > self.max_bytes - len(buffer):
                            raise AudioTooLarge
                        buffer.extend(chunk)
            except TimeoutError:
                raise InvalidAudio from None
            if declared_length is not None and len(buffer) != declared_length:
                raise InvalidAudio
            audio = validate_wav(bytes(buffer), self.max_bytes, self.max_duration)
            buffer.clear()
            transcript = validate_transcript(await self._provider.transcribe(audio, language_hint))
            return TranscriptionResponse(
                transcript=transcript, requested_language_hint=language_hint
            )
        finally:
            _TRANSCRIPTION_SLOTS.release()
