"""Opt-in real Qwen acceptance; isolated outputs/history, no user task changes.

Run with the application's Python runtime; optionally pass --repo for the paid
edition. Models are read from --assets (default: this public checkout).
"""
import argparse
import asyncio
import hashlib
import inspect
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def main():
    public = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=public)
    parser.add_argument("--assets", type=Path, default=public)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    from app.core.config import settings
    from app.core.provider_lifecycle import NoopProviderLifecycle
    from app.services.dubbing_service import DubbingService

    logging.basicConfig(level=logging.INFO)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "source.mp4"
    ffmpeg = str(args.assets / "ffmpeg" / "ffmpeg.exe")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=24:duration=24", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(source)], check=True)
    report = {"repo": str(args.repo.resolve()), "provider": "qwen", "runs": []}
    task_state = {}

    def update(_task, data):
        task_state.update(data)

    async def run():
        service = DubbingService(gpu_lifecycle=NoopProviderLifecycle())
        service.ffmpeg_path = ffmpeg
        originals = ["欢迎观看。", "火箭正在飞行。", "感谢您的关注。"]
        texts_by_run = [originals, [originals[0], "火箭已经进入轨道。", originals[2]],
                        [originals[0], "火箭已经进入轨道。", originals[2]]]
        previous_hashes = None
        for index, texts in enumerate(texts_by_run):
            work = output / f"run-{index + 1}"
            work.mkdir()
            # Third run changes timing only, not spoken text.
            shift = 0.25 if index == 2 else 0
            rows = [SimpleNamespace(start=i * 8 + shift, end=i * 8 + 7 + shift,
                                    text=text) for i, text in enumerate(texts)]
            paths = await service._synthesize_all(rows, "zh_female_1", 1.0,
                                                  "acceptance", work, "qwen", "Chinese")
            hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
            counts = dict(task_state["dubbing_synthesis"])
            expected = [(0, 3), (2, 1), (3, 0)][index]
            assert (counts["reused"], counts["synthesized"]) == expected, counts
            if previous_hashes:
                assert hashes[0] == previous_hashes[0] and hashes[2] == previous_hashes[2]
                assert (hashes[1] == previous_hashes[1]) == (index == 2)
            timeline_options = {"provider": "qwen"}
            if "task_id" in inspect.signature(service._build_timeline).parameters:
                timeline_options["task_id"] = "acceptance"
            track = service._build_timeline(rows, paths, 24, work, **timeline_options)
            video = work / "dubbed.mp4"
            service._mux_video(source, track, video, 24, "acceptance")
            assert abs(service._probe_duration(track) - 24) < 0.1
            assert abs(service._probe_duration(video) - 24) < 0.1
            report["runs"].append({"counts": counts, "raw_hashes": hashes,
                                   "texts": texts, "timing": dict(task_state.get("dubbing_timing", {})),
                                   "video": str(video), "track": str(track)})
            previous_hashes = hashes
            print(json.dumps({"run": index + 1, "counts": counts}), flush=True)

    with patch.object(settings, "BASE_DIR", args.assets / "app"), patch.object(
        settings, "TEMP_DIR", output / "cache"
    ), patch("app.services.dubbing_service.history_manager.update_task", side_effect=update), patch(
        "app.services.dubbing_service.emit_event"
    ):
        asyncio.run(run())
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output / "report.json"), flush=True)


if __name__ == "__main__":
    main()
