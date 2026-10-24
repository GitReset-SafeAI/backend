"""Synthetic audio fixtures; no real recordings or personal information."""

import wave
from io import BytesIO

import pytest


@pytest.fixture
def wav_factory():
    def create(*, frames=160, rate=16000, channels=1, width=2, silent=False):
        count = frames * channels * width
        pattern = b"\x10\x00\xf0\xff" if not silent else b"\x00"
        samples = (pattern * ((count + len(pattern) - 1) // len(pattern)))[:count]
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(width)
            wav.setframerate(rate)
            wav.writeframes(samples)
        return output.getvalue()

    return create


@pytest.fixture
def wav_audio(wav_factory) -> bytes:
    return wav_factory()
