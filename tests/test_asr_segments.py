from types import SimpleNamespace

import pytest

from app.services.asr_segments import (
    LOCAL_ASR_CONTEXT_SECONDS,
    split_faster_whisper_segments,
)


def word(start, end, text):
    return SimpleNamespace(start=start, end=end, word=text)


def segment(words=None, *, start=0, end=20, text=" original upstream text "):
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=-0.35, words=words)


def values(rows):
    return [(row.start, row.end, row.text, row.confidence) for row in rows]


@pytest.mark.parametrize("end", [5, 8])
def test_short_and_exact_duration_preserve_upstream_fields(end):
    upstream = segment([word(1, 2, "different")], end=end)
    assert values(split_faster_whisper_segments([upstream], 8)) == [
        (0, end, " original upstream text ", -0.35)
    ]


@pytest.mark.parametrize("punctuation", list(".?!,;:。！？；：，"))
def test_uses_latest_punctuation_within_limit(punctuation):
    words = [word(0, 2, "First."), word(2, 5, " Next" + punctuation),
             word(5, 8, " more"), word(8, 10, " finish.")]
    rows = split_faster_whisper_segments([segment(words, end=10)], 8)
    assert values(rows) == [
        (0, 5, "First. Next" + punctuation, -0.35),
        (5, 10, "more finish.", -0.35),
    ]
    assert "".join(row.text.replace(" ", "") for row in rows) == (
        "First.Next" + punctuation + "morefinish."
    )
    assert all(left.end <= right.start for left, right in zip(rows, rows[1:]))


def test_chinese_words_keep_order_without_inserting_spaces():
    words = [word(0, 3, "你好，"), word(3, 7, "世界。"), word(7, 10, "再见！")]
    rows = split_faster_whisper_segments([segment(words, end=10)], 8)
    assert values(rows) == [(0, 7, "你好，世界。", -0.35), (7, 10, "再见！", -0.35)]
    assert "".join(row.text for row in rows) == "你好，世界。再见！"


def test_without_punctuation_uses_latest_word_end_inclusive_limit():
    words = [word(0, 3, "one"), word(3, 8, " two"), word(8, 11, " three"),
             word(11, 16, " four"), word(16, 19, " five")]
    rows = split_faster_whisper_segments(iter([segment(words)]), 8)
    assert values(rows) == [(0, 8, "one two", -0.35), (8, 16, "three four", -0.35),
                            (16, 19, "five", -0.35)]


def test_punctuation_after_limit_is_not_selected():
    words = [word(0, 8, "one"), word(8, 9, " two."), word(9, 18, " three")]
    assert values(split_faster_whisper_segments([segment(words)], 8)) == [
        (0, 8, "one", -0.35), (8, 9, "two.", -0.35), (9, 18, "three", -0.35)
    ]


@pytest.mark.parametrize("words", [
    None, [],
    [word(None, 2, "one")], [word(0, None, "one")],
    [word(float("nan"), 2, "one")], [word(0, float("nan"), "one")],
    [word(float("inf"), 2, "one")], [word(0, float("inf"), "one")],
    [word(3, 2, "one")], [word(2, 2, "one")],
    [word(2, 3, "one"), word(0, 1, " two")],
    [word(0, 4, "one"), word(3, 6, " two")],
    [word(-1, 2, "one")], [word(0, 21, "one")],
    [word(0, 2, "   ")], [word(0, 2, None)],
    [SimpleNamespace(start=0, end=2)],
    [word("bad", 2, "one")],
    # The blank second fragment must roll back even a valid earlier fragment.
    [word(0, 8, "one."), word(8, 17, "   ")],
])
def test_malformed_words_preserve_only_the_affected_original_segment(words):
    upstream = segment(words)
    valid = segment([word(30, 38, "good"), word(38, 40, " words")], start=30, end=40)
    rows = split_faster_whisper_segments([upstream, valid], 8)
    assert values(rows) == [(0, 20, " original upstream text ", -0.35),
                            (30, 38, "good", -0.35), (38, 40, "words", -0.35)]


def test_missing_words_attribute_preserves_original():
    upstream = segment()
    del upstream.words
    assert values(split_faster_whisper_segments([upstream], 8)) == [
        (0, 20, " original upstream text ", -0.35)
    ]


def test_long_gap_starts_next_fragment_at_its_first_word():
    words = [word(1, 2, "first"), word(25, 26, " next"), word(26, 30, " last")]
    assert values(split_faster_whisper_segments([segment(words, end=30)], 8)) == [
        (1, 2, "first", -0.35), (25, 30, "next last", -0.35)
    ]


def test_overlong_single_word_is_consumed_once_without_empty_rows():
    words = [word(0, 12, "long"), word(12, 14, " next")]
    assert values(split_faster_whisper_segments([segment(words, end=14)], 8)) == [
        (0, 12, "long", -0.35), (12, 14, "next", -0.35)
    ]


def test_custom_limit_controls_fragment_duration_and_input_is_not_mutated():
    words = [word(0, 3, "first"), word(3, 6, " second"), word(6, 9, " third")]
    upstream = segment(words, end=9)
    assert values(split_faster_whisper_segments([upstream], 4)) == [
        (0, 3, "first", -0.35), (3, 6, "second", -0.35), (6, 9, "third", -0.35)
    ]
    assert upstream.words == words
    assert [item.word for item in words] == ["first", " second", " third"]
    assert upstream.text == " original upstream text "


@pytest.mark.parametrize("limit", [0, -1, float("nan"), float("inf")])
def test_invalid_limit_is_rejected(limit):
    with pytest.raises(ValueError):
        split_faster_whisper_segments([], limit)


def test_empty_input_and_fixed_local_context():
    assert split_faster_whisper_segments(iter([]), 8) == []
    assert LOCAL_ASR_CONTEXT_SECONDS == 30
