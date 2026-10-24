"""Bounded uploads, strict PCM WAV validation, and isolated draft transcription."""

import asyncio
import struct

import pytest

from app.modules.reporting.transcription.service import (
    AudioTooLarge,
    InvalidAudio,
    TranscriptionError,
    TranscriptionService,
    UnsupportedAudio,
    validate_wav,
)
from app.schemas.report import LanguageHint


class Provider:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, LanguageHint]] = []
        self.text = "  Synthetic draft: I am not sure.\n"

    async def transcribe(self, audio: bytes, language_hint: LanguageHint) -> str:
        self.calls.append((audio, language_hint))
        return self.text


async def chunks(*parts):
    for part in parts:
        yield part


def service(provider: Provider) -> TranscriptionService:
    return TranscriptionService(provider, max_bytes=10 * 1024 * 1024, max_duration=60)


@pytest.mark.parametrize("hint", list(LanguageHint))
def test_returns_reviewable_draft_without_changing_text(
    wav_audio: bytes, hint: LanguageHint
) -> None:
    provider = Provider()
    result = asyncio.run(service(provider).transcribe(chunks(wav_audio[:10], wav_audio[10:]), hint))
    assert result.transcript == provider.text
    assert result.requested_language_hint is hint
    assert result.review_required is True
    assert set(result.model_dump()) == {"transcript", "requested_language_hint", "review_required"}
    assert provider.calls == [(wav_audio, hint)]


@pytest.mark.parametrize("channels", [1, 2])
@pytest.mark.parametrize("rate", [8000, 16000, 44100, 48000])
def test_valid_pcm_rates_and_channels(wav_factory, rate: int, channels: int) -> None:
    audio = wav_factory(rate=rate, channels=channels)
    assert validate_wav(audio, len(audio), 60) == audio


@pytest.mark.parametrize("width", [1, 3, 4])
def test_other_bit_depths_are_unsupported(wav_factory, width: int) -> None:
    with pytest.raises(UnsupportedAudio):
        validate_wav(wav_factory(width=width), 10485760, 60)


@pytest.mark.parametrize("rate", [1, 7999, 48001, 96000])
def test_unsupported_sample_rates(wav_factory, rate: int) -> None:
    with pytest.raises(UnsupportedAudio):
        validate_wav(wav_factory(rate=rate), 10485760, 60)


def test_surround_channels_are_unsupported(wav_factory) -> None:
    with pytest.raises(UnsupportedAudio):
        validate_wav(wav_factory(channels=3), 10485760, 60)


@pytest.mark.parametrize("encoding", [3, 6, 7, 65534])
def test_non_pcm_encoding_is_unsupported(wav_audio: bytes, encoding: int) -> None:
    audio = bytearray(wav_audio)
    struct.pack_into("<H", audio, 20, encoding)
    with pytest.raises(UnsupportedAudio):
        validate_wav(bytes(audio), 10485760, 60)


@pytest.mark.parametrize("payload", [b"", b"not audio", b"RIFF", b"RIFF\x00\x00\x00\x00WAVE"])
def test_empty_and_spoofed_audio(payload: bytes) -> None:
    with pytest.raises(InvalidAudio):
        validate_wav(payload, 10485760, 60)


@pytest.mark.parametrize(
    "mutation",
    [
        "truncate",
        "append",
        "riff",
        "alignment",
        "byte_rate",
        "data_size",
        "duplicate",
        "no_fmt",
        "no_data",
        "chunk_header",
    ],
)
def test_inconsistent_wav_structure(wav_audio: bytes, mutation: str) -> None:
    audio = bytearray(wav_audio)
    if mutation == "truncate":
        audio.pop()
    elif mutation == "append":
        audio.extend(b"x")
    elif mutation == "riff":
        struct.pack_into("<I", audio, 4, 0)
    elif mutation == "alignment":
        struct.pack_into("<H", audio, 32, 1)
    elif mutation == "byte_rate":
        struct.pack_into("<I", audio, 28, 1)
    elif mutation == "data_size":
        struct.pack_into("<I", audio, 40, 1)
    elif mutation == "duplicate":
        audio.extend(audio[36:])
        struct.pack_into("<I", audio, 4, len(audio) - 8)
    elif mutation == "no_fmt":
        audio[12:16] = b"JUNK"
    elif mutation == "no_data":
        audio[36:40] = b"JUNK"
    else:
        audio.extend(b"tail")
        struct.pack_into("<I", audio, 4, len(audio) - 8)
    with pytest.raises(InvalidAudio):
        validate_wav(bytes(audio), 10485760, 60)


def test_unrelated_wav_metadata_is_not_forwarded(wav_audio: bytes) -> None:
    private_metadata = b"synthetic-private-metadata"
    extra = b"LIST" + struct.pack("<I", len(private_metadata)) + private_metadata
    extra += b"\0" if len(private_metadata) % 2 else b""
    audio = bytearray(wav_audio + extra)
    struct.pack_into("<I", audio, 4, len(audio) - 8)
    assert validate_wav(bytes(audio), 10485760, 60) == wav_audio


def test_zero_frames_and_silence_fail_without_provider(wav_factory) -> None:
    provider = Provider()
    for audio in (wav_factory(frames=0), wav_factory(silent=True)):
        with pytest.raises(InvalidAudio):
            asyncio.run(service(provider).transcribe(chunks(audio), LanguageHint.UNKNOWN))
    assert provider.calls == []


def test_size_and_duration_boundaries(wav_audio: bytes, wav_factory) -> None:
    assert validate_wav(wav_audio, len(wav_audio), 60) == wav_audio
    with pytest.raises(AudioTooLarge):
        validate_wav(wav_audio, len(wav_audio) - 1, 60)
    at_limit = wav_factory(frames=8000 * 60, rate=8000)
    assert validate_wav(at_limit, 10485760, 60) == at_limit
    with pytest.raises(AudioTooLarge):
        validate_wav(wav_factory(frames=8000 * 60 + 1, rate=8000), 10485760, 60)


@pytest.mark.parametrize("declared_length", [None, 1])
def test_actual_stream_size_is_bounded_and_remaining_chunks_are_not_read(
    wav_audio: bytes, declared_length: int | None
) -> None:
    provider = Provider()
    transcription = TranscriptionService(provider, max_bytes=len(wav_audio), max_duration=60)

    async def oversized():
        yield wav_audio
        yield b"x"
        raise AssertionError("Must stop reading as soon as the limit is exceeded")

    with pytest.raises(AudioTooLarge):
        asyncio.run(transcription.transcribe(oversized(), LanguageHint.ENGLISH, declared_length))
    assert provider.calls == []


def test_declared_oversize_is_rejected_before_stream_read(wav_audio: bytes) -> None:
    provider = Provider()

    async def unread():
        raise AssertionError("Body must not be read")
        yield b""  # pragma: no cover

    with pytest.raises(AudioTooLarge):
        asyncio.run(service(provider).transcribe(unread(), LanguageHint.ENGLISH, 10485761))
    assert provider.calls == []


def test_inconsistent_content_length_is_rejected(wav_audio: bytes) -> None:
    with pytest.raises(InvalidAudio):
        asyncio.run(service(Provider()).transcribe(chunks(wav_audio), LanguageHint.UNKNOWN, 1))


def test_upload_timeout_releases_capacity(
    monkeypatch: pytest.MonkeyPatch, wav_audio: bytes
) -> None:
    monkeypatch.setattr("app.modules.reporting.transcription.service.UPLOAD_TIMEOUT_SECONDS", 0.01)
    provider = Provider()

    async def slow():
        await asyncio.sleep(1)
        yield wav_audio

    with pytest.raises(InvalidAudio):
        asyncio.run(service(provider).transcribe(slow(), LanguageHint.UNKNOWN))
    assert provider.calls == []
    assert asyncio.run(
        service(provider).transcribe(chunks(wav_audio), LanguageHint.UNKNOWN)
    ).review_required


def test_two_slots_cover_uploads_and_fail_fast_then_release(wav_audio: bytes, caplog) -> None:
    async def scenario():
        started = 0
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def held_upload():
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await release.wait()
            yield wav_audio

        provider = Provider()
        tasks = [
            asyncio.create_task(service(provider).transcribe(held_upload(), LanguageHint.UNKNOWN))
            for _ in range(2)
        ]
        try:
            await both_started.wait()
            with pytest.raises(TranscriptionError):
                await service(provider).transcribe(chunks(wav_audio), LanguageHint.UNKNOWN)
            assert provider.calls == []
        finally:
            release.set()
            await asyncio.gather(*tasks)
        assert len(provider.calls) == 2
        assert (
            await service(provider).transcribe(chunks(wav_audio), LanguageHint.UNKNOWN)
        ).review_required

    asyncio.run(scenario())
    assert "transcription_failure stage=capacity category=busy" in caplog.text


def test_cancellation_releases_capacity(wav_audio: bytes) -> None:
    async def scenario():
        started = asyncio.Event()

        async def held():
            started.set()
            await asyncio.Event().wait()
            yield b""

        task = asyncio.create_task(service(Provider()).transcribe(held(), LanguageHint.UNKNOWN))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (
            await service(Provider()).transcribe(chunks(wav_audio), LanguageHint.UNKNOWN)
        ).review_required

    asyncio.run(scenario())


def test_missing_converter_logs_preparation_and_does_not_call_provider(caplog):
    provider = Provider()
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(
            service(provider).transcribe(
                chunks(b"synthetic-private-audio"), LanguageHint.UNKNOWN, media_type="audio/mp4"
            )
        )
    assert error.value.status_code == 503 and provider.calls == []
    assert (
        "transcription_failure stage=conversion_preparation category=converter_missing"
        in caplog.text
    )
    assert "synthetic-private-audio" not in caplog.text
