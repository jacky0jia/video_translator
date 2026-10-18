from pathlib import Path
import shutil
import subprocess

import pytest


def test_processing_callbacks_belong_to_their_task_scope():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not available')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, '--test', str(root / 'tests/task-processing-scope.test.cjs')],
        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
