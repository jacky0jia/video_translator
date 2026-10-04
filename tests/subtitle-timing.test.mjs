import assert from 'node:assert/strict';
import test from 'node:test';
import { findSubtitleOverlaps, parseSubtitleTime } from '../app/frontend/src/utils/subtitleTiming.js';
import { subtitlePreviewLayout } from '../app/frontend/src/utils/subtitlePreviewLayout.js';

test('overlap detection ignores touching boundaries and flags both rows', () => {
  assert.deepEqual(findSubtitleOverlaps([
    { start: 0, end: 2 }, { start: 2, end: 4 }, { start: 3.5, end: 5 },
  ]), [[2, 3]]);
  assert.equal(parseSubtitleTime('01:59.50'), 119.5);
  assert.ok(Number.isNaN(parseSubtitleTime('bad')));
});

test('preview baseline scales with source geometry and letterboxing', () => {
  const layout = subtitlePreviewLayout({ containerWidth: 640, containerHeight: 400, videoWidth: 1920, videoHeight: 1080, fontSize: 18, offsetY: 10 });
  assert.equal(layout.imageWidth, 640);
  assert.equal(layout.bottom, 20 + 80 * 360 / 1080 + 10);
  assert.equal(layout.fontSize, 18);
});
