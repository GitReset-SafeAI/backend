"""Offline conversion, resource bounds, process lifecycle, and private-file cleanup."""

import asyncio
import json
import subprocess
import threading
import wave
from io import BytesIO
from pathlib import Path

import pytest

from app.modules.reporting.transcription import conversion
from app.modules.reporting.transcription.conversion import FFmpegAudioConverter
from app.modules.reporting.transcription.service import (
    AudioTooLarge,
    InvalidAudio,
    TranscriptionError,
    TranscriptionService,
    UnsupportedAudio,
)
from app.schemas.report import LanguageHint


def probe_body(*, container="mov,mp4,m4a,3gp,3g2,mj2", codec="aac", duration="0.1"):
    return json.dumps(
        {
            "format": {"format_name": container},
            "streams": [
                {
                    "codec_name": codec,
                    "codec_type": "audio",
                    "sample_rate": "44100",
                    "channels": 1,
                    "duration": duration,
                }
            ],
        }
    ).encode()


class Process:
    def __init__(self, body, code=0, *, blocked=False):
        self.code = code
        self.returncode = None
        self.killed = threading.Event()
        self.started = threading.Event()
        self.reader = BytesIO(body)
        self.blocked = blocked
        self.stdout = self

    def read(self, size):
        self.started.set()
        if self.blocked:
            assert self.killed.wait(5), "Cancellation/timeout must kill the child"
            return b""
        return self.reader.read(size)

    def kill(self):
        self.returncode = -9
        self.killed.set()

    def poll(self):
        return self.returncode

    def wait(self):
        self.returncode = self.code if self.returncode is None else self.returncode
        return self.returncode

    def __enter__(self):
        return self

    def __exit__(self, *args):
        assert self.returncode is not None


@pytest.fixture
def processes(monkeypatch, tmp_path):
    monkeypatch.setattr(conversion.shutil, "which", lambda name: str(tmp_path / name))
    monkeypatch.setattr(conversion.tempfile, "tempdir", str(tmp_path))
    calls = []
    plans = [Process(probe_body()), Process(b"\x10\x00" * 1600)]

    def popen(arguments, **kwargs):
        directory = Path(kwargs["cwd"])
        assert (directory / "input.media").read_bytes() == b"synthetic-private-audio"
        assert kwargs["shell"] is False
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert set(kwargs["env"]) <= {"SYSTEMROOT", "WINDIR", "AV_LOG_FORCE_NOCOLOR"}
        process = plans[len(calls)]
        calls.append((arguments, directory, process))
        return process

    monkeypatch.setattr(conversion.subprocess, "Popen", popen)
    return plans, calls


def assert_clean(calls):
    assert calls
    for _, directory, process in calls:
        assert not directory.exists()
        assert process.returncode is not None


def test_conversion_fixed_arguments_and_cleanup(processes, monkeypatch, caplog):
    plans, calls = processes
    monkeypatch.setenv("GROQ_API_KEY", "dummy-private-key")
    monkeypatch.setenv("FFREPORT", "file=should-not-exist.log")
    output = asyncio.run(
        FFmpegAudioConverter().convert(b"synthetic-private-audio", "audio/mp4", 60)
    )
    with wave.open(BytesIO(output)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert wav.getnframes() == 1600
    arguments = calls[1][0]
    for flag, value in {
        "-protocol_whitelist": "file,pipe",
        "-format_whitelist": "mov",
        "-enable_drefs": "0",
        "-use_absolute_path": "0",
        "-map": "0:a:0",
        "-ac": "1",
        "-ar": "16000",
        "-c:a": "pcm_s16le",
        "-f": "s16le",
    }.items():
        assert arguments[arguments.index(flag) + 1] == value
    assert "-vn" in arguments and "-sn" in arguments and "-dn" in arguments
    assert "synthetic-private-audio" not in str(calls) + caplog.text
    assert "dummy-private-key" not in str(calls) + caplog.text
    assert_clean(calls)


@pytest.mark.parametrize("failure", ["probe", "decode", "spawn"])
def test_subprocess_failure_is_sanitized_and_cleans_up(
    processes, monkeypatch, tmp_path, failure, caplog
):
    plans, calls = processes
    if failure == "spawn":

        def unavailable(*args, **kwargs):
            raise OSError("private path and diagnostics")

        monkeypatch.setattr(conversion.subprocess, "Popen", unavailable)
        expected = TranscriptionError
    else:
        plans[0 if failure == "probe" else 1].code = 1
        expected = InvalidAudio
    with pytest.raises(expected) as error:
        asyncio.run(FFmpegAudioConverter().convert(b"synthetic-private-audio", "audio/mp4", 60))
    assert "private" not in str(error.value)
    assert list(tmp_path.glob("saferoute-audio-*")) == []
    if calls:
        assert_clean(calls)
    stage = "conversion_decode" if failure == "decode" else "conversion_probe"
    category = "io_error" if failure == "spawn" else "invalid_media"
    events = [record for record in caplog.records if "transcription_failure" in record.getMessage()]
    assert len(events) == 1
    assert events[0].getMessage() == f"transcription_failure stage={stage} category={category}"
    assert events[0].exc_info is None and events[0].stack_info is None
    for value in ("private path and diagnostics", "synthetic-private-audio", str(tmp_path)):
        assert value not in caplog.text


@pytest.mark.parametrize("missing", ["ffmpeg", "ffprobe"])
def test_missing_runtime_is_controlled_before_temporary_files(
    monkeypatch, tmp_path, missing, caplog
):
    monkeypatch.setattr(conversion.shutil, "which", lambda name: None if name == missing else name)
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(FFmpegAudioConverter().convert(b"data", "audio/mp4", 60))
    assert error.value.status_code == 503
    assert list(tmp_path.iterdir()) == []
    assert (
        "transcription_failure stage=conversion_preparation category=runtime_missing" in caplog.text
    )


@pytest.mark.parametrize("phase", [0, 1], ids=["probe", "decode"])
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_timeout_and_cancellation_kill_reap_and_remove_input(processes, phase, mode, caplog):
    plans, calls = processes
    plans[phase].blocked = True

    async def scenario():
        converter = FFmpegAudioConverter(0.1 if mode == "timeout" else 5)
        task = asyncio.create_task(converter.convert(b"synthetic-private-audio", "audio/mp4", 60))
        if mode == "cancel":
            for _ in range(500):
                if plans[phase].started.is_set():
                    break
                await asyncio.sleep(0.001)
            assert plans[phase].started.is_set()
            task.cancel()
        with pytest.raises(TranscriptionError if mode == "timeout" else asyncio.CancelledError):
            await task
        assert plans[phase].killed.is_set()
        assert_clean(calls)

    asyncio.run(scenario())
    events = [record for record in caplog.records if "transcription_failure" in record.getMessage()]
    if mode == "cancel":
        assert events == []
    else:
        stage = "conversion_probe" if phase == 0 else "conversion_decode"
        assert len(events) == 1
        assert events[0].getMessage() == f"transcription_failure stage={stage} category=timeout"
        assert events[0].exc_info is None and events[0].stack_info is None


@pytest.mark.parametrize(
    "problem",
    [
        "empty",
        "json",
        "no_audio",
        "codec",
        "channels",
        "rate",
        "long",
        "nan",
        "container",
        "too_much_json",
    ],
)
def test_probe_rejects_invalid_streams_without_decode(processes, problem):
    plans, calls = processes
    result = json.loads(probe_body())
    expected = InvalidAudio
    if problem == "empty":
        result = None
    elif problem == "no_audio":
        result["streams"] = []
    elif problem == "codec":
        result["streams"][0]["codec_name"] = "alac"
        expected = UnsupportedAudio
    elif problem == "channels":
        result["streams"][0]["channels"] = 8
        expected = UnsupportedAudio
    elif problem == "rate":
        result["streams"][0]["sample_rate"] = "96000"
        expected = UnsupportedAudio
    elif problem == "long":
        result["streams"][0]["duration"] = "60.1"
        expected = AudioTooLarge
    elif problem == "nan":
        result["streams"][0]["duration"] = "NaN"
    elif problem == "container":
        result["format"]["format_name"] = "mp3"
        expected = UnsupportedAudio
    body = json.dumps(result).encode()
    if problem == "json":
        body = b"private invalid diagnostics"
    elif problem == "too_much_json":
        body = b"x" * (conversion.MAX_PROBE_BYTES + 1)
    plans[0].reader = BytesIO(body)
    with pytest.raises(expected):
        asyncio.run(FFmpegAudioConverter().convert(b"synthetic-private-audio", "audio/mp4", 60))
    assert len(calls) == 1
    assert_clean(calls)


@pytest.mark.parametrize("kind", ["oversized", "odd", "silent", "empty"])
def test_decoded_pcm_is_bounded_independently_of_metadata(processes, kind):
    plans, calls = processes
    output = {"oversized": b"\x10\x00" * 1601, "odd": b"x", "silent": b"\0" * 32, "empty": b""}[
        kind
    ]
    plans[1].reader = BytesIO(output)
    expected = AudioTooLarge if kind == "oversized" else InvalidAudio
    with pytest.raises(expected):
        asyncio.run(FFmpegAudioConverter().convert(b"synthetic-private-audio", "audio/mp4", 0.1))
    assert_clean(calls)


def test_conversion_capacity_is_shared_and_cancellation_releases_it(wav_audio):
    async def scenario():
        active = 0
        both_started = asyncio.Event()
        release = asyncio.Event()

        class Converter:
            async def convert(self, *args):
                nonlocal active
                active += 1
                if active == 2:
                    both_started.set()
                try:
                    await release.wait()
                    return wav_audio
                finally:
                    active -= 1

        class Provider:
            async def transcribe(self, *args):
                return "Synthetic draft"

        async def chunks():
            yield b"data"

        service = TranscriptionService(Provider(), 10485760, 60, Converter())
        tasks = [
            asyncio.create_task(
                service.transcribe(chunks(), LanguageHint.UNKNOWN, media_type="audio/mp4")
            )
            for _ in range(2)
        ]
        try:
            await both_started.wait()
            with pytest.raises(TranscriptionError):
                await service.transcribe(chunks(), LanguageHint.UNKNOWN, media_type="audio/mp4")
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await tasks[0]
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert active == 0
        assert (
            await service.transcribe(chunks(), LanguageHint.UNKNOWN, media_type="audio/mp4")
        ).review_required

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "extension,media_type",
    [("m4a", "audio/mp4"), ("m4a", "audio/x-m4a"), ("mp4", "video/mp4"), ("mp3", "audio/mpeg")],
)
def test_real_conversion_to_canonical_wav(media_factory, extension, media_type):
    audio = media_factory(extension)
    output = asyncio.run(FFmpegAudioConverter().convert(audio, media_type, 60))
    with wave.open(BytesIO(output)) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        assert 0 < wav.getnframes() <= 16000


@pytest.mark.parametrize("kind", ["no_audio", "corrupt", "mismatch", "long"])
def test_real_parser_rejects_unusable_or_mislabelled_media(media_factory, kind):
    audio = media_factory(
        "mp4", video_only=kind == "no_audio", duration=61 if kind == "long" else 0.25
    )
    media_type = "audio/mpeg" if kind == "mismatch" else "video/mp4"
    if kind == "corrupt":
        audio = audio[:30]
    with pytest.raises(AudioTooLarge if kind == "long" else InvalidAudio):
        asyncio.run(FFmpegAudioConverter().convert(audio, media_type, 60))


def test_real_mp4_with_video_extracts_only_audio(media_factory):
    audio = media_factory("mp4", with_video=True)
    output = asyncio.run(FFmpegAudioConverter().convert(audio, "video/mp4", 60))
    with wave.open(BytesIO(output)) as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 16000
        assert 0 < wav.getnframes() <= 16000


@pytest.mark.parametrize("timeout", [0, 31, float("nan"), float("inf")])
def test_invalid_conversion_timeout_logs_only_fixed_configuration_event(processes, timeout, caplog):
    _, calls = processes
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(
            FFmpegAudioConverter(timeout).convert(b"synthetic-private-audio", "audio/mp4", 60)
        )
    assert error.value.status_code == 503 and calls == []
    assert (
        "transcription_failure stage=conversion_preparation category=invalid_configuration"
        in caplog.text
    )


def test_temporary_storage_failure_logs_preparation_without_exception_details(
    processes, monkeypatch, caplog
):
    def unavailable(*args, **kwargs):
        raise PermissionError("dummy-sensitive-filesystem-details")

    monkeypatch.setattr(conversion.tempfile, "TemporaryDirectory", unavailable)
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(FFmpegAudioConverter().convert(b"synthetic-private-audio", "audio/mp4", 60))
    assert error.value.status_code == 503
    assert "transcription_failure stage=conversion_preparation category=io_error" in caplog.text
    assert "dummy-sensitive-filesystem-details" not in caplog.text


@pytest.mark.parametrize("failure", [None, "probe", "decode", "probe_io", "decode_io"])
def test_cleanup_failure_preserves_primary_diagnostic(
    processes, monkeypatch, tmp_path, failure, caplog
):
    plans, calls = processes
    original_directory = conversion.tempfile.TemporaryDirectory

    class FailingCleanup(original_directory):
        def __exit__(self, *args):
            super().__exit__(*args)
            raise PermissionError("dummy-sensitive-cleanup-details")

    monkeypatch.setattr(conversion.tempfile, "TemporaryDirectory", FailingCleanup)
    stage, category = "conversion_cleanup", "io_error"
    if failure is not None:
        phase = 0 if failure.startswith("probe") else 1
        stage = "conversion_probe" if phase == 0 else "conversion_decode"
        if failure.endswith("_io"):

            def unavailable(*args):
                raise OSError("dummy-sensitive-processing-details")

            monkeypatch.setattr(plans[phase], "read", unavailable)
        else:
            plans[phase].code = 1
            category = "invalid_media"
    with pytest.raises(TranscriptionError) as error:
        asyncio.run(FFmpegAudioConverter().convert(b"synthetic-private-audio", "audio/mp4", 60))
    # A cleanup I/O error already produces 503; only its diagnostic precedence changes.
    assert error.value.status_code == 503
    assert error.value.detail == "Audio transcription is unavailable."
    assert_clean(calls)
    events = [record for record in caplog.records if "transcription_failure" in record.getMessage()]
    assert len(events) == 1
    assert events[0].getMessage() == f"transcription_failure stage={stage} category={category}"
    assert events[0].args == (stage, category)
    assert events[0].exc_info is None and events[0].exc_text is None
    assert events[0].stack_info is None
    for sensitive in (
        "dummy-sensitive-processing-details",
        "dummy-sensitive-cleanup-details",
        "synthetic-private-audio",
        str(tmp_path),
    ):
        assert sensitive not in caplog.text + str(error.value)
