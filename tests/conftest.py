"""Synthetic audio fixtures; no real recordings or personal information."""

import os
import shutil
import subprocess
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


@pytest.fixture
def media_factory(tmp_path):
    """Generate tiny fictional tones locally; never download recordings or call ASR."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        if os.environ.get("SAFEROUTE_REQUIRE_FFMPEG") == "1":
            pytest.fail("FFmpeg and FFprobe are required for conversion integration tests")
        pytest.skip("FFmpeg and FFprobe are not installed")

    def create(extension="m4a", *, duration=0.25, video_only=False, with_video=False, silent=False):
        destination = tmp_path / f"synthetic.{extension}"
        if video_only:
            source = f"color=c=black:s=16x16:d={duration}"
            encoding = ["-an", "-c:v", "mpeg4"]
        else:
            source = (
                f"anullsrc=r=44100:cl=mono:d={duration}"
                if silent
                else f"sine=frequency=440:sample_rate=44100:duration={duration}"
            )
            encoding = ["-c:a", "libmp3lame" if extension == "mp3" else "aac"]
        extra_input = []
        if with_video:
            extra_input = ["-f", "lavfi", "-i", f"color=c=black:s=16x16:d={duration}"]
            encoding += ["-c:v", "mpeg4", "-shortest"]
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                source,
                *extra_input,
                *encoding,
                str(destination),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
            shell=False,
        )
        return destination.read_bytes()

    return create
