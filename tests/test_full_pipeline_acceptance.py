import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "full_pipeline_acceptance", Path(__file__).with_name("manual_full_pipeline_acceptance.py")
)
acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(acceptance)


def test_acceptance_accepts_timing_preserving_translation():
    source = [{"start": 0, "end": 2, "text": "Hello"}]
    target = [{"start": 0, "end": 2, "text": "你好"}]
    acceptance.validate_alignment(source, target)


@pytest.mark.parametrize("source,target", [
    ([], []),
    ([{"start": 0, "end": 2, "text": "Hi"}], []),
    ([{"start": 0, "end": 2, "text": "Hi"}], [{"start": 1, "end": 2, "text": "你好"}]),
    ([{"start": 0, "end": 2, "text": "Hi"}], [{"start": 0, "end": 2, "text": " "}]),
    ([{"start": 0, "end": 2, "text": "Hi"}], [{"start": 0, "end": 2, "text": "[Translation failed]"}]),
])
def test_acceptance_rejects_bad_translations(source, target):
    with pytest.raises(RuntimeError):
        acceptance.validate_alignment(source, target)


def test_review_labels_use_one_based_segment_numbers():
    acceptance.validate_review_rows([1, 4], 4)
    acceptance.validate_review_rows([], 0)


@pytest.mark.parametrize("rows", [[0], [5], [-1], [True], [1.5], ["2"]])
def test_acceptance_rejects_out_of_range_review_labels(rows):
    with pytest.raises(RuntimeError):
        acceptance.validate_review_rows(rows, 4)
