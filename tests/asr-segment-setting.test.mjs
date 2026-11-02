import assert from 'node:assert/strict';
import test from 'node:test';
import { translations } from '../app/frontend/src/i18n/translations.js';

test('subtitle segment duration has localized labels and removes obsolete ASR window labels', () => {
  assert.equal(translations.en.subtitleSegmentSeconds, 'Subtitle segment duration (seconds)');
  assert.equal(translations.zh.subtitleSegmentSeconds, '字幕分片时间（秒）');
  assert.equal(Object.hasOwn(translations.en, 'asrChunkLength'), false);
  assert.equal(Object.hasOwn(translations.zh, 'asrChunkLength'), false);
});
