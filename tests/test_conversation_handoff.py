from pathlib import Path
import subprocess


def test_conversation_handoff_is_local_only():
    root = Path(__file__).resolve().parents[1]
    path = "specs/next-conversation-handoff.md"
    assert f"/{path}" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    tracked = subprocess.run(["git", "ls-files", "--", path], cwd=root, capture_output=True, text=True, check=True)
    assert not tracked.stdout.strip(), "The handoff must stay outside the Git index"


def test_private_specifications_are_not_tracked():
    root = Path(__file__).resolve().parents[1]
    assert "/specs/" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    tracked = subprocess.run(
        ["git", "ls-files", "--", "specs/", "tests/test_project_task_checklist.py"],
        cwd=root, capture_output=True, text=True, check=True,
    )
    assert not tracked.stdout.strip(), "Private development and handoff records must remain local"
