import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import subprocess
from threading import Barrier
from types import SimpleNamespace
import wave

from fastapi import UploadFile
import pytest

from app.services import audio_service as module


@pytest.fixture
def extraction(monkeypatch, tmp_path):
    temp = tmp_path / "temporary"
    monkeypatch.setattr(module.settings, "TEMP_DIR", temp)
    service = module.AudioExtractionService()
    service.ffmpeg_path = "test-ffmpeg"
    return service, temp


def copy_audio(command, **kwargs):
    source = Path(command[command.index("-i") + 1])
    output = Path(command[-1])
    assert output.is_file()  # destination was atomically reserved
    assert command[command.index("-acodec") + 1] == "pcm_s16le"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-ac") + 1] == "1"
    assert kwargs["check"] is True
    output.write_bytes(source.read_bytes())
    return SimpleNamespace(stdout="", stderr="", returncode=0)


@pytest.mark.parametrize("kind", ["same_path", "same_basename", "upload"])
def test_concurrent_extractions_cannot_overwrite_or_delete_each_other(extraction, monkeypatch, tmp_path, kind):
    service, temp = extraction
    original = tmp_path / "source.mp4"
    original.write_bytes(b"first video")
    second = tmp_path / "other" / "source.mp4"
    second.parent.mkdir()
    second.write_bytes(b"second video")
    expected = [b"first video", b"first video" if kind == "same_path" else b"second video"]
    if kind == "upload":
        sources = [UploadFile(file=BytesIO(data), filename="source.mp4") for data in expected]
    else:
        sources = [str(original), str(original if kind == "same_path" else second)]
    barrier = Barrier(2)

    def concurrent_copy(command, **kwargs):
        barrier.wait(timeout=10)
        return copy_audio(command, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", concurrent_copy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outputs = list(pool.map(lambda source: asyncio.run(service.extract_audio(source)), sources))
    assert outputs[0] != outputs[1]
    assert [path.read_bytes() for path in outputs] == expected
    assert all(path.parent == temp for path in outputs)
    assert not list(temp.glob("input_*"))
    asyncio.run(service.cleanup_audio(outputs[0]))
    assert outputs[1].read_bytes() == expected[1]
    assert original.read_bytes() == b"first video"
    assert second.read_bytes() == b"second video"


def test_upload_filename_cannot_escape_temp_directory(extraction, monkeypatch, tmp_path):
    service, temp = extraction
    sentinel = tmp_path / "outside.mp4"
    sentinel.write_bytes(b"do not touch")
    commands = []

    def capture(command, **kwargs):
        commands.append(command)
        assert Path(command[command.index("-i") + 1]).parent == temp
        return copy_audio(command, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", capture)
    upload = UploadFile(file=BytesIO(b"uploaded"), filename="../../outside.mp4")
    output = asyncio.run(service.extract_audio(upload))
    assert output.read_bytes() == b"uploaded"
    assert sentinel.read_bytes() == b"do not touch"
    assert len(commands) == 1
    assert list(temp.iterdir()) == [output]


@pytest.mark.parametrize("failure", ["ffmpeg", "launch", "upload_read"])
def test_failures_clean_only_invocation_owned_files(extraction, monkeypatch, tmp_path, failure):
    service, temp = extraction
    temp.mkdir()
    sentinel = temp / "extracted_other.wav"
    sentinel.write_bytes(b"another running task")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")

    def fail(command, **kwargs):
        Path(command[-1]).write_bytes(b"partial output")
        if failure == "ffmpeg":
            raise subprocess.CalledProcessError(1, command, stderr="decode failed")
        raise OSError("cannot start ffmpeg")

    monkeypatch.setattr(module.subprocess, "run", fail)
    upload = UploadFile(file=BytesIO(b"uploaded"), filename="source.mp4")
    if failure == "upload_read":
        async def fail_read(_size):
            raise OSError("upload interrupted")
        monkeypatch.setattr(upload, "read", fail_read)
    with pytest.raises(RuntimeError if failure == "ffmpeg" else OSError):
        asyncio.run(service.extract_audio(upload))
    assert list(temp.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"another running task"
    assert source.read_bytes() == b"source"


def test_missing_ffmpeg_creates_no_files(extraction):
    service, temp = extraction
    service.ffmpeg_path = ""
    with pytest.raises(RuntimeError, match="FFmpeg executable not found"):
        asyncio.run(service.extract_audio("unused.mp4"))
    assert not temp.exists()


def test_cancelled_upload_removes_its_partial_input(extraction, monkeypatch):
    service, temp = extraction
    upload = UploadFile(file=BytesIO(b"uploaded"), filename="source.mp4")

    async def cancelled(_size):
        raise asyncio.CancelledError()

    monkeypatch.setattr(upload, "read", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.extract_audio(upload))
    assert list(temp.iterdir()) == []


def test_real_ffmpeg_handles_reserved_output_and_extensionless_media(extraction, tmp_path):
    service, temp = extraction
    real_ffmpeg = module.settings.ffmpeg_path
    if not real_ffmpeg or not Path(real_ffmpeg).is_file():
        pytest.skip("Bundled FFmpeg is not available")
    service.ffmpeg_path = real_ffmpeg
    source = tmp_path / "tiny.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\0\0" * 1600)
    original_bytes = source.read_bytes()
    first = asyncio.run(service.extract_audio(str(source)))
    upload = UploadFile(file=BytesIO(original_bytes), filename="tiny.wav")
    second = asyncio.run(service.extract_audio(upload))
    assert first != second
    for output in (first, second):
        with wave.open(str(output), "rb") as audio:
            assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 16000)
            assert audio.getnframes() == 1600
    assert source.read_bytes() == original_bytes
    assert set(temp.iterdir()) == {first, second}
