import assert from 'node:assert/strict';
import test from 'node:test';
import { transcriptionReview } from '../app/frontend/src/utils/transcriptionReview.js';

const row = (start, end, text, confidence = -0.2) => ({ start, end, text, confidence });

test('sustained repetition links to every original row without changing subtitles', () => {
  const rows = [row(0, 2, 'Introduction'), ...Array.from({ length: 19 }, (_, i) =>
    row(208 + i * 8, 210 + i * 8, i % 2 ? 'thanks for watching!' : 'Thanks for watching.'))];
  const original = structuredClone(rows);
  assert.deepEqual(transcriptionReview(rows, 510), Array.from({ length: 19 }, (_, i) => ({ row: i + 2, reason: 'asrReviewRepeat' })));
  assert.deepEqual(rows, original);
});

test('repetition is language-neutral, not a phrase blacklist', () => {
  for (const text of ['Okay.', '你好！', 'رحبا', 'A different sentence']) {
    const rows = Array.from({ length: 6 }, (_, i) => row(i * 8, i * 8 + 2, text));
    assert.equal(transcriptionReview(rows).length, 6);
  }
  assert.deepEqual(transcriptionReview([row(10, 12, 'Thanks for watching!')]), []);
});

test('short cheering, small runs, blank and distinct rows do not trigger repeated-text hints', () => {
  assert.deepEqual(transcriptionReview(Array.from({ length: 12 }, (_, i) => row(i, i + 0.5, 'Whoa!'))), []);
  assert.deepEqual(transcriptionReview(Array.from({ length: 5 }, (_, i) => row(i * 8, i * 8 + 2, 'Thanks.'))), []);
  assert.deepEqual(transcriptionReview(Array.from({ length: 8 }, (_, i) => row(i * 8, i * 8 + 2, '   '))), []);
  assert.deepEqual(transcriptionReview(Array.from({ length: 8 }, (_, i) => row(i * 8, i * 8 + 2, `Line ${i}`))), []);
});

test('boundary candidates need low known confidence and end-of-media geometry', () => {
  const rows = [row(0, 8, 'Thank you for watching!', -1.0075), row(16, 24, 'This.'), row(1609, 1609.578688, 'You', -0.707)];
  assert.deepEqual(transcriptionReview(rows, 1609.564), [
    { row: 1, reason: 'asrReviewOpening' }, { row: 3, reason: 'asrReviewEnding' },
  ]);
  for (const duration of [undefined, NaN, Infinity, 0, 1800]) {
    assert.deepEqual(transcriptionReview(rows, duration), [{ row: 1, reason: 'asrReviewOpening' }]);
  }
  assert.deepEqual(transcriptionReview([row(5, 6, 'You', -0.9)], 20), []);
  assert.deepEqual(transcriptionReview([row(0, 8, 'Normal speech.', -0.2)], 8), []);
});

test('unknown legacy confidence is not evidence; manual source edits survive refresh', () => {
  for (const confidence of [null, undefined, NaN, Infinity, -1, '-1.2']) {
    assert.deepEqual(transcriptionReview([row(0, 0.5, 'You', confidence)], 0.5), []);
  }
  const rows = [row(0, 8, 'Corrected opening', -1.2), row(40, 40.5, 'No', -0.9)].map(item => ({ ...item, source_text_edited: true }));
  assert.deepEqual(transcriptionReview(JSON.parse(JSON.stringify(rows)), 40.5), []);
  assert.deepEqual(transcriptionReview(Array.from({ length: 8 }, (_, i) => ({ ...row(i * 8, i * 8 + 2, 'A chant'), source_text_edited: true }))), []);
});

test('missing rows, invalid timestamps and out-of-range tail do not yield broken links', () => {
  assert.deepEqual(transcriptionReview(null), []);
  assert.deepEqual(transcriptionReview([]), []);
  assert.deepEqual(transcriptionReview([row(NaN, Infinity, 'You', -1.1)], 10), []);
  assert.deepEqual(transcriptionReview([row(40, 42, 'You', -0.9)], 40.5), []);
});
