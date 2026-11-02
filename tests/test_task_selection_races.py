from pathlib import Path
import shutil
import subprocess

import pytest


def test_app_request_ordering_and_selection_ownership():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is not available')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, '--test', str(root / 'tests/task-selection-races.test.cjs')],
        cwd=root, capture_output=True, text=True, encoding='utf-8',
    )
    assert result.returncode == 0, result.stdout + result.stderr
